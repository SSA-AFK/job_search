"""Unit tests for EntryDiscoveryService using fake collaborators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pytest
import httpx
from pydantic import HttpUrl

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.entry_discovery.contracts import (
    CompanyNamePool,
    EntryPlatform,
)
from app.ingestion.entry_discovery.service import DiscoveryResult, EntryDiscoveryService


# ---------------------------------------------------------------------------
# Fake Serper Provider
# ---------------------------------------------------------------------------

class _FakeSerper:
    name = "serper"

    def __init__(self, responses: dict[str, ProviderResult] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[ProviderQuery] = []

    async def search(self, query: ProviderQuery) -> ProviderResult:
        self.calls.append(query)
        return self._responses.get(query.query, ProviderResult(documents=()))


def _doc(url: str, *, title: str | None = None, text: str = "", provider: str = "serper") -> RawDocument:
    # 用 http:// 临时包装以避免校验失败，测试用的假 URL
    try:
        u = HttpUrl(url)
    except Exception:
        u = HttpUrl(f"https://example.com/placeholder")
    return RawDocument(provider=provider, external_id=None, url=u, title=title, text=text, published_at=None)


# ---------------------------------------------------------------------------
# Fake Careers Prober (httpx mock)
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""
    url: Any = None


class _FakeProber:
    def __init__(self, pages: dict[str, _FakeResponse] | None = None) -> None:
        self._pages = pages or {}
        self.calls: list[str] = []

    async def get(self, url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        if url in self._pages:
            resp = self._pages[url]
            return _FakeResponse(resp.status_code, resp.text, resp.url or url)
        return _FakeResponse(404, url=url)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def moonshot_pool() -> CompanyNamePool:
    return CompanyNamePool(
        canonical_name="月之暗面",
        legal_name="北京月之暗面科技有限公司",
        brand_names=("Kimi",),
        historical_aliases=(),
        domains=("moonshot.cn",),
    )


# ---------------------------------------------------------------------------
# Tests - Serper site: search path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_site_queries_discovers_feishu_ats(moonshot_pool: CompanyNamePool) -> None:
    # Serper 对 site:jobs.feishu.cn 查询返回一条正确的月之暗面 ATS 页面
    ok_q = '"月之暗面" site:jobs.feishu.cn'
    serper = _FakeSerper(
        {
            ok_q: ProviderResult(
                documents=(
                    _doc(
                        "https://jobs.feishu.cn/careers/moonshot",
                        title="月之暗面 - 加入我们",
                        text="月之暗面 加入我们 招聘 page",
                    ),
                )
            )
        }
    )
    svc = EntryDiscoveryService(serper_provider=serper)
    result = await svc.discover(moonshot_pool)
    assert isinstance(result, DiscoveryResult)
    assert any(c.platform == EntryPlatform.ATS_FEISHU for c in result.high_confidence)
    # 候选里应该至少包含 careers/moonshot
    assert any("moonshot" in c.url for c in result.candidates)


@pytest.mark.asyncio
async def test_site_queries_rejects_unrelated(moonshot_pool: CompanyNamePool) -> None:
    ok_q = '"月之暗面" site:jobs.feishu.cn'
    serper = _FakeSerper(
        {
            ok_q: ProviderResult(
                documents=(
                    _doc(
                        "https://jobs.feishu.cn/careers/aliyun",
                        title="阿里云招聘",
                        text="阿里云 阿里巴巴 招聘",
                    ),
                )
            )
        }
    )
    svc = EntryDiscoveryService(serper_provider=serper)
    result = await svc.discover(moonshot_pool)
    # 这条候选会在 validate 时被降置信度，不再进入 high_confidence
    assert all(c.platform != EntryPlatform.ATS_FEISHU or not c.is_high_confidence()
               for c in result.high_confidence)
    assert result.ats_entries == ()


# ---------------------------------------------------------------------------
# Tests - Careers page probe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_careers_probe_finds_company_site(moonshot_pool: CompanyNamePool) -> None:
    prober = _FakeProber(
        {
            "https://moonshot.cn/careers": _FakeResponse(
                200,
                "<html><title>月之暗面 - 加入我们</title><body>Kimi 公司招聘页面</body></html>",
            )
        }
    )
    svc = EntryDiscoveryService(careers_prober=prober)
    result = await svc.discover(moonshot_pool)
    assert result.company_site_entries, "应至少探测到一个官网 careers 入口"
    entry = result.company_site_entries[0]
    assert entry.title and "月之暗面" in entry.title
    assert entry.overall_confidence >= 0.6


@pytest.mark.asyncio
async def test_careers_probe_404_ignored(moonshot_pool: CompanyNamePool) -> None:
    prober = _FakeProber({})  # 所有探测均 404
    svc = EntryDiscoveryService(careers_prober=prober, confidence_threshold=0.0)
    result = await svc.discover(moonshot_pool)
    # 404 会被过滤，但其它路径也均失败 → 结果应该没有高置信度项
    assert result.company_site_entries == ()


# ---------------------------------------------------------------------------
# Tests - combined flow + diagnostics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_channels_returns_diagnostic(moonshot_pool: CompanyNamePool) -> None:
    svc = EntryDiscoveryService()  # 两个通道都没开
    result = await svc.discover(CompanyNamePool(canonical_name="x", domains=()))
    assert "no_discovery_channel_enabled" in result.diagnostics


@pytest.mark.asyncio
async def test_site_queries_concurrent_limit(moonshot_pool: CompanyNamePool) -> None:
    """确认 Serper site: 查询数量对名称池变体数量有合理上界，不会爆炸。"""
    serper = _FakeSerper({})
    svc = EntryDiscoveryService(serper_provider=serper)
    await svc.discover(moonshot_pool)
    # 变体数（canonical + 去后缀工商 + Kimi + 插入 canonical 去重） ≤ 4
    # × 站点数（jobs.feishu.cn + app.mokahr.com + zhipin + liepin + lagou）= 5
    # 所以总调用 ≤ ~20。如果 bug 导致爆炸，调用数会非常大。
    assert len(serper.calls) <= 25, f"Serper 调用数过多: {len(serper.calls)}"
