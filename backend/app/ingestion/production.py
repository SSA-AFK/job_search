"""Checked-in production composition for collection workers."""

import re
from typing import Protocol

from app.ingestion.providers.ats import AtsProvider
from app.ingestion.providers.ats_extractors.feishu import FeishuAtsExtractor
from app.ingestion.providers.ats_extractors.lagou import LagouAtsExtractor
from app.ingestion.providers.ats_extractors.liepin import LiepinAtsExtractor
from app.ingestion.providers.ats_extractors.moka import MokaAtsExtractor
from app.ingestion.providers.ats_extractors.zhipin import ZhipinAtsExtractor
from app.ingestion.providers.ats_renderer import AtsRenderer
from app.ingestion.providers.company_site import CompanySiteProvider
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.limits import ControlledProvider
from app.ingestion.providers.robots import RobotsPolicy
from app.ingestion.providers.serper import SerperProvider
from app.ingestion.providers.tianyancha import TianyanchaProvider
from app.ingestion.providers.ymicp import YmicpProvider
from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider
from app.ingestion.runtime import RuntimeComponents

_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


class RuntimeSettings(Protocol):
    zhihu_provider_enabled: bool
    zhihu_access_secret: str | None
    serper_provider_enabled: bool
    serper_api_key: str | None
    serper_gl: str
    serper_hl: str
    company_site_provider_enabled: bool
    company_site_approved_hosts: str
    provider_max_concurrency: int
    provider_min_interval_seconds: float
    ats_provider_enabled: bool
    ats_feishu_enabled: bool
    ats_moka_enabled: bool
    ats_zhipin_enabled: bool
    ats_liepin_enabled: bool
    ats_lagou_enabled: bool
    ats_approved_hosts: str
    playwright_pool_size: int
    playwright_page_timeout_seconds: float
    tianyancha_provider_enabled: bool
    tianyancha_cli_executable: str
    tianyancha_call_budget: int
    ymicp_provider_enabled: bool
    ymicp_base_url: str
    ymicp_timeout_seconds: float


class ProductionRuntimeConfigurationError(Exception):
    pass


def create_runtime_components(config: RuntimeSettings) -> RuntimeComponents:
    if config.provider_max_concurrency < 1:
        raise ProductionRuntimeConfigurationError("provider concurrency must be positive")
    if config.provider_min_interval_seconds < 0:
        raise ProductionRuntimeConfigurationError("provider interval must not be negative")

    providers: list[object] = []
    if config.ymicp_provider_enabled:
        providers.append(
            YmicpProvider(
                enabled=True,
                base_url=config.ymicp_base_url,
                timeout_seconds=config.ymicp_timeout_seconds,
            )
        )
    if config.tianyancha_provider_enabled:
        if config.tianyancha_call_budget < 1:
            raise ProductionRuntimeConfigurationError(
                "TIANYANCHA_CALL_BUDGET must be positive when Tianyancha is enabled"
            )
        providers.append(
            TianyanchaProvider(
                enabled=True,
                cli_executable=config.tianyancha_cli_executable,
                call_budget=config.tianyancha_call_budget,
            )
        )
    if config.zhihu_provider_enabled:
        if not config.zhihu_access_secret:
            raise ProductionRuntimeConfigurationError(
                "ZHIHU_ACCESS_SECRET is required when Zhihu is enabled"
            )
        providers.append(
            ZhihuGlobalSearchProvider(
                enabled=True, access_secret=config.zhihu_access_secret
            )
        )
    if config.serper_provider_enabled:
        providers.append(
            SerperProvider(
                enabled=True,
                api_key=config.serper_api_key,
                gl=config.serper_gl,
                hl=config.serper_hl,
            )
        )
    if config.company_site_provider_enabled:
        approved_hosts = frozenset(
            host.strip().lower().rstrip(".")
            for host in config.company_site_approved_hosts.split(",")
            if host.strip()
        )
        if not approved_hosts or any(
            _HOST_PATTERN.fullmatch(host) is None for host in approved_hosts
        ):
            raise ProductionRuntimeConfigurationError(
                "COMPANY_SITE_APPROVED_HOSTS must contain public hostnames"
            )
        http_client = SafeHttpClient()
        providers.append(
            CompanySiteProvider(
                http_client=http_client,
                robots_policy=RobotsPolicy(http_client=http_client),
                approved_hosts=approved_hosts,
            )
        )
    if config.ats_provider_enabled:
        enabled_platforms = frozenset(
            p
            for p, on in (
                ("feishu", config.ats_feishu_enabled),
                ("moka", config.ats_moka_enabled),
                ("zhipin", config.ats_zhipin_enabled),
                ("liepin", config.ats_liepin_enabled),
                ("lagou", config.ats_lagou_enabled),
            )
            if on
        )
        if not enabled_platforms:
            raise ProductionRuntimeConfigurationError(
                "ATS_PROVIDER_ENABLED requires at least one platform flag"
            )
        renderer = AtsRenderer(
            pool_size=config.playwright_pool_size,
            page_timeout_seconds=config.playwright_page_timeout_seconds,
        )
        http_client = SafeHttpClient()
        providers.append(
            AtsProvider(
                http_client=http_client,
                robots_policy=RobotsPolicy(http_client=http_client),
                renderer=renderer,
                feishu_extractor=FeishuAtsExtractor(),
                moka_extractor=MokaAtsExtractor(),
                zhipin_extractor=ZhipinAtsExtractor(),
                liepin_extractor=LiepinAtsExtractor(),
                lagou_extractor=LagouAtsExtractor(),
                enabled_platforms=enabled_platforms,
            )
        )
    if not providers:
        raise ProductionRuntimeConfigurationError("at least one Provider must be enabled")

    controlled = tuple(
        ControlledProvider(
            provider,
            max_concurrency=config.provider_max_concurrency,
            min_interval_seconds=config.provider_min_interval_seconds,
        )
        for provider in providers
    )
    return RuntimeComponents(
        providers=controlled,
        extractor=None,
    )
