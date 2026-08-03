"""Deterministic parsing of untrusted salary text."""

import re
from dataclasses import dataclass
from decimal import Decimal

_MONTHS_PATTERN = re.compile(r"(?:[\u00b7*\u00d7xX]\s*)?(\d{1,2})\s*(?:\u85aa|\u4e2a\u6708)")
_MONTHLY_RANGE_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[kK]\s*[-~\u81f3\u5230]\s*"
    r"(\d+(?:\.\d+)?)\s*[kK](?:\s*[\u00b7*\u00d7xX]?\s*\d{1,2}\s*(?:\u85aa|\u4e2a\u6708))?\s*$"
)


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

    minimum_value = Decimal(match.group(1)) * 1_000
    maximum_value = Decimal(match.group(2)) * 1_000
    if (
        minimum_value != minimum_value.to_integral_value()
        or maximum_value != maximum_value.to_integral_value()
    ):
        return _invalid_salary()
    minimum = int(minimum_value)
    maximum = int(maximum_value)
    if minimum > maximum:
        return _invalid_salary()

    months_match = _MONTHS_PATTERN.search(raw_salary)
    months = int(months_match.group(1)) if months_match is not None else None
    return NormalizedSalary(minimum, maximum, months)


def _invalid_salary() -> NormalizedSalary:
    return NormalizedSalary(None, None, None, ("invalid_salary",))
