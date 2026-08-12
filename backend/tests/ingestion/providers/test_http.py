import asyncio
import gzip
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest
import respx

from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import MAX_RESPONSE_BYTES, SafeHttpClient


async def public_dns(_host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def safe_client() -> SafeHttpClient:
    return SafeHttpClient(dns_resolver=public_dns)


class RecordingBackend(httpcore.AsyncMockBackend):
    def __init__(self, response: bytes) -> None:
        super().__init__([response])
        self.connected_hosts: list[str] = []

    async def connect_tcp(self, host: str, *args: object, **kwargs: object) -> httpcore.AsyncNetworkStream:
        self.connected_hosts.append(host)
        return await super().connect_tcp(host, *args, **kwargs)


@pytest.mark.anyio
async def test_pins_connection_to_validated_address_despite_second_dns_answer() -> None:
    dns_answers = iter((["93.184.216.34"], ["127.0.0.1"]))
    dns_calls: list[str] = []

    async def changing_dns(host: str) -> list[str]:
        dns_calls.append(host)
        return next(dns_answers)

    backend = RecordingBackend(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 6\r\nConnection: close\r\n\r\npublic"
    )
    client = SafeHttpClient(dns_resolver=changing_dns, network_backend=backend)

    document = await client.get_text("http://example.com/a", allowed_hosts={"example.com"})

    assert document.text == "public"
    assert dns_calls == ["example.com"]
    assert backend.connected_hosts == ["93.184.216.34"]


@pytest.mark.anyio
@respx.mock
async def test_rejects_redirect_outside_allowlist(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.test/a"})
    )

    with pytest.raises(ProviderError, match="unsafe_redirect"):
        await safe_client.get_text("https://example.com/a", allowed_hosts={"example.com"})


@pytest.mark.anyio
@respx.mock
async def test_rejects_redirect_before_request_when_provider_policy_disallows(
    safe_client: SafeHttpClient,
) -> None:
    respx.get("https://example.com/about").mock(
        return_value=httpx.Response(302, headers={"location": "/private"})
    )
    forbidden = respx.get("https://example.com/private").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="private"
        )
    )
    redirect_validator = AsyncMock(return_value=False)

    with pytest.raises(ProviderError, match="unsafe_redirect"):
        await safe_client.get_text(
            "https://example.com/about",
            allowed_hosts={"example.com"},
            redirect_validator=redirect_validator,
        )

    redirect_validator.assert_awaited_once_with("https://example.com/private")
    assert forbidden.call_count == 0


@pytest.mark.anyio
@respx.mock
async def test_converts_html_to_plain_text_without_scripts_or_styles(
    safe_client: SafeHttpClient,
) -> None:
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><style>.hidden { display: none; }</style><body><script>steal()</script><h1>Title</h1><p>Visible text</p></body></html>",
        )
    )

    document = await safe_client.get_text("https://example.com/a", allowed_hosts={"example.com"})

    assert document.url == "https://example.com/a"
    assert document.text == "Title Visible text"
    assert "steal" not in document.text
    assert "hidden" not in document.text


@pytest.mark.anyio
@respx.mock
async def test_extracts_immutable_title_and_links_from_bounded_html(
    safe_client: SafeHttpClient,
) -> None:
    respx.get("https://example.com/about").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title> Example Company </title></head><body>"
                '<a href="/careers">Careers</a><a href="team">Team</a>'
                '<script><a href="https://evil.test/hidden">hidden</a></script>'
                "</body></html>"
            ),
        )
    )

    document = await safe_client.get_text(
        "https://example.com/about", allowed_hosts={"example.com"}
    )

    assert document.title == "Example Company"
    assert document.links == ("/careers", "team")
    assert document.anchors == (("/careers", "Careers"), ("team", "Team"))
    with pytest.raises(FrozenInstanceError):
        document.title = "Changed"


@pytest.mark.anyio
@respx.mock
async def test_plain_text_response_has_no_html_metadata(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="User-agent: *\nDisallow: /private",
        )
    )

    document = await safe_client.get_text(
        "https://example.com/robots.txt", allowed_hosts={"example.com"}
    )

    assert document.title is None
    assert document.links == ()


@pytest.mark.anyio
@respx.mock
async def test_accepts_content_type_with_whitespace_before_parameters(
    safe_client: SafeHttpClient,
) -> None:
    respx.get("https://example.com/spaced").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html ; charset=utf-8"},
            text="<p>Visible text</p>",
        )
    )

    document = await safe_client.get_text(
        "https://example.com/spaced", allowed_hosts={"example.com"}
    )

    assert document.text == "Visible text"


@pytest.mark.anyio
@respx.mock
async def test_rejects_response_larger_than_two_mebibytes(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/large").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * (MAX_RESPONSE_BYTES + 1),
        )
    )

    with pytest.raises(ProviderError, match="body_too_large"):
        await safe_client.get_text("https://example.com/large", allowed_hosts={"example.com"})


@pytest.mark.anyio
@respx.mock
async def test_rejects_compressed_content_before_decoding(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/compressed").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-encoding": "gzip"},
            content=gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1)),
        )
    )

    with pytest.raises(ProviderError, match="unsupported_content_encoding"):
        await safe_client.get_text("https://example.com/compressed", allowed_hosts={"example.com"})


@pytest.mark.anyio
@respx.mock
async def test_rejects_unsupported_content_type(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/image").mock(
        return_value=httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
    )

    with pytest.raises(ProviderError, match="unsupported_content_type"):
        await safe_client.get_text("https://example.com/image", allowed_hosts={"example.com"})


@pytest.mark.anyio
@respx.mock
async def test_reports_connect_timeout_as_retryable(safe_client: SafeHttpClient) -> None:
    respx.get("https://example.com/slow").mock(side_effect=httpx.ConnectTimeout("connect timed out"))

    with pytest.raises(ProviderError, match="connect_timeout") as caught:
        await safe_client.get_text("https://example.com/slow", allowed_hosts={"example.com"})

    assert caught.value.retryable is True


@pytest.mark.anyio
@respx.mock
async def test_enforces_total_timeout() -> None:
    async def delayed_response(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="too late")

    respx.get("https://example.com/slow").mock(side_effect=delayed_response)
    client = SafeHttpClient(dns_resolver=public_dns, total_timeout_seconds=0.01)

    with pytest.raises(ProviderError, match="total_timeout") as caught:
        await client.get_text("https://example.com/slow", allowed_hosts={"example.com"})

    assert caught.value.retryable is True


@pytest.mark.anyio
@respx.mock
async def test_request_start_hook_runs_for_initial_and_redirect_requests() -> None:
    starts: list[str] = []

    async def record_start(url: str) -> None:
        starts.append(url)

    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(302, headers={"location": "/final"})
    )
    respx.get("https://example.com/final").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="done"
        )
    )
    client = SafeHttpClient(dns_resolver=public_dns, before_request=record_start)

    document = await client.get_text(
        "https://example.com/start", allowed_hosts={"example.com"}
    )

    assert document.text == "done"
    assert starts == ["https://example.com/start", "https://example.com/final"]


@pytest.mark.anyio
@respx.mock
async def test_per_call_request_hook_counts_redirects() -> None:
    starts: list[str] = []

    async def record_start(url: str) -> None:
        starts.append(url)

    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(302, headers={"location": "/final"})
    )
    respx.get("https://example.com/final").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text="done")
    )
    client = SafeHttpClient(dns_resolver=public_dns)

    await client.get_text(
        "https://example.com/start",
        allowed_hosts={"example.com"},
        request_started=record_start,
    )

    assert starts == ["https://example.com/start", "https://example.com/final"]


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("status_code", "error_code", "retryable"),
    [
        (401, "provider_access_denied", False),
        (403, "provider_access_denied", False),
        (404, "http_not_found", False),
        (429, "provider_rate_limited", True),
        (500, "http_status", True),
    ],
)
async def test_maps_stop_statuses_to_stable_provider_errors(
    status_code: int, error_code: str, retryable: bool
) -> None:
    respx.get("https://example.com/status").mock(
        return_value=httpx.Response(status_code, text="diagnostic body")
    )
    client = SafeHttpClient(dns_resolver=public_dns)

    with pytest.raises(ProviderError) as caught:
        await client.get_text(
            "https://example.com/status", allowed_hosts={"example.com"}
        )

    assert caught.value.code == error_code
    assert caught.value.retryable is retryable
