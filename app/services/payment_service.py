from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging import get_logger
from app.models.outbox import OutboxEvent, OutboxStatus
from app.models.payment import Payment
from app.repositories.payment import PaymentRepository
from app.schemas.payment import PaymentCreate

logger = get_logger(__name__)


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._payments = PaymentRepository(session)

    async def get_payment(self, payment_id: UUID) -> Payment | None:
        return await self._payments.get_by_id(payment_id)

    async def create_payment(
        self,
        payload: PaymentCreate,
        idempotency_key: str,
    ) -> tuple[Payment, bool]:
        try:
            async with self._session.begin():
                existing = await self._payments.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    logger.info(
                        "payment.idempotency_hit",
                        payment_id=str(existing.id),
                        idempotency_key=idempotency_key,
                    )
                    return existing, False

                payment = Payment(
                    amount=payload.amount,
                    currency=payload.currency.value,
                    description=payload.description,
                    payment_metadata=payload.metadata,
                    idempotency_key=idempotency_key,
                    webhook_url=str(payload.webhook_url),
                )
                self._session.add(payment)
                await self._session.flush()
                await self._session.refresh(payment)

                event_payload: dict[str, Any] = {
                    "event_id": str(uuid4()),
                    "event_type": "payment.created",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payment_id": str(payment.id),
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                    "idempotency_key": payment.idempotency_key,
                    "webhook_url": payment.webhook_url,
                }
                if payment.payment_metadata is not None:
                    event_payload["metadata"] = payment.payment_metadata

                outbox = OutboxEvent(
                    aggregate_id=payment.id,
                    event_type="payment.created",
                    exchange=self._settings.payments_exchange,
                    routing_key=self._settings.payments_routing_key,
                    payload=event_payload,
                    status=OutboxStatus.PENDING.value,
                )
                self._session.add(outbox)

            logger.info(
                "payment.created",
                payment_id=str(payment.id),
                amount=str(payment.amount),
                currency=payment.currency,
            )
            return payment, True
        except IntegrityError as e:
            logger.warning(
                "payment.create_race",
                idempotency_key=idempotency_key,
                error=str(e.orig),
            )
            async with self._session.begin():
                existing = await self._payments.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing, False
            raise
