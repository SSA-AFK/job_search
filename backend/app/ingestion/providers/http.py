"""Defensive HTTP fetching for ingestion providers."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address

import httpcore
import httpx

from app.ingestion.errors import ProviderError
from app.ingestion.providers.security import DnsResolver, is_public_ip, resolve_host

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 200_000
MAX_REDIRECTS = 5
SUPPORTED_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
RedirectValidator = Callable[[str], Awaitable[bool]]
RequestStartHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HttpDocument:
    url: str
    text: str
    content_type: str
    title: str | None = None
    links: tuple[str, ...] = ()
    anchors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _ValidatedUrl:
    url: httpx.URL
    address: str


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial validated addresses while preserving the original HTTP origin for Host and SNI."""

    def __init__(self, delegate: httpcore.AsyncNetworkBackend) -> None:
        self._delegate = delegate
        self._addresses: dict[str, str] = {}

    def pin(self, host: str, address: str) -> None:
        self._addresses[host.lower().rstrip(".")] = address

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = self._addresses.get(host.lower().rstrip("."))
        if address is None:
            raise httpcore.ConnectError("attempted connection without a validated address")
        return await self._delegate.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._delegate.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, network_backend: httpcore.AsyncNetworkBackend | None) -> None:
        super().__init__(trust_env=False)
        delegate = network_backend or self._pool._network_backend
        self._pinned_network_backend = _PinnedNetworkBackend(delegate)
        self._pool._network_backend = self._pinned_network_backend

    def pin(self, validated_url: _ValidatedUrl) -> None:
        host = validated_url.url.host
        assert host is not None
        self._pinned_network_backend.pin(host, validated_url.address)


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
        self._title_parts: list[str] = []
        self._links: list[str] = []
        self._anchors: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self._ignored_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: Iterable[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth:
            if normalized_tag == "title":
                self._title_depth += 1
            elif normalized_tag == "a":
                href = next(
                    (value for name, value in attrs if name.lower() == "href" and value), None
                )
                if href is not None:
                    self._links.append(href)
                    self._anchor_href = href
                    self._anchor_parts = []
            if normalized_tag in self._BLOCK_TAGS:
                self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth:
            if normalized_tag == "title" and self._title_depth:
                self._title_depth -= 1
            elif normalized_tag == "a" and self._anchor_href is not None:
                text = " ".join("".join(self._anchor_parts).split())
                self._anchors.append((self._anchor_href, text))
                self._anchor_href = None
                self._anchor_parts = []
            if normalized_tag in self._BLOCK_TAGS:
                self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)
            if self._title_depth:
                self._title_parts.append(data)
            if self._anchor_href is not None:
                self._anchor_parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self._parts).split())

    def title(self) -> str | None:
        title = " ".join("".join(self._title_parts).split())
        return title or None

    def links(self) -> tuple[str, ...]:
        return tuple(self._links)

    def anchors(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._anchors)


class SafeHttpClient:
    def __init__(
        self,
        *,
        dns_resolver: DnsResolver = resolve_host,
        connect_timeout_seconds: float = 5.0,
        total_timeout_seconds: float = 15.0,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
        before_request: RequestStartHook | None = None,
    ) -> None:
        self._dns_resolver = dns_resolver
        self._connect_timeout_seconds = connect_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._network_backend = network_backend
        self._before_request = before_request

    async def get_text(
        self,
        url: str,
        *,
        allowed_hosts: set[str],
        redirect_validator: RedirectValidator | None = None,
    ) -> HttpDocument:
        """Fetch an allowlisted public URL, following only validated redirects."""
        normalized_hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await self._get_text(url, normalized_hosts, redirect_validator)
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

    async def _get_text(
        self,
        url: str,
        allowed_hosts: frozenset[str],
        redirect_validator: RedirectValidator | None,
    ) -> HttpDocument:
        current_url = await self._validate_url(url, allowed_hosts, error_code="unsafe_url")
        timeout = httpx.Timeout(self._connect_timeout_seconds)
        transport = _PinnedAsyncHTTPTransport(self._network_backend)

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=transport, trust_env=False
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                transport.pin(current_url)
                if self._before_request is not None:
                    await self._before_request(str(current_url.url))
                async with client.stream(
                    "GET", current_url.url, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if location is None:
                            raise ProviderError(
                                code="invalid_redirect",
                                retryable=False,
                                detail="redirect response did not include a Location header",
                            )
                        redirected_url = await self._validate_url(
                            str(current_url.url.join(location)),
                            allowed_hosts,
                            error_code="unsafe_redirect",
                        )
                        if redirect_validator is not None and not await redirect_validator(
                            str(redirected_url.url)
                        ):
                            raise ProviderError(
                                code="unsafe_redirect",
                                retryable=False,
                                detail="redirect target rejected by provider policy",
                            )
                        current_url = redirected_url
                        continue

                    if response.is_error:
                        if response.status_code in {401, 403}:
                            error_code = "provider_access_denied"
                        elif response.status_code == 429:
                            error_code = "provider_rate_limited"
                        else:
                            error_code = "http_status"
                        raise ProviderError(
                            code=error_code,
                            retryable=response.status_code == 429 or response.status_code >= 500,
                            detail=f"received HTTP {response.status_code}",
                        )

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in SUPPORTED_CONTENT_TYPES:
                        raise ProviderError(
                            code="unsupported_content_type",
                            retryable=False,
                            detail=f"received {content_type or 'no content type'}",
                        )

                    # httpx decodes content before aiter_bytes(); reject compression first.
                    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if content_encoding not in {"", "identity"}:
                        raise ProviderError(
                            code="unsupported_content_encoding",
                            retryable=False,
                            detail=f"received {content_encoding}",
                        )

                    body = await self._read_limited_body(response)
                    decoded = body.decode(response.encoding or "utf-8", errors="replace")
                    text, title, links, anchors = self._extract_content(decoded, content_type)
                    return HttpDocument(
                        url=str(current_url.url),
                        text=text[:MAX_TEXT_LENGTH],
                        content_type=content_type,
                        title=title,
                        links=links,
                        anchors=anchors,
                    )

        raise ProviderError(
            code="too_many_redirects", retryable=False, detail="redirect limit exceeded"
        )

    async def _validate_url(
        self, url: str, allowed_hosts: frozenset[str], *, error_code: str
    ) -> _ValidatedUrl:
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
            return _ValidatedUrl(url=parsed, address=str(literal_address))

        if normalized_host not in allowed_hosts:
            raise ProviderError(code=error_code, retryable=False, detail="URL host is not allowlisted")

        addresses = await self._dns_resolver(normalized_host)
        if not addresses or any(not is_public_ip(address) for address in addresses):
            raise ProviderError(code=error_code, retryable=False, detail="URL DNS result is not public")
        return _ValidatedUrl(url=parsed, address=addresses[0])

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
    def _extract_content(
        body: str, content_type: str
    ) -> tuple[str, str | None, tuple[str, ...], tuple[tuple[str, str], ...]]:
        if content_type == "text/plain":
            return body, None, (), ()
        parser = _PlainTextExtractor()
        parser.feed(body)
        parser.close()
        return parser.text(), parser.title(), parser.links(), parser.anchors()
