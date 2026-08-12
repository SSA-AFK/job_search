import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from pydantic import HttpUrl

from app.ingestion.entry_discovery.contracts import CompanyNamePool, normalize_for_compare
from app.ingestion.entry_verification.contracts import (
    EntryVerificationResult,
    EntryVerificationStatus,
)
from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_RECRUITING_TERMS = (
    "career",
    "careers",
    "job",
    "jobs",
    "position",
    "recruit",
    "招聘",
    "职位",
    "加入我们",
    "人才招聘",
)
_LOGIN_TERMS = ("login", "sign in", "登录后继续", "请先登录", "登录查看")
_BLOCK_TERMS = ("captcha", "verify you are human", "验证码", "安全验证", "访问频繁")


class EntryUrlValidator:
    def __init__(self, *, http_client: SafeHttpClient, robots_policy: RobotsPolicy) -> None:
        self._http = http_client
        self._robots = robots_policy

    async def verify(
        self,
        url: str,
        *,
        company: CompanyNamePool,
        trusted_existing_binding: bool = False,
        linked_from_verified_website: bool = False,
        request_started: Callable[[], Awaitable[None]] | None = None,
    ) -> EntryVerificationResult:
        requests = 0

        async def count_request(_url: str) -> None:
            nonlocal requests
            requests += 1
            if request_started is not None:
                await request_started()

        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if not host:
            return self._failed(url, "unsafe_url", requests)
        try:
            if not await self._robots.can_fetch(url, request_started=count_request):
                return self._failed(url, "robots_disallowed", requests)
            document = await self._http.get_text(
                url,
                allowed_hosts={host},
                redirect_validator=self._same_company_redirect(host),
                request_started=count_request,
            )
        except ProviderError as error:
            return self._failed(url, _public_reason(error.code), requests)

        classification = _classify_document(document)
        if classification is not None:
            return self._failed(url, classification, requests, final_url=document.url)
        if not _has_recruiting_semantics(document):
            return self._unverified(url, document.url, "not_recruiting_page", requests)

        ownership = _ownership_evidence(
            document,
            company,
            trusted_existing_binding=trusted_existing_binding,
            linked_from_verified_website=linked_from_verified_website,
        )
        if ownership is None:
            return self._unverified(
                url, document.url, "company_ownership_unverified", requests
            )
        return EntryVerificationResult(
            candidate_url=HttpUrl(url),
            final_url=HttpUrl(document.url),
            status=EntryVerificationStatus.VERIFIED,
            http_requests=requests,
            ownership_evidence=ownership,
        )

    @staticmethod
    def _same_company_redirect(original_host: str):
        original_root = ".".join(original_host.split(".")[-2:])

        async def validate(url: str) -> bool:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            return host == original_host or host.endswith("." + original_root)

        return validate

    @staticmethod
    def _failed(
        url: str, reason: str, requests: int, *, final_url: str | None = None
    ) -> EntryVerificationResult:
        return EntryVerificationResult(
            candidate_url=HttpUrl(url),
            final_url=HttpUrl(final_url) if final_url is not None else None,
            status=EntryVerificationStatus.UNAVAILABLE,
            reason_code=reason,
            http_requests=requests,
        )

    @staticmethod
    def _unverified(
        url: str, final_url: str, reason: str, requests: int
    ) -> EntryVerificationResult:
        return EntryVerificationResult(
            candidate_url=HttpUrl(url),
            final_url=HttpUrl(final_url),
            status=EntryVerificationStatus.UNVERIFIED,
            reason_code=reason,
            http_requests=requests,
        )


def _public_reason(code: str) -> str:
    if code in {"unsafe_url", "unsafe_redirect", "invalid_redirect", "too_many_redirects"}:
        return "unsafe_url"
    if code in {"connect_timeout", "request_timeout", "total_timeout"}:
        return "request_timeout"
    if code == "http_not_found":
        return "not_found"
    if code in {"provider_access_denied", "provider_rate_limited"}:
        return "access_blocked"
    return "provider_unavailable"


def _classify_document(document: HttpDocument) -> str | None:
    blob = f"{document.url}\n{document.title or ''}\n{document.text}".lower()
    if any(term in blob for term in _BLOCK_TERMS):
        return "access_blocked"
    if any(term in blob for term in _LOGIN_TERMS):
        return "login_required"
    return None


def _has_recruiting_semantics(document: HttpDocument) -> bool:
    path = urlsplit(document.url).path.lower()
    visible = f"{document.title or ''} {document.text}".lower()
    path_signal = any(term in path for term in _RECRUITING_TERMS)
    visible_signals = sum(term in visible for term in _RECRUITING_TERMS)
    link_signal = any(
        any(term in f"{href} {label}".lower() for term in _RECRUITING_TERMS)
        for href, label in document.anchors
    )
    return (path_signal and visible_signals >= 1) or visible_signals >= 2 or link_signal


def _ownership_evidence(
    document: HttpDocument,
    company: CompanyNamePool,
    *,
    trusted_existing_binding: bool,
    linked_from_verified_website: bool,
) -> str | None:
    if trusted_existing_binding:
        return "trusted_existing_binding"
    if linked_from_verified_website:
        return "verified_website_link"
    host = (urlsplit(document.url).hostname or "").lower().rstrip(".")
    if any(host == root or host.endswith("." + root) for root in company.root_domains()):
        return "company_domain"
    text = normalize_for_compare(f"{document.title or ''} {document.text[:20_000]}")
    for name in company.all_name_variants():
        normalized = normalize_for_compare(name)
        if len(normalized) >= 2 and re.search(re.escape(normalized), text):
            return "company_name"
    return None
