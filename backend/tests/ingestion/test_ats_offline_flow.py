# backend/tests/ingestion/test_ats_offline_flow.py
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ingestion.contracts import ProviderQuery
from app.ingestion.providers.ats import AtsProvider
from app.ingestion.providers.ats_extractors.feishu import FeishuAtsExtractor
from app.ingestion.providers.ats_extractors.moka import MokaAtsExtractor

_DATA = Path(__file__).parents[2] / "data" / "ats"


@pytest.mark.asyncio
async def test_ats_offline_flow_produces_document_and_candidates() -> None:
    html = (_DATA / "feishu_list.html").read_text(encoding="utf-8")
    renderer = AsyncMock()
    renderer.render.return_value = type(
        "Page", (), {"url": "https://jobs.feishu.cn/x", "html": html, "status_code": 200, "title": "Feishu"}
    )()
    robots = AsyncMock()
    robots.can_fetch.return_value = True
    provider = AtsProvider(
        http_client=None,  # type: ignore[arg-type]
        robots_policy=robots,
        renderer=renderer,
        feishu_extractor=FeishuAtsExtractor(),
        moka_extractor=MokaAtsExtractor(),
        enabled_platforms=frozenset({"feishu"}),
    )
    query = ProviderQuery(query="ai", website=None, allowed_hosts=frozenset({"jobs.feishu.cn"}), max_results=5)
    result = await provider.search_with_url("https://jobs.feishu.cn/x", query)
    assert len(result.documents) == 1
    assert result.documents[0].provider == "ats_feishu"
