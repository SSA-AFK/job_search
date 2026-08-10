# backend/tests/ingestion/providers/ats_extractors/test_feishu.py
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.providers.ats_extractors.feishu import FeishuAtsExtractor

_DATA = Path(__file__).parents[4] / "data" / "ats"  # parents[4] = backend/ (see Amendment 1)


@pytest.mark.asyncio
async def test_feishu_extractor_parses_rendered_fixture() -> None:
    html = (_DATA / "feishu_list.html").read_text(encoding="utf-8")
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page", (), {"url": "https://jobs.feishu.cn/x", "html": html, "status_code": 200, "title": "Feishu"}
    )()
    renderer.aclose = AsyncMock()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = FeishuAtsExtractor()
    document, result = await extractor.fetch_list(
        url="https://jobs.feishu.cn/x",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert document.provider == "ats_feishu"
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) > 0


@pytest.mark.asyncio
async def test_feishu_extractor_returns_login_required_when_challenge_detected() -> None:
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page", (), {"url": "https://jobs.feishu.cn/login", "html": "<html>sign in to continue</html>", "status_code": 200, "title": "Login"}
    )()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = FeishuAtsExtractor()
    _document, result = await extractor.fetch_list(
        url="https://jobs.feishu.cn/login",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "login_required"
