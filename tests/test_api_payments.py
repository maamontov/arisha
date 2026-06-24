from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.models import OutboxEvent, Payment, PaymentStatus


def _create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "amount": "100.00",
        "currency": "USD",
        "description": "Test order",
        "metadata": {"order_id": "1234"},
        "webhook_url": "https://example.com/webhook",
    }
    payload.update(overrides)
    return payload


async def test_create_payment_success(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
    db_engine: AsyncEngine,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "key-success-1"}
    response = await api_client.post("/api/v1/payments", json=_create_payload(), headers=headers)
    assert response.status_code == 202, response.text
    body = response.json()
    assert "payment_id" in body
    assert body["status"] == "pending"
    assert "created_at" in body

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        count_payments = await session.execute(select(func.count()).select_from(Payment))
        count_outbox = await session.execute(select(func.count()).select_from(OutboxEvent))
        assert count_payments.scalar() == 1
        assert count_outbox.scalar() == 1


async def test_create_payment_idempotency_returns_existing(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "duplicate-key-2"}
    payload = _create_payload()
    first = await api_client.post("/api/v1/payments", json=payload, headers=headers)
    assert first.status_code == 202
    first_id = first.json()["payment_id"]

    second = await api_client.post("/api/v1/payments", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["payment_id"] == first_id


async def test_create_payment_idempotency_different_payload_same_key(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "shared-key"}
    first = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(amount="10.00"),
        headers=headers,
    )
    assert first.status_code == 202

    second = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(amount="99.99"),
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["payment_id"] == first.json()["payment_id"]


async def test_create_payment_missing_api_key(
    api_client: AsyncClient,
    db_clean: None,
) -> None:
    headers = {"Idempotency-Key": "no-auth-1"}
    response = await api_client.post("/api/v1/payments", json=_create_payload(), headers=headers)
    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


async def test_create_payment_invalid_api_key(
    api_client: AsyncClient,
    db_clean: None,
) -> None:
    headers = {"X-API-Key": "wrong", "Idempotency-Key": "bad-auth-1"}
    response = await api_client.post("/api/v1/payments", json=_create_payload(), headers=headers)
    assert response.status_code == 403


async def test_create_payment_missing_idempotency_key(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    response = await api_client.post(
        "/api/v1/payments", json=_create_payload(), headers=api_headers
    )
    assert response.status_code == 422
    assert "Idempotency-Key" in str(response.json())


async def test_create_payment_invalid_amount(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "bad-amount-1"}
    response = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(amount="-5.00"),
        headers=headers,
    )
    assert response.status_code == 422


async def test_create_payment_invalid_currency(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "bad-currency-1"}
    response = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(currency="GBP"),
        headers=headers,
    )
    assert response.status_code == 422


async def test_create_payment_invalid_webhook_url(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "bad-url-1"}
    response = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(webhook_url="not-a-url"),
        headers=headers,
    )
    assert response.status_code == 422


async def test_create_payment_atomic_outbox_creation(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_engine: AsyncEngine,
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "atomic-outbox"}
    response = await api_client.post("/api/v1/payments", json=_create_payload(), headers=headers)
    assert response.status_code == 202
    payment_id = response.json()["payment_id"]

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        from uuid import UUID

        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(payment_id))
        )
        event = result.scalar_one()
        assert event.event_type == "payment.created"
        assert event.status == "pending"
        assert event.payload["payment_id"] == payment_id
        assert event.payload["amount"] == "100.0000"
        assert event.payload["currency"] == "USD"
        assert event.payload["idempotency_key"] == "atomic-outbox"


async def test_get_payment_success(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "get-success-1"}
    created = await api_client.post("/api/v1/payments", json=_create_payload(), headers=headers)
    assert created.status_code == 202
    payment_id = created.json()["payment_id"]

    response = await api_client.get(f"/api/v1/payments/{payment_id}", headers=api_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == payment_id
    assert body["amount"] == "100.0000"
    assert body["currency"] == "USD"
    assert body["status"] == "pending"
    assert body["metadata"] == {"order_id": "1234"}
    assert body["idempotency_key"] == "get-success-1"
    assert body["processed_at"] is None


async def test_get_payment_not_found(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    response = await api_client.get(f"/api/v1/payments/{uuid4()}", headers=api_headers)
    assert response.status_code == 404


async def test_get_payment_requires_auth(
    api_client: AsyncClient,
    db_clean: None,
) -> None:
    response = await api_client.get(f"/api/v1/payments/{uuid4()}")
    assert response.status_code == 401


async def test_create_payment_preserves_decimal_precision(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "decimal-precision"}
    response = await api_client.post(
        "/api/v1/payments",
        json=_create_payload(amount="123.4567"),
        headers=headers,
    )
    assert response.status_code == 202

    payment_id = response.json()["payment_id"]
    detail = await api_client.get(f"/api/v1/payments/{payment_id}", headers=api_headers)
    assert detail.status_code == 200
    assert detail.json()["amount"] == "123.4567"


async def test_create_payment_extra_fields_rejected(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "extra-fields"}
    payload = _create_payload()
    payload["unexpected"] = "value"
    response = await api_client.post("/api/v1/payments", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_payment_with_minimal_payload(
    api_client: AsyncClient,
    api_headers: dict[str, str],
    db_clean: None,
) -> None:
    headers = {**api_headers, "Idempotency-Key": "minimal-1"}
    response = await api_client.post(
        "/api/v1/payments",
        json={
            "amount": "50.00",
            "currency": "EUR",
            "webhook_url": "https://example.com/hook",
        },
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == PaymentStatus.PENDING.value
