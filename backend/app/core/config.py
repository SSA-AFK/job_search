from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./company_search.db"
    collection_enabled: bool = False


settings = Settings()
