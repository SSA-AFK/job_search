from unittest.mock import AsyncMock

import pytest

from app.ingestion.entry_discovery.contracts import CompanyNamePool
from app.ingestion.entry_verification.contracts import EntryVerificationStatus
from app.ingestion.entry_verification.validator import EntryUrlValidator
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy


def _company() -> CompanyNamePool:
    return CompanyNamePool(canonical_name="Acme", domains=("acme.example",))


@pytest.mark.anyio
async def test_verifies_company_career_page() -> None:
    http = AsyncMock(spec=SafeHttpClient)
    async def fetch_page(url: str, **kwargs: object) -> HttpDocument:
        callback = kwargs["request_started"]
        await callback(url)  # type: ignore[operator]
        return HttpDocument(
            url="https://acme.example/careers",
            text="Acme careers open positions 招聘职位",
            content_type="text/html",
            title="Acme Careers",
            links=(),
            anchors=(),
        )

    http.get_text.side_effect = fetch_page
    robots = AsyncMock(spec=RobotsPolicy)
    async def allow_robots(url: str, **kwargs: object) -> bool:
        callback = kwargs["request_started"]
        await callback(f"{url.rstrip('/')}/robots.txt")  # type: ignore[operator]
        return True

    robots.can_fetch.side_effect = allow_robots

    result = await EntryUrlValidator(http_client=http, robots_policy=robots).verify(
        "https://acme.example/careers", company=_company()
    )

    assert result.status is EntryVerificationStatus.VERIFIED
    assert result.ownership_evidence == "company_domain"
    assert result.http_requests == 2


@pytest.mark.anyio
async def test_accessible_page_without_company_evidence_is_unverified() -> None:
    http = AsyncMock(spec=SafeHttpClient)
    http.get_text.return_value = HttpDocument(
        url="https://jobs.example/careers",
        text="Careers open jobs positions",
        content_type="text/html",
        title="Careers",
        links=(),
        anchors=(),
    )
    robots = AsyncMock(spec=RobotsPolicy)
    robots.can_fetch.return_value = True

    result = await EntryUrlValidator(http_client=http, robots_policy=robots).verify(
        "https://jobs.example/careers", company=_company()
    )

    assert result.status is EntryVerificationStatus.UNVERIFIED
    assert result.reason_code == "company_ownership_unverified"


@pytest.mark.anyio
async def test_login_page_is_unavailable() -> None:
    http = AsyncMock(spec=SafeHttpClient)
    http.get_text.return_value = HttpDocument(
        url="https://acme.example/login",
        text="请先登录后继续",
        content_type="text/html",
        title="Login",
        links=(),
        anchors=(),
    )
    robots = AsyncMock(spec=RobotsPolicy)
    robots.can_fetch.return_value = True

    result = await EntryUrlValidator(http_client=http, robots_policy=robots).verify(
        "https://acme.example/careers", company=_company()
    )

    assert result.status is EntryVerificationStatus.UNAVAILABLE
    assert result.reason_code == "login_required"
