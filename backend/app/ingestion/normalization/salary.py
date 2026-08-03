"""Deterministic parsing of untrusted salary text."""

import re
from dataclasses import dataclass

_MONTHS_PATTERN = re.compile(r"(?:[\u00b7*\u00d7xX]\s*)?(\d+)\s*(?:\u85aa|\u4e2a\u6708)")
_MONTHLY_RANGE_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[kK]\s*[-~\u81f3\u5230]\s*"
    r"(\d+(?:\.\d+)?)\s*[kK](?:\s*[\u00b7*\u00d7xX]?\s*\d+\s*(?:\u85aa|\u4e2a\u6708))?\s*$"
)
_MAX_SQL_INTEGER = 2_147_483_647
_MAX_SQL_SMALLINT = 32_767


@dataclass(frozen=True)
class NormalizedSalary:
    minimum_monthly: int | None
    maximum_monthly: int | None
    months: int | None
    warnings: tuple[str, ...] = ()


def normalize_salary(raw_salary: str | None) -> NormalizedSalary:
    """Return monthly RMB bounds only when the source expresses them unambiguously."""
    if raw_salary is None:
        return NormalizedSalary(None, None, None)

    match = _MONTHLY_RANGE_PATTERN.fullmatch(raw_salary)
    if match is None:
        return _invalid_salary()

    minimum = _monthly_rmb_from_k(match.group(1))
    maximum = _monthly_rmb_from_k(match.group(2))
    if minimum is None or maximum is None:
        return _invalid_salary()
    if minimum > maximum:
        return _invalid_salary()

    months_match = _MONTHS_PATTERN.search(raw_salary)
    months = (
        _salary_months_from_digits(months_match.group(1))
        if months_match is not None
        else None
    )
    if months_match is not None and months is None:
        return _invalid_salary()
    return NormalizedSalary(minimum, maximum, months)


def _invalid_salary() -> NormalizedSalary:
    return NormalizedSalary(None, None, None, ("invalid_salary",))


def _salary_months_from_digits(value: str) -> int | None:
    normalized = value.lstrip("0") or "0"
    maximum = str(_MAX_SQL_SMALLINT)
    if len(normalized) > len(maximum) or (
        len(normalized) == len(maximum) and normalized > maximum
    ):
        return None
    months = int(normalized)
    return months if months >= 1 else None


def _monthly_rmb_from_k(value: str) -> int | None:
    whole, separator, fractional = value.partition(".")
    if not separator:
        monthly_rmb = int(whole) * 1_000
        return monthly_rmb if monthly_rmb <= _MAX_SQL_INTEGER else None

    coefficient = int(f"{whole}{fractional}") * 1_000
    divisor = 10 ** len(fractional)
    monthly_rmb, remainder = divmod(coefficient, divisor)
    if remainder != 0 or monthly_rmb > _MAX_SQL_INTEGER:
        return None
    return monthly_rmb
