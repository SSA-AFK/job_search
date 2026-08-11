from unittest.mock import AsyncMock

import pytest

from app.ingestion.contracts import ProviderQuery
from app.ingestion.errors import ProviderError
from app.ingestion.providers.tianyancha import TianyanchaProvider


@pytest.mark.anyio
async def test_tianyancha_uses_one_registration_call_and_enforces_budget() -> None:
    provider = TianyanchaProvider(call_budget=1)
    provider._run_cli = AsyncMock(  # type: ignore[method-assign]
        return_value={"sources": {"base": {"id": "42"}}}
    )

    result = await provider.search(ProviderQuery(query="Example"))

    assert len(result.documents) == 1
    provider._run_cli.assert_awaited_once_with(  # type: ignore[attr-defined]
        "company", "registration-info", "Example"
    )
    with pytest.raises(ProviderError, match="budget"):
        await provider.search(ProviderQuery(query="Example Two"))
