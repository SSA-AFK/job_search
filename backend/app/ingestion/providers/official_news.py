"""Focused crawler for public product updates and company news."""

from app.ingestion.providers.company_site import CompanySiteProvider
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_NEWS_PATHS = ("/news", "/blog", "/updates", "/press", "/products", "/solutions")


class OfficialNewsProvider(CompanySiteProvider):
    """Collect only public first-party news and product pages from a known website."""

    name = "official_news"

    def __init__(
        self,
        *,
        http_client: SafeHttpClient,
        robots_policy: RobotsPolicy,
        approved_hosts: frozenset[str],
    ) -> None:
        super().__init__(
            http_client=http_client,
            robots_policy=robots_policy,
            approved_hosts=approved_hosts,
            seed_paths=_NEWS_PATHS,
        )
