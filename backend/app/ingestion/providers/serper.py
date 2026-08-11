"""Serper-backed public web search for recruiting-entry discovery."""

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError

_URL = TypeAdapter(HttpUrl)


class SerperProvider:
    name = "serper"
    endpoint = "https://google.serper.dev/search"

    def __init__(self, *, enabled: bool, api_key: str | None, gl: str, hl: str) -> None:
        if enabled and not api_key:
            raise ProviderError(
                code="missing_access_secret",
                retryable=False,
                detail="SERPER_API_KEY is required when the provider is enabled",
            )
        self._enabled = enabled
        self._api_key = api_key
        self._gl = gl
        self._hl = hl

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if not self._enabled:
            return ProviderResult(documents=())
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"X-API-KEY": self._api_key or "", "Content-Type": "application/json"},
                    json={
                        "q": query.query,
                        "gl": self._gl,
                        "hl": self._hl,
                        "num": min(query.max_results, 10),
                    },
                )
        except httpx.HTTPError as error:
            raise ProviderError(code="http_error", retryable=True, detail=str(error)) from error
        if response.status_code >= 400:
            raise ProviderError(
                code="provider_auth_failed" if response.status_code in {401, 403} else "http_status",
                retryable=response.status_code == 429 or response.status_code >= 500,
                detail=f"received HTTP {response.status_code}",
            )
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise ProviderError(code="invalid_response", retryable=True, detail="invalid JSON") from error
        documents: list[RawDocument] = []
        for item in payload.get("organic", [])[: min(query.max_results, 10)]:
            if not isinstance(item, dict) or not isinstance(item.get("link"), str):
                continue
            try:
                url = _URL.validate_python(item["link"])
            except ValidationError:
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else None
            snippet = item.get("snippet") if isinstance(item.get("snippet"), str) else ""
            documents.append(
                RawDocument(
                    provider=self.name,
                    external_id=str(item.get("position")) if item.get("position") else None,
                    url=url,
                    title=title,
                    text=" ".join(value for value in (title, snippet, str(url)) if value),
                    published_at=datetime.now(UTC),
                    authority_level=2,
                )
            )
        return ProviderResult(documents=tuple(documents))
