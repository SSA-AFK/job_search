from unittest.mock import AsyncMock

import pytest

from app.ingestion.contracts import ProviderQuery
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.official_news import OfficialNewsProvider
from app.ingestion.providers.robots import RobotsPolicy


@pytest.mark.anyio
async def test_official_news_stays_on_approved_host_and_news_paths() -> None:
    client = AsyncMock(spec=SafeHttpClient)
    robots = AsyncMock(spec=RobotsPolicy)
    robots.can_fetch.return_value = True
    client.get_text.side_effect = lambda url, **_kwargs: HttpDocument(
        url=url, text="Official update", content_type="text/html", title="Update"
    )
    provider = OfficialNewsProvider(
        http_client=client,
        robots_policy=robots,
        approved_hosts=frozenset({"example.com"}),
    )

    result = await provider.search(
        ProviderQuery(
            query="Example", website="https://example.com", allowed_hosts=frozenset({"example.com"})
        )
    )

    assert [str(item.url) for item in result.documents] == [
        "https://example.com/news",
        "https://example.com/blog",
        "https://example.com/updates",
        "https://example.com/press",
        "https://example.com/products",
        "https://example.com/solutions",
    ]
