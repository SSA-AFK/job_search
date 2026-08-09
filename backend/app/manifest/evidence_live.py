"""Bounded live model evaluation for public entry evidence."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from uuid import UUID

from pydantic import Field, StrictBool, ValidationError, field_validator, model_validator

from app.ingestion.errors import ExtractionError
from app.ingestion.extraction.client import LlmClient
from app.manifest.contracts import CandidateDecisionStatus, FrozenManifestDTO
from app.manifest.evidence import ModelEvidenceAssessment, PublicEvidence
from app.manifest.evidence_policy import (
    EntryEvidenceCandidate,
    EvidenceAcceptancePolicy,
    EvidencePolicyDecision,
)

MAX_LIVE_EVIDENCE_ITEMS = 20
_SENSITIVE_PUBLIC_DIAGNOSTIC = re.compile(
    r"(?i)(?:\b(?:access[_-]?secret|api[_-]?key|authorization|database[_-]?url|password|token)\s*[:=]\s*\S+|\b(?:postgres|postgresql|mysql|sqlite)://\S+|(?:[A-Za-z]:\\|/(?:Users|home|tmp)/))"
)


class EvidenceInputError(ValueError):
    """Sanitized failure raised for an invalid external evidence document."""


class EvidenceCandidateInput(FrozenManifestDTO):
    """Strict public candidate plus local deterministic rule results."""

    company_id: UUID
    source_url: str = Field(min_length=1, max_length=2_000)
    candidate_url: str = Field(min_length=1, max_length=2_000)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4_000)
    anchor: str = Field(min_length=1, max_length=500)
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    source_registered: StrictBool
    robots_approved: StrictBool
    ownership_evidence: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("ownership_evidence")
    @classmethod
    def require_public_ownership_diagnostics(cls, value: str | None) -> str | None:
        if value is not None and _SENSITIVE_PUBLIC_DIAGNOSTIC.search(value):
            raise ValueError("ownership evidence must contain only public diagnostics")
        return value

    @field_validator("platform")
    @classmethod
    def require_known_platform(cls, value: str) -> str:
        if value == "unknown":
            raise ValueError("evidence input requires a known platform")
        return value

    @model_validator(mode="after")
    def require_public_model_safe_evidence(self) -> EvidenceCandidateInput:
        self._public_evidence()
        return self

    def _public_evidence(self) -> PublicEvidence:
        return PublicEvidence.model_validate(
            {
                "source_url": self.source_url,
                "candidate_url": self.candidate_url,
                "page_title": self.title,
                "visible_summary": self.summary,
                "anchor_text": self.anchor,
            }
        )

    def to_policy_candidate(self) -> EntryEvidenceCandidate:
        return EntryEvidenceCandidate(
            source_id=self.source_id,
            platform=self.platform,
            evidence=self._public_evidence(),
            source_registered=self.source_registered,
            robots_approved=self.robots_approved,
            ownership_evidence=self.ownership_evidence,
        )


class EvidenceBatchGate(FrozenManifestDTO):
    """Every operator and configuration switch required for model execution."""

    cli_live: StrictBool
    cli_model: StrictBool
    config_live: StrictBool
    config_model: StrictBool
    dry_run: StrictBool = False

    @property
    def enabled(self) -> bool:
        return self.cli_live and self.cli_model and self.config_live and self.config_model


class EvidenceEvaluation(FrozenManifestDTO):
    """Sanitized local decision; model responses and rationales are never retained."""

    input: EvidenceCandidateInput
    decision: EvidencePolicyDecision
    model_called: bool


def parse_evidence_candidates(content: bytes) -> tuple[EvidenceCandidateInput, ...]:
    """Parse a strict, finite external JSON list without returning raw errors."""

    try:
        document = json.loads(content)
        if not isinstance(document, list) or not document:
            raise TypeError("evidence input must be a non-empty list")
        if len(document) > MAX_LIVE_EVIDENCE_ITEMS:
            raise EvidenceInputError("evidence input exceeds item limit")
        candidates = tuple(EvidenceCandidateInput.model_validate(item) for item in document)
        company_ids = {candidate.company_id for candidate in candidates}
        if len(company_ids) != len(candidates):
            raise TypeError("evidence input contains duplicate companies")
        candidate_urls = {candidate.candidate_url for candidate in candidates}
        if len(candidate_urls) != len(candidates):
            raise TypeError("evidence input contains duplicate candidate URLs")
        for candidate in candidates:
            candidate.to_policy_candidate()
        return candidates
    except EvidenceInputError:
        raise
    except (TypeError, ValueError, ValidationError) as error:
        raise EvidenceInputError("evidence input is invalid") from error


def _model_prompt(evidence: PublicEvidence) -> str:
    payload = {
        "instruction": (
            "Assess whether the public candidate URL is the company's official recruitment "
            "entry. Return only JSON with confidence (0..1), rationale, and risk_labels."
        ),
        "evidence": evidence.model_payload().model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _pre_model_decision(
    policy: EvidenceAcceptancePolicy, candidate: EntryEvidenceCandidate
) -> EvidencePolicyDecision | None:
    precheck = policy.evaluate(
        candidate,
        ModelEvidenceAssessment(
            confidence=Decimal(1),
            rationale="Deterministic pre-model policy check.",
            risk_labels=frozenset(),
        ),
    )
    if precheck.status is CandidateDecisionStatus.REJECTED:
        return precheck
    if precheck.reason_code == "audit_stratum_paused":
        return precheck
    return None


async def evaluate_evidence_batch(
    inputs: tuple[EvidenceCandidateInput, ...],
    *,
    llm_client: LlmClient,
    policy: EvidenceAcceptancePolicy,
    gate: EvidenceBatchGate,
) -> tuple[EvidenceEvaluation, ...]:
    """Evaluate one bounded batch while guaranteeing disabled paths make no calls."""

    if gate.dry_run:
        return ()
    if not gate.enabled:
        raise PermissionError("live evidence execution is disabled")
    if len(inputs) > MAX_LIVE_EVIDENCE_ITEMS:
        raise EvidenceInputError("evidence input exceeds item limit")

    evaluations: list[EvidenceEvaluation] = []
    for item in inputs:
        candidate = item.to_policy_candidate()
        pre_model = _pre_model_decision(policy, candidate)
        if pre_model is not None:
            evaluations.append(
                EvidenceEvaluation(input=item, decision=pre_model, model_called=False)
            )
            continue

        try:
            raw_assessment = await llm_client.complete(_model_prompt(candidate.evidence))
            assessment = ModelEvidenceAssessment.model_validate_json(raw_assessment)
        except ExtractionError as error:
            reason_code = (
                "model_unavailable" if error.code == "model_unavailable" else "model_invalid_output"
            )
            decision = EvidencePolicyDecision(
                status=CandidateDecisionStatus.REJECTED,
                reason_code=reason_code,
            )
        except (TypeError, ValueError, ValidationError):
            decision = EvidencePolicyDecision(
                status=CandidateDecisionStatus.REJECTED,
                reason_code="model_invalid_output",
            )
        else:
            decision = policy.evaluate(candidate, assessment)
        evaluations.append(EvidenceEvaluation(input=item, decision=decision, model_called=True))
    return tuple(evaluations)
