"""Immutable, model-safe contracts for public entry evidence."""

import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ingestion.contracts import DocumentUrl

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:access[_-]?secret|api[_-]?key|authorization|database[_-]?url|password|token)\s*[:=]\s*\S+"
)
_DATABASE_URL = re.compile(r"(?i)\b(?:postgres|postgresql|mysql|sqlite)://\S+")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:\\|/(?:Users|home|tmp)/)")


class FrozenEvidenceDTO(BaseModel):
    """Base class for evidence that must remain replayable and immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelRiskLabel(StrEnum):
    CROSS_HOST = "cross_host"
    GROUP_SUBSIDIARY_CONFLICT = "group_subsidiary_conflict"
    INCONSISTENT_EVIDENCE = "inconsistent_evidence"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    REDIRECT = "redirect"


class ModelEvidenceInput(FrozenEvidenceDTO):
    """The complete, public-only payload permitted to leave this service."""

    source_url: DocumentUrl
    candidate_url: DocumentUrl
    page_title: str = Field(min_length=1, max_length=500)
    visible_summary: str = Field(min_length=1, max_length=4_000)
    anchor_text: str = Field(min_length=1, max_length=500)

    @field_validator("page_title", "visible_summary", "anchor_text")
    @classmethod
    def reject_sensitive_text(cls, value: str) -> str:
        if _SENSITIVE_ASSIGNMENT.search(value) or _DATABASE_URL.search(value) or _LOCAL_PATH.search(value):
            raise ValueError("model input must not contain a sensitive value or local path")
        return value


class PublicEvidence(ModelEvidenceInput):
    """Public webpage evidence retained for later deterministic evaluation."""

    def model_payload(self) -> ModelEvidenceInput:
        return ModelEvidenceInput(**self.model_dump())


class ModelEvidenceAssessment(FrozenEvidenceDTO):
    """Strict structured result returned by the model provider."""

    confidence: Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))]
    rationale: str = Field(min_length=1, max_length=2_000)
    risk_labels: frozenset[ModelRiskLabel] = Field(default_factory=frozenset)


class ModelDisabledError(RuntimeError):
    """Raised before a model request when explicit operator opt-in is absent."""


class DashScopeEvidenceClient(FrozenEvidenceDTO):
    """Model boundary that is disabled unless the caller explicitly enables it."""

    enabled: bool = False
    model_name: str = Field(default="qwen-plus", min_length=1, max_length=100)
    confidence_threshold: Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))] = Decimal(
        "0.90"
    )

    def classify(self, evidence: PublicEvidence) -> ModelEvidenceInput:
        """Validate opt-in and expose only the scrubbed public model payload.

        Transport is deliberately introduced by the operator-flow task; this boundary
        guarantees that any future request can only use this returned payload.
        """

        if not self.enabled:
            raise ModelDisabledError("DashScope evidence classification is disabled")
        return evidence.model_payload()
