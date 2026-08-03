import pytest

from app.ingestion.normalization.salary import normalize_salary


def test_normalizes_monthly_k_range_and_salary_months() -> None:
    salary = normalize_salary("30k-50k\u00b714\u85aa")

    assert salary.minimum_monthly == 30_000
    assert salary.maximum_monthly == 50_000
    assert salary.months == 14
    assert salary.warnings == ()


@pytest.mark.parametrize(
    ("raw", "minimum", "maximum"),
    [
        ("1.001k-2.001k", 1_001, 2_001),
        ("0.001k-0.001k", 1, 1),
    ],
)
def test_decimal_k_ranges_preserve_exact_rmb_yuan_bounds(
    raw: str, minimum: int, maximum: int
) -> None:
    salary = normalize_salary(raw)

    assert salary.minimum_monthly == minimum
    assert salary.maximum_monthly == maximum
    assert salary.warnings == ()


def test_fractional_rmb_salary_bound_is_unknown_and_warned() -> None:
    salary = normalize_salary("1.0001k-2.001k")

    assert salary.minimum_monthly is None
    assert salary.maximum_monthly is None
    assert salary.warnings == ("invalid_salary",)


def test_excessive_decimal_precision_is_unknown_and_warned() -> None:
    salary = normalize_salary("1.0000000000000000000000000001k-2k")

    assert salary.minimum_monthly is None
    assert salary.maximum_monthly is None
    assert salary.warnings == ("invalid_salary",)


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
