import asyncio
import os
from uuid import uuid4

import httpx
import pytest

API_BASE_URL = os.environ.get("E2E_API_URL", "http://localhost:8000")
WEBHOOK_RECEIVER_URL = os.environ.get("E2E_WEBHOOK_RECEIVER_URL", "http://localhost:9000")
WEBHOOK_URL = os.environ.get("E2E_WEBHOOK_URL", "http://webhook-receiver:9000/webhook")
API_KEY = os.environ.get("E2E_API_KEY", "dev-secret-key-change-me")
DEFAULT_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 1.0


async def _check_health(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
            response = await client.get("/health")
            return response.status_code == 200
    except httpx.RequestError:
        return False


async def _wait_for_terminal_status(
    api: httpx.AsyncClient,
    payment_id: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_interval_s: float = POLL_INTERVAL_S,
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_data: dict | None = None
    while asyncio.get_event_loop().time() < deadline:
        response = await api.get(
            f"/api/v1/payments/{payment_id}",
            headers={"X-API-Key": API_KEY},
        )
        if response.status_code == 200:
            last_data = response.json()
            if last_data.get("status") in ("succeeded", "failed"):
                return last_data
        await asyncio.sleep(poll_interval_s)
    last_status = last_data.get("status") if last_data else "unknown"
    pytest.fail(
        f"Payment {payment_id} not processed within {timeout_s}s " f"(last status: {last_status})"
    )


async def _create_payment(
    api: httpx.AsyncClient,
    *,
    amount: str,
    currency: str,
    webhook_url: str = WEBHOOK_URL,
    description: str | None = None,
    metadata: dict | None = None,
) -> str:
    payload: dict = {
        "amount": amount,
        "currency": currency,
        "webhook_url": webhook_url,
    }
    if description is not None:
        payload["description"] = description
    if metadata is not None:
        payload["metadata"] = metadata

    response = await api.post(
        "/api/v1/payments",
        headers={"X-API-Key": API_KEY, "Idempotency-Key": f"e2e-{uuid4()}"},
        json=payload,
    )
    assert response.status_code == 202, response.text
    return response.json()["payment_id"]


async def _clear_webhook_receiver(receiver: httpx.AsyncClient) -> None:
    await receiver.delete("/received")


async def test_e2e_payment_flow_terminates() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")
    if not await _check_health(WEBHOOK_RECEIVER_URL):
        pytest.skip(f"Webhook receiver not reachable at {WEBHOOK_RECEIVER_URL}")

    idempotency_key = f"e2e-flow-{uuid4()}"

    async with (
        httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api,
        httpx.AsyncClient(base_url=WEBHOOK_RECEIVER_URL, timeout=5.0) as receiver,
    ):
        await _clear_webhook_receiver(receiver)

        response = await api.post(
            "/api/v1/payments",
            headers={
                "X-API-Key": API_KEY,
                "Idempotency-Key": idempotency_key,
            },
            json={
                "amount": "50.00",
                "currency": "USD",
                "description": "E2E flow test",
                "webhook_url": WEBHOOK_URL,
            },
        )
        assert response.status_code == 202, response.text
        payment_id = response.json()["payment_id"]

        data = await _wait_for_terminal_status(api, payment_id)
        assert data["status"] in ("succeeded", "failed")
        assert data["processed_at"] is not None
        assert data["idempotency_key"] == idempotency_key
        assert data["currency"] == "USD"
        assert data["amount"] == "50.0000"


async def test_e2e_webhook_received_with_correct_payload() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")
    if not await _check_health(WEBHOOK_RECEIVER_URL):
        pytest.skip(f"Webhook receiver not reachable at {WEBHOOK_RECEIVER_URL}")

    amount = "75.25"
    currency = "EUR"

    async with (
        httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api,
        httpx.AsyncClient(base_url=WEBHOOK_RECEIVER_URL, timeout=5.0) as receiver,
    ):
        await _clear_webhook_receiver(receiver)

        payment_id = await _create_payment(
            api,
            amount=amount,
            currency=currency,
            description="E2E webhook test",
        )

        data = await _wait_for_terminal_status(api, payment_id)

        response = await receiver.get(f"/received?payment_id={payment_id}")
        assert response.status_code == 200
        events = response.json()["events"]
        assert len(events) == 1, f"expected 1 event, got {len(events)}"

        event = events[0]
        body = event["body"]
        assert body["payment_id"] == payment_id
        assert body["status"] == data["status"]
        assert body["currency"] == currency
        assert body["amount"] == "75.2500"
        assert body["event_type"] == "payment.created"
        assert "processed_at" in body
        assert "event_id" in body

        headers = event["headers"]
        assert headers.get("content-type") == "application/json"


async def test_e2e_idempotency_returns_same_payment() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")

    idempotency_key = f"e2e-idem-{uuid4()}"
    payload = {
        "amount": "99.99",
        "currency": "USD",
        "webhook_url": WEBHOOK_URL,
    }
    headers = {"X-API-Key": API_KEY, "Idempotency-Key": idempotency_key}

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api:
        first = await api.post("/api/v1/payments", headers=headers, json=payload)
        assert first.status_code == 202
        first_id = first.json()["payment_id"]

        second = await api.post("/api/v1/payments", headers=headers, json=payload)
        assert second.status_code == 200
        assert second.json()["payment_id"] == first_id

        different_payload = {**payload, "amount": "1.00"}
        third = await api.post("/api/v1/payments", headers=headers, json=different_payload)
        assert third.status_code == 200
        assert third.json()["payment_id"] == first_id


async def test_e2e_get_payment_returns_full_detail() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")

    idempotency_key = f"e2e-get-{uuid4()}"

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api:
        response = await api.post(
            "/api/v1/payments",
            headers={"X-API-Key": API_KEY, "Idempotency-Key": idempotency_key},
            json={
                "amount": "10.00",
                "currency": "RUB",
                "description": "E2E GET test",
                "metadata": {"order_id": "e2e-1", "channel": "web"},
                "webhook_url": WEBHOOK_URL,
            },
        )
        assert response.status_code == 202
        payment_id = response.json()["payment_id"]

        response = await api.get(
            f"/api/v1/payments/{payment_id}",
            headers={"X-API-Key": API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == payment_id
        assert data["currency"] == "RUB"
        assert data["amount"] == "10.0000"
        assert data["idempotency_key"] == idempotency_key
        assert data["description"] == "E2E GET test"
        assert data["metadata"] == {"order_id": "e2e-1", "channel": "web"}


async def test_e2e_unauthorized_without_api_key() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api:
        response = await api.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": f"e2e-noauth-{uuid4()}"},
            json={
                "amount": "10.00",
                "currency": "USD",
                "webhook_url": WEBHOOK_URL,
            },
        )
        assert response.status_code == 401


async def test_e2e_invalid_api_key_returns_403() -> None:
    if not await _check_health(API_BASE_URL):
        pytest.skip(f"API not reachable at {API_BASE_URL}")

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as api:
        response = await api.post(
            "/api/v1/payments",
            headers={
                "X-API-Key": "definitely-wrong",
                "Idempotency-Key": f"e2e-badauth-{uuid4()}",
            },
            json={
                "amount": "10.00",
                "currency": "USD",
                "webhook_url": WEBHOOK_URL,
            },
        )
        assert response.status_code == 403
