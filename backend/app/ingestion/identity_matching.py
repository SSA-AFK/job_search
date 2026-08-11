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


def company_name_mentioned(requested_name: str, text: str) -> bool:
    requested = _comparison_name(requested_name)
    return bool(requested and requested in _comparison_name(text))


def company_name_variants(company_name: str) -> tuple[str, ...]:
    """Produce bounded search variants without treating them as verified aliases."""
    compact = "".join(company_name.split())
    if not compact:
        return ()
    variants = [compact]
    without_location = compact
    for location in ("北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "成都", "武汉", "厦门", "苏州"):
        if without_location.startswith(location):
            without_location = without_location.removeprefix(location)
            variants.append(without_location)
            break
    for suffix in ("有限公司", "集团", "研究院", "公司"):
        without_location = without_location.removesuffix(suffix)
    for suffix in ("科技", "信息技术", "数据", "人工智能", "数智"):
        without_location = without_location.removesuffix(suffix)
    if len(without_location) >= 3:
        variants.append(without_location)
        if any("\u4e00" <= character <= "\u9fff" for character in without_location):
            variants.append(without_location[:3])
    return tuple(dict.fromkeys(value for value in variants if len(value) >= 3))


def _comparison_name(value: str) -> str:
    return "".join(character for character in normalize_name(value) if character.isalnum())
