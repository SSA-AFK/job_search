from unittest.mock import AsyncMock

import pytest
from pydantic import HttpUrl

from app.ingestion.contracts import ProviderQuery, RawDocument
from app.ingestion.jobs.contracts import AtsListResult, AtsParseStatus
from app.ingestion.providers.ats import AtsProvider


def _doc(name: str) -> RawDocument:
    return RawDocument(
        provider=name,
        external_id=None,
        url=HttpUrl("https://jobs.feishu.cn/x"),
        title=None,
        text="",
        published_at=None,
        authority_level=2,
    )


@pytest.mark.asyncio
async def test_ats_provider_routes_feishu_url_to_feishu_extractor() -> None:
    feishu = AsyncMock()
    feishu.fetch_list.return_value = (
        _doc("ats_feishu"),
        AtsListResult(candidates=(), status=AtsParseStatus.SUCCEEDED, error_code=None),
    )
    moka = AsyncMock()
    renderer = AsyncMock()
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=renderer,
        feishu_extractor=feishu,
        moka_extractor=moka,
        enabled_platforms=frozenset({"feishu"}),
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset({"jobs.feishu.cn"}), max_results=5)
    result = await provider.search_with_url("https://jobs.feishu.cn/x", query)
    assert feishu.fetch_list.await_count == 1
    assert moka.fetch_list.await_count == 0
    assert len(result.documents) == 1


@pytest.mark.asyncio
async def test_ats_provider_returns_warning_when_platform_disabled() -> None:
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=AsyncMock(),
        feishu_extractor=AsyncMock(),
        moka_extractor=AsyncMock(),
        enabled_platforms=frozenset(),
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset({"jobs.feishu.cn"}), max_results=5)
    result = await provider.search_with_url("https://jobs.feishu.cn/x", query)
    assert result.documents == ()
    assert "platform_disabled" in result.warnings
