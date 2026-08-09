import asyncio
from unittest.mock import AsyncMock, call

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.ingestion.contracts import ProviderQuery
from app.ingestion.errors import ProviderError
from app.ingestion.providers.company_site import CompanySiteProvider
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy


def company_query(
    website: str = "https://example.com",
    *,
    allowed_hosts: frozenset[str] = frozenset({"example.com"}),
) -> ProviderQuery:
    return ProviderQuery(
        query="Example Company",
        website=website,
        allowed_hosts=allowed_hosts,
    )


def page(
    url: str,
    *,
    text: str = "Company information",
    title: str | None = "Example Company",
    links: tuple[str, ...] = (),
) -> HttpDocument:
    return HttpDocument(
        url=url,
        text=text,
        content_type="text/html",
        title=title,
        links=links,
    )


@pytest.fixture
def safe_client() -> AsyncMock:
    return AsyncMock(spec=SafeHttpClient)


@pytest.fixture
def robots_policy() -> AsyncMock:
    policy = AsyncMock(spec=RobotsPolicy)
    policy.can_fetch.return_value = True
    return policy


@pytest.fixture
def provider(safe_client: AsyncMock, robots_policy: AsyncMock) -> CompanySiteProvider:
    return CompanySiteProvider(
        http_client=safe_client,
        robots_policy=robots_policy,
        approved_hosts=frozenset({"example.com"}),
    )


@pytest.mark.anyio
async def test_does_not_fetch_candidate_outside_operator_approved_hosts(
    safe_client: AsyncMock, robots_policy: AsyncMock
) -> None:
    provider = CompanySiteProvider(
        http_client=safe_client,
        robots_policy=robots_policy,
        approved_hosts=frozenset({"approved.example"}),
    )

    result = await provider.search(company_query())

    assert result.documents == ()
    robots_policy.can_fetch.assert_not_awaited()
    safe_client.get_text.assert_not_awaited()


@pytest.mark.anyio
async def test_does_not_fetch_candidate_missing_from_trusted_query_allowlist(
    provider: CompanySiteProvider, safe_client: AsyncMock, robots_policy: AsyncMock
) -> None:
    result = await provider.search(
        company_query(allowed_hosts=frozenset({"other.example"}))
    )

    assert result.documents == ()
    robots_policy.can_fetch.assert_not_awaited()
    safe_client.get_text.assert_not_awaited()


@pytest.mark.anyio
async def test_does_not_fetch_when_robots_disallows(
    provider: CompanySiteProvider, robots_policy: AsyncMock, safe_client: AsyncMock
) -> None:
    robots_policy.can_fetch.return_value = False

    result = await provider.search(company_query())

    assert result.documents == ()
    assert result.warnings == ("robots_disallowed",)
    safe_client.get_text.assert_not_awaited()


@pytest.mark.anyio
async def test_crawls_only_documented_and_discovered_eligible_same_host_pages(
    provider: CompanySiteProvider, safe_client: AsyncMock
) -> None:
    responses = {
        "https://example.com/about": page(
            "https://example.com/about",
            links=(
                "/about/team#people",
                "https://EXAMPLE.com/jobs/engineering",
                "https://evil.test/careers",
                "/contact",
                "/about#duplicate-seed",
                "javascript:alert(1)",
            ),
        ),
        "https://example.com/jobs": page("https://example.com/jobs", links=("/careers/openings",)),
        "https://example.com/careers": page("https://example.com/careers"),
        "https://example.com/about/team": page(
            "https://example.com/about/team", links=("/about/team/leadership",)
        ),
        "https://example.com/jobs/engineering": page("https://example.com/jobs/engineering"),
        "https://example.com/careers/openings": page("https://example.com/careers/openings"),
    }

    async def fetch(
        url: str, *, allowed_hosts: set[str], redirect_validator: object
    ) -> HttpDocument:
        assert allowed_hosts == {"example.com"}
        assert callable(redirect_validator)
        return responses[url]

    safe_client.get_text.side_effect = fetch

    result = await provider.search(company_query())

    assert [str(document.url) for document in result.documents] == [
        "https://example.com/about",
        "https://example.com/jobs",
        "https://example.com/careers",
        "https://example.com/about/team",
        "https://example.com/jobs/engineering",
        "https://example.com/careers/openings",
    ]
    assert [args.args[0] for args in safe_client.get_text.await_args_list] == [
        "https://example.com/about",
        "https://example.com/jobs",
        "https://example.com/careers",
        "https://example.com/about/team",
        "https://example.com/jobs/engineering",
        "https://example.com/careers/openings",
    ]


@pytest.mark.anyio
async def test_caps_crawl_at_ten_pages(
    provider: CompanySiteProvider, safe_client: AsyncMock
) -> None:
    links = tuple(f"/jobs/{index}" for index in range(20))

    async def fetch(
        url: str, *, allowed_hosts: set[str], redirect_validator: object
    ) -> HttpDocument:
        del allowed_hosts, redirect_validator
        return page(url, links=links if url.endswith("/about") else ())

    safe_client.get_text.side_effect = fetch

    result = await provider.search(company_query())

    assert len(result.documents) == 10
    assert safe_client.get_text.await_count == 10
    assert result.truncated is True


@pytest.mark.anyio
async def test_preserves_redirect_result_and_reports_safe_http_redirect_failure(
    provider: CompanySiteProvider, safe_client: AsyncMock
) -> None:
    safe_client.get_text.side_effect = [
        page("https://example.com/about/company", title="About us"),
        ProviderError(code="unsafe_redirect", retryable=False),
        page("https://example.com/careers"),
    ]

    result = await provider.search(company_query())

    assert [str(document.url) for document in result.documents] == [
        "https://example.com/about/company",
        "https://example.com/careers",
    ]
    assert result.warnings == ("page_failed:unsafe_redirect",)


@pytest.mark.anyio
async def test_access_challenges_are_skipped_without_following_their_links(
    provider: CompanySiteProvider, safe_client: AsyncMock
) -> None:
    safe_client.get_text.side_effect = [
        page(
            "https://example.com/login",
            title="Login required",
            text="Sign in to continue",
            links=("/about/private",),
        ),
        page(
            "https://example.com/jobs",
            title="Verify you are human",
            text="Complete the CAPTCHA",
            links=("/careers/private",),
        ),
        page("https://example.com/careers", text="Open roles"),
    ]

    result = await provider.search(company_query())

    assert [str(document.url) for document in result.documents] == [
        "https://example.com/careers"
    ]
    assert result.warnings == ("login_required", "captcha_required")
    assert safe_client.get_text.await_count == 3


@pytest.mark.anyio
async def test_individual_page_failure_returns_partial_documents_and_stable_warning(
    provider: CompanySiteProvider, safe_client: AsyncMock
) -> None:
    safe_client.get_text.side_effect = [
        page("https://example.com/about"),
        ProviderError(code="total_timeout", retryable=True),
        page("https://example.com/careers"),
    ]

    result = await provider.search(company_query())

    assert len(result.documents) == 2
    assert result.warnings == ("page_failed:total_timeout",)


@pytest.mark.anyio
async def test_missing_website_returns_empty_result_without_fetching(
    provider: CompanySiteProvider, safe_client: AsyncMock, robots_policy: AsyncMock
) -> None:
    result = await provider.search(ProviderQuery(query="Example Company"))

    assert result.documents == ()
    assert result.warnings == ()
    robots_policy.can_fetch.assert_not_awaited()
    safe_client.get_text.assert_not_awaited()


def test_query_and_result_extensions_remain_immutable() -> None:
    query = company_query()

    with pytest.raises(ValidationError, match="frozen"):
        query.website = None


@pytest.mark.anyio
async def test_robots_policy_fetches_once_per_host_and_uses_real_parser() -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)
    safe_client.get_text.return_value = HttpDocument(
        url="https://example.com/robots.txt",
        text="User-agent: company-search\nDisallow: /careers",
        content_type="text/plain",
    )
    policy = RobotsPolicy(http_client=safe_client, user_agent="company-search")

    decisions = (
        await policy.can_fetch("https://example.com/about"),
        await policy.can_fetch("https://example.com/careers/private"),
        await policy.can_fetch("https://example.com/jobs/open"),
    )

    assert decisions == (True, False, True)
    assert safe_client.get_text.await_args_list == [
        call("https://example.com/robots.txt", allowed_hosts={"example.com"})
    ]


@pytest.mark.anyio
async def test_robots_policy_fails_closed_when_robots_cannot_be_fetched() -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)
    safe_client.get_text.side_effect = ProviderError(code="total_timeout", retryable=True)
    policy = RobotsPolicy(http_client=safe_client)

    assert await policy.can_fetch("https://example.com/about") is False
    assert await policy.can_fetch("https://example.com/careers") is False
    assert safe_client.get_text.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("error_code", ["provider_access_denied", "provider_rate_limited"])
async def test_robots_policy_preserves_source_stop_errors(error_code: str) -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)
    safe_client.get_text.side_effect = ProviderError(
        code=error_code,
        retryable=error_code == "provider_rate_limited",
    )
    policy = RobotsPolicy(http_client=safe_client)

    with pytest.raises(ProviderError) as captured:
        await policy.can_fetch("https://example.com/about")

    assert captured.value.code == error_code
    assert safe_client.get_text.await_count == 1


@pytest.mark.anyio
async def test_robots_policy_fetches_once_for_concurrent_same_host_checks() -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def fetch_robots(url: str, *, allowed_hosts: set[str]) -> HttpDocument:
        del url, allowed_hosts
        fetch_started.set()
        await release_fetch.wait()
        return HttpDocument(
            url="https://example.com/robots.txt",
            text="User-agent: *\nAllow: /",
            content_type="text/plain",
        )

    safe_client.get_text.side_effect = fetch_robots
    policy = RobotsPolicy(http_client=safe_client)
    first = asyncio.create_task(policy.can_fetch("https://example.com/about"))
    await fetch_started.wait()
    second = asyncio.create_task(policy.can_fetch("https://example.com/careers"))
    await asyncio.sleep(0)
    release_fetch.set()

    assert await asyncio.gather(first, second) == [True, True]
    assert safe_client.get_text.await_count == 1


@pytest.mark.anyio
async def test_robots_policy_caches_rules_by_normalized_origin() -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)

    async def fetch_robots(url: str, *, allowed_hosts: set[str]) -> HttpDocument:
        assert allowed_hosts == {"example.com"}
        return HttpDocument(
            url=url,
            text="User-agent: *\nAllow: /",
            content_type="text/plain",
        )

    safe_client.get_text.side_effect = fetch_robots
    policy = RobotsPolicy(http_client=safe_client)

    assert await policy.can_fetch("https://example.com/about") is True
    assert await policy.can_fetch("https://example.com.:443/jobs") is True
    assert await policy.can_fetch("https://example.com:8443/careers") is True
    assert await policy.can_fetch("http://example.com/about") is True
    assert [args.args[0] for args in safe_client.get_text.await_args_list] == [
        "https://example.com/robots.txt",
        "https://example.com:8443/robots.txt",
        "http://example.com/robots.txt",
    ]


@pytest.mark.anyio
async def test_robots_policy_keeps_explicit_zero_port_as_distinct_origin() -> None:
    safe_client = AsyncMock(spec=SafeHttpClient)

    async def fetch_robots(url: str, *, allowed_hosts: set[str]) -> HttpDocument:
        assert allowed_hosts == {"example.com"}
        return HttpDocument(
            url=url,
            text="User-agent: *\nAllow: /",
            content_type="text/plain",
        )

    safe_client.get_text.side_effect = fetch_robots
    policy = RobotsPolicy(http_client=safe_client)

    assert await policy.can_fetch("https://example.com/about") is True
    assert await policy.can_fetch("https://example.com:0/jobs") is True
    assert [args.args[0] for args in safe_client.get_text.await_args_list] == [
        "https://example.com/robots.txt",
        "https://example.com:0/robots.txt",
    ]


@pytest.mark.anyio
async def test_canonicalizes_configured_origin_before_seed_deduplication(
    safe_client: AsyncMock, robots_policy: AsyncMock
) -> None:
    provider = CompanySiteProvider(
        http_client=safe_client,
        robots_policy=robots_policy,
        approved_hosts=frozenset({"example.com"}),
    )

    async def fetch(
        url: str, *, allowed_hosts: set[str], redirect_validator: object
    ) -> HttpDocument:
        assert allowed_hosts == {"example.com"}
        assert callable(redirect_validator)
        links = ("https://example.com/about",) if url.endswith("/about") else ()
        return page(url, links=links)

    safe_client.get_text.side_effect = fetch

    result = await provider.search(company_query("https://example.com.:443/company"))

    assert len(result.documents) == 3
    assert [args.args[0] for args in safe_client.get_text.await_args_list] == [
        "https://example.com/about",
        "https://example.com/jobs",
        "https://example.com/careers",
    ]


async def public_dns(_host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.anyio
@respx.mock
async def test_rejects_robots_disallowed_redirect_before_fetching_target() -> None:
    client = SafeHttpClient(dns_resolver=public_dns)
    robots = RobotsPolicy(http_client=client)
    provider = CompanySiteProvider(
        http_client=client,
        robots_policy=robots,
        approved_hosts=frozenset({"example.com"}),
    )
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="User-agent: *\nDisallow: /private",
        )
    )
    respx.get("https://example.com/about").mock(
        return_value=httpx.Response(302, headers={"location": "/private"})
    )
    forbidden = respx.get("https://example.com/private").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="private"
        )
    )
    respx.get("https://example.com/jobs").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="Jobs"
        )
    )
    respx.get("https://example.com/careers").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="Careers"
        )
    )

    result = await provider.search(company_query())

    assert [str(document.url) for document in result.documents] == [
        "https://example.com/jobs",
        "https://example.com/careers",
    ]
    assert result.warnings == ("page_failed:unsafe_redirect",)
    assert forbidden.call_count == 0


@pytest.mark.anyio
@respx.mock
async def test_rejects_ineligible_redirect_before_fetching_target() -> None:
    client = SafeHttpClient(dns_resolver=public_dns)
    robots = RobotsPolicy(http_client=client)
    provider = CompanySiteProvider(
        http_client=client,
        robots_policy=robots,
        approved_hosts=frozenset({"example.com"}),
    )
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="User-agent: *\nAllow: /"
        )
    )
    respx.get("https://example.com/about").mock(
        return_value=httpx.Response(302, headers={"location": "/contact"})
    )
    forbidden = respx.get("https://example.com/contact").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="Contact"
        )
    )
    respx.get("https://example.com/jobs").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="Jobs"
        )
    )
    respx.get("https://example.com/careers").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="Careers"
        )
    )

    result = await provider.search(company_query())

    assert len(result.documents) == 2
    assert result.warnings == ("page_failed:unsafe_redirect",)
    assert forbidden.call_count == 0
