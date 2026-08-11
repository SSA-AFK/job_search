from urllib.parse import urlsplit

from app.ingestion.contracts import ParsedJob, ProviderQuery, ProviderResult
from app.ingestion.providers.ats_extractors.feishu import FeishuAtsExtractor
from app.ingestion.providers.ats_extractors.lagou import LagouAtsExtractor
from app.ingestion.providers.ats_extractors.liepin import LiepinAtsExtractor
from app.ingestion.providers.ats_extractors.moka import MokaAtsExtractor
from app.ingestion.providers.ats_extractors.zhipin import ZhipinAtsExtractor
from app.ingestion.providers.ats_renderer import AtsRenderer
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_PLATFORM_HOSTS = {
    "feishu": "jobs.feishu.cn",
    "moka": "app.mokahr.com",
    "zhipin": "zhipin.com",
    "liepin": "liepin.com",
    "lagou": "lagou.com",
}

_EMPLOYMENT_TYPE_TO_JOB_TYPE = {
    "full_time": "full_time",
    "internship": "internship",
    "part_time": "part_time",
    "temporary": "temporary",
}

_AtsExtractor = (
    FeishuAtsExtractor | MokaAtsExtractor | ZhipinAtsExtractor | LiepinAtsExtractor | LagouAtsExtractor
)


def _salary_from_raw(raw: dict[str, str]) -> tuple[int | None, int | None, int | None]:
    def _int(key: str) -> int | None:
        v = raw.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return _int("salary_min_k"), _int("salary_max_k"), _int("salary_months")


class AtsProvider:
    name = "ats"
    requires_website = True

    def __init__(
        self,
        *,
        http_client: SafeHttpClient,
        robots_policy: RobotsPolicy,
        renderer: AtsRenderer,
        feishu_extractor: FeishuAtsExtractor,
        moka_extractor: MokaAtsExtractor,
        zhipin_extractor: ZhipinAtsExtractor,
        liepin_extractor: LiepinAtsExtractor,
        lagou_extractor: LagouAtsExtractor,
        enabled_platforms: frozenset[str],
    ) -> None:
        self._http = http_client
        self._robots = robots_policy
        self._renderer = renderer
        self._extractors: dict[str, _AtsExtractor] = {
            "feishu": feishu_extractor,
            "moka": moka_extractor,
            "zhipin": zhipin_extractor,
            "liepin": liepin_extractor,
            "lagou": lagou_extractor,
        }
        self._enabled = enabled_platforms
        self._approved_hosts = frozenset(
            host
            for platform, host in _PLATFORM_HOSTS.items()
            if platform in enabled_platforms
        )

    @property
    def approved_hosts(self) -> frozenset[str]:
        return self._approved_hosts

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if query.website is not None:
            return await self.search_with_url(str(query.website), query)
        return ProviderResult(documents=())

    async def search_with_url(self, url: str, query: ProviderQuery) -> ProviderResult:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        # Accept both exact host and subdomain (e.g. www.zhipin.com, www.liepin.com)
        platform: str | None = None
        for p, target_host in _PLATFORM_HOSTS.items():
            if host == target_host or host.endswith("." + target_host):
                platform = p
                break
        if platform is None or platform not in self._enabled:
            return ProviderResult(documents=(), warnings=("platform_disabled",))
        extractor = self._extractors[platform]
        document, result = await extractor.fetch_list(
            url=url, http_client=self._http, robots_policy=self._robots, renderer=self._renderer
        )
        warnings: list[str] = []
        if result.error_code is not None and result.error_code != "no_candidates":
            warnings.append(result.error_code)

        parsed_jobs: list[ParsedJob] = []
        for candidate in result.candidates:
            salary_min_k, salary_max_k, salary_months = _salary_from_raw(candidate.raw_attributes)
            parsed_jobs.append(
                ParsedJob(
                    title=candidate.title,
                    url=str(candidate.url),
                    city=candidate.city,
                    employment_type=candidate.employment_type,
                    job_type=_EMPLOYMENT_TYPE_TO_JOB_TYPE.get(candidate.employment_type) if candidate.employment_type else None,  # type: ignore[arg-type]
                    salary_min_monthly=salary_min_k,
                    salary_max_monthly=salary_max_k,
                    salary_months=salary_months,
                    provider=f"ats_{platform}",
                    source_raw_id=candidate.external_id or str(candidate.url),
                    external_id=candidate.external_id,
                )
            )
        return ProviderResult(
            documents=(document,),
            warnings=tuple(warnings),
            parsed_jobs=tuple(parsed_jobs),
        )
