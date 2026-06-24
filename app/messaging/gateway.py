import asyncio
import random


class Gateway:
    def __init__(self, min_delay: float, max_delay: float, success_rate: float) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate

    async def process(self) -> bool:
        delay = random.uniform(self._min_delay, self._max_delay)
        await asyncio.sleep(delay)
        return random.random() < self._success_rate
