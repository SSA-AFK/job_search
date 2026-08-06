import json
from collections.abc import Mapping
from types import MappingProxyType
from uuid import UUID

import pytest

from app.manifest.allocation import (
    ManifestAllocationError,
    QuotaAllocation,
    ResolvedCandidate,
    allocate_quotas,
    canonical_manifest_bytes,
    select_manifest_members,
)
from app.manifest.contracts import (
    AiCategory,
    ConfidenceTier,
    ManifestCompany,
    ManifestMemberData,
)


def category_map(value: int = 0) -> dict[AiCategory, int]:
    return {category: value for category in AiCategory}


def allocation_for(
    category: AiCategory, count: int
) -> QuotaAllocation:
    counts = category_map()
    counts[category] = count
    empty = category_map()
    return QuotaAllocation(
        total=count,
        counts=MappingProxyType(counts),
        floor=MappingProxyType(empty),
        proportional=MappingProxyType(counts.copy()),
        final=MappingProxyType(counts.copy()),
    )


def resolved_candidate(
    label: int,
    *,
    category: AiCategory = AiCategory.FOUNDATION_MODELS,
    confidence: ConfidenceTier = ConfidenceTier.HIGH,
    normalized_name: str | None = None,
    scale: str | None = None,
    city: str | None = None,
) -> ResolvedCandidate:
    return ResolvedCandidate(
        company_id=UUID(int=label),
        canonical_name=f"Company {label}",
        normalized_name=normalized_name or f"company {label:04d}",
        primary_category=category,
        official_website=f"https://company-{label}.example/about",
        recruitment_url=f"https://company-{label}.example/jobs",
        confidence_tier=confidence,
        stable_evidence_id=f"{label:064x}",
        scale=scale,
        city=city,
    )


def test_mixed_allocation_is_exact_and_deterministic() -> None:
    counts = {category: 200 + index for index, category in enumerate(AiCategory)}

    allocation = allocate_quotas(counts)

    assert sum(allocation.final.values()) == 1000
    assert sum(allocation.floor.values()) == 400
    assert sum(allocation.proportional.values()) == 600
    assert allocate_quotas(dict(reversed(tuple(counts.items())))) == allocation


def test_four_extra_floor_seats_use_largest_counts_then_identifier() -> None:
    lexical_categories = sorted(AiCategory, key=lambda category: category.value)
    counts = category_map(120)
    counts[lexical_categories[-1]] = 122
    counts[lexical_categories[-2]] = 121

    allocation = allocate_quotas(counts)

    extra_floor_categories = {
        category for category, seats in allocation.floor.items() if seats == 45
    }
    assert extra_floor_categories == {
        lexical_categories[0],
        lexical_categories[1],
        lexical_categories[-2],
        lexical_categories[-1],
    }
    assert set(allocation.floor.values()) == {44, 45}


def test_proportional_seats_use_remaining_counts_and_largest_remainders() -> None:
    lexical_categories = sorted(AiCategory, key=lambda category: category.value)
    counts = category_map(2)

    allocation = allocate_quotas(counts, total=10)

    assert allocation.floor == {
        category: (1 if category in lexical_categories[:4] else 0)
        for category in AiCategory
    }
    assert allocation.proportional == {
        category: (
            1
            if category == lexical_categories[0] or category in lexical_categories[4:]
            else 0
        )
        for category in AiCategory
    }
    assert allocation.final[lexical_categories[0]] == 2
    assert sum(allocation.final.values()) == 10


def test_allocation_rejects_floor_shortage_and_non_integral_inputs() -> None:
    counts = category_map(120)
    counts[AiCategory.FOUNDATION_MODELS] = 43

    with pytest.raises(ManifestAllocationError, match="category floor shortage"):
        allocate_quotas(counts)
    with pytest.raises(ManifestAllocationError, match="non-negative integers"):
        allocate_quotas({**category_map(100), AiCategory.FOUNDATION_MODELS: -1})
    with pytest.raises(ManifestAllocationError, match="divisible by 5"):
        allocate_quotas(category_map(100), total=11)


def test_quota_allocation_deep_freezes_all_mappings() -> None:
    allocation = allocate_quotas(category_map(200))

    for values in (
        allocation.counts,
        allocation.floor,
        allocation.proportional,
        allocation.final,
    ):
        with pytest.raises(TypeError):
            values[AiCategory.FOUNDATION_MODELS] = 0  # type: ignore[index]


def test_selection_prioritizes_confidence_then_round_robins_diversity_buckets() -> None:
    category = AiCategory.FOUNDATION_MODELS
    candidates = (
        resolved_candidate(1, normalized_name="zeta", scale="large", city="beijing"),
        resolved_candidate(2, normalized_name="alpha", scale="large", city="beijing"),
        resolved_candidate(3, normalized_name="beta", scale="small", city="shanghai"),
        resolved_candidate(4, normalized_name="charlie", scale="small", city="shanghai"),
        resolved_candidate(
            5,
            confidence=ConfidenceTier.MEDIUM,
            normalized_name="aardvark",
            scale="large",
            city="beijing",
        ),
    )

    members = select_manifest_members(candidates, allocation_for(category, 5))

    assert [member.company.company_id.int for member in members] == [2, 3, 1, 4, 5]
    assert [member.position for member in members] == [1, 2, 3, 4, 5]


def test_selection_uses_evidence_id_tie_break_and_round_robins_categories() -> None:
    lexical_categories = sorted(AiCategory, key=lambda category: category.value)
    first_category, second_category = lexical_categories[:2]
    candidates = (
        resolved_candidate(12, category=second_category, normalized_name="same"),
        resolved_candidate(2, category=first_category, normalized_name="same"),
        resolved_candidate(1, category=first_category, normalized_name="same"),
        resolved_candidate(11, category=second_category, normalized_name="same"),
    )
    counts = category_map()
    counts[first_category] = 2
    counts[second_category] = 2
    allocation = QuotaAllocation(
        total=4,
        counts=counts,
        floor=category_map(),
        proportional=counts,
        final=counts,
    )

    members = select_manifest_members(tuple(reversed(candidates)), allocation)

    assert [member.company.company_id.int for member in members] == [1, 11, 2, 12]


def test_selection_rejects_duplicate_or_insufficient_company_identities() -> None:
    candidate = resolved_candidate(1)

    with pytest.raises(ManifestAllocationError, match="company identities must be unique"):
        select_manifest_members((candidate, candidate), allocation_for(candidate.primary_category, 1))
    with pytest.raises(ManifestAllocationError, match="cannot fill allocated quota"):
        select_manifest_members((candidate,), allocation_for(candidate.primary_category, 2))


def test_canonical_manifest_bytes_are_compact_utf8_and_position_ordered() -> None:
    members = (
        ManifestMemberData(
            position=2,
            company=ManifestCompany(
                company_id=UUID("00000000-0000-0000-0000-000000000002"),
                canonical_name="上海智能",
                primary_category=AiCategory.ROBOTICS_EMBODIED_AI,
                official_website="https://two.example/about",
            ),
        ),
        ManifestMemberData(
            position=1,
            company=ManifestCompany(
                company_id=UUID("00000000-0000-0000-0000-000000000001"),
                canonical_name="北京智能",
                primary_category=AiCategory.FOUNDATION_MODELS,
                recruitment_url="https://one.example/jobs",
            ),
        ),
    )

    encoded = canonical_manifest_bytes(members)

    assert encoded == (
        b'{"members":[{"company":{"canonical_name":"\xe5\x8c\x97\xe4\xba\xac\xe6\x99\xba\xe8\x83\xbd",'
        b'"company_id":"00000000-0000-0000-0000-000000000001",'
        b'"official_website":null,"primary_category":"foundation_models",'
        b'"recruitment_url":"https://one.example/jobs"},"position":1},'
        b'{"company":{"canonical_name":"\xe4\xb8\x8a\xe6\xb5\xb7\xe6\x99\xba\xe8\x83\xbd",'
        b'"company_id":"00000000-0000-0000-0000-000000000002",'
        b'"official_website":"https://two.example/about",'
        b'"primary_category":"robotics_embodied_ai","recruitment_url":null},'
        b'"position":2}]}'
    )
    assert json.loads(encoded)["members"][0]["position"] == 1


def test_canonical_manifest_rejects_duplicate_or_gapped_positions() -> None:
    company = ManifestCompany(
        company_id=UUID(int=1),
        canonical_name="Example",
        primary_category=AiCategory.FOUNDATION_MODELS,
    )

    with pytest.raises(ManifestAllocationError, match="contiguous"):
        canonical_manifest_bytes((ManifestMemberData(position=2, company=company),))


def test_quota_maps_are_mappings_for_static_contract() -> None:
    allocation = allocate_quotas(category_map(200))

    assert isinstance(allocation.final, Mapping)
