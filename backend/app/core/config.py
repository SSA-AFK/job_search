from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./company_search.db"
    collection_enabled: bool = True
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    cache_redis_url: str | None = None
    celery_task_always_eager: bool = False
    collection_runtime_factory: str | None = None


settings = Settings()
