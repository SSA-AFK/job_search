from app.rankings.scoring import ScoreComponent, calibrate_by_stage, raw_scores_from_signals
from app.rankings.staging import CompanyStage


def test_stage_calibration_does_not_compare_raw_scale_across_stages() -> None:
    raw = {
        "early-a": {ScoreComponent.AI_CORE: 5},
        "early-b": {ScoreComponent.AI_CORE: 1},
        "mature-a": {ScoreComponent.AI_CORE: 100},
        "mature-b": {ScoreComponent.AI_CORE: 120},
    }
    stages = {
        "early-a": CompanyStage.EARLY,
        "early-b": CompanyStage.EARLY,
        "mature-a": CompanyStage.MATURE,
        "mature-b": CompanyStage.MATURE,
    }
    scores = calibrate_by_stage(raw, stages)
    assert (
        scores["early-a"].component_scores["ai_core"]
        > scores["mature-a"].component_scores["ai_core"]
    )


def test_stage_calibration_never_awards_points_for_zero_raw_evidence() -> None:
    raw = {
        "a": {ScoreComponent.MARKET_VALIDATION: 0},
        "b": {ScoreComponent.MARKET_VALIDATION: 0},
    }
    stages = {"a": CompanyStage.GROWTH, "b": CompanyStage.GROWTH}

    scores = calibrate_by_stage(raw, stages)

    assert scores["a"].component_scores["market_validation"] == 0
    assert scores["a"].percentiles["market_validation"] == 0.0


def test_missing_signals_do_not_create_negative_score_but_material_risk_reduces_reliability() -> (
    None
):
    empty = raw_scores_from_signals([], set())
    risky = raw_scores_from_signals(["material_risk", "material_risk"], set())
    assert min(empty.values()) >= 0
    assert risky[ScoreComponent.RELIABILITY] < empty[ScoreComponent.RELIABILITY]


def test_scope_and_ai_ip_automatically_score_ai_core_without_official_website() -> None:
    scope_only = raw_scores_from_signals(["ai_business_scope"], set())
    combined = raw_scores_from_signals(
        ["ai_business_scope", "ai_invention_patent", "ai_software_copyright"], set()
    )

    assert scope_only[ScoreComponent.AI_CORE] == 15
    assert combined[ScoreComponent.AI_CORE] == 21
    assert combined[ScoreComponent.INDUSTRY_INFLUENCE] == 6
