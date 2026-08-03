"""Company-scoped job resolution with bounded semantic review."""

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from uuid import UUID

from app.ingestion.deduplication.semantic import SemanticDuplicateJudge
from app.ingestion.extraction.schemas import EmploymentType, JobCandidate
from app.ingestion.normalization.job import normalize_job
from app.models.enums import JobType

_SEMANTIC_MINIMUM = 75.0
_SEMANTIC_MAXIMUM = 85.0


@dataclass(frozen=True)
class JobForComparison:
    job_posting_id: UUID
    normalized_title: str
    city: str
    job_type: JobType
    employment_type: EmploymentType | None = None


@dataclass(frozen=True)
class SourceJobMatch:
    job_posting_id: UUID
    company_id: UUID


@dataclass(frozen=True)
class JobMatch:
    kind: str
    job_posting_id: UUID | None


class JobDeduplicationRepository(Protocol):
    async def find_by_source(
        self, provider: str, source_raw_id: str
    ) -> SourceJobMatch | None: ...

    async def list_for_company(self, company_id: UUID) -> Iterable[JobForComparison]: ...


class JobDeduplicator:
    def __init__(
        self, repository: JobDeduplicationRepository, semantic_judge: SemanticDuplicateJudge
    ) -> None:
        self._repository = repository
        self._semantic_judge = semantic_judge

    async def resolve(self, company_id: UUID, candidate: JobCandidate) -> JobMatch:
        if candidate.provider is not None and candidate.source_raw_id is not None:
            exact_match = await self._repository.find_by_source(
                candidate.provider, candidate.source_raw_id
            )
            if exact_match is not None and exact_match.company_id == company_id:
                return JobMatch("existing", exact_match.job_posting_id)

        normalized = normalize_job(candidate)
        comparisons = await self._repository.list_for_company(company_id)
        eligible = [
            item
            for item in comparisons
            if item.city == normalized.normalized_city
            and self._types_are_compatible(
                self._comparison_employment_type(item), normalized.employment_type
            )
        ]
        best_match = max(
            (
                (self._similarity(normalized.normalized_title, item.normalized_title), item)
                for item in eligible
            ),
            default=None,
            key=lambda result: (result[0], str(result[1].job_posting_id)),
        )
        if best_match is None or best_match[0] < _SEMANTIC_MINIMUM:
            return JobMatch("new", None)
        if best_match[0] > _SEMANTIC_MAXIMUM:
            return JobMatch("existing", best_match[1].job_posting_id)

        decision = await self._semantic_judge.jobs_are_duplicates(
            JobForComparison(
                job_posting_id=best_match[1].job_posting_id,
                normalized_title=normalized.normalized_title,
                city=normalized.normalized_city,
                job_type=normalized.job_type,
                employment_type=normalized.employment_type,
            ),
            best_match[1],
        )
        if decision.is_duplicate:
            return JobMatch("existing", best_match[1].job_posting_id)
        return JobMatch("new", None)

    @staticmethod
    def _comparison_employment_type(item: JobForComparison) -> EmploymentType | None:
        if item.employment_type is not None:
            return item.employment_type
        if item.job_type is JobType.FULL_TIME:
            return EmploymentType.FULL_TIME
        if item.job_type is JobType.INTERNSHIP:
            return EmploymentType.INTERNSHIP
        return None

    @staticmethod
    def _types_are_compatible(
        left: EmploymentType | None, right: EmploymentType | None
    ) -> bool:
        return left is None or right is None or left is right

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return SequenceMatcher(a=left, b=right, autojunk=False).ratio() * 100
