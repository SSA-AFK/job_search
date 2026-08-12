# backend/tests/ingestion/providers/ats_extractors/test_bytedance.py
import json
from unittest.mock import AsyncMock

import pytest

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.providers.ats_extractors.bytedance import BytedanceAtsExtractor

# Simulated JSON data as returned by page.evaluate(_EXTRACT_POSITION_ITEMS)
_MOCK_EXTRACTED = json.dumps([
    {
        "title": "AI全栈开发工程师 - 抖音研发",
        "href": "/experienced/position/7671143217518250293/detail",
        "subText": "北京 正式 研发 - 前端",
        "fullText": "AI全栈开发工程师 - 抖音研发 北京 正式 研发 - 前端",
    },
    {
        "title": "餐饮小组负责人-抖音生活服务（西安）",
        "href": "/experienced/position/7669680957906159925/detail",
        "subText": "西安 正式 销售",
        "fullText": "餐饮小组负责人-抖音生活服务（西安） 西安 正式 销售",
    },
    {
        "title": "软件开发实习生",
        "href": "/campus/position/7208470442158950693/detail",
        "subText": "深圳 实习 研发",
        "fullText": "软件开发实习生 深圳 实习 研发",
    },
])


def _make_rendered_page(*, url: str, html: str, title: str = "ByteDance", extracted_html: str | None = None) -> object:
    return type(
        "Page",
        (),
        {
            "url": url,
            "html": html,
            "status_code": 200,
            "title": title,
            "extracted_html": extracted_html,
        },
    )()


@pytest.mark.asyncio
async def test_bytedance_extractor_parses_extracted_dom_data() -> None:
    renderer = AsyncMock()
    renderer.render.return_value = _make_rendered_page(
        url="https://jobs.bytedance.com/experienced/position",
        html="<html><body>...</body></html>",
        extracted_html=_MOCK_EXTRACTED,
    )
    renderer.aclose = AsyncMock()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = BytedanceAtsExtractor()
    document, result = await extractor.fetch_list(
        url="https://jobs.bytedance.com/experienced/position",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert document.provider == "ats_bytedance"
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) == 3
    titles = {c.title for c in result.candidates}
    assert "AI全栈开发工程师 - 抖音研发" in titles
    assert "软件开发实习生" in titles
    # Verify city extraction
    cities = {c.city for c in result.candidates}
    assert "北京" in cities
    assert "西安" in cities
    assert "深圳" in cities
    # Verify employment_type extraction
    types = {c.employment_type for c in result.candidates}
    assert "full_time" in types
    assert "internship" in types
    # Verify URL construction
    urls = {str(c.url) for c in result.candidates}
    assert "https://jobs.bytedance.com/experienced/position/7671143217518250293/detail" in urls


@pytest.mark.asyncio
async def test_bytedance_extractor_returns_no_candidates_when_extracted_html_is_none() -> None:
    renderer = AsyncMock()
    renderer.render.return_value = _make_rendered_page(
        url="https://jobs.bytedance.com/experienced/position",
        html="<html><body>no items</body></html>",
        extracted_html=None,
    )
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = BytedanceAtsExtractor()
    _document, result = await extractor.fetch_list(
        url="https://jobs.bytedance.com/experienced/position",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert result.status == AtsParseStatus.PARTIAL
    assert result.error_code == "no_candidates"


@pytest.mark.asyncio
async def test_bytedance_extractor_returns_login_required_when_challenge_detected() -> None:
    renderer = AsyncMock()
    renderer.render.return_value = _make_rendered_page(
        url="https://jobs.bytedance.com/login",
        html="<html>登录 请先登录后继续</html>",
        title="Login",
        extracted_html=None,
    )
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = BytedanceAtsExtractor()
    _document, result = await extractor.fetch_list(
        url="https://jobs.bytedance.com/login",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "login_required"
