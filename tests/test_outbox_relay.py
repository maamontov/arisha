from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import get_settings
from app.models.outbox import OutboxEvent, OutboxStatus
from app.outbox.relay import _process_batch


def _create_outbox_event(**overrides: object) -> OutboxEvent:
    defaults: dict[str, object] = {
        "aggregate_id": uuid4(),
        "event_type": "payment.created",
        "exchange": "payments",
        "routing_key": "payment.created",
        "payload": {"event_id": str(uuid4()), "payment_id": str(uuid4())},
    }
    defaults.update(overrides)
    return OutboxEvent(**defaults)  # type: ignore[arg-type]


def _make_mock_broker(publish_side_effect: Exception | None = None) -> MagicMock:
    broker = MagicMock()
    if publish_side_effect is None:
        broker.publish = AsyncMock()
    else:
        broker.publish = AsyncMock(side_effect=publish_side_effect)
    return broker


async def test_relay_no_events_published(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    broker = _make_mock_broker()
    has_failures, processed = await _process_batch(broker, db_session_factory)
    assert processed == 0
    assert has_failures is False
    broker.publish.assert_not_called()


async def test_relay_publishes_pending_event_and_marks_published(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    event = _create_outbox_event()
    async with db_session_factory() as session, session.begin():
        session.add(event)

    broker = _make_mock_broker()
    has_failures, processed = await _process_batch(broker, db_session_factory)
    assert processed == 1
    assert has_failures is False
    broker.publish.assert_awaited_once()
    call_kwargs = broker.publish.await_args.kwargs
    assert call_kwargs["exchange"] == "payments"
    assert call_kwargs["routing_key"] == "payment.created"
    assert call_kwargs["timeout"] == 5.0

    async with db_session_factory() as session:
        result = await session.execute(select(OutboxEvent).where(OutboxEvent.id == event.id))
        loaded = result.scalar_one()
        assert loaded.status == OutboxStatus.PUBLISHED.value
        assert loaded.published_at is not None
        assert loaded.attempts == 0
        assert loaded.last_error is None


async def test_relay_publishes_multiple_events(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    events = [_create_outbox_event() for _ in range(3)]
    async with db_session_factory() as session, session.begin():
        session.add_all(events)

    broker = _make_mock_broker()
    has_failures, processed = await _process_batch(broker, db_session_factory)
    assert processed == 3
    assert has_failures is False
    assert broker.publish.await_count == 3

    async with db_session_factory() as session:
        result = await session.execute(select(OutboxEvent))
        loaded = result.scalars().all()
        assert all(e.status == OutboxStatus.PUBLISHED.value for e in loaded)


async def test_relay_publish_failure_increments_attempts_keeps_pending(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    event = _create_outbox_event()
    async with db_session_factory() as session, session.begin():
        session.add(event)

    broker = _make_mock_broker(publish_side_effect=ConnectionError("broker down"))
    has_failures, processed = await _process_batch(broker, db_session_factory)
    assert processed == 1
    assert has_failures is True
    broker.publish.assert_awaited_once()

    async with db_session_factory() as session:
        result = await session.execute(select(OutboxEvent).where(OutboxEvent.id == event.id))
        loaded = result.scalar_one()
        assert loaded.status == OutboxStatus.PENDING.value
        assert loaded.attempts == 1
        assert loaded.published_at is None
        assert "broker down" in (loaded.last_error or "")


async def test_relay_publish_failure_at_max_attempts_marks_failed(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    settings = get_settings()
    event = _create_outbox_event(attempts=settings.outbox_max_attempts - 1)
    async with db_session_factory() as session, session.begin():
        session.add(event)

    broker = _make_mock_broker(publish_side_effect=ConnectionError("still down"))
    await _process_batch(broker, db_session_factory)

    async with db_session_factory() as session:
        result = await session.execute(select(OutboxEvent).where(OutboxEvent.id == event.id))
        loaded = result.scalar_one()
        assert loaded.status == OutboxStatus.FAILED.value
        assert loaded.attempts == settings.outbox_max_attempts


async def test_relay_preserves_payload(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payload = {
        "event_id": str(uuid4()),
        "event_type": "payment.created",
        "payment_id": str(uuid4()),
        "amount": "42.5000",
        "currency": "USD",
    }
    event = _create_outbox_event(payload=payload)
    async with db_session_factory() as session, session.begin():
        session.add(event)

    broker = _make_mock_broker()
    await _process_batch(broker, db_session_factory)
    published_payload = broker.publish.await_args.args[0]
    assert published_payload == payload


async def test_relay_skips_already_published_events(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    pending = _create_outbox_event()
    published = _create_outbox_event(status=OutboxStatus.PUBLISHED.value)
    failed = _create_outbox_event(status=OutboxStatus.FAILED.value)
    async with db_session_factory() as session, session.begin():
        session.add_all([pending, published, failed])

    broker = _make_mock_broker()
    has_failures, processed = await _process_batch(broker, db_session_factory)
    assert processed == 1
    assert has_failures is False
    broker.publish.assert_awaited_once()
    assert broker.publish.await_args.args[0] == pending.payload


async def test_relay_respects_batch_size(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    settings = get_settings()
    original = settings.outbox_batch_size
    settings.outbox_batch_size = 2
    try:
        events = [_create_outbox_event() for _ in range(5)]
        async with db_session_factory() as session, session.begin():
            session.add_all(events)

        broker = _make_mock_broker()
        has_failures, processed = await _process_batch(broker, db_session_factory)
        assert processed == 2
        assert has_failures is False
        assert broker.publish.await_count == 2

        async with db_session_factory() as session:
            result = await session.execute(select(OutboxEvent).order_by(OutboxEvent.created_at))
            all_events = result.scalars().all()
            published = [e for e in all_events if e.status == OutboxStatus.PUBLISHED.value]
            pending = [e for e in all_events if e.status == OutboxStatus.PENDING.value]
            assert len(published) == 2
            assert len(pending) == 3
    finally:
        settings.outbox_batch_size = original
