from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
)

from app.models.payment import Currency, PaymentStatus

IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]


class PaymentCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        str_max_length=2048,
    )

    amount: Annotated[
        Decimal,
        Field(
            max_digits=18,
            decimal_places=4,
            gt=0,
            description="Payment amount (positive, up to 14 digits before decimal, 4 after)",
        ),
    ]
    currency: Annotated[
        Currency,
        Field(description="Currency code: RUB, USD, or EUR"),
    ]
    description: Annotated[
        str | None,
        Field(default=None, max_length=2000, description="Free-text description"),
    ] = None
    metadata: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="Arbitrary JSON metadata"),
    ] = None
    webhook_url: Annotated[
        AnyHttpUrl,
        Field(description="URL to call when processing completes"),
    ]


class PaymentCreatedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID = Field(validation_alias="id", description="Unique payment identifier")
    status: PaymentStatus = Field(description="Initial payment status")
    created_at: datetime = Field(description="When the payment was created")

    @field_serializer("status")
    def serialize_status(self, value: PaymentStatus | str) -> str:
        return value if isinstance(value, str) else value.value


class PaymentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    amount: Decimal
    currency: str
    description: str | None = None
    payment_metadata: dict[str, Any] | None = Field(
        default=None,
        serialization_alias="metadata",
        description="Arbitrary JSON metadata",
    )
    status: PaymentStatus
    idempotency_key: str = Field(
        description="Idempotency key that was used to create the payment",
    )
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None = None

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("status")
    def serialize_status(self, value: PaymentStatus | str) -> str:
        return value if isinstance(value, str) else value.value


class ErrorResponse(BaseModel):
    detail: str
