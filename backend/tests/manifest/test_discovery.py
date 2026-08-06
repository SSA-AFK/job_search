from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
import respx

from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy
from app.manifest.contracts import (
    AiCategory,
    AtsClassification,
    DiscoveryStatus,
    EntryDiscoveryResult,
    ManifestCompany,
)
from app.manifest.discovery import (
    DomainStartLimiter,
    EntryDiscoveryCoordinator,
    OfficialEntryDiscoverer,
    classify_recruitment_url,
)

FIXTURES = Path(__file__).with_name("fixtures")
COMPANY_ID = UUID("00000000-0000-0000-0000-000000000007")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def public_dns(_host: str) -> list[str]:
    return ["93.184.216.34"]


def company(**overrides: object) -> ManifestCompany:
    values: dict[str, object] = {
        "company_id": COMPANY_ID,
        "canonical_name": "Acme AI",
        "primary_category": AiCategory.FOUNDATION_MODELS,
        "official_website": "https://acme.cn/company",
    }
    values.update(overrides)
    return ManifestCompany(**values)


def discoverer(*, before_request: Callable[[str], Awaitable[None]] | None = None) -> OfficialEntryDiscoverer:
    client = SafeHttpClient(dns_resolver=public_dns, before_request=before_request)
    return OfficialEntryDiscoverer(
        http_client=client,
        robots_policy=RobotsPolicy(http_client=client),
    )


def route_robots(text: str = "User-agent: *\nAllow: /") -> None:
    respx.get("https://acme.cn/robots.txt").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text=text
        )
    )


@pytest.mark.parametrize(
    ("url", "platform", "requires_rendering"),
    [
        ("https://jobs.feishu.cn/acme", "feishu", True),
        ("https://app.mokahr.com/social-recruitment/acme", "moka", True),
        ("https://tenant.beisen.cn/recruit", "beisen", True),
        ("https://tenant.dayee.com/jobs", "dayee", True),
        ("https://jobs.acme.cn/careers", "self_hosted", False),
        ("https://www.zhipin.com/gongsi/acme.html", "unknown", False),
        ("https://jobs.feishu.cn.evil.test/acme", "unknown", False),
        ("https://user@jobs.feishu.cn/acme", "unknown", False),
    ],
)
def test_classifier_uses_hostname_and_excludes_unowned_job_boards(
    url: str, platform: str, requires_rendering: bool
) -> None:
    classification = classify_recruitment_url(url, "acme.cn")

    assert classification == AtsClassification(
        platform=platform, requires_rendering=requires_rendering
    )


@pytest.mark.anyio
async def test_evidenced_recruitment_url_takes_priority_without_fetching() -> None:
    http_client = AsyncMock(spec=SafeHttpClient)
    robots = AsyncMock(spec=RobotsPolicy)
    subject = OfficialEntryDiscoverer(http_client=http_client, robots_policy=robots)

    result = await subject.discover(
        company(recruitment_url="https://jobs.feishu.cn/acme")
    )

    assert result.status is DiscoveryStatus.ACCEPTED
    assert str(result.normalized_url) == "https://jobs.feishu.cn/acme"
    assert result.method == "evidenced_recruitment_url"
    assert result.classification == AtsClassification(
        platform="feishu", requires_rendering=True
    )
    http_client.get_text.assert_not_awaited()
    robots.can_fetch.assert_not_awaited()


@pytest.mark.anyio
@respx.mock
async def test_fixture_navigation_labels_and_duplicate_urls_find_one_same_host_entry() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(FIXTURES / "official_careers.html").read_text(encoding="utf-8"),
        )
    )
    careers = respx.get("https://acme.cn/careers").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>Acme Careers</title><p>Open roles</p></html>",
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.ACCEPTED
    assert result.method == "official_navigation"
    assert str(result.normalized_url) == "https://acme.cn/careers"
    assert result.classification == AtsClassification(platform="self_hosted")
    assert careers.call_count == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "label",
    ["招聘", "社会招聘", "校园招聘", "加入我们", "人才招聘", "careers", "jobs"],
)
async def test_each_approved_navigation_label_finds_an_opaque_same_host_path(
    label: str,
) -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f'<nav><a href="/openings">{label}</a></nav>',
        )
    )
    respx.get("https://acme.cn/openings").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>Open roles</p>"
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.ACCEPTED
    assert str(result.normalized_url) == "https://acme.cn/openings"


@pytest.mark.anyio
@respx.mock
async def test_cross_host_ats_anchor_is_accepted_as_official_ownership_evidence() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<nav><a href="https://jobs.feishu.cn/acme">社会招聘</a></nav>',
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.ACCEPTED
    assert result.method == "official_navigation"
    assert str(result.normalized_url) == "https://jobs.feishu.cn/acme"
    assert result.ownership_evidence == "official_navigation_anchor:社会招聘"
    assert result.classification == AtsClassification(
        platform="feishu", requires_rendering=True
    )


@pytest.mark.anyio
@respx.mock
async def test_distinct_official_ats_candidates_remain_ambiguous_for_review() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<a href="https://jobs.feishu.cn/acme">招聘</a>'
                '<a href="https://app.mokahr.com/social-recruitment/acme">社会招聘</a>'
            ),
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.REVIEW_REQUIRED
    assert result.error_code == "ambiguous_recruitment_entries"


@pytest.mark.anyio
@respx.mock
async def test_tenantless_ats_anchor_remains_review_required() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<nav><a href="https://jobs.feishu.cn/">招聘</a></nav>',
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.REVIEW_REQUIRED
    assert str(result.normalized_url) == "https://jobs.feishu.cn/"
    assert result.error_code == "ownership_unverified"


@pytest.mark.anyio
async def test_tenantless_evidenced_ats_url_remains_review_required() -> None:
    result = await discoverer().discover(
        company(recruitment_url="https://jobs.feishu.cn/")
    )

    assert result.status is DiscoveryStatus.REVIEW_REQUIRED
    assert result.error_code == "ownership_unverified"


@pytest.mark.anyio
@respx.mock
async def test_general_job_board_link_without_tenant_evidence_is_not_accepted() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(FIXTURES / "no_careers.html").read_text(encoding="utf-8"),
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.NOT_FOUND
    assert result.error_code == "recruitment_entry_not_found"


@pytest.mark.anyio
@respx.mock
async def test_unsafe_redirect_returns_stable_failure_without_fetching_target() -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://evil.test/careers"}
        )
    )
    forbidden = respx.get("https://evil.test/careers").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="no"
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.FAILED
    assert result.error_code == "unsafe_redirect"
    assert forbidden.call_count == 0


@pytest.mark.anyio
@respx.mock
async def test_robots_denial_returns_blocked_without_fetching_page() -> None:
    route_robots("User-agent: *\nDisallow: /")
    root = respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="should not fetch"
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.BLOCKED
    assert result.error_code == "robots_disallowed"
    assert root.call_count == 0


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (401, "provider_access_denied"),
        (403, "provider_access_denied"),
        (429, "provider_rate_limited"),
    ],
)
async def test_access_and_rate_limit_responses_stop_discovery(
    status_code: int, error_code: str
) -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(return_value=httpx.Response(status_code))

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.BLOCKED
    assert result.error_code == error_code


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("page", "error_code"),
    [
        ("<title>Login required</title><p>Sign in to continue</p>", "login_required"),
        ("<title>Verify you are human</title><p>CAPTCHA</p>", "captcha_required"),
    ],
)
async def test_access_challenge_on_candidate_is_classified_as_blocked(
    page: str, error_code: str
) -> None:
    route_robots()
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/careers">招聘</a>',
        )
    )
    respx.get("https://acme.cn/careers").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=page
        )
    )

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.BLOCKED
    assert result.error_code == error_code


@pytest.mark.anyio
@respx.mock
async def test_candidate_page_count_is_bounded() -> None:
    route_robots()
    links = "".join(f'<a href="/careers/{index}">招聘</a>' for index in range(10))
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=links
        )
    )
    candidates = [
        respx.get(f"https://acme.cn/careers/{index}").mock(
            return_value=httpx.Response(500)
        )
        for index in range(10)
    ]

    result = await discoverer().discover(company())

    assert result.status is DiscoveryStatus.FAILED
    assert result.error_code == "http_status"
    assert [route.call_count for route in candidates] == [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.anyio
@respx.mock
async def test_one_second_start_spacing_is_shared_by_robots_page_and_redirect() -> None:
    clock = _FakeClock()
    limiter = DomainStartLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    starts: list[tuple[str, float]] = []

    async def before_request(url: str) -> None:
        await limiter.wait(url)
        starts.append((url, clock.now))

    client = SafeHttpClient(dns_resolver=public_dns, before_request=before_request)
    robots = RobotsPolicy(http_client=client)
    respx.get("https://acme.cn/robots.txt").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="User-agent: *\nAllow: /"
        )
    )
    respx.get("https://acme.cn/").mock(
        return_value=httpx.Response(302, headers={"location": "/home"})
    )
    respx.get("https://acme.cn/home").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>Home</p>"
        )
    )

    assert await robots.can_fetch("https://acme.cn/") is True
    await client.get_text(
        "https://acme.cn/",
        allowed_hosts={"acme.cn"},
        redirect_validator=robots.can_fetch,
    )

    assert starts == [
        ("https://acme.cn/robots.txt", 0.0),
        ("https://acme.cn/", 1.0),
        ("https://acme.cn/home", 2.0),
    ]
    assert clock.sleeps == [1.0, 1.0]


class _StaticDiscoverer:
    def __init__(self, result: EntryDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def discover(self, _company: ManifestCompany) -> EntryDiscoveryResult:
        self.calls += 1
        return self.result


@pytest.mark.anyio
async def test_zhihu_fallback_runs_last_and_remains_review_required() -> None:
    official = _StaticDiscoverer(
        EntryDiscoveryResult(
            status=DiscoveryStatus.NOT_FOUND,
            method="official_navigation",
            error_code="recruitment_entry_not_found",
        )
    )
    zhihu = _StaticDiscoverer(
        EntryDiscoveryResult(
            status=DiscoveryStatus.ACCEPTED,
            method="zhihu_global_search",
            candidate_url="https://jobs.feishu.cn/acme",
            normalized_url="https://jobs.feishu.cn/acme",
            source_id="zhihu_global_search",
            classification=AtsClassification(platform="feishu", requires_rendering=True),
        )
    )
    coordinator = EntryDiscoveryCoordinator(
        official_discoverer=official, fallback_discoverer=zhihu
    )

    result = await coordinator.discover(company())

    assert official.calls == 1
    assert zhihu.calls == 1
    assert result.status is DiscoveryStatus.REVIEW_REQUIRED
    assert result.method == "zhihu_global_search"
    assert result.error_code == "ownership_unverified"


@pytest.mark.anyio
async def test_coordinator_does_not_invoke_fallback_after_official_acceptance() -> None:
    accepted = EntryDiscoveryResult(
        status=DiscoveryStatus.ACCEPTED,
        method="evidenced_recruitment_url",
        candidate_url="https://jobs.feishu.cn/acme",
        normalized_url="https://jobs.feishu.cn/acme",
        classification=AtsClassification(platform="feishu", requires_rendering=True),
    )
    official = _StaticDiscoverer(accepted)
    zhihu = _StaticDiscoverer(
        EntryDiscoveryResult(
            status=DiscoveryStatus.NOT_FOUND,
            method="zhihu_global_search",
            error_code="recruitment_entry_not_found",
        )
    )

    result = await EntryDiscoveryCoordinator(
        official_discoverer=official, fallback_discoverer=zhihu
    ).discover(company(recruitment_url="https://jobs.feishu.cn/acme"))

    assert result == accepted
    assert zhihu.calls == 0
