from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from pydantic import HttpUrl

from app.core.normalization import normalize_url
from app.ingestion.entry_discovery.contracts import CompanyNamePool
from app.ingestion.entry_verification.contracts import (
    EntryVerificationBudget,
    EntryVerificationResult,
    EntryVerificationStatus,
)
from app.ingestion.entry_verification.validator import EntryUrlValidator


class EntryBudgetExhausted(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EntryCandidateInput:
    url: str
    trusted_existing_binding: bool = False
    linked_from_verified_website: bool = False


SearchCandidates = Callable[[], Awaitable[Iterable[EntryCandidateInput]]]


class EntryVerificationService:
    def __init__(
        self,
        *,
        validator: EntryUrlValidator,
        budget: EntryVerificationBudget | None = None,
    ) -> None:
        self._validator = validator
        self._budget = budget or EntryVerificationBudget()

    async def find_verified_entry(
        self,
        *,
        company: CompanyNamePool,
        existing: Iterable[EntryCandidateInput] = (),
        website_links: Iterable[EntryCandidateInput] = (),
        search: SearchCandidates | None = None,
    ) -> EntryVerificationResult | None:
        candidates = [*existing, *website_links]
        searched = False
        checked = 0
        requests = 0
        seen: set[str] = set()
        last_result: EntryVerificationResult | None = None

        while True:
            while candidates:
                candidate = candidates.pop(0)
                key = normalize_url(candidate.url).rstrip("/")
                if key in seen:
                    continue
                if checked >= self._budget.max_candidates:
                    return _budget_result(candidate.url, requests)
                seen.add(key)
                checked += 1

                async def count_request() -> None:
                    nonlocal requests
                    if requests >= self._budget.max_http_requests:
                        raise EntryBudgetExhausted
                    requests += 1

                try:
                    result = await self._validator.verify(
                        candidate.url,
                        company=company,
                        trusted_existing_binding=candidate.trusted_existing_binding,
                        linked_from_verified_website=candidate.linked_from_verified_website,
                        request_started=count_request,
                    )
                except EntryBudgetExhausted:
                    return _budget_result(candidate.url, requests)
                last_result = result.model_copy(update={"http_requests": requests})
                if result.status is EntryVerificationStatus.VERIFIED:
                    return last_result

            if searched or search is None or self._budget.max_search_calls == 0:
                return last_result
            searched = True
            candidates.extend(await search())


def _budget_result(url: str, requests: int) -> EntryVerificationResult:
    return EntryVerificationResult(
        candidate_url=HttpUrl(url),
        status=EntryVerificationStatus.UNAVAILABLE,
        reason_code="budget_exhausted",
        http_requests=requests,
    )
