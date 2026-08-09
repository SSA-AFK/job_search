"""Deterministic acceptance and audit policy for public entry evidence."""

from collections import defaultdict
from collections.abc import Iterable
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from app.manifest.contracts import CandidateDecisionStatus, FrozenManifestDTO
from app.manifest.evidence import ModelEvidenceAssessment, PublicEvidence


class AuditStratum(FrozenManifestDTO):
    """Independent audit partition for a registered source and ATS platform."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")


class EntryEvidenceCandidate(FrozenManifestDTO):
    """Public evidence and completed deterministic checks for one entry candidate."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    platform: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    evidence: PublicEvidence
    source_registered: bool
    robots_approved: bool
    ownership_evidence: str | None = Field(default=None, min_length=1, max_length=2_000)

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
        if not candidate.source_registered:
            return "unregistered_source"
        if not candidate.robots_approved:
            return "robots_disallowed"
        if urlsplit(str(candidate.evidence.source_url)).scheme != "https":
            return "source_not_https"
        if urlsplit(str(candidate.evidence.candidate_url)).scheme != "https":
            return "candidate_not_https"
        if candidate.ownership_evidence is None:
            return "ownership_unverified"
        return None
