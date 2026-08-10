# backend/app/ingestion/jobs/parser.py
import hashlib
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import HttpUrl

from app.ingestion.jobs.contracts import AtsJobCandidate, AtsListResult, AtsParseStatus

_PLATFORM_SELECTORS: dict[str, tuple[str, str]] = {
    "feishu": (".positionItem, div.job-card, li.job-item, [data-job-id]", "a"),
    "moka": ("a.link-abc, .link-abc, div.position-list-item, li.position-item, [data-position-id]", "a"),
    "generic": ("a.job-card, .job-listing a, .position a", "a"),
}


def parse_html_job_list(html: str, platform: str) -> AtsListResult:
    if not html or not html.strip():
        return AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="parse_failed")
    try:
        soup = BeautifulSoup(html, "html.parser")
        card_selector, link_selector = _PLATFORM_SELECTORS.get(
            platform, _PLATFORM_SELECTORS["generic"]
        )
        candidates: list[AtsJobCandidate] = []
        for card in soup.select(card_selector):
            link = card if card.name == "a" else card.select_one(link_selector)
            if link is None:
                continue
            href_val = link.get("href", "")
            href = href_val[0] if isinstance(href_val, list) else href_val
            title_el = (
                link.select_one(".positionItem-title-text")
                or link.select_one("[class*='title-']")
                or link
            )
            title = title_el.get_text(strip=True)
            if not href or not title:
                continue
            url = href if href.startswith("http") else urljoin("https://example.com", href)
            try:
                candidate = AtsJobCandidate(title=title[:500], url=HttpUrl(url), external_id=None)
            except Exception:  # noqa: BLE001, S112 - skip malformed candidate; parser never raises.
                continue
            candidates.append(candidate)
        fingerprint = hashlib.sha256(html.encode("utf-8")).hexdigest()[:128]
        status = AtsParseStatus.SUCCEEDED if candidates else AtsParseStatus.PARTIAL
        return AtsListResult(
            candidates=tuple(candidates),
            status=status,
            observed_count=len(candidates),
            reported_total=None,
            error_code=None if candidates else "no_candidates",
            content_fingerprint=fingerprint,
        )
    except Exception:  # noqa: BLE001 - parser boundary: never raise, return FAILED.
        return AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="parse_failed")
