from urllib.parse import urlsplit

from app.ingestion.contracts import ProviderQuery, ProviderResult
from app.ingestion.providers.ats_extractors.feishu import FeishuAtsExtractor
from app.ingestion.providers.ats_extractors.moka import MokaAtsExtractor
from app.ingestion.providers.ats_renderer import AtsRenderer
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_PLATFORM_HOSTS = {"feishu": "jobs.feishu.cn", "moka": "app.mokahr.com"}


class AtsProvider:
    name = "ats"

    def __init__(
        self,
        *,
        http_client: SafeHttpClient,
        robots_policy: RobotsPolicy,
        renderer: AtsRenderer,
        feishu_extractor: FeishuAtsExtractor,
        moka_extractor: MokaAtsExtractor,
        enabled_platforms: frozenset[str],
    ) -> None:
        self._http = http_client
        self._robots = robots_policy
        self._renderer = renderer
        self._extractors: dict[str, FeishuAtsExtractor | MokaAtsExtractor] = {
            "feishu": feishu_extractor,
            "moka": moka_extractor,
        }
        self._enabled = enabled_platforms

    async def search(self, query: ProviderQuery) -> ProviderResult:
        return ProviderResult(documents=())

    async def search_with_url(self, url: str, query: ProviderQuery) -> ProviderResult:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        platform = next((p for p, h in _PLATFORM_HOSTS.items() if h == host), None)
        if platform is None or platform not in self._enabled:
            return ProviderResult(documents=(), warnings=("platform_disabled",))
        extractor = self._extractors[platform]
        document, result = await extractor.fetch_list(
            url=url, http_client=self._http, robots_policy=self._robots, renderer=self._renderer
        )
        warnings: list[str] = []
        if result.error_code is not None and result.error_code != "no_candidates":
            warnings.append(result.error_code)
        return ProviderResult(documents=(document,), warnings=tuple(warnings))
