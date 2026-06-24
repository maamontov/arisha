import os
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from pydantic import BaseModel

app = FastAPI(
    title="Arisha Webhook Receiver",
    version="0.1.0",
    description="Test receiver for payment webhooks",
)

_RECEIVED: dict[str, deque[dict]] = defaultdict(deque)
_FAIL_UNTIL: dict[str, datetime] = {}


class ReceivedWebhook(BaseModel):
    received_at: datetime
    headers: dict[str, str]
    body: dict


class FailureControl(BaseModel):
    webhook_url: str
    until: datetime | None = None
    status_code: int = 500
    error_count: int = 1


@app.post("/webhook", status_code=200)
async def receive_webhook(request: Request) -> dict:
    body = await request.json()
    headers = dict(request.headers)
    url_path = str(request.url)

    payment_id = body.get("payment_id", "unknown")
    entry = {
        "received_at": datetime.now(UTC).isoformat(),
        "url": url_path,
        "headers": {
            k: v for k, v in headers.items() if k.lower().startswith("x-") or k == "content-type"
        },
        "body": body,
    }
    _RECEIVED[payment_id].append(entry)

    fail_until = _FAIL_UNTIL.get(url_path)
    if fail_until and datetime.now(UTC) < fail_until:
        return _build_error_response(503, "Simulated failure")

    return {"status": "ok", "received": True, "payment_id": payment_id}


def _build_error_response(status_code: int, message: str) -> dict:
    return {"detail": message, "status_code": status_code}


@app.get("/received", tags=["meta"])
async def list_received(payment_id: str | None = None) -> dict:
    if payment_id:
        return {"payment_id": payment_id, "events": list(_RECEIVED.get(payment_id, []))}
    return {"all": {k: list(v) for k, v in _RECEIVED.items()}}


@app.post("/simulate/fail", tags=["meta"])
async def simulate_failure(control: FailureControl) -> dict:
    until = control.until or datetime.now(UTC)
    _FAIL_UNTIL[control.webhook_url] = until
    return {
        "configured": True,
        "webhook_url": control.webhook_url,
        "until": until.isoformat(),
        "status_code": control.status_code,
    }


@app.delete("/received", tags=["meta"])
async def clear_received() -> dict:
    _RECEIVED.clear()
    return {"cleared": True}


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "arisha-webhook-receiver",
        "instance_id": os.environ.get("HOSTNAME", str(uuid.uuid4())),
    }
