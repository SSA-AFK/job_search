"""Process-wide provider controls shared by independent collection runs."""

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.ingestion.contracts import ProviderQuery, ProviderResult


@dataclass
class _Gate:
    semaphore: threading.BoundedSemaphore
    rate_lock: threading.Lock = field(default_factory=threading.Lock)
    next_allowed: float = 0.0


_GATES: dict[tuple[str, int, float], _Gate] = {}
_GATES_LOCK = threading.Lock()


@asynccontextmanager
async def provider_slot(
    name: str, *, max_concurrency: int, min_interval_seconds: float
) -> AsyncIterator[None]:
    key = (name, max_concurrency, min_interval_seconds)
    with _GATES_LOCK:
        gate = _GATES.setdefault(
            key, _Gate(threading.BoundedSemaphore(max_concurrency))
        )
    await asyncio.to_thread(gate.semaphore.acquire)
    try:
        with gate.rate_lock:
            now = time.monotonic()
            delay = max(0.0, gate.next_allowed - now)
            gate.next_allowed = max(now, gate.next_allowed) + min_interval_seconds
        if delay:
            await asyncio.sleep(delay)
        yield
    finally:
        gate.semaphore.release()


class ControlledProvider:
    def __init__(
        self,
        provider: object,
        *,
        max_concurrency: int,
        min_interval_seconds: float,
    ) -> None:
        self._provider = provider
        self.name = str(getattr(provider, "name", type(provider).__name__))[:50]
        self.requires_website = getattr(provider, "requires_website", False) is True
        self.approved_hosts = frozenset(getattr(provider, "approved_hosts", ()))
        self._max_concurrency = max_concurrency
        self._min_interval_seconds = min_interval_seconds

    async def search(self, query: ProviderQuery) -> ProviderResult:
        async with provider_slot(
            self.name,
            max_concurrency=self._max_concurrency,
            min_interval_seconds=self._min_interval_seconds,
        ):
            return await self._provider.search(query)  # type: ignore[attr-defined,no-any-return]
