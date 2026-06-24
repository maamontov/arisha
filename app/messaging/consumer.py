import asyncio
import contextlib
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aio_pika
import httpx
from faststream.rabbit import RabbitBroker, RabbitMessage
from sqlalchemy import select

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.logging import get_logger, setup_logging
from app.messaging.broker import create_broker
from app.messaging.gateway import Gateway
from app.messaging.topology import (
    PAYMENTS_DLX,
    PAYMENTS_EXCHANGE,
    PAYMENTS_FAILED_ROUTING_KEY,
    PAYMENTS_NEW_QUEUE,
    PAYMENTS_RETRY_EXCHANGE,
    PAYMENTS_ROUTING_KEY,
    declare_topology,
)
from app.messaging.webhook import WebhookSender
from app.models.payment import Payment, PaymentStatus
from app.schemas.events import PaymentCreatedEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = get_logger(__name__)


async def run_consumer() -> None:
    settings = get_settings()
    setup_logging()
    logger.info(
        "consumer.starting",
        prefetch=settings.consumer_prefetch,
        gateway=(
            f"{settings.gateway_min_delay_s}-{settings.gateway_max_delay_s}s"
            f"@{(settings.gateway_success_rate or 0) * 100:.0f}%"
        ),
        max_attempts=settings.payments_max_attempts,
    )

    topology_connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        timeout=10.0,
    )
    try:
        await declare_topology(topology_connection)
        logger.info("consumer.topology_declared")
    finally:
        await topology_connection.close()

    broker = create_broker()
    gateway = Gateway(
        min_delay=settings.gateway_min_delay_s,
        max_delay=settings.gateway_max_delay_s,
        success_rate=settings.gateway_success_rate,
    )
    http_client = httpx.AsyncClient(timeout=settings.webhook_timeout_s)
    webhook_sender = WebhookSender(
        client=http_client,
        max_attempts=settings.webhook_max_attempts,
        base_delay=settings.webhook_retry_base_delay_s,
    )
    session_factory = get_session_factory()

    @broker.subscriber(
        queue=PAYMENTS_NEW_QUEUE,
        exchange=PAYMENTS_EXCHANGE,
    )
    async def process_payment(
        event: PaymentCreatedEvent,
        msg: RabbitMessage,
    ) -> None:
        await _consume(
            event=event,
            msg=msg,
            gateway=gateway,
            webhook_sender=webhook_sender,
            session_factory=session_factory,
            broker=broker,
            max_attempts=settings.payments_max_attempts,
            base_delay=settings.payments_retry_base_delay_s,
        )

    await broker.start()
    logger.info("consumer.connected")

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown_event.set)

    try:
        await shutdown_event.wait()
    finally:
        await http_client.aclose()
        await broker.close()
        await dispose_engine()
        logger.info("consumer.stopped")


async def _consume(
    *,
    event: PaymentCreatedEvent,
    msg: RabbitMessage,
    gateway: Gateway,
    webhook_sender: WebhookSender,
    session_factory: "async_sessionmaker",
    broker: RabbitBroker,
    max_attempts: int,
    base_delay: float,
) -> str:
    headers = msg.headers or {}
    try:
        attempt = int(headers.get("x-attempt", 0))
    except (TypeError, ValueError):
        attempt = 0

    try:
        await _process_payment(event, gateway, webhook_sender, session_factory)
    except Exception as e:
        return await _handle_failure(
            event=event,
            error=e,
            attempt=attempt,
            broker=broker,
            max_attempts=max_attempts,
            base_delay=base_delay,
        )
    return "ok"


async def _process_payment(
    event: PaymentCreatedEvent,
    gateway: Gateway,
    webhook_sender: WebhookSender,
    session_factory: "async_sessionmaker",
) -> None:
    event_id = str(event.event_id)
    payment_id = event.payment_id
    logger.info(
        "consumer.processing",
        event_id=event_id,
        payment_id=str(payment_id),
    )

    async with session_factory() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if payment is None:
            logger.warning(
                "consumer.payment_not_found",
                event_id=event_id,
                payment_id=str(payment_id),
            )
            return
        if payment.status in (PaymentStatus.SUCCEEDED.value, PaymentStatus.FAILED.value):
            logger.info(
                "consumer.payment_already_processed",
                event_id=event_id,
                payment_id=str(payment_id),
                status=payment.status,
            )
            return

    success = await gateway.process()
    new_status = PaymentStatus.SUCCEEDED if success else PaymentStatus.FAILED
    processed_at = datetime.now(UTC)

    payload = {
        "event_id": event_id,
        "event_type": event.event_type,
        "payment_id": str(payment_id),
        "status": new_status.value,
        "amount": str(event.amount),
        "currency": event.currency,
        "processed_at": processed_at.isoformat(),
    }

    await webhook_sender.send(str(event.webhook_url), payload)

    async with session_factory() as session, session.begin():
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one()
        payment.status = new_status.value
        payment.processed_at = processed_at

    logger.info(
        "consumer.processed",
        event_id=event_id,
        payment_id=str(payment_id),
        status=new_status.value,
    )


async def _handle_failure(
    *,
    event: PaymentCreatedEvent,
    error: Exception,
    attempt: int,
    broker: RabbitBroker,
    max_attempts: int,
    base_delay: float,
) -> str:
    new_attempt = attempt + 1
    event_id = str(event.event_id)
    payment_id = str(event.payment_id)
    error_msg = str(error)[:200]

    payload = event.model_dump(mode="json")

    if new_attempt < max_attempts:
        delay = base_delay * (2**attempt)
        await broker.publish(
            payload,
            exchange=PAYMENTS_RETRY_EXCHANGE,
            routing_key=PAYMENTS_ROUTING_KEY,
            headers={"x-attempt": new_attempt, "x-last-error": error_msg},
            expiration=delay,
        )
        logger.warning(
            "consumer.retry_scheduled",
            event_id=event_id,
            payment_id=payment_id,
            attempt=new_attempt,
            max_attempts=max_attempts,
            delay_s=delay,
            error=error_msg,
        )
        return "retry"

    await broker.publish(
        payload,
        exchange=PAYMENTS_DLX,
        routing_key=PAYMENTS_FAILED_ROUTING_KEY,
        headers={"x-attempt": new_attempt, "x-last-error": error_msg},
    )
    logger.error(
        "consumer.dlq",
        event_id=event_id,
        payment_id=payment_id,
        attempts=new_attempt,
        error=error_msg,
    )
    return "dlq"


if __name__ == "__main__":
    asyncio.run(run_consumer())
