from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.messaging.webhook import WebhookError, WebhookSender


def _client_returning(*responses: httpx.Response | Exception) -> MagicMock:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=list(responses))
    client.aclose = AsyncMock()
    return client


async def test_webhook_send_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(httpx.Response(200, json={"ok": True}))

    sender = WebhookSender(client=client, max_attempts=3, base_delay=1.0)
    await sender.send("http://test/webhook", {"data": "value"})

    client.post.assert_awaited_once_with("http://test/webhook", json={"data": "value"})


async def test_webhook_retries_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(200),
    )

    sender = WebhookSender(client=client, max_attempts=3, base_delay=0.1)
    await sender.send("http://test/webhook", {"data": "value"})

    assert client.post.await_count == 3


async def test_webhook_no_retry_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(httpx.Response(400))

    sender = WebhookSender(client=client, max_attempts=3, base_delay=0.1)
    with pytest.raises(WebhookError) as exc_info:
        await sender.send("http://test/webhook", {"data": "value"})

    assert client.post.await_count == 1
    assert exc_info.value.status_code == 400


async def test_webhook_all_attempts_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(500),
    )

    sender = WebhookSender(client=client, max_attempts=3, base_delay=0.01)
    with pytest.raises(WebhookError) as exc_info:
        await sender.send("http://test/webhook", {"data": "value"})

    assert client.post.await_count == 3
    assert exc_info.value.status_code == 500


async def test_webhook_retries_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        httpx.Response(200),
    )

    sender = WebhookSender(client=client, max_attempts=3, base_delay=0.01)
    await sender.send("http://test/webhook", {"data": "value"})

    assert client.post.await_count == 3


async def test_webhook_request_error_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = _client_returning(
        httpx.ConnectError("boom1"),
        httpx.ConnectError("boom2"),
        httpx.ConnectError("boom3"),
    )

    sender = WebhookSender(client=client, max_attempts=3, base_delay=0.01)
    with pytest.raises(httpx.ConnectError):
        await sender.send("http://test/webhook", {"data": "value"})

    assert client.post.await_count == 3


async def test_webhook_exponential_backoff_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)
    client = _client_returning(
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(200),
    )

    sender = WebhookSender(client=client, max_attempts=3, base_delay=1.0)
    await sender.send("http://test/webhook", {"data": "value"})

    assert sleep_mock.await_count == 2
    assert sleep_mock.await_args_list[0].args == (1.0,)
    assert sleep_mock.await_args_list[1].args == (2.0,)


async def test_webhook_no_sleep_after_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)
    client = _client_returning(
        httpx.Response(500),
        httpx.Response(500),
    )

    sender = WebhookSender(client=client, max_attempts=2, base_delay=1.0)
    with pytest.raises(WebhookError):
        await sender.send("http://test/webhook", {"data": "value"})

    assert sleep_mock.await_count == 1
