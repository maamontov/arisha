from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status

from app.auth import verify_api_key
from app.db import DbSessionDep
from app.logging import get_logger
from app.schemas.payment import (
    ErrorResponse,
    IdempotencyKey,
    PaymentCreate,
    PaymentCreatedResponse,
    PaymentDetailResponse,
)
from app.services.payment_service import PaymentService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentCreatedResponse,
    responses={
        status.HTTP_200_OK: {
            "model": PaymentCreatedResponse,
            "description": "Idempotency hit — existing payment returned",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
    },
    summary="Create payment",
    description="Create a new payment. Idempotent via the Idempotency-Key header.",
)
async def create_payment(
    response: Response,
    payload: PaymentCreate,
    session: DbSessionDep,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    _api_key: Annotated[None, Depends(verify_api_key)],
) -> PaymentCreatedResponse:
    service = PaymentService(session)
    payment, created = await service.create_payment(payload, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return PaymentCreatedResponse.model_validate(payment)


@router.get(
    "/{payment_id}",
    response_model=PaymentDetailResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
    summary="Get payment by ID",
    description="Retrieve detailed information about a previously created payment.",
)
async def get_payment(
    payment_id: Annotated[UUID, Path(description="Unique payment identifier")],
    session: DbSessionDep,
    _api_key: Annotated[None, Depends(verify_api_key)],
) -> PaymentDetailResponse:
    service = PaymentService(session)
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment {payment_id} not found",
        )
    return PaymentDetailResponse.model_validate(payment)
