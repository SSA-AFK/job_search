import pytest

from app.ingestion.normalization.salary import normalize_salary


def test_normalizes_monthly_k_range_and_salary_months() -> None:
    salary = normalize_salary("30k-50k\u00b714\u85aa")

    assert salary.minimum_monthly == 30_000
    assert salary.maximum_monthly == 50_000
    assert salary.months == 14
    assert salary.warnings == ()


def test_missing_salary_remains_unknown_without_a_warning() -> None:
    salary = normalize_salary(None)

    assert salary.minimum_monthly is None
    assert salary.maximum_monthly is None
    assert salary.months is None
    assert salary.warnings == ()


@pytest.mark.parametrize("raw", ["50k-30k", "competitive"])
def test_uncertain_or_reversed_salary_range_is_unknown_and_warned(raw: str) -> None:
    salary = normalize_salary(raw)

    assert salary.minimum_monthly is None
    assert salary.maximum_monthly is None
    assert salary.months is None
    assert salary.warnings == ("invalid_salary",)
