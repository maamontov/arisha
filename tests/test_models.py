from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.models import OutboxEvent, OutboxStatus, Payment, PaymentStatus


async def test_payment_create_and_retrieve(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    payment = Payment(
        amount=Decimal("100.50"),
        currency="USD",
        description="Test order",
        payment_metadata={"order_id": "1234", "items": 3},
        idempotency_key="test-key-1",
        webhook_url="https://example.com/webhook",
    )
    async with db_session_factory() as session:
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    assert payment.id is not None
    assert payment.status == PaymentStatus.PENDING.value
    assert payment.created_at is not None
    assert payment.processed_at is None
    assert payment.amount == Decimal("100.50")
    assert payment.payment_metadata == {"order_id": "1234", "items": 3}

    async with db_session_factory() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment.id))
        loaded = result.scalar_one()
        assert loaded.id == payment.id
        assert loaded.currency == "USD"


async def test_payment_idempotency_key_unique(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    p1 = Payment(
        amount=Decimal("10.00"),
        currency="RUB",
        idempotency_key="duplicate-key",
        webhook_url="https://example.com/hook",
    )
    p2 = Payment(
        amount=Decimal("20.00"),
        currency="EUR",
        idempotency_key="duplicate-key",
        webhook_url="https://example.com/hook",
    )

    async with db_session_factory() as session:
        session.add(p1)
        await session.commit()

    async with db_session_factory() as session:
        session.add(p2)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_payment_amount_positive_constraint(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    p = Payment(
        amount=Decimal("-1.00"),
        currency="USD",
        idempotency_key="negative-amount",
        webhook_url="https://example.com/hook",
    )
    async with db_session_factory() as session:
        session.add(p)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_payment_currency_constraint(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    p = Payment(
        amount=Decimal("10.00"),
        currency="GBP",
        idempotency_key="invalid-currency",
        webhook_url="https://example.com/hook",
    )
    async with db_session_factory() as session:
        session.add(p)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_payment_status_constraint(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    p = Payment(
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="invalid-status",
        webhook_url="https://example.com/hook",
        status="unknown",
    )
    async with db_session_factory() as session:
        session.add(p)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_outbox_event_create(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    aggregate_id = uuid4()
    event = OutboxEvent(
        aggregate_id=aggregate_id,
        event_type="payment.created",
        exchange="payments",
        routing_key="payment.created",
        payload={"payment_id": str(aggregate_id), "amount": "50.00"},
        status=OutboxStatus.PENDING.value,
    )
    async with db_session_factory() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)

    assert event.id is not None
    assert event.attempts == 0
    assert event.published_at is None
    assert event.created_at is not None
    assert event.payload["amount"] == "50.00"


async def test_outbox_status_constraint(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    event = OutboxEvent(
        aggregate_id=uuid4(),
        event_type="payment.created",
        exchange="payments",
        routing_key="payment.created",
        payload={},
        status="invalid",
    )
    async with db_session_factory() as session:
        session.add(event)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_outbox_payment_atomic_insert(
    db_engine: AsyncEngine,
    db_session_factory: async_sessionmaker,
    db_clean: None,
) -> None:
    from app.models import OutboxEvent, OutboxStatus, Payment

    payment = Payment(
        amount=Decimal("25.00"),
        currency="EUR",
        idempotency_key="atomic-test",
        webhook_url="https://example.com/hook",
    )
    outbox = OutboxEvent(
        aggregate_id=payment.id,
        event_type="payment.created",
        exchange="payments",
        routing_key="payment.created",
        payload={"payment_id": str(payment.id)},
        status=OutboxStatus.PENDING.value,
    )
    async with db_session_factory() as session:
        session.add(payment)
        await session.flush()
        outbox.aggregate_id = payment.id
        session.add(outbox)
        await session.commit()

    async with db_session_factory() as session:
        from sqlalchemy import func, select

        count_payments = await session.execute(select(func.count()).select_from(Payment))
        count_outbox = await session.execute(select(func.count()).select_from(OutboxEvent))
        assert count_payments.scalar() == 1
        assert count_outbox.scalar() == 1
