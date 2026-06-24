from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

EventType = Literal["payment.created"]


class PaymentCreatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(description="Unique event identifier for deduplication")
    event_type: EventType = "payment.created"
    occurred_at: datetime = Field(description="When the event was emitted")
    payment_id: UUID
    amount: Decimal
    currency: str
    idempotency_key: str
    webhook_url: str
    metadata: dict[str, Any] | None = None

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return str(value)
