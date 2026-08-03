from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.security import is_public_ip


async def public_dns(_host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def safe_client() -> SafeHttpClient:
    return SafeHttpClient(dns_resolver=public_dns)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://127.0.0.1/a", "http://169.254.169.254/latest"]
)
async def test_rejects_unsafe_urls(url: str, safe_client: SafeHttpClient) -> None:
    with pytest.raises(ProviderError, match="unsafe_url"):
        await safe_client.get_text(url, allowed_hosts={"example.com"})


@pytest.mark.anyio
async def test_rejects_hosts_outside_allowlist(safe_client: SafeHttpClient) -> None:
    with pytest.raises(ProviderError, match="unsafe_url"):
        await safe_client.get_text("https://other.example/a", allowed_hosts={"example.com"})


@pytest.mark.anyio
async def test_rejects_dns_results_in_private_ranges() -> None:
    async def private_dns(_host: str) -> list[str]:
        return ["10.0.0.8"]

    client = SafeHttpClient(dns_resolver=private_dns)

    with pytest.raises(ProviderError, match="unsafe_url"):
        await client.get_text("https://example.com/a", allowed_hosts={"example.com"})


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("93.184.216.34", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("100.64.0.1", False),
        ("169.254.169.254", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fc00::1", False),
        ("fe80::1", False),
        ("ff00::1", False),
        ("::", False),
    ],
)
def test_only_public_ip_addresses_are_allowed(address: str, expected: bool) -> None:
    assert is_public_ip(address) is expected


def test_provider_error_exposes_stable_metadata() -> None:
    error = ProviderError(code="connect_timeout", retryable=True, detail="timed out")

    assert str(error) == "connect_timeout"
    assert error.code == "connect_timeout"
    assert error.retryable is True
    assert error.detail == "timed out"


def test_contracts_are_immutable_and_bound_raw_text() -> None:
    query = ProviderQuery(
        query="Example Technologies",
        allowed_hosts=frozenset({"example.com"}),
        max_results=5,
    )
    document = RawDocument(
        provider="example",
        external_id="external-1",
        url="https://example.com/article",
        title="Example",
        text="A public document",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        authority_level=2,
    )
    result = ProviderResult(documents=(document,), truncated=True)

    with pytest.raises(ValidationError):
        document.text = "changed"

    assert query.allowed_hosts == frozenset({"example.com"})
    assert query.max_results == 5
    assert result.documents == (document,)
    assert result.truncated is True
