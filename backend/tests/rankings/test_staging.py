from datetime import date

from app.rankings.selection import RankingCandidate
from app.rankings.staging import CompanyStage, classify_company_stage, merge_small_stages


def _candidate(established_at: date, employees: int) -> RankingCandidate:
    return RankingCandidate(
        canonical_name="示例公司",
        normalized_name="示例公司",
        source_row=3,
        province="北京",
        city="北京",
        industry_major="软件",
        score=80,
        identity_hash="a" * 64,
        website_candidate=None,
        company_size="小型",
        established_at=established_at,
        insured_employee_count=employees,
        employee_report_year=2025,
        business_scope="人工智能",
        registered_capital=None,
        paid_in_capital=None,
        district=None,
        company_type=None,
        industry_sector=None,
        industry_middle=None,
    )


def test_stage_uses_age_and_employee_band() -> None:
    as_of = date(2026, 8, 12)
    assert classify_company_stage(_candidate(date(2025, 1, 1), 20), as_of=as_of) == "early"
    assert classify_company_stage(_candidate(date(2020, 1, 1), 120), as_of=as_of) == "growth"
    assert classify_company_stage(_candidate(date(2010, 1, 1), 20), as_of=as_of) == "mature"


def test_small_edge_stage_merges_into_growth() -> None:
    stages = {
        "early-one": CompanyStage.EARLY,
        **{f"growth-{i}": CompanyStage.GROWTH for i in range(5)},
    }
    merged = merge_small_stages(stages)
    assert merged["early-one"] == CompanyStage.GROWTH
