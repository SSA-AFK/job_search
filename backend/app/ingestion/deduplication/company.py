"""Exact-first company resolution with deterministic fuzzy fallback."""

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from uuid import UUID

from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.normalization.company import normalize_company

_FUZZY_MATCH_THRESHOLD = 80.0


@dataclass(frozen=True)
class CompanyForComparison:
    company_id: UUID
    normalized_name: str


@dataclass(frozen=True)
class CompanyMatch:
    kind: str
    company_id: UUID | None


class CompanyDeduplicationRepository(Protocol):
    async def find_by_normalized_name_or_alias(self, normalized_name: str) -> UUID | None: ...

    async def list_for_deduplication(self) -> Iterable[CompanyForComparison]: ...


class CompanyDeduplicator:
    def __init__(self, repository: CompanyDeduplicationRepository) -> None:
        self._repository = repository

    async def resolve(self, candidate: CompanyCandidate) -> CompanyMatch:
        normalized = normalize_company(candidate)
        exact_match = await self._repository.find_by_normalized_name_or_alias(
            normalized.normalized_name
        )
        if exact_match is not None:
            return CompanyMatch("existing", exact_match)

        comparisons = await self._repository.list_for_deduplication()
        best_match = max(
            (
                (self._similarity(normalized.normalized_name, item.normalized_name), item)
                for item in comparisons
            ),
            default=None,
            key=lambda result: (result[0], str(result[1].company_id)),
        )
        if best_match is not None and best_match[0] >= _FUZZY_MATCH_THRESHOLD:
            return CompanyMatch("existing", best_match[1].company_id)
        return CompanyMatch("new", None)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return SequenceMatcher(a=left, b=right, autojunk=False).ratio() * 100
