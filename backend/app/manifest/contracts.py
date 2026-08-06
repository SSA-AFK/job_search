"""Validated immutable contracts for the Gate 1 manifest domain."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ingestion.contracts import DocumentUrl


class AiCategory(StrEnum):
    FOUNDATION_MODELS = "foundation_models"
    AI_CLOUD_MODEL_PLATFORMS = "ai_cloud_model_platforms"
    AI_CHIPS_COMPUTE = "ai_chips_compute"
    AUTONOMOUS_DRIVING_TRANSPORT = "autonomous_driving_transport"
    ROBOTICS_EMBODIED_AI = "robotics_embodied_ai"
    COMPUTER_VISION_IMAGING = "computer_vision_imaging"
    SPEECH_LANGUAGE_TECHNOLOGY = "speech_language_technology"
    ENTERPRISE_VERTICAL_AI = "enterprise_vertical_ai"
    DATA_INFRASTRUCTURE_MLOPS = "data_infrastructure_mlops"


class ConfidenceTier(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateDecisionStatus(StrEnum):
    REVIEW_REQUIRED = "review_required"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class DiscoveryStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    FAILED = "failed"


class SourceClass(StrEnum):
    GOVERNMENT = "government"
    EXCHANGE = "exchange"
    ASSOCIATION = "association"
    INDUSTRIAL_PARK = "industrial_park"
    OFFICIAL_COMPANY_SITE = "official_company_site"
    AUTHORIZED_API = "authorized_api"


class SourceRole(StrEnum):
    CANDIDATE_POOL = "candidate_pool"
    ENTRY_DISCOVERY_FALLBACK = "entry_discovery_fallback"


class FrozenManifestDTO(BaseModel):
    """Base class for untrusted manifest data that must not be mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRegistryEntry(FrozenManifestDTO):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    name: str = Field(min_length=1, max_length=100)
    base_url: DocumentUrl
    source_class: SourceClass
    authorization_basis: str = Field(min_length=10, max_length=500)
    robots_policy: Literal["required", "api_contract"]
    roles: frozenset[SourceRole] = Field(min_length=1)
    requests_per_second: Decimal = Field(gt=0, le=Decimal("1.0"))
    rehearsal_request_budget: int | None = Field(default=None, ge=1, le=100_000)
    enabled: bool = True


class SourceRegistry(FrozenManifestDTO):
    entries: tuple[SourceRegistryEntry, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_entries(self) -> "SourceRegistry":
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        if any(not entry.enabled for entry in self.entries):
            raise ValueError("source registry cannot contain disabled entries")
        return self

    def require(self, source_id: str) -> SourceRegistryEntry:
        for entry in self.entries:
            if entry.id == source_id:
                return entry
        raise KeyError(source_id)


class CandidateFactInput(FrozenManifestDTO):
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    source_url: DocumentUrl
    retrieved_at: AwareDatetime
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(
        default=(), max_length=100
    )
    primary_category: AiCategory
    official_website: DocumentUrl | None = None
    recruitment_url: DocumentUrl | None = None
    evidence_summary: str = Field(min_length=1, max_length=2_000)

    @field_validator("retrieved_at")
    @classmethod
    def normalize_retrieved_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ReviewDecisionInput(FrozenManifestDTO):
    stable_evidence_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: ReviewAction
    resulting_status: CandidateDecisionStatus
    resolved_company_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2_000)
    decided_at: AwareDatetime

    @field_validator("decided_at")
    @classmethod
    def normalize_decided_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ManifestCompany(FrozenManifestDTO):
    company_id: UUID
    canonical_name: str = Field(min_length=1, max_length=200)
    primary_category: AiCategory
    official_website: DocumentUrl | None = None
    recruitment_url: DocumentUrl | None = None


class ManifestMemberData(FrozenManifestDTO):
    position: int = Field(ge=1, le=1_000)
    company: ManifestCompany


class AtsClassification(FrozenManifestDTO):
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    requires_rendering: bool = False


class EntryDiscoveryResult(FrozenManifestDTO):
    status: DiscoveryStatus
    method: str = Field(min_length=1, max_length=100)
    candidate_url: DocumentUrl | None = None
    normalized_url: DocumentUrl | None = None
    source_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,49}$")
    ownership_evidence: str | None = Field(default=None, min_length=1, max_length=2_000)
    classification: AtsClassification | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,99}$")


class RecordDiscoveryCommand(FrozenManifestDTO):
    manifest_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    company_id: UUID
    result: EntryDiscoveryResult
    observed_at: AwareDatetime

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AtsCensus(FrozenManifestDTO):
    manifest_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_companies: int = Field(ge=0, le=1_000)
    accepted_entries: int = Field(ge=0, le=100_000)
    platform_entry_counts: dict[str, int] = Field(default_factory=dict, max_length=100)
    status_counts: dict[DiscoveryStatus, int] = Field(default_factory=dict, max_length=100)
