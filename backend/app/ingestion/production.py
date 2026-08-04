"""Checked-in production composition for collection workers."""

import re
from typing import Protocol

from app.ingestion.deduplication.semantic import LlmSemanticDuplicateJudge
from app.ingestion.extraction.client import OpenAICompatibleLlmClient
from app.ingestion.extraction.crew import CrewExtractor
from app.ingestion.providers.company_site import CompanySiteProvider
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.limits import ControlledProvider
from app.ingestion.providers.robots import RobotsPolicy
from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider
from app.ingestion.runtime import RuntimeComponents

_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


class RuntimeSettings(Protocol):
    openai_compatible_base_url: str | None
    openai_compatible_model: str | None
    openai_compatible_api_key: str | None
    openai_request_timeout_seconds: float
    zhihu_provider_enabled: bool
    zhihu_access_secret: str | None
    company_site_provider_enabled: bool
    company_site_approved_hosts: str
    provider_max_concurrency: int
    provider_min_interval_seconds: float


class ProductionRuntimeConfigurationError(Exception):
    pass


def create_runtime_components(config: RuntimeSettings) -> RuntimeComponents:
    llm_values = (
        config.openai_compatible_base_url,
        config.openai_compatible_model,
        config.openai_compatible_api_key,
    )
    if any(not value or not value.strip() for value in llm_values):
        raise ProductionRuntimeConfigurationError(
            "OPENAI_COMPATIBLE_BASE_URL, MODEL, and API_KEY are required"
        )
    if config.provider_max_concurrency < 1:
        raise ProductionRuntimeConfigurationError("provider concurrency must be positive")
    if config.provider_min_interval_seconds < 0:
        raise ProductionRuntimeConfigurationError("provider interval must not be negative")

    llm = OpenAICompatibleLlmClient(
        base_url=llm_values[0],  # type: ignore[arg-type]
        model=llm_values[1],  # type: ignore[arg-type]
        api_key=llm_values[2],  # type: ignore[arg-type]
        timeout_seconds=config.openai_request_timeout_seconds,
    )
    providers: list[object] = []
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
        extractor=CrewExtractor(llm),
        semantic_judge=LlmSemanticDuplicateJudge(llm),
    )
