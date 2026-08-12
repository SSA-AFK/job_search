"""Plan only ranking data that is absent from the local workbook baseline."""

from dataclasses import dataclass
from enum import StrEnum

from app.rankings.selection import RankingCandidate


class EnrichmentCategory(StrEnum):
    GROWTH = "growth"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    MARKET_VALIDATION = "market_validation"
    MATERIAL_RISK = "material_risk"


_CATEGORY_CACHE_KEYS = {
    EnrichmentCategory.GROWTH: "growth.material_events_3y",
    EnrichmentCategory.INTELLECTUAL_PROPERTY: "ai.intellectual_property_3y",
    EnrichmentCategory.MARKET_VALIDATION: "market.public_proofs_3y",
    EnrichmentCategory.MATERIAL_RISK: "risk.material_events",
}


@dataclass(frozen=True)
class RankingEnrichmentPlan:
    categories: tuple[EnrichmentCategory, ...]
    baseline_fields: frozenset[str]


def plan_ranking_enrichment(
    candidate: RankingCandidate, *, fresh_field_keys: frozenset[str] = frozenset()
) -> RankingEnrichmentPlan:
    """Return at most four missing categories; never re-query baseline company facts."""
    baseline = {
        "company.name",
        "company.status",
        "company.province",
        "company.industry_major",
    }
    if candidate.company_size is not None:
        baseline.add("organization.company_size")
    if candidate.established_at is not None:
        baseline.add("company.established_at")
    if candidate.insured_employee_count is not None:
        baseline.add("organization.insured_employee_count")
    if candidate.employee_report_year is not None:
        baseline.add("organization.employee_report_year")
    if candidate.business_scope is not None:
        baseline.add("company.business_scope")
    if candidate.website_candidate is not None:
        baseline.add("company.website_candidate")

    categories = tuple(
        category
        for category, cache_key in _CATEGORY_CACHE_KEYS.items()
        if cache_key not in fresh_field_keys
    )
    return RankingEnrichmentPlan(categories=categories, baseline_fields=frozenset(baseline))
