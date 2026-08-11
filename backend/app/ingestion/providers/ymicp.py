"""Local ymicp adapter for active ICP record verification."""

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from pydantic import HttpUrl, TypeAdapter

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError

_MIIT_URL = TypeAdapter(HttpUrl).validate_python("https://beian.miit.gov.cn/")
_QueryParams = Mapping[str, str | int | float | bool | None]
_JsonGetter = Callable[[str, _QueryParams], Awaitable[dict[str, object]]]


class YmicpProvider:
    """Queries the user-operated ymicp service by a company's public domain."""

    name = "ymicp"

    def __init__(
        self,
        *,
        enabled: bool = True,
        base_url: str = "http://127.0.0.1:16181",
        timeout_seconds: float = 30.0,
        get_json: _JsonGetter | None = None,
    ) -> None:
        self._enabled = enabled
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._get_json = get_json or self._default_get_json

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if not self._enabled:
            return ProviderResult(documents=())
        domain = _domain_from(query)
        if domain is None:
            return ProviderResult(documents=(), warnings=("ymicp_domain_unavailable",))
        try:
            payload = await self._get_json(
                f"{self._base_url}/query/web",
                {"search": domain, "pageNum": 1, "pageSize": query.max_results},
            )
        except httpx.HTTPError as error:
            raise ProviderError(
                code="ymicp_unavailable", retryable=True, detail=str(error)
            ) from error
        if payload.get("code") != 200 or payload.get("success") is False:
            raise ProviderError(
                code="ymicp_query_failed",
                retryable=True,
                detail=str(payload.get("msg", "unknown ymicp failure")),
            )
        params = payload.get("params")
        records = params.get("list", []) if isinstance(params, dict) else []
        if not isinstance(records, list) or not records:
            return ProviderResult(documents=(), warnings=("ymicp_no_match",))
        document = RawDocument(
            provider=self.name,
            external_id=domain,
            url=_MIIT_URL,
            title=f"ICP verification for {domain}",
            text=json.dumps({"query_domain": domain, "records": records}, ensure_ascii=False),
            published_at=datetime.now(UTC),
            authority_level=4,
        )
        return ProviderResult(documents=(document,))

    async def _default_get_json(self, url: str, params: _QueryParams) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError(
                code="ymicp_invalid_response",
                retryable=False,
                detail="ymicp returned a non-object JSON payload",
            )
        return payload


def _domain_from(query: ProviderQuery) -> str | None:
    if query.website is not None and query.website.host:
        return query.website.host.lower().removeprefix("www.").rstrip(".")
    candidate = query.query.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    return parsed.hostname.lower().removeprefix("www.").rstrip(".") if parsed.hostname else None
