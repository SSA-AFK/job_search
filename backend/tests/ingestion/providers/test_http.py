import asyncio

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
