"""Provider-facing immutable data contracts."""

from datetime import datetime
from typing import Annotated, Protocol, runtime_checkable

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl


def _bounded_document_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 2_000:
        raise ValueError("URL must not exceed 2000 characters")
    return value


DocumentUrl = Annotated[HttpUrl, AfterValidator(_bounded_document_url)]


class ProviderQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None
    allowed_hosts: frozenset[str] = frozenset()
    max_results: int = Field(default=10, ge=1, le=20)


class RawDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1, max_length=50)
    external_id: str | None = Field(max_length=255)
    url: DocumentUrl
    title: str | None = Field(max_length=500)
    text: str = Field(max_length=200_000)
    published_at: datetime | None
    authority_level: int | None = Field(default=None, ge=1, le=4)


class ProviderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    documents: tuple[RawDocument, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()


class Provider(Protocol):
    async def search(self, query: ProviderQuery) -> ProviderResult: ...


@runtime_checkable
class WebsiteDependentProvider(Provider, Protocol):
    @property
    def requires_website(self) -> bool: ...
