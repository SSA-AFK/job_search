"""Bounded recruitment-entry discovery from evidenced public sources."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from app.core.normalization import normalize_url
from app.ingestion.contracts import DocumentUrl
from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy
from app.manifest.contracts import (
    AtsClassification,
    DiscoveryStatus,
    EntryDiscoveryResult,
    ManifestCompany,
)

_KNOWN_ATS_HOSTS = {
    "jobs.feishu.cn": "feishu",
    "app.mokahr.com": "moka",
}
_KNOWN_ATS_SUFFIXES = {
    ".beisen.cn": "beisen",
    ".dayee.com": "dayee",
}
_RECRUITMENT_LABELS = (
    "招聘",
    "社会招聘",
    "校园招聘",
    "加入我们",
    "人才招聘",
    "careers",
    "jobs",
)
_RECRUITMENT_PATH_PARTS = ("career", "careers", "job", "jobs", "recruit")
_GENERIC_ATS_PATH_PARTS = frozenset(
    {"career", "careers", "job", "jobs", "recruit", "recruitment", "social-recruitment", "campus-recruitment"}
)
_MAX_CANDIDATE_PAGES = 5
_BLOCKING_ERROR_CODES = frozenset(
    {
        "provider_access_denied",
        "provider_rate_limited",
        "login_required",
        "captcha_required",
        "robots_disallowed",
    }
)
_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(DocumentUrl)


def _document_url(value: str | None) -> HttpUrl | None:
    return None if value is None else _URL_ADAPTER.validate_python(value)


class EntryDiscoverer(Protocol):
    async def discover(self, company: ManifestCompany) -> EntryDiscoveryResult: ...


class DomainStartLimiter:
    """Serialize request starts per host with a minimum one-second interval."""

    def __init__(
        self,
        *,
        interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_start: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = self._monotonic()
            last_start = self._last_start.get(host)
            if last_start is not None:
                delay = self._interval_seconds - (now - last_start)
                if delay > 0:
                    await self._sleep(delay)
                    now = self._monotonic()
            self._last_start[host] = now


def classify_recruitment_url(url: str, official_host: str) -> AtsClassification:
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        return AtsClassification(platform="unknown")
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        return AtsClassification(platform="unknown")

    platform = _KNOWN_ATS_HOSTS.get(host)
    if platform is None:
        platform = next(
            (
                candidate
                for suffix, candidate in _KNOWN_ATS_SUFFIXES.items()
                if host.endswith(suffix)
            ),
            None,
        )
    if platform is not None:
        return AtsClassification(platform=platform, requires_rendering=True)

    normalized_official = official_host.lower().rstrip(".")
    if normalized_official and (
        host == normalized_official or host.endswith(f".{normalized_official}")
    ):
        return AtsClassification(platform="self_hosted")
    return AtsClassification(platform="unknown")


class OfficialEntryDiscoverer:
    def __init__(
        self, *, http_client: SafeHttpClient, robots_policy: RobotsPolicy
    ) -> None:
        self._http_client = http_client
        self._robots_policy = robots_policy

    async def discover(self, company: ManifestCompany) -> EntryDiscoveryResult:
        official = self._official_origin(company)
        official_host = official[1] if official is not None else ""

        if company.recruitment_url is not None:
            return self._evidenced_result(str(company.recruitment_url), official_host)
        if official is None:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.NOT_FOUND,
                method="official_navigation",
                error_code="official_website_missing",
            )

        root_url, official_host = official
        if not await self._robots_policy.can_fetch(root_url):
            return self._failure(
                method="official_navigation", code="robots_disallowed"
            )

        try:
            root = await self._http_client.get_text(
                root_url,
                allowed_hosts={official_host},
                redirect_validator=self._redirect_validator(official_host),
            )
        except ProviderError as error:
            return self._failure(method="official_navigation", code=error.code)

        challenge = self._access_challenge(root)
        if challenge is not None:
            return self._failure(method="official_navigation", code=challenge)

        candidates = self._candidate_links(root, official_host)
        external = [
            candidate
            for candidate in candidates
            if urlsplit(candidate[0]).hostname not in {official_host, f"{official_host}."}
            and candidate[2].platform != "unknown"
        ]
        if len(external) > 1:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.REVIEW_REQUIRED,
                method="official_navigation",
                error_code="ambiguous_recruitment_entries",
            )
        if external:
            url, label, classification = external[0]
            if not self._has_tenant_identity(url, classification):
                return EntryDiscoveryResult(
                    status=DiscoveryStatus.REVIEW_REQUIRED,
                    method="official_navigation",
                    candidate_url=_document_url(url),
                    normalized_url=_document_url(url),
                    ownership_evidence=f"official_navigation_anchor:{label}",
                    classification=classification,
                    error_code="ownership_unverified",
                )
            return self._accepted_navigation(url, label, classification)

        last_error: str | None = None
        fetched = 0
        for url, label, classification in candidates:
            if classification.platform != "self_hosted":
                continue
            if fetched >= _MAX_CANDIDATE_PAGES:
                break
            fetched += 1
            if not await self._robots_policy.can_fetch(url):
                last_error = "robots_disallowed"
                continue
            try:
                page = await self._http_client.get_text(
                    url,
                    allowed_hosts={official_host},
                    redirect_validator=self._redirect_validator(official_host),
                )
            except ProviderError as error:
                last_error = error.code
                if error.code in {"provider_access_denied", "provider_rate_limited"}:
                    break
                continue

            challenge = self._access_challenge(page)
            if challenge is not None:
                return self._failure(
                    method="official_navigation", code=challenge, candidate_url=url
                )
            normalized_page = normalize_url(page.url)
            return self._accepted_navigation(normalized_page, label, classification)

        if last_error is not None:
            return self._failure(method="official_navigation", code=last_error)
        return EntryDiscoveryResult(
            status=DiscoveryStatus.NOT_FOUND,
            method="official_navigation",
            error_code="recruitment_entry_not_found",
        )

    @staticmethod
    def _official_origin(company: ManifestCompany) -> tuple[str, str] | None:
        if company.official_website is None:
            return None
        parsed = urlsplit(str(company.official_website))
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or parsed.username is not None:
            return None
        display_host = f"[{host}]" if ":" in host else host
        default_port = (parsed.scheme == "http" and parsed.port == 80) or (
            parsed.scheme == "https" and parsed.port == 443
        )
        netloc = display_host if parsed.port is None or default_port else f"{display_host}:{parsed.port}"
        return urlunsplit((parsed.scheme.lower(), netloc, "/", "", "")), host

    @staticmethod
    def _evidenced_result(url: str, official_host: str) -> EntryDiscoveryResult:
        try:
            normalized = normalize_url(url)
        except ValueError:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.FAILED,
                method="evidenced_recruitment_url",
                error_code="unsafe_url",
            )
        classification = classify_recruitment_url(normalized, official_host)
        if classification.platform == "unknown" or not OfficialEntryDiscoverer._has_tenant_identity(
            normalized, classification
        ):
            return EntryDiscoveryResult(
                status=DiscoveryStatus.REVIEW_REQUIRED,
                method="evidenced_recruitment_url",
                candidate_url=_document_url(normalized),
                normalized_url=_document_url(normalized),
                classification=classification,
                error_code="ownership_unverified",
            )
        return EntryDiscoveryResult(
            status=DiscoveryStatus.ACCEPTED,
            method="evidenced_recruitment_url",
            candidate_url=_document_url(normalized),
            normalized_url=_document_url(normalized),
            ownership_evidence="evidenced_recruitment_url",
            classification=classification,
        )

    @staticmethod
    def _has_tenant_identity(url: str, classification: AtsClassification) -> bool:
        if classification.platform == "self_hosted":
            return True
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if classification.platform in {"beisen", "dayee"}:
            return host.count(".") >= 2
        path_parts = tuple(part.casefold() for part in parsed.path.split("/") if part)
        return any(part not in _GENERIC_ATS_PATH_PARTS for part in path_parts)

    def _candidate_links(
        self, document: HttpDocument, official_host: str
    ) -> list[tuple[str, str, AtsClassification]]:
        candidates: list[tuple[str, str, AtsClassification]] = []
        seen: set[str] = set()
        for href, label in document.anchors:
            try:
                joined = urljoin(document.url, href)
                normalized = normalize_url(joined)
            except ValueError:
                continue
            if not self._is_recruitment_link(label, normalized):
                continue
            classification = classify_recruitment_url(normalized, official_host)
            if classification.platform == "unknown" or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((normalized, " ".join(label.split()), classification))
        return candidates

    @staticmethod
    def _is_recruitment_link(label: str, url: str) -> bool:
        normalized_label = " ".join(label.split()).casefold()
        path_parts = tuple(
            part.casefold() for part in urlsplit(url).path.split("/") if part
        )
        return any(term in normalized_label for term in _RECRUITMENT_LABELS) or any(
            any(marker in part for marker in _RECRUITMENT_PATH_PARTS)
            for part in path_parts
        )

    def _redirect_validator(self, official_host: str) -> Callable[[str], Awaitable[bool]]:
        async def validate(url: str) -> bool:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            return (
                parsed.username is None
                and host == official_host
                and await self._robots_policy.can_fetch(url)
            )

        return validate

    @staticmethod
    def _access_challenge(document: HttpDocument) -> str | None:
        path = urlsplit(document.url).path.lower().rstrip("/")
        content = f"{document.title or ''}\n{document.text}".lower()
        if path.endswith(("/login", "/signin", "/sign-in")) or any(
            marker in content for marker in ("login required", "sign in to continue")
        ):
            return "login_required"
        if any(
            marker in content
            for marker in ("captcha", "verify you are human", "human verification")
        ):
            return "captcha_required"
        return None

    @staticmethod
    def _accepted_navigation(
        url: str, label: str, classification: AtsClassification
    ) -> EntryDiscoveryResult:
        evidence = f"official_navigation_anchor:{label}" if label else "official_navigation_path"
        return EntryDiscoveryResult(
            status=DiscoveryStatus.ACCEPTED,
            method="official_navigation",
            candidate_url=_document_url(url),
            normalized_url=_document_url(url),
            ownership_evidence=evidence,
            classification=classification,
        )

    @staticmethod
    def _failure(
        *, method: str, code: str, candidate_url: str | None = None
    ) -> EntryDiscoveryResult:
        status = (
            DiscoveryStatus.BLOCKED
            if code in _BLOCKING_ERROR_CODES
            else DiscoveryStatus.FAILED
        )
        return EntryDiscoveryResult(
            status=status,
            method=method,
            candidate_url=_document_url(candidate_url),
            normalized_url=_document_url(candidate_url),
            error_code=code,
        )


class EntryDiscoveryCoordinator:
    def __init__(
        self,
        *,
        official_discoverer: EntryDiscoverer,
        fallback_discoverer: EntryDiscoverer,
    ) -> None:
        self._official_discoverer = official_discoverer
        self._fallback_discoverer = fallback_discoverer

    async def discover(self, company: ManifestCompany) -> EntryDiscoveryResult:
        official = await self._official_discoverer.discover(company)
        if official.status is not DiscoveryStatus.NOT_FOUND:
            return official

        fallback = await self._fallback_discoverer.discover(company)
        if fallback.candidate_url is None:
            return official
        return EntryDiscoveryResult(
            status=DiscoveryStatus.REVIEW_REQUIRED,
            method=fallback.method,
            candidate_url=fallback.candidate_url,
            normalized_url=fallback.normalized_url,
            source_id=fallback.source_id,
            ownership_evidence=fallback.ownership_evidence,
            classification=fallback.classification,
            error_code="ownership_unverified",
        )
