"""Deterministic acceptance and audit policy for public entry evidence."""

from collections import defaultdict
from collections.abc import Iterable
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from app.ingestion.contracts import DocumentUrl
from app.manifest.contracts import (
    AtsClassification,
    CandidateDecisionStatus,
    FrozenManifestDTO,
    SourceRole,
)
from app.manifest.evidence import ModelEvidenceAssessment, PublicEvidence


class AuditStratum(FrozenManifestDTO):
    """Independent audit partition for a registered source and ATS platform."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")


class RegisteredSourceValidation(FrozenManifestDTO):
    """Registry fact produced locally from the loaded, fingerprinted snapshot."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    exact_source_url: DocumentUrl
    role: Literal[SourceRole.CANDIDATE_POOL]
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RobotsValidation(FrozenManifestDTO):
    """Successful in-process robots evaluation for the exact candidate URL."""

    exact_candidate_url: DocumentUrl
    allowed: Literal[True]
    evaluator: Literal["robots_policy_v1"] = "robots_policy_v1"


class OwnershipValidation(FrozenManifestDTO):
    """Structured navigation evidence produced by an in-process discoverer."""

    exact_source_url: DocumentUrl
    exact_candidate_url: DocumentUrl
    basis: Literal["official_navigation_anchor", "evidenced_recruitment_url"]
    detail: str = Field(min_length=1, max_length=500)


class IndependentEvidenceValidation(FrozenManifestDTO):
    """Trusted local checks kept separate from every external JSON assertion."""

    registered_source: RegisteredSourceValidation | None = None
    robots: RobotsValidation | None = None
    ownership: OwnershipValidation | None = None
    classification: AtsClassification = Field(
        default_factory=lambda: AtsClassification(platform="unknown")
    )


class EntryEvidenceCandidate(FrozenManifestDTO):
    """Public evidence and completed deterministic checks for one entry candidate."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    evidence: PublicEvidence
    source_registered: bool = False
    robots_approved: bool = False
    ownership_evidence: str | None = Field(default=None, min_length=1, max_length=2_000)
    independent_validation: IndependentEvidenceValidation | None = None

    @property
    def stratum(self) -> AuditStratum:
        return AuditStratum(source_id=self.source_id, platform=self.platform)


class EvidencePolicyDecision(FrozenManifestDTO):
    """Replayable outcome of applying the acceptance rules to one candidate."""

    status: CandidateDecisionStatus
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")


class AuditSample(FrozenManifestDTO):
    """One automatically accepted candidate selected for independent audit."""

    stratum: AuditStratum
    candidate_url: str = Field(min_length=1, max_length=2_000)


class AuditFinding(FrozenManifestDTO):
    """The audit result used to pause unsafe automatic acceptance strata."""

    stratum: AuditStratum
    severe_error: bool


class AuditSampleSelector(FrozenManifestDTO):
    """Stable five-percent sampling without any random process state."""

    sample_rate: Annotated[Decimal, Field(gt=Decimal(0), le=Decimal(1))] = Decimal("0.05")

    def select(self, candidates: Iterable[EntryEvidenceCandidate]) -> tuple[AuditSample, ...]:
        grouped: defaultdict[AuditStratum, list[EntryEvidenceCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate.stratum].append(candidate)

        samples: list[AuditSample] = []
        for stratum, group in sorted(
            grouped.items(), key=lambda item: (item[0].source_id, item[0].platform)
        ):
            sample_size = max(
                1, int((len(group) * self.sample_rate).to_integral_value(ROUND_CEILING))
            )
            for candidate in sorted(group, key=lambda item: self._candidate_rank(item))[
                :sample_size
            ]:
                samples.append(
                    AuditSample(
                        stratum=stratum, candidate_url=str(candidate.evidence.candidate_url)
                    )
                )
        return tuple(samples)

    @staticmethod
    def paused_strata(findings: Iterable[AuditFinding]) -> frozenset[AuditStratum]:
        return frozenset(finding.stratum for finding in findings if finding.severe_error)

    @staticmethod
    def _candidate_rank(candidate: EntryEvidenceCandidate) -> str:
        material = (
            f"{candidate.source_id}\0{candidate.platform}\0{candidate.evidence.candidate_url}"
        )
        return sha256(material.encode("utf-8")).hexdigest()


class EvidenceAcceptancePolicy(FrozenManifestDTO):
    """Require hard public-evidence checks before model-assisted acceptance."""

    confidence_threshold: Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))] = Decimal("0.90")
    paused_strata: frozenset[AuditStratum] = Field(default_factory=frozenset)

    @field_validator("paused_strata")
    @classmethod
    def freeze_paused_strata(cls, value: frozenset[AuditStratum]) -> frozenset[AuditStratum]:
        return frozenset(value)

    def evaluate(
        self,
        candidate: EntryEvidenceCandidate,
        assessment: ModelEvidenceAssessment,
    ) -> EvidencePolicyDecision:
        hard_failure = self._hard_failure(candidate)
        if hard_failure is not None:
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REJECTED, reason_code=hard_failure
            )
        validation = candidate.independent_validation
        if validation is None:
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REVIEW_REQUIRED,
                reason_code="independent_validation_required",
            )
        robots = validation.robots
        ownership = validation.ownership
        if (
            robots is None
            or str(robots.exact_candidate_url) != str(candidate.evidence.candidate_url)
            or ownership is None
            or str(ownership.exact_source_url) != str(candidate.evidence.source_url)
            or str(ownership.exact_candidate_url) != str(candidate.evidence.candidate_url)
            or validation.classification.platform == "unknown"
        ):
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REVIEW_REQUIRED,
                reason_code="independent_validation_required",
            )
        if candidate.stratum in self.paused_strata:
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REVIEW_REQUIRED,
                reason_code="audit_stratum_paused",
            )
        if assessment.confidence < self.confidence_threshold:
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REVIEW_REQUIRED,
                reason_code="model_confidence_below_threshold",
            )
        if assessment.risk_labels:
            return EvidencePolicyDecision(
                status=CandidateDecisionStatus.REVIEW_REQUIRED,
                reason_code="model_risk_flagged",
            )
        return EvidencePolicyDecision(
            status=CandidateDecisionStatus.ACCEPTED, reason_code="accepted"
        )

    @staticmethod
    def _hard_failure(candidate: EntryEvidenceCandidate) -> str | None:
        validation = candidate.independent_validation
        if validation is None:
            return None
        registered = validation.registered_source
        if registered is None:
            return "unregistered_source"
        if registered.source_id != candidate.source_id:
            return "unregistered_source"
        if str(registered.exact_source_url) != str(candidate.evidence.source_url):
            return "unregistered_source"
        if urlsplit(str(candidate.evidence.source_url)).scheme != "https":
            return "source_not_https"
        if urlsplit(str(candidate.evidence.candidate_url)).scheme != "https":
            return "candidate_not_https"
        return None
