"""Exact quota allocation and deterministic manifest member ordering."""

import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from app.manifest.contracts import (
    AiCategory,
    ConfidenceTier,
    ManifestCompany,
    ManifestMemberData,
)

_CATEGORIES: tuple[AiCategory, ...] = (
    AiCategory.FOUNDATION_MODELS,
    AiCategory.AI_CLOUD_MODEL_PLATFORMS,
    AiCategory.AI_CHIPS_COMPUTE,
    AiCategory.AUTONOMOUS_DRIVING_TRANSPORT,
    AiCategory.ROBOTICS_EMBODIED_AI,
    AiCategory.COMPUTER_VISION_IMAGING,
    AiCategory.SPEECH_LANGUAGE_TECHNOLOGY,
    AiCategory.ENTERPRISE_VERTICAL_AI,
    AiCategory.DATA_INFRASTRUCTURE_MLOPS,
)
_CONFIDENCE_TIERS: tuple[ConfidenceTier, ...] = (
    ConfidenceTier.HIGH,
    ConfidenceTier.MEDIUM,
    ConfidenceTier.LOW,
)


class ManifestAllocationError(ValueError):
    """Raised when the reviewed candidate pool cannot satisfy the manifest contract."""


def _category_mapping(values: Mapping[AiCategory, int]) -> Mapping[AiCategory, int]:
    unexpected = tuple(key for key in values if not isinstance(key, AiCategory))
    if unexpected:
        raise ManifestAllocationError("quota mappings must use AiCategory keys")
    normalized = {category: values.get(category, 0) for category in _CATEGORIES}
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in normalized.values()
    ):
        raise ManifestAllocationError("candidate counts must be non-negative integers")
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class ResolvedCandidate:
    company_id: UUID
    canonical_name: str
    normalized_name: str
    primary_category: AiCategory
    official_website: str | None
    recruitment_url: str | None
    confidence_tier: ConfidenceTier
    stable_evidence_id: str
    scale: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class QuotaAllocation:
    total: int
    counts: Mapping[AiCategory, int]
    floor: Mapping[AiCategory, int]
    proportional: Mapping[AiCategory, int]
    final: Mapping[AiCategory, int]

    def __post_init__(self) -> None:
        if isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0:
            raise ManifestAllocationError("quota total must be a non-negative integer")
        object.__setattr__(self, "counts", _category_mapping(self.counts))
        object.__setattr__(self, "floor", _category_mapping(self.floor))
        object.__setattr__(self, "proportional", _category_mapping(self.proportional))
        object.__setattr__(self, "final", _category_mapping(self.final))
        if sum(self.final.values()) != self.total:
            raise ManifestAllocationError("final quota must equal the requested total")
        for category in _CATEGORIES:
            if self.floor[category] + self.proportional[category] != self.final[category]:
                raise ManifestAllocationError("final quota must equal floor plus proportional seats")


def allocate_quotas(
    counts: Mapping[AiCategory, int], total: int = 1000
) -> QuotaAllocation:
    """Allocate a 40 percent category floor and 60 percent largest-remainder share."""

    normalized_counts = _category_mapping(counts)
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise ManifestAllocationError("quota total must be a positive integer")
    if total % 5:
        raise ManifestAllocationError("quota total must be divisible by 5")
    if sum(normalized_counts.values()) < total:
        raise ManifestAllocationError("candidate pool cannot fill requested total")

    floor_total = total * 2 // 5
    base_floor, extra_floor_seats = divmod(floor_total, len(_CATEGORIES))
    largest_categories = sorted(
        _CATEGORIES,
        key=lambda category: (-normalized_counts[category], category.value),
    )
    extra_floor = frozenset(largest_categories[:extra_floor_seats])
    floor = {
        category: base_floor + (1 if category in extra_floor else 0)
        for category in _CATEGORIES
    }
    shortages = tuple(
        category
        for category in _CATEGORIES
        if normalized_counts[category] < floor[category]
    )
    if shortages:
        raise ManifestAllocationError(
            "category floor shortage: " + ", ".join(category.value for category in shortages)
        )

    proportional_total = total - floor_total
    remaining_counts = {
        category: normalized_counts[category] - floor[category]
        for category in _CATEGORIES
    }
    total_remaining = sum(remaining_counts.values())
    proportional: dict[AiCategory, int] = {}
    remainders: dict[AiCategory, int] = {}
    for category in _CATEGORIES:
        share_numerator = remaining_counts[category] * proportional_total
        base, remainder = divmod(share_numerator, total_remaining)
        proportional[category] = base
        remainders[category] = remainder

    leftover = proportional_total - sum(proportional.values())
    remainder_order = sorted(
        _CATEGORIES,
        key=lambda category: (-remainders[category], category.value),
    )
    for category in remainder_order[:leftover]:
        proportional[category] += 1

    final = {
        category: floor[category] + proportional[category] for category in _CATEGORIES
    }
    if any(final[category] > normalized_counts[category] for category in _CATEGORIES):
        raise ManifestAllocationError("candidate pool cannot fill allocated quota")
    return QuotaAllocation(
        total=total,
        counts=normalized_counts,
        floor=floor,
        proportional=proportional,
        final=final,
    )


def _diversity_order(candidates: Sequence[ResolvedCandidate]) -> tuple[ResolvedCandidate, ...]:
    ordered: list[ResolvedCandidate] = []
    for confidence_tier in _CONFIDENCE_TIERS:
        buckets: dict[tuple[str, str], list[ResolvedCandidate]] = defaultdict(list)
        for candidate in candidates:
            if candidate.confidence_tier is confidence_tier:
                bucket = (candidate.scale or "unknown", candidate.city or "unknown")
                buckets[bucket].append(candidate)
        queues = {
            bucket: deque(
                sorted(
                    values,
                    key=lambda candidate: (
                        candidate.normalized_name,
                        candidate.stable_evidence_id,
                    ),
                )
            )
            for bucket, values in buckets.items()
        }
        bucket_order = sorted(queues)
        while any(queues[bucket] for bucket in bucket_order):
            for bucket in bucket_order:
                if queues[bucket]:
                    ordered.append(queues[bucket].popleft())
    return tuple(ordered)


def select_manifest_members(
    candidates: Sequence[ResolvedCandidate], allocation: QuotaAllocation
) -> tuple[ManifestMemberData, ...]:
    """Select allocated identities and interleave categories for deterministic prefixes."""

    company_ids = [candidate.company_id for candidate in candidates]
    if len(company_ids) != len(set(company_ids)):
        raise ManifestAllocationError("resolved company identities must be unique")

    by_category: dict[AiCategory, list[ResolvedCandidate]] = {
        category: [] for category in _CATEGORIES
    }
    for candidate in candidates:
        by_category[candidate.primary_category].append(candidate)

    selected_by_category: dict[AiCategory, tuple[ResolvedCandidate, ...]] = {}
    for category in _CATEGORIES:
        quota = allocation.final[category]
        ordered = _diversity_order(by_category[category])
        if len(ordered) < quota:
            raise ManifestAllocationError(f"{category.value} cannot fill allocated quota")
        selected_by_category[category] = ordered[:quota]

    category_order = sorted(_CATEGORIES, key=lambda category: category.value)
    members: list[ManifestMemberData] = []
    offset = 0
    while len(members) < allocation.total:
        added = False
        for category in category_order:
            selected = selected_by_category[category]
            if offset >= len(selected):
                continue
            candidate = selected[offset]
            members.append(
                ManifestMemberData(
                    position=len(members) + 1,
                    company=ManifestCompany.model_validate(
                        {
                            "company_id": candidate.company_id,
                            "canonical_name": candidate.canonical_name,
                            "primary_category": candidate.primary_category,
                            "official_website": candidate.official_website,
                            "recruitment_url": candidate.recruitment_url,
                        }
                    ),
                )
            )
            added = True
        if not added:
            raise ManifestAllocationError("candidate pool cannot fill allocated quota")
        offset += 1
    return tuple(members)


def canonical_manifest_bytes(members: Sequence[ManifestMemberData]) -> bytes:
    """Return the canonical membership-only JSON used as the manifest hash input."""

    ordered = sorted(members, key=lambda member: member.position)
    positions = [member.position for member in ordered]
    if positions != list(range(1, len(ordered) + 1)):
        raise ManifestAllocationError("manifest positions must be contiguous from one")
    company_ids = [member.company.company_id for member in ordered]
    if len(company_ids) != len(set(company_ids)):
        raise ManifestAllocationError("manifest company identities must be unique")
    document = {
        "members": [member.model_dump(mode="json") for member in ordered],
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
