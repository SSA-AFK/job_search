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


@ pytest.mark.asyncio
async def test_ats_provider_search_routes_to_search_with_url_when_website_provided() -> None:
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
    query = ProviderQuery(
        query="ai",
        website=HttpUrl("https://jobs.feishu.cn/x"),
        allowed_hosts=frozenset({"jobs.feishu.cn"}),
        max_results=5,
    )
    result = await provider.search(query)
    assert feishu.fetch_list.await_count == 1
    assert moka.fetch_list.await_count == 0
    assert len(result.documents) == 1


@ pytest.mark.asyncio
async def test_ats_provider_search_returns_empty_without_website() -> None:
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=AsyncMock(),
        feishu_extractor=AsyncMock(),
        moka_extractor=AsyncMock(),
        enabled_platforms=frozenset({"feishu"}),
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset(), max_results=5)
    result = await provider.search(query)
    assert result.documents == ()


@ pytest.mark.asyncio
async def test_ats_provider_has_website_dependent_properties() -> None:
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=AsyncMock(),
        feishu_extractor=AsyncMock(),
        moka_extractor=AsyncMock(),
        enabled_platforms=frozenset({"feishu", "moka"}),
    )
    assert provider.requires_website is True
    assert provider.approved_hosts == frozenset({"jobs.feishu.cn", "app.mokahr.com"})


@pytest.mark.asyncio
async def test_ats_provider_records_block_stats_for_access_challenge() -> None:
    zhipin = AsyncMock()
    zhipin.fetch_list.return_value = (
        _doc("ats_zhipin"),
        AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="captcha_required"),
    )
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=AsyncMock(),
        feishu_extractor=AsyncMock(),
        moka_extractor=AsyncMock(),
        zhipin_extractor=zhipin,
        enabled_platforms=frozenset({"zhipin"}),
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset({"zhipin.com"}), max_results=5)

    result = await provider.search_with_url("https://www.zhipin.com/web/geek/job", query)

    assert "captcha_required" in result.warnings
    assert len(result.stats) == 1
    assert result.stats[0].platform == "zhipin"
    assert result.stats[0].entries_discovered == 1
    assert result.stats[0].pages_fetched == 1
    assert result.stats[0].parsed_jobs == 0
    assert result.stats[0].blocked_pages == 1
    assert result.stats[0].error_code == "captcha_required"


@pytest.mark.asyncio
async def test_ats_provider_enters_platform_cooldown_after_repeated_blocks() -> None:
    zhipin = AsyncMock()
    zhipin.fetch_list.return_value = (
        _doc("ats_zhipin"),
        AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="captcha_required"),
    )
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=AsyncMock(),
        renderer=AsyncMock(),
        feishu_extractor=AsyncMock(),
        moka_extractor=AsyncMock(),
        zhipin_extractor=zhipin,
        enabled_platforms=frozenset({"zhipin"}),
        platform_block_threshold=1,
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset({"zhipin.com"}), max_results=5)

    first = await provider.search_with_url("https://www.zhipin.com/web/geek/job", query)
    second = await provider.search_with_url("https://www.zhipin.com/web/geek/job?page=2", query)

    assert first.stats[0].error_code == "captcha_required"
    assert second.documents == ()
    assert second.warnings == ("platform_cooldown",)
    assert second.stats[0].blocked_pages == 1
    assert second.stats[0].error_code == "platform_cooldown"
    assert zhipin.fetch_list.await_count == 1
