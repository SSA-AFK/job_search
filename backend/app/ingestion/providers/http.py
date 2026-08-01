"""Defensive HTTP fetching for ingestion providers."""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address

import httpx

from app.ingestion.errors import ProviderError
from app.ingestion.providers.security import DnsResolver, is_public_ip, resolve_host

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 200_000
MAX_REDIRECTS = 5
SUPPORTED_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})


@dataclass(frozen=True, slots=True)
class HttpDocument:
    url: str
    text: str
    content_type: str


class _PlainTextExtractor(HTMLParser):
    _BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "blockquote",
            "br",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "p",
            "section",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: Iterable[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self._parts).split())


class SafeHttpClient:
    def __init__(
        self,
        *,
        dns_resolver: DnsResolver = resolve_host,
        connect_timeout_seconds: float = 5.0,
        total_timeout_seconds: float = 15.0,
    ) -> None:
        self._dns_resolver = dns_resolver
        self._connect_timeout_seconds = connect_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds

    async def get_text(self, url: str, *, allowed_hosts: set[str]) -> HttpDocument:
        """Fetch an allowlisted public URL, following only validated redirects."""
        normalized_hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await self._get_text(url, normalized_hosts)
        except TimeoutError as error:
            raise ProviderError(
                code="total_timeout", retryable=True, detail="request exceeded total timeout"
            ) from error
        except httpx.ConnectTimeout as error:
            raise ProviderError(code="connect_timeout", retryable=True, detail=str(error)) from error
        except httpx.TimeoutException as error:
            raise ProviderError(code="request_timeout", retryable=True, detail=str(error)) from error
        except httpx.HTTPError as error:
            raise ProviderError(code="http_error", retryable=True, detail=str(error)) from error

    async def _get_text(self, url: str, allowed_hosts: frozenset[str]) -> HttpDocument:
        current_url = await self._validate_url(url, allowed_hosts, error_code="unsafe_url")
        timeout = httpx.Timeout(self._connect_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if location is None:
                            raise ProviderError(
                                code="invalid_redirect",
                                retryable=False,
                                detail="redirect response did not include a Location header",
                            )
                        current_url = await self._validate_url(
                            str(current_url.join(location)), allowed_hosts, error_code="unsafe_redirect"
                        )
                        continue

                    if response.is_error:
                        raise ProviderError(
                            code="http_status",
                            retryable=response.status_code >= 500,
                            detail=f"received HTTP {response.status_code}",
                        )

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in SUPPORTED_CONTENT_TYPES:
                        raise ProviderError(
                            code="unsupported_content_type",
                            retryable=False,
                            detail=f"received {content_type or 'no content type'}",
                        )

                    body = await self._read_limited_body(response)
                    decoded = body.decode(response.encoding or "utf-8", errors="replace")
                    text = self._to_text(decoded, content_type)
                    return HttpDocument(url=str(current_url), text=text[:MAX_TEXT_LENGTH], content_type=content_type)

        raise ProviderError(
            code="too_many_redirects", retryable=False, detail="redirect limit exceeded"
        )

    async def _validate_url(
        self, url: str, allowed_hosts: frozenset[str], *, error_code: str
    ) -> httpx.URL:
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL as error:
            raise ProviderError(code=error_code, retryable=False, detail=str(error)) from error

        host = parsed.host
        if parsed.scheme not in {"http", "https"} or host is None or parsed.userinfo:
            raise ProviderError(code=error_code, retryable=False, detail="URL scheme or authority is invalid")

        normalized_host = host.lower().rstrip(".")
        try:
            literal_address = ip_address(normalized_host)
        except ValueError:
            literal_address = None

        if literal_address is not None:
            if not is_public_ip(str(literal_address)) or normalized_host not in allowed_hosts:
                raise ProviderError(code=error_code, retryable=False, detail="URL host is not public")
            return parsed

        if normalized_host not in allowed_hosts:
            raise ProviderError(code=error_code, retryable=False, detail="URL host is not allowlisted")

        addresses = await self._dns_resolver(normalized_host)
        if not addresses or any(not is_public_ip(address) for address in addresses):
            raise ProviderError(code=error_code, retryable=False, detail="URL DNS result is not public")
        return parsed

    async def _read_limited_body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total_size = 0
        async for chunk in response.aiter_bytes():
            total_size += len(chunk)
            if total_size > MAX_RESPONSE_BYTES:
                raise ProviderError(
                    code="body_too_large", retryable=False, detail="response exceeded 2 MiB limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _to_text(body: str, content_type: str) -> str:
        if content_type == "text/plain":
            return body
        parser = _PlainTextExtractor()
        parser.feed(body)
        parser.close()
        return parser.text()
