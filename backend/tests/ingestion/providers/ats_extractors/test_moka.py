# backend/tests/ingestion/providers/ats_extractors/test_moka.py
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.providers.ats_extractors.moka import MokaAtsExtractor

_DATA = Path(__file__).parents[4] / "data" / "ats"  # parents[4] = backend/ (see Amendment 1)


@pytest.mark.asyncio
async def test_moka_extractor_parses_rendered_fixture() -> None:
    html = (_DATA / "moka_list.html").read_text(encoding="utf-8")
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page", (), {"url": "https://app.mokahr.com/x", "html": html, "status_code": 200, "title": "Moka"}
    )()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    extractor = MokaAtsExtractor()
    document, result = await extractor.fetch_list(
        url="https://app.mokahr.com/x",
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
    )
    assert document.provider == "ats_moka"
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) > 0
