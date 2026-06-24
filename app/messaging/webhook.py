import asyncio
from typing import Any

import httpx


class WebhookError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WebhookSender:
    def __init__(
        self,
        client: httpx.AsyncClient,
        max_attempts: int,
        base_delay: float,
    ) -> None:
        self._client = client
        self._max_attempts = max_attempts
        self._base_delay = base_delay

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(url, json=payload)
                if 200 <= response.status_code < 300:
                    return
                last_error = WebhookError(
                    f"HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            except httpx.RequestError as e:
                last_error = e

            if attempt < self._max_attempts:
                delay = self._base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        if last_error is None:
            last_error = WebhookError("All webhook attempts failed")
        raise last_error
