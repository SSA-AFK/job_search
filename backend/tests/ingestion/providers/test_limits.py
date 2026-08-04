import asyncio

import pytest

from app.ingestion.contracts import ProviderQuery, ProviderResult
from app.ingestion.providers.limits import ControlledProvider


@pytest.mark.asyncio
async def test_provider_concurrency_limit_is_shared_across_wrapper_instances() -> None:
    active = 0
    maximum_active = 0
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    class Delegate:
        name = "shared-provider"

        async def search(self, _query: ProviderQuery) -> ProviderResult:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            if not first_entered.is_set():
                first_entered.set()
                await release_first.wait()
            active -= 1
            return ProviderResult(documents=())

    first = ControlledProvider(Delegate(), max_concurrency=1, min_interval_seconds=0)
    second = ControlledProvider(Delegate(), max_concurrency=1, min_interval_seconds=0)
    first_task = asyncio.create_task(first.search(ProviderQuery(query="one")))
    await first_entered.wait()
    second_task = asyncio.create_task(second.search(ProviderQuery(query="two")))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert maximum_active == 1
