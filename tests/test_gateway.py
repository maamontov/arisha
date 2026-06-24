from unittest.mock import AsyncMock

import pytest

from app.messaging.gateway import Gateway


async def test_gateway_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr("app.messaging.gateway.random.uniform", lambda a, b: 3.0)
    monkeypatch.setattr("app.messaging.gateway.random.random", lambda: 0.5)

    gateway = Gateway(min_delay=2.0, max_delay=5.0, success_rate=0.9)
    result = await gateway.process()
    assert result is True


async def test_gateway_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr("app.messaging.gateway.random.uniform", lambda a, b: 3.0)
    monkeypatch.setattr("app.messaging.gateway.random.random", lambda: 0.95)

    gateway = Gateway(min_delay=2.0, max_delay=5.0, success_rate=0.9)
    result = await gateway.process()
    assert result is False


async def test_gateway_uses_delay_range(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)
    monkeypatch.setattr("app.messaging.gateway.random.random", lambda: 0.5)

    captured: list[tuple[float, float]] = []

    def mock_uniform(a: float, b: float) -> float:
        captured.append((a, b))
        return 3.5

    monkeypatch.setattr("app.messaging.gateway.random.uniform", mock_uniform)

    gateway = Gateway(min_delay=2.0, max_delay=5.0, success_rate=0.9)
    await gateway.process()

    sleep_mock.assert_awaited_once_with(3.5)
    assert captured == [(2.0, 5.0)]


async def test_gateway_at_boundary_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr("app.messaging.gateway.random.uniform", lambda a, b: 2.0)
    monkeypatch.setattr("app.messaging.gateway.random.random", lambda: 0.89)

    gateway = Gateway(min_delay=2.0, max_delay=5.0, success_rate=0.9)
    result = await gateway.process()
    assert result is True


async def test_gateway_at_boundary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr("app.messaging.gateway.random.uniform", lambda a, b: 2.0)
    monkeypatch.setattr("app.messaging.gateway.random.random", lambda: 0.9)

    gateway = Gateway(min_delay=2.0, max_delay=5.0, success_rate=0.9)
    result = await gateway.process()
    assert result is False
