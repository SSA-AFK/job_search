"""Frozen public contracts for company identity resolution and review."""

import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Self
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.normalization import normalize_name, normalize_url

_MAX_NAME_LENGTH = 200
_MAX_ALIASES = 100
_MAX_LEGAL_IDENTIFIERS = 20
_MAX_EVIDENCE_REFERENCES = 100
_MAX_CANDIDATE_MATCHES = 20
_MAX_REVIEW_REASONS = 7
_MAX_TEXT_LENGTH = 2_000
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
_EVIDENCE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_MATCH_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
_SAFE_REASON_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;()'/-]{0,1999}$")


class IdentityResolutionKind(StrEnum):
    EXISTING = "existing"
    NEW = "new"
    REVIEW_REQUIRED = "review_required"


class IdentityReviewStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class IdentityReviewAction(StrEnum):
    LINK_AS_ALIAS = "link_as_alias"
    CREATE_NEW = "create_new"
    RENAME_CANONICAL = "rename_canonical"
    REJECT = "reject"


class IdentityReviewReason(StrEnum):
    AMBIGUOUS_EXACT_OWNER = "ambiguous_exact_owner"
    FUZZY_NAME_NEIGHBOR = "fuzzy_name_neighbor"
    SHORT_NAME_COLLISION = "short_name_collision"
    WEBSITE_IDENTITY_CONFLICT = "website_identity_conflict"
    RECRUITMENT_IDENTITY_CONFLICT = "recruitment_identity_conflict"
    LEGAL_IDENTITY_CONFLICT = "legal_identity_conflict"
    SIMILARITY_SEARCH_UNAVAILABLE = "similarity_search_unavailable"


class IdentityAuditSeverity(StrEnum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    MINOR = "minor"


class _FrozenIdentityDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


def _clean_display_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _normalized_identifier(value: str) -> str:
    return normalize_name(value)


def _public_url(value: str) -> str:
    normalized = normalize_url(value)
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _unique_normalized(values: tuple[str, ...], *, limit: int) -> tuple[str, ...]:
    normalized_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_identifier(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    if len(normalized_values) > limit:
        raise ValueError("too many distinct values")
    return tuple(normalized_values)


class PublicEvidenceReference(_FrozenIdentityDTO):
    provider: str = Field(min_length=1, max_length=50)
    url: str = Field(min_length=1, max_length=2_000)
    evidence_id: str = Field(min_length=1, max_length=255)
    confidence: Decimal = Field(ge=Decimal(0), le=Decimal(1))

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = _normalized_identifier(value)
        if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
            raise ValueError("provider is invalid")
        return normalized

    @field_validator("url")
    @classmethod
    def normalize_public_url(cls, value: str) -> str:
        return _public_url(value)

    @field_validator("evidence_id")
    @classmethod
    def normalize_evidence_id(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            raise ValueError("evidence_id is required")
        return normalized


class CompanyIdentityInput(_FrozenIdentityDTO):
    canonical_name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    aliases: tuple[str, ...] = Field(default=(), max_length=_MAX_ALIASES)
    official_website: str | None = Field(default=None, max_length=2_000)
    recruitment_identity: str | None = Field(default=None, max_length=255)
    legal_identifiers: tuple[str, ...] = Field(default=(), max_length=_MAX_LEGAL_IDENTIFIERS)
    city: str | None = Field(default=None, max_length=100)
    evidence: tuple[PublicEvidenceReference, ...] = Field(
        default=(), max_length=_MAX_EVIDENCE_REFERENCES
    )

    @field_validator("canonical_name")
    @classmethod
    def normalize_canonical_name(cls, value: str) -> str:
        normalized = _clean_display_name(value)
        if not normalized:
            raise ValueError("canonical_name is required")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        aliases: list[str] = []
        seen: set[str] = set()
        for alias in value:
            display_name = _clean_display_name(alias)
            normalized = normalize_name(display_name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(display_name)
        return tuple(aliases)

    @field_validator("official_website")
    @classmethod
    def normalize_official_website(cls, value: str | None) -> str | None:
        return None if value is None else _public_url(value)

    @field_validator("recruitment_identity")
    @classmethod
    def normalize_recruitment_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalized_identifier(value)
        return normalized or None

    @field_validator("legal_identifiers")
    @classmethod
    def normalize_legal_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_normalized(value, limit=_MAX_LEGAL_IDENTIFIERS)

    @field_validator("city")
    @classmethod
    def normalize_city(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalized_identifier(value)
        return normalized or None

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(
        cls, value: tuple[PublicEvidenceReference, ...]
    ) -> tuple[PublicEvidenceReference, ...]:
        return tuple(
            sorted(
                set(value),
                key=lambda reference: (
                    reference.provider,
                    reference.url,
                    reference.evidence_id,
                    str(reference.confidence),
                ),
            )
        )

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.canonical_name)

    @property
    def normalized_aliases(self) -> tuple[str, ...]:
        normalized = {normalize_name(alias) for alias in self.aliases}
        normalized.discard(self.normalized_name)
        return tuple(sorted(normalized))


class CompanyIdentityNameOwner(_FrozenIdentityDTO):
    company_id: UUID
    normalized_name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)

    @field_validator("normalized_name")
    @classmethod
    def normalize_owned_name(cls, value: str) -> str:
        normalized = normalize_name(value)
        if not normalized:
            raise ValueError("normalized_name is required")
        return normalized


class CompanyIdentityCandidateMatch(_FrozenIdentityDTO):
    company_id: UUID
    canonical_name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    normalized_name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    match_kind: str = Field(min_length=1, max_length=50)
    score: Decimal = Field(ge=Decimal(0), le=Decimal(100))
    conflict_reasons: tuple[IdentityReviewReason, ...] = Field(
        default=(), max_length=_MAX_REVIEW_REASONS
    )

    @field_validator("canonical_name")
    @classmethod
    def normalize_candidate_name(cls, value: str) -> str:
        normalized = _clean_display_name(value)
        if not normalized:
            raise ValueError("canonical_name is required")
        return normalized

    @field_validator("normalized_name")
    @classmethod
    def normalize_candidate_normalized_name(cls, value: str) -> str:
        normalized = normalize_name(value)
        if not normalized:
            raise ValueError("normalized_name is required")
        return normalized

    @field_validator("match_kind")
    @classmethod
    def validate_match_kind(cls, value: str) -> str:
        normalized = _normalized_identifier(value)
        if _MATCH_KIND_PATTERN.fullmatch(normalized) is None:
            raise ValueError("match_kind is invalid")
        return normalized

    @field_validator("conflict_reasons")
    @classmethod
    def deduplicate_conflict_reasons(
        cls, value: tuple[IdentityReviewReason, ...]
    ) -> tuple[IdentityReviewReason, ...]:
        return tuple(sorted(set(value), key=lambda reason: reason.value))

    @model_validator(mode="after")
    def validate_normalized_name(self) -> Self:
        if self.normalized_name != normalize_name(self.canonical_name):
            raise ValueError("normalized_name is invalid")
        return self


class CompanyIdentityResolution(_FrozenIdentityDTO):
    kind: IdentityResolutionKind
    company_id: UUID | None = None
    stable_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_matches: tuple[CompanyIdentityCandidateMatch, ...] = Field(
        default=(), max_length=_MAX_CANDIDATE_MATCHES
    )
    review_reasons: tuple[IdentityReviewReason, ...] = Field(
        default=(), max_length=_MAX_REVIEW_REASONS
    )

    @field_validator("candidate_matches")
    @classmethod
    def order_candidate_matches(
        cls, value: tuple[CompanyIdentityCandidateMatch, ...]
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        return _canonical_candidate_matches(value)

    @field_validator("review_reasons")
    @classmethod
    def deduplicate_review_reasons(
        cls, value: tuple[IdentityReviewReason, ...]
    ) -> tuple[IdentityReviewReason, ...]:
        return tuple(sorted(set(value), key=lambda reason: reason.value))

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if self.kind is IdentityResolutionKind.EXISTING:
            if self.company_id is None:
                raise ValueError("existing resolution requires company_id")
            if self.candidate_matches or self.review_reasons:
                raise ValueError("existing resolution cannot include review data")
        elif self.kind is IdentityResolutionKind.NEW:
            if self.company_id is not None:
                raise ValueError("new resolution cannot include company_id")
            if self.candidate_matches or self.review_reasons:
                raise ValueError("new resolution cannot include review data")
        else:
            if self.company_id is not None:
                raise ValueError("review resolution cannot include company_id")
            if not self.review_reasons:
                raise ValueError("review resolution requires a reason")
        return self


class CompanyIdentityReviewDraft(_FrozenIdentityDTO):
    identity: CompanyIdentityInput
    candidate_matches: tuple[CompanyIdentityCandidateMatch, ...] = Field(
        default=(), max_length=_MAX_CANDIDATE_MATCHES
    )
    review_reasons: tuple[IdentityReviewReason, ...] = Field(
        min_length=1, max_length=_MAX_REVIEW_REASONS
    )
    observed_at: AwareDatetime

    @field_validator("candidate_matches")
    @classmethod
    def order_candidate_matches(
        cls, value: tuple[CompanyIdentityCandidateMatch, ...]
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        return _canonical_candidate_matches(value)

    @field_validator("review_reasons")
    @classmethod
    def deduplicate_review_reasons(
        cls, value: tuple[IdentityReviewReason, ...]
    ) -> tuple[IdentityReviewReason, ...]:
        return tuple(sorted(set(value), key=lambda reason: reason.value))

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @property
    def evidence(self) -> tuple[PublicEvidenceReference, ...]:
        return self.identity.evidence

    @property
    def stable_identity_hash(self) -> str:
        return sha256(_canonical_identity_bytes(self.identity)).hexdigest()


class IdentityReviewDecisionInput(_FrozenIdentityDTO):
    review_item_id: UUID
    action: IdentityReviewAction
    target_company_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)
    decided_at: AwareDatetime

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if _SAFE_REASON_PATTERN.fullmatch(value) is None:
            raise ValueError("reason is invalid")
        return value

    @field_validator("decided_at")
    @classmethod
    def normalize_decided_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_action_target(self) -> Self:
        requires_target = {
            IdentityReviewAction.LINK_AS_ALIAS,
            IdentityReviewAction.RENAME_CANONICAL,
        }
        if self.action in requires_target and self.target_company_id is None:
            raise ValueError("action requires target_company_id")
        if self.action in {
            IdentityReviewAction.CREATE_NEW,
            IdentityReviewAction.REJECT,
        } and self.target_company_id is not None:
            raise ValueError("action cannot include target_company_id")
        return self


class IdentityReviewRecordSummary(_FrozenIdentityDTO):
    review_item_id: UUID
    stable_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: IdentityReviewStatus
    first_crawl_run_id: UUID
    created: bool


class IdentityReviewApplySummary(_FrozenIdentityDTO):
    applied: int = Field(ge=0)
    replayed: int = Field(ge=0)


class IdentityReviewItem(_FrozenIdentityDTO):
    review_item_id: UUID
    stable_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_crawl_run_id: UUID
    status: IdentityReviewStatus
    draft: CompanyIdentityReviewDraft
    created_at: AwareDatetime
    resolved_at: AwareDatetime | None = None

    @field_validator("created_at", "resolved_at")
    @classmethod
    def normalize_review_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class IdentityAuditFinding(_FrozenIdentityDTO):
    finding_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    code: str = Field(min_length=1, max_length=100)
    severity: IdentityAuditSeverity
    company_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    display_names: tuple[str, ...] = Field(default=(), max_length=100)
    evidence_codes: tuple[str, ...] = Field(default=(), max_length=100)
    recommended_action: str = Field(min_length=1, max_length=100)

    @field_validator("display_names")
    @classmethod
    def canonicalize_display_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        display_by_normalized: dict[str, str] = {}
        for name in value:
            cleaned = _clean_display_name(name)
            normalized = normalize_name(cleaned)
            if (
                not cleaned
                or not normalized
                or len(cleaned) > _MAX_NAME_LENGTH
                or any(
                    character in "<>" or unicodedata.category(character).startswith("C")
                    for character in cleaned
                )
            ):
                raise ValueError("display_names is invalid")
            existing = display_by_normalized.get(normalized)
            if existing is None or cleaned < existing:
                display_by_normalized[normalized] = cleaned
        return tuple(
            display_by_normalized[normalized] for normalized in sorted(display_by_normalized)
        )

    @field_validator("evidence_codes")
    @classmethod
    def canonicalize_evidence_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized_codes: set[str] = set()
        for code in value:
            normalized = _normalized_identifier(code)
            if _EVIDENCE_CODE_PATTERN.fullmatch(normalized) is None:
                raise ValueError("evidence_codes is invalid")
            normalized_codes.add(normalized)
        return tuple(sorted(normalized_codes))


class IdentityAuditReport(_FrozenIdentityDTO):
    findings: tuple[IdentityAuditFinding, ...] = Field(default=(), max_length=10_000)
    scanned_companies: int = Field(ge=0)
    scanned_aliases: int = Field(ge=0)
    scanned_review_items: int = Field(ge=0)
    finding_counts: Mapping[IdentityAuditSeverity, int] = Field(
        default_factory=dict, max_length=len(IdentityAuditSeverity), validate_default=True
    )

    @field_validator("finding_counts")
    @classmethod
    def freeze_finding_counts(
        cls, value: Mapping[IdentityAuditSeverity, int]
    ) -> Mapping[IdentityAuditSeverity, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("finding counts must be nonnegative")
        return MappingProxyType(
            {
                severity: value[severity]
                for severity in IdentityAuditSeverity
                if severity in value
            }
        )

    @field_serializer("finding_counts")
    def serialize_finding_counts(
        self, value: Mapping[IdentityAuditSeverity, int]
    ) -> dict[IdentityAuditSeverity, int]:
        return {
            severity: value[severity]
            for severity in IdentityAuditSeverity
            if severity in value
        }


def _canonical_identity_bytes(identity: CompanyIdentityInput) -> bytes:
    payload = {
        "aliases": list(identity.normalized_aliases),
        "city": identity.city,
        "evidence": [
            {
                "confidence": _canonical_decimal(reference.confidence),
                "evidence_id": reference.evidence_id,
                "provider": reference.provider,
                "url": reference.url,
            }
            for reference in identity.evidence
        ],
        "legal_identifiers": sorted(identity.legal_identifiers),
        "normalized_name": identity.normalized_name,
        "official_website": identity.official_website,
        "recruitment_identity": identity.recruitment_identity,
    }
    return json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonical_candidate_matches(
    value: tuple[CompanyIdentityCandidateMatch, ...],
) -> tuple[CompanyIdentityCandidateMatch, ...]:
    if len({match.company_id for match in value}) != len(value):
        raise ValueError("candidate company ids must be unique")
    return tuple(
        sorted(
            value,
            key=lambda match: (-match.score, match.normalized_name, str(match.company_id)),
        )
    )
