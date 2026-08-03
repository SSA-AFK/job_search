"""The narrow advisory boundary for ambiguous job matches."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.ingestion.deduplication.job import JobForComparison


@dataclass(frozen=True)
class DuplicateDecision:
    is_duplicate: bool


class SemanticDuplicateJudge(Protocol):
    async def jobs_are_duplicates(
        self, left: "JobForComparison", right: "JobForComparison"
    ) -> DuplicateDecision: ...
