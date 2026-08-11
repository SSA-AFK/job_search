from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./company_search.db"
    collection_enabled: bool = False
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    cache_redis_url: str | None = None
    celery_task_always_eager: bool = False
    collection_runtime_factory: str | None = None
    zhihu_provider_enabled: bool = False
    zhihu_access_secret: str | None = None
    serper_provider_enabled: bool = False
    serper_api_key: str | None = None
    serper_gl: str = "cn"
    serper_hl: str = "zh-cn"
    openai_compatible_base_url: str | None = None
    openai_compatible_model: str | None = None
    openai_compatible_api_key: str | None = None
    openai_request_timeout_seconds: float = 30.0
    company_site_provider_enabled: bool = False
    company_site_approved_hosts: str = ""
    provider_max_concurrency: int = 2
    provider_min_interval_seconds: float = 0.25
    collection_stale_queued_seconds: int = 300
    collection_stale_running_seconds: int = 1_800
    gate1_live_discovery_enabled: bool = False
    gate1_source_registry_path: str = "data/gate1/source_registry.json"
    gate1_zhihu_request_budget: int = 200
    gate1_domain_min_interval_seconds: float = 1.0
    entry_evidence_model_enabled: bool = False
    entry_evidence_model_name: str = "qwen-plus"
    entry_evidence_model_confidence_threshold: Decimal = Field(
        default=Decimal("0.90"), ge=Decimal(0), le=Decimal(1)
    )
    ats_provider_enabled: bool = False
    ats_feishu_enabled: bool = False
    ats_moka_enabled: bool = False
    ats_zhipin_enabled: bool = False
    ats_liepin_enabled: bool = False
    ats_lagou_enabled: bool = False
    ats_approved_hosts: str = "jobs.feishu.cn,app.mokahr.com,zhipin.com,liepin.com,lagou.com"
    zhipin_cdp_company_provider_enabled: bool = False
    zhipin_cdp_endpoint_url: str = "http://127.0.0.1:9222"
    zhipin_cdp_min_match_score: float = 80.0
    zhipin_cdp_page_size: int = 30
    zhipin_cdp_max_pages: int = 20
    zhipin_cdp_block_threshold: int = 2
    playwright_pool_size: int = 2
    playwright_page_timeout_seconds: float = 30.0
    tianyancha_provider_enabled: bool = False
    tianyancha_cli_executable: str = "npx"
    tianyancha_call_budget: int = 100
    ymicp_provider_enabled: bool = True
    ymicp_base_url: str = "http://127.0.0.1:16181"
    ymicp_timeout_seconds: float = 30.0


settings = Settings()
