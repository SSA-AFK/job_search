"""Evidence scoring and within-stage percentile calibration."""

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from app.rankings.staging import CompanyStage


class ScoreComponent(StrEnum):
    AI_CORE = "ai_core"
    MARKET_VALIDATION = "market_validation"
    GROWTH_MOMENTUM = "growth_momentum"
    INDUSTRY_INFLUENCE = "industry_influence"
    RELIABILITY = "reliability"


COMPONENT_MAXIMUMS = {
    ScoreComponent.AI_CORE: 30,
    ScoreComponent.MARKET_VALIDATION: 25,
    ScoreComponent.GROWTH_MOMENTUM: 20,
    ScoreComponent.INDUSTRY_INFLUENCE: 15,
    ScoreComponent.RELIABILITY: 10,
}


@dataclass(frozen=True)
class CalibratedScore:
    total: int
    component_scores: dict[str, int]
    percentiles: dict[str, float]


def calibrate_by_stage(
    raw_scores: dict[str, dict[ScoreComponent, int]],
    stages: dict[str, CompanyStage],
) -> dict[str, CalibratedScore]:
    results: dict[str, CalibratedScore] = {}
    for company, components in raw_scores.items():
        calibrated: dict[str, int] = {}
        percentiles: dict[str, float] = {}
        for component, maximum in COMPONENT_MAXIMUMS.items():
            raw_value = components.get(component, 0)
            if raw_value <= 0:
                percentiles[component.value] = 0.0
                calibrated[component.value] = 0
                continue
            peers = [
                scores.get(component, 0)
                for peer, scores in raw_scores.items()
                if stages[peer] == stages[company]
            ]
            percentile = midrank_percentile(raw_value, peers)
            percentiles[component.value] = percentile
            calibrated[component.value] = round(percentile * maximum)
        results[company] = CalibratedScore(
            total=sum(calibrated.values()),
            component_scores=calibrated,
            percentiles=percentiles,
        )
    return results


def midrank_percentile(value: int, population: list[int]) -> float:
    if not population:
        return 0.0
    if len(population) == 1:
        return 1.0
    below = sum(item < value for item in population)
    equal = sum(item == value for item in population)
    return (below + equal / 2) / len(population)


def raw_scores_from_signals(
    signal_keys: list[str], verified_profile_keys: set[str]
) -> dict[ScoreComponent, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for key in signal_keys:
        counts[key] += 1
    ai_core = 0
    if counts["ai_business_scope"]:
        ai_core += 15
    if "ai.core_level" in verified_profile_keys:
        ai_core += 15
    if "ai.products" in verified_profile_keys:
        ai_core += 10
    ai_ip_count = counts["ai_invention_patent"] + counts["ai_software_copyright"]
    ai_core += min(15, ai_ip_count * 3)
    ai_core = min(30, ai_core)
    market = min(25, counts["winning_bid"] * 5)
    if "ai.market_proofs" in verified_profile_keys:
        market = max(market, 15)
    growth = min(20, counts["financing"] * 5)
    if "ai.growth_events" in verified_profile_keys:
        growth = max(growth, 10)
    influence = min(15, ai_ip_count * 3)
    if "ai.technology_signals" in verified_profile_keys:
        influence = max(influence, 8)
    material_risk = counts["material_risk"]
    reliability = max(0, 10 - min(10, material_risk * 3))
    return {
        ScoreComponent.AI_CORE: ai_core,
        ScoreComponent.MARKET_VALIDATION: market,
        ScoreComponent.GROWTH_MOMENTUM: growth,
        ScoreComponent.INDUSTRY_INFLUENCE: influence,
        ScoreComponent.RELIABILITY: reliability,
    }
