"""Rule-based advisory boundary for ambiguous job matches."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ingestion.deduplication.job import JobForComparison


@dataclass(frozen=True)
class DuplicateDecision:
    is_duplicate: bool


def jobs_are_duplicates(
    left: "JobForComparison", right: "JobForComparison"
) -> DuplicateDecision:
    """Rule-based duplicate judgment: compare title similarity and city match."""
    similarity = SequenceMatcher(
        a=left.normalized_title, b=right.normalized_title, autojunk=False
    ).ratio() * 100
    city_match = left.city == right.city
    return DuplicateDecision(is_duplicate=similarity >= 75.0 and city_match)
