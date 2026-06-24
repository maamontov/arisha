from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.messaging.consumer import _consume, _handle_failure
from app.messaging.gateway import Gateway
from app.messaging.webhook import WebhookError, WebhookSender
from app.models.payment import Payment, PaymentStatus
from app.schemas.events import PaymentCreatedEvent


def _make_event(
    payment: Payment,
    *,
    event_id: uuid4 | None = None,
) -> PaymentCreatedEvent:
    return PaymentCreatedEvent(
        event_id=event_id or uuid4(),
        payment_id=payment.id,
        amount=Decimal("100.0000"),
        currency=payment.currency,
        idempotency_key=payment.idempotency_key,
        webhook_url=payment.webhook_url,
        metadata=None,
        occurred_at=datetime.now(UTC),
    )


def _make_msg(headers: dict | None = None) -> MagicMock:
    msg = MagicMock(spec=RabbitMessage)
    msg.headers = headers or {}
    return msg


def _make_gateway(returns: bool) -> MagicMock:
    gateway = MagicMock(spec=Gateway)
    gateway.process = AsyncMock(return_value=returns)
    return gateway


def _make_webhook(raises: Exception | None = None) -> MagicMock:
    sender = MagicMock(spec=WebhookSender)
    if raises is None:
        sender.send = AsyncMock()
    else:
        sender.send = AsyncMock(side_effect=raises)
    return sender


async def _create_pending_payment(
    db_session_factory: async_sessionmaker,
    *,
    status: str = PaymentStatus.PENDING.value,
    webhook_url: str = "https://example.com/hook",
) -> Payment:
    payment = Payment(
        amount=Decimal("100.0000"),
        currency="USD",
        idempotency_key=f"key-{uuid4()}",
        webhook_url=webhook_url,
        status=status,
    )
    async with db_session_factory() as session, session.begin():
        session.add(payment)
    return payment


async def test_handle_failure_first_retry_scheduled_with_1s_ttl() -> None:
    event = _make_event(MagicMock())
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _handle_failure(
        event=event,
        error=WebhookError("HTTP 500", status_code=500),
        attempt=0,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["exchange"] == "payments.retry"
    assert call.kwargs["routing_key"] == "payment.created"
    assert call.kwargs["headers"]["x-attempt"] == 1
    assert call.kwargs["headers"]["x-last-error"] == "HTTP 500"
    assert call.kwargs["expiration"] == 1.0

    published_payload = call.args[0]
    assert published_payload["payment_id"] == str(event.payment_id)
    assert published_payload["event_id"] == str(event.event_id)


async def test_handle_failure_second_retry_scheduled_with_2s_ttl() -> None:
    event = _make_event(MagicMock())
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _handle_failure(
        event=event,
        error=ValueError("boom"),
        attempt=1,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["headers"]["x-attempt"] == 2
    assert call.kwargs["expiration"] == 2.0


async def test_handle_failure_third_attempt_goes_to_dlq() -> None:
    event = _make_event(MagicMock())
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _handle_failure(
        event=event,
        error=ValueError("persistent failure"),
        attempt=2,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "dlq"
    call = broker.publish.await_args
    assert call.kwargs["exchange"] == "payments.dlx"
    assert call.kwargs["routing_key"] == "payment.failed"
    assert call.kwargs["headers"]["x-attempt"] == 3
    assert "expiration" not in call.kwargs
    assert "persistent failure" in call.kwargs["headers"]["x-last-error"]


async def test_handle_failure_custom_base_delay() -> None:
    event = _make_event(MagicMock())
    broker = MagicMock()
    broker.publish = AsyncMock()

    await _handle_failure(
        event=event,
        error=ValueError("boom"),
        attempt=0,
        broker=broker,
        max_attempts=10,
        base_delay=0.5,
    )

    call = broker.publish.await_args
    assert call.kwargs["expiration"] == 0.5

    await _handle_failure(
        event=event,
        error=ValueError("boom"),
        attempt=1,
        broker=broker,
        max_attempts=10,
        base_delay=0.5,
    )

    call = broker.publish.await_args
    assert call.kwargs["expiration"] == 1.0


async def test_handle_failure_truncates_long_error() -> None:
    event = _make_event(MagicMock())
    broker = MagicMock()
    broker.publish = AsyncMock()

    long_error = "x" * 1000

    await _handle_failure(
        event=event,
        error=ValueError(long_error),
        attempt=2,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    call = broker.publish.await_args
    assert len(call.kwargs["headers"]["x-last-error"]) == 200


async def test_consume_success_returns_ok(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers={})
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook()
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "ok"
    gateway.process.assert_awaited_once()
    webhook.send.assert_awaited_once()
    broker.publish.assert_not_awaited()


async def test_consume_first_failure_retries_with_attempt_1(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers={})
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["headers"]["x-attempt"] == 1
    assert call.kwargs["expiration"] == 1.0


async def test_consume_uses_existing_attempt_from_headers(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers={"x-attempt": 1})
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["headers"]["x-attempt"] == 2
    assert call.kwargs["expiration"] == 2.0


async def test_consume_third_attempt_goes_to_dlq(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers={"x-attempt": 2})
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "dlq"
    call = broker.publish.await_args
    assert call.kwargs["exchange"] == "payments.dlx"
    assert call.kwargs["headers"]["x-attempt"] == 3


async def test_consume_handles_invalid_attempt_header(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers={"x-attempt": "not-a-number"})
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["headers"]["x-attempt"] == 1


async def test_consume_handles_missing_headers(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    msg = _make_msg(headers=None)
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "retry"
    call = broker.publish.await_args
    assert call.kwargs["headers"]["x-attempt"] == 1


async def test_consume_idempotent_skip_no_retry(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(
        db_session_factory, status=PaymentStatus.SUCCEEDED.value
    )
    event = _make_event(payment)
    msg = _make_msg(headers={"x-attempt": 2})
    gateway = _make_gateway(returns=False)
    webhook = _make_webhook()
    broker = MagicMock()
    broker.publish = AsyncMock()

    result = await _consume(
        event=event,
        msg=msg,
        gateway=gateway,
        webhook_sender=webhook,
        session_factory=db_session_factory,
        broker=broker,
        max_attempts=3,
        base_delay=1.0,
    )

    assert result == "ok"
    broker.publish.assert_not_awaited()
    gateway.process.assert_not_awaited()


async def test_consumer_gateway_success_marks_succeeded_and_sends_webhook(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook()

    from app.messaging.consumer import _process_payment

    await _process_payment(event, gateway, webhook, db_session_factory)

    gateway.process.assert_awaited_once()
    webhook.send.assert_awaited_once()

    call_args = webhook.send.await_args
    assert call_args.args[0] == "https://example.com/hook"
    payload = call_args.args[1]
    assert payload["payment_id"] == str(payment.id)
    assert payload["status"] == PaymentStatus.SUCCEEDED.value

    async with db_session_factory() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment.id))
        loaded = result.scalar_one()
        assert loaded.status == PaymentStatus.SUCCEEDED.value


async def test_consumer_webhook_failure_raises(
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = await _create_pending_payment(db_session_factory)
    event = _make_event(payment)
    gateway = _make_gateway(returns=True)
    webhook = _make_webhook(raises=WebhookError("HTTP 500", status_code=500))

    from app.messaging.consumer import _process_payment

    with pytest.raises(WebhookError):
        await _process_payment(event, gateway, webhook, db_session_factory)

    async with db_session_factory() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment.id))
        loaded = result.scalar_one()
        assert loaded.status == PaymentStatus.PENDING.value
