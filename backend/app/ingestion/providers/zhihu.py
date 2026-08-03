"""Zhihu Global Search API provider."""

import asyncio
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError

_RETRY_DELAYS = (0.5, 1.0, 2.0)
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
_URL_ADAPTER = TypeAdapter(HttpUrl)


class _MarkupTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


class ZhihuGlobalSearchProvider:
    name = "zhihu_global_search"
    endpoint = "https://developer.zhihu.com/api/v1/content/global_search"

    def __init__(
        self,
        *,
        enabled: bool,
        access_secret: str | None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.0, 0.1),
        total_timeout_seconds: float = 15.0,
        timeout: Callable[[float | None], Any] = asyncio.timeout,
    ) -> None:
        if enabled and not access_secret:
            raise ProviderError(
                code="missing_access_secret",
                retryable=False,
                detail="ZHIHU_ACCESS_SECRET is required when the provider is enabled",
            )
        self._enabled = enabled
        self._access_secret = access_secret
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._jitter = jitter
        self._total_timeout_seconds = total_timeout_seconds
        self._timeout = timeout

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if not self._enabled:
            return ProviderResult(documents=())

        filter_expression = self._filter_expression(query.allowed_hosts)
        if query.allowed_hosts and filter_expression is None:
            return ProviderResult(documents=())
        try:
            async with self._timeout(self._total_timeout_seconds):
                response = await self._request(query, filter_expression)
        except TimeoutError as error:
            raise ProviderError(
                code="request_timeout", retryable=True, detail="request exceeded total timeout"
            ) from error
        payload = self._parse_json(response)
        return self._parse_result(payload)

    async def _request(
        self, query: ProviderQuery, filter_expression: str | None
    ) -> httpx.Response:
        timeout = httpx.Timeout(self._total_timeout_seconds, connect=5.0)
        params: dict[str, str] = {
            "Query": query.query,
            "Count": str(min(query.max_results, 20)),
            "SearchDB": "all",
        }
        if filter_expression:
            params["Filter"] = filter_expression

        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    response = await client.get(
                        self.endpoint,
                        params=params,
                        headers={
                            "Authorization": f"Bearer {self._access_secret}",
                            "X-Request-Timestamp": str(int(self._clock().timestamp())),
                            "Content-Type": "application/json",
                        },
                    )
            except httpx.ConnectTimeout as error:
                provider_error = ProviderError(
                    code="connect_timeout", retryable=True, detail=str(error)
                )
            except httpx.TimeoutException as error:
                provider_error = ProviderError(
                    code="request_timeout", retryable=True, detail=str(error)
                )
            except httpx.HTTPError as error:
                provider_error = ProviderError(code="http_error", retryable=True, detail=str(error))
            else:
                if response.status_code < 400:
                    return response
                provider_error = ProviderError(
                    code="http_status",
                    retryable=response.status_code == 429 or response.status_code >= 500,
                    detail=f"received HTTP {response.status_code}",
                )

            if not provider_error.retryable or attempt == len(_RETRY_DELAYS):
                raise provider_error
            await self._sleep(_RETRY_DELAYS[attempt] + self._jitter())

        raise AssertionError("unreachable")

    @staticmethod
    def _filter_expression(allowed_hosts: frozenset[str]) -> str | None:
        hosts = sorted(
            {
                normalized
                for host in allowed_hosts
                if (normalized := host.lower().rstrip("."))
                and _HOST_PATTERN.fullmatch(normalized)
                and normalized != "zhihu.com"
                and not normalized.endswith(".zhihu.com")
            }
        )
        if not hosts:
            return None
        clauses = [f'host=="{host}"' for host in hosts]
        return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"

    @staticmethod
    def _parse_json(response: httpx.Response) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError(code="invalid_json", retryable=False, detail=str(error)) from error
        if not isinstance(payload, Mapping):
            raise ProviderError(code="invalid_response", retryable=False, detail="response is not an object")
        return payload

    @classmethod
    def _parse_result(cls, payload: Mapping[str, Any]) -> ProviderResult:
        code = payload.get("Code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise ProviderError(code="invalid_response", retryable=False, detail="Code is not an integer")
        if code != 0:
            raise ProviderError(
                code="api_error", retryable=False, detail=str(payload.get("Message", "unknown error"))
            )
        data = payload.get("Data")
        if not isinstance(data, Mapping):
            raise ProviderError(code="invalid_response", retryable=False, detail="Data is not an object")
        items = data.get("Items")
        has_more = data.get("HasMore")
        if not isinstance(items, list) or not isinstance(has_more, bool):
            raise ProviderError(code="invalid_response", retryable=False, detail="Data fields are invalid")
        try:
            documents = tuple(cls._parse_document(item) for item in items)
        except (KeyError, TypeError, ValueError, OverflowError, OSError, ValidationError) as error:
            raise ProviderError(code="invalid_response", retryable=False, detail=str(error)) from error
        return ProviderResult(documents=documents, truncated=has_more)

    @staticmethod
    def _parse_document(item: object) -> RawDocument:
        if not isinstance(item, Mapping):
            raise TypeError("item is not an object")
        return RawDocument(
            provider=ZhihuGlobalSearchProvider.name,
            external_id=_required_string(item, "ContentID"),
            url=_required_url(item),
            title=_strip_markup(_required_string(item, "Title")),
            text=_strip_markup(_required_string(item, "ContentText")),
            published_at=datetime.fromtimestamp(_required_int(item, "EditTime"), tz=UTC),
            authority_level=_required_int(item, "AuthorityLevel"),
        )


def _required_string(item: Mapping[str, Any], field: str) -> str:
    value = item[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} is not a string")
    return value


def _required_int(item: Mapping[str, Any], field: str) -> int:
    value = item[field]
    if isinstance(value, bool):
        raise TypeError(f"{field} is not an integer")
    return int(value)


def _required_url(item: Mapping[str, Any]) -> HttpUrl:
    return _URL_ADAPTER.validate_python(_required_string(item, "Url"))


def _strip_markup(value: str) -> str:
    parser = _MarkupTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()
