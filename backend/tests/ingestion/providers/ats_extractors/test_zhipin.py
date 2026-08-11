from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.providers.ats_extractors.zhipin import ZhipinAtsExtractor

_DATA = Path(__file__).parents[4] / "data" / "ats"


@pytest.mark.asyncio
async def test_zhipin_extractor_parses_rendered_fixture() -> None:
    html = (_DATA / "zhipin_list.html").read_text(encoding="utf-8")
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page",
        (),
        {
            "url": "https://www.zhipin.com/gongsi/job/x.html",
            "html": html,
            "status_code": 200,
            "title": "BOSS直聘",
        },
    )()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = ZhipinAtsExtractor()

    document, result = await extractor.fetch_list(
        url="https://www.zhipin.com/gongsi/job/x.html",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )

    assert document.provider == "ats_zhipin"
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) == 3


@pytest.mark.asyncio
async def test_zhipin_extractor_classifies_real_security_wait_page() -> None:
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page",
        (),
        {
            "url": "https://m.zhipin.com/zhaopin/ee5f30306f9bed4c03F-29W6EA~~/",
            "html": "<html><head><title>请稍候 - BOSS直聘</title></head><body><div class='page-security'>loading</div></body></html>",
            "status_code": 200,
            "title": "请稍候 - BOSS直聘",
        },
    )()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = ZhipinAtsExtractor()

    _document, result = await extractor.fetch_list(
        url="https://m.zhipin.com/zhaopin/ee5f30306f9bed4c03F-29W6EA~~/",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )

    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "captcha_required"
