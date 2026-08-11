"""Provider-facing immutable data contracts."""

from datetime import datetime
from ipaddress import ip_address
from typing import Annotated, Protocol, runtime_checkable

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl


class ParsedJob(BaseModel):
    """Pre-parsed job from a structured source (e.g. ATS), bypasses LLM extraction."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2_000)
    city: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    source_raw_id: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, max_length=255)


def _bounded_document_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 2_000:
        raise ValueError("URL must not exceed 2000 characters")
    return value


def require_statically_public_url(value: HttpUrl) -> HttpUrl:
    host = (value.host or "").lower().rstrip(".")
    if value.username is not None or value.password is not None:
        raise ValueError("URL must be a public URL without credentials")
    if not host or host in {"localhost", "home.arpa"} or host.endswith(
        (
            ".localhost",
            ".local",
            ".localdomain",
            ".internal",
            ".lan",
            ".home",
            ".home.arpa",
        )
    ):
        raise ValueError("URL must be a public URL")
    literal_host = host.removeprefix("[").removesuffix("]")
    try:
        address = ip_address(literal_host)
    except ValueError:
        if "." not in host:
            raise ValueError("URL must be a public URL") from None
    else:
        if not address.is_global:
            raise ValueError("URL must be a public URL")
    return value


DocumentUrl = Annotated[
    HttpUrl,
    AfterValidator(_bounded_document_url),
    AfterValidator(require_statically_public_url),
]


from app.ingestion.extraction.schemas import JobCandidate


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
    parsed_jobs: tuple[ParsedJob, ...] = ()


class Provider(Protocol):
    async def search(self, query: ProviderQuery) -> ProviderResult: ...


@runtime_checkable
class WebsiteDependentProvider(Provider, Protocol):
    @property
    def requires_website(self) -> bool: ...

    @property
    def approved_hosts(self) -> frozenset[str]: ...
