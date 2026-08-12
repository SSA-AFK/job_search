"""Deterministic company-stage rules for cross-stage ranking calibration."""

from datetime import date
from enum import StrEnum

from app.rankings.selection import RankingCandidate


class CompanyStage(StrEnum):
    EARLY = "early"
    GROWTH = "growth"
    MATURE = "mature"


STAGE_RULE_VERSION = "company-stage-v1"


def classify_company_stage(candidate: RankingCandidate, *, as_of: date) -> CompanyStage:
    established_at = candidate.established_at
    if established_at is None:
        return CompanyStage.GROWTH
    age_days = max(0, (as_of - established_at).days)
    employee_count = candidate.insured_employee_count or 0
    if age_days < 3 * 365 and employee_count < 100:
        return CompanyStage.EARLY
    if age_days >= 10 * 365 or employee_count >= 500:
        return CompanyStage.MATURE
    return CompanyStage.GROWTH


def merge_small_stages(
    stages: dict[str, CompanyStage], *, minimum_size: int = 5
) -> dict[str, CompanyStage]:
    """Merge undersized edge stages into growth for stable percentiles."""
    counts = {stage: sum(value == stage for value in stages.values()) for stage in CompanyStage}
    return {
        company: (
            CompanyStage.GROWTH
            if stage != CompanyStage.GROWTH and counts[stage] < minimum_size
            else stage
        )
        for company, stage in stages.items()
    }
