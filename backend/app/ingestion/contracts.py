"""Provider-facing immutable data contracts."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProviderQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None
    allowed_hosts: frozenset[str] = frozenset()
    max_results: int = Field(default=10, ge=1, le=20)


class RawDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    external_id: str | None
    url: HttpUrl
    title: str | None
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
