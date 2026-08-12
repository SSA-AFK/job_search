from pathlib import Path

from app.rankings.gap_plan import EnrichmentCategory, plan_ranking_enrichment
from app.rankings.selection import read_ranking_candidates
from tests.rankings.test_selection import _workbook


def test_workbook_baseline_removes_company_and_history_api_calls(tmp_path: Path) -> None:
    candidate = read_ranking_candidates(_workbook(tmp_path / "companies.xlsx", count=1))[0]

    plan = plan_ranking_enrichment(candidate)

    assert plan.categories == tuple(EnrichmentCategory)
    assert len(plan.categories) == 4
    assert "company.established_at" in plan.baseline_fields
    assert "organization.company_size" in plan.baseline_fields
    assert "organization.insured_employee_count" in plan.baseline_fields
    assert all(category.value not in {"company", "history"} for category in plan.categories)


def test_fresh_cached_categories_are_not_requested_again(tmp_path: Path) -> None:
    candidate = read_ranking_candidates(_workbook(tmp_path / "companies.xlsx", count=1))[0]

    plan = plan_ranking_enrichment(
        candidate,
        fresh_field_keys=frozenset({"growth.material_events_3y", "risk.material_events"}),
    )

    assert plan.categories == (
        EnrichmentCategory.INTELLECTUAL_PROPERTY,
        EnrichmentCategory.MARKET_VALIDATION,
    )
