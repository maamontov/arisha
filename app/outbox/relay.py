import asyncio
import contextlib
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aio_pika
from faststream.rabbit import RabbitBroker

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.logging import get_logger, setup_logging
from app.messaging.broker import create_broker
from app.messaging.topology import declare_topology
from app.models.outbox import OutboxStatus
from app.repositories.outbox import OutboxRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = get_logger(__name__)


async def run_relay() -> None:
    settings = get_settings()
    setup_logging()
    logger.info(
        "outbox_relay.starting",
        batch_size=settings.outbox_batch_size,
        poll_interval_ms=settings.outbox_poll_interval_ms,
        max_attempts=settings.outbox_max_attempts,
    )

    topology_connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        timeout=10.0,
    )
    try:
        await declare_topology(topology_connection)
        logger.info("outbox_relay.topology_declared")
    finally:
        await topology_connection.close()

    broker = create_broker()
    await broker.start()
    logger.info("outbox_relay.broker_connected")

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown_event.set)

    session_factory = get_session_factory()
    consecutive_failures = 0

    try:
        while not shutdown_event.is_set():
            try:
                has_failures, processed = await _process_batch(broker, session_factory)
                if processed == 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            shutdown_event.wait(),
                            timeout=settings.outbox_poll_interval_ms / 1000.0,
                        )
                else:
                    logger.info("outbox_relay.batch_processed", count=processed)

                if has_failures:
                    consecutive_failures += 1
                    backoff = min(
                        settings.outbox_poll_interval_ms / 1000.0 * (2**consecutive_failures), 30.0
                    )
                    logger.warning(
                        "outbox_relay.backoff",
                        delay_s=backoff,
                        consecutive_failures=consecutive_failures,
                    )
                    await asyncio.sleep(backoff)
                else:
                    consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                logger.exception("outbox_relay.error", error=str(e))
                await asyncio.sleep(min(1.0 * (2**consecutive_failures), 30.0))
    finally:
        await broker.close()
        await dispose_engine()
        logger.info("outbox_relay.stopped")


async def _process_batch(
    broker: RabbitBroker,
    session_factory: "async_sessionmaker",
) -> tuple[bool, int]:
    settings = get_settings()
    has_failures = False

    async with session_factory() as session, session.begin():
        repo = OutboxRepository(session)
        events = await repo.fetch_pending_batch(settings.outbox_batch_size)

        if not events:
            return False, 0

        now = datetime.now(UTC)
        for event in events:
            try:
                await broker.publish(
                    event.payload,
                    exchange=event.exchange,
                    routing_key=event.routing_key,
                    timeout=5.0,
                )
                event.status = OutboxStatus.PUBLISHED.value
                event.published_at = now
                logger.info(
                    "outbox_relay.event_published",
                    event_id=str(event.id),
                    event_type=event.event_type,
                    aggregate_id=str(event.aggregate_id),
                )
            except Exception as e:
                has_failures = True
                event.attempts += 1
                event.last_error = str(e)[:500]
                if event.attempts >= settings.outbox_max_attempts:
                    event.status = OutboxStatus.FAILED.value
                logger.warning(
                    "outbox_relay.publish_failed",
                    event_id=str(event.id),
                    attempts=event.attempts,
                    error=str(e),
                )

    return has_failures, len(events)


if __name__ == "__main__":
    asyncio.run(run_relay())
