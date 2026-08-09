from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.manifest.contracts import AiCategory, AtsCensus, CandidateFactInput, DiscoveryStatus


def candidate_fact(**overrides: object) -> CandidateFactInput:
    values: dict[str, object] = {
        "source_id": "zhihu_global_search",
        "source_url": "https://developer.zhihu.com/api/v1/content/global_search",
        "retrieved_at": datetime(2026, 8, 6, 9, tzinfo=timezone(timedelta(hours=8))),
        "canonical_name": "Acme AI",
        "aliases": ("Acme",),
        "primary_category": AiCategory.FOUNDATION_MODELS,
        "official_website": "https://www.acme.ai",
        "recruitment_url": "https://careers.acme.ai",
        "evidence_summary": "Public company profile identifies the company and its official website.",
    }
    values.update(overrides)
    return CandidateFactInput(**values)


def test_ai_categories_match_the_approved_manifest_taxonomy() -> None:
    assert [category.value for category in AiCategory] == [
        "foundation_models",
        "ai_cloud_model_platforms",
        "ai_chips_compute",
        "autonomous_driving_transport",
        "robotics_embodied_ai",
        "computer_vision_imaging",
        "speech_language_technology",
        "enterprise_vertical_ai",
        "data_infrastructure_mlops",
    ]


def test_candidate_fact_normalizes_timestamps_and_is_immutable() -> None:
    fact = candidate_fact()

    assert fact.retrieved_at == datetime(2026, 8, 6, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        fact.canonical_name = "Changed"  # type: ignore[misc]


def test_candidate_fact_rejects_unsafe_urls_extra_fields_and_unbounded_data() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        candidate_fact(source_url="https://token@example.com/source")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        candidate_fact(unexpected="value")
    with pytest.raises(ValidationError, match="at most 100"):
        candidate_fact(aliases=tuple(str(index) for index in range(101)))
    with pytest.raises(ValidationError, match="at most 2000"):
        candidate_fact(evidence_summary="x" * 2001)


def test_candidate_fact_keeps_public_identity_fields_typed() -> None:
    fact = candidate_fact()

    assert fact.official_website is not None
    assert fact.recruitment_url is not None
    assert fact.primary_category is AiCategory.FOUNDATION_MODELS


def test_ats_census_deep_freezes_count_mappings_without_changing_json_output() -> None:
    census = AtsCensus(
        manifest_version="a" * 64,
        manifest_companies=1,
        accepted_entries=1,
        platform_entry_counts={"moka": 1},
        status_counts={DiscoveryStatus.ACCEPTED: 1},
    )

    with pytest.raises(TypeError):
        census.platform_entry_counts["moka"] = 2
    with pytest.raises(TypeError):
        census.status_counts[DiscoveryStatus.NOT_FOUND] = 1
    assert census.model_dump(mode="json") == {
        "manifest_version": "a" * 64,
        "manifest_companies": 1,
        "accepted_entries": 1,
        "platform_entry_counts": {"moka": 1},
        "status_counts": {"accepted": 1},
    }
