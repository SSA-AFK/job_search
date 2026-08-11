from dataclasses import dataclass

from rapidfuzz.fuzz import ratio

from app.core.normalization import normalize_name

_MINIMUM_SIMILARITY = 92.0


@dataclass(frozen=True)
class CompanyMatch:
    accepted: bool
    score: float


def match_company_name(requested_name: str, observed_name: str) -> CompanyMatch:
    """Accept exact names or a high-confidence normalized similarity match."""
    requested = _comparison_name(requested_name)
    observed = _comparison_name(observed_name)
    if not requested or not observed:
        return CompanyMatch(False, 0.0)
    if requested == observed:
        return CompanyMatch(True, 100.0)
    score = ratio(requested, observed)
    return CompanyMatch(score >= _MINIMUM_SIMILARITY, score)


def _comparison_name(value: str) -> str:
    return "".join(character for character in normalize_name(value) if character.isalnum())
