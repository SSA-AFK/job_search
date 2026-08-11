"""Database-backed reporting for a frozen manifest and its entry census."""

import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.manifest.contracts import AtsCensus, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    EntryDiscoveryRound,
    EntryEvidenceAuditFinding,
    EntryEvidenceAuditSample,
    EntryEvidenceQuarantine,
)

_RATE_QUANTUM = Decimal("0.0001")
_CODE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{7,40}")


class ManifestReportError(ValueError):
    """Raised when persisted manifest census data is inconsistent."""


class ManifestCoverageReport(AtsCensus):
    """Immutable, explicitly denominated manifest discovery census."""

    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovered_companies: int = Field(ge=0)
    discovery_company_denominator: int = Field(ge=0)
    discovery_coverage_rate: Decimal | None
    entry_companies: int = Field(ge=0)
    entry_company_denominator: int = Field(ge=0)
    entry_coverage_rate: Decimal | None
    entries_per_company: Decimal | None
    platform_entry_denominator: int = Field(ge=0)
    self_hosted_entries: int = Field(ge=0)
    self_hosted_rate: Decimal | None

    @field_validator(
        "discovery_coverage_rate",
        "entry_coverage_rate",
        "entries_per_company",
        "self_hosted_rate",
    )
    @classmethod
    def quantize_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


class _FrozenReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PausedStratumReport(_FrozenReport):
    source_id: str
    platform: str


class DiscoveryRoundReport(_FrozenReport):
    round_id: UUID
    name: str
    predecessor_round_id: UUID | None
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    company_denominator: int = Field(ge=0)
    processed_companies: int = Field(ge=0)
    coverage_rate: Decimal | None
    status_counts: dict[DiscoveryStatus, int]
    accepted_entries: int = Field(ge=0)
    entry_companies: int = Field(ge=0)
    audit_samples: int = Field(ge=0)
    audited_samples: int = Field(ge=0)
    severe_errors: int = Field(ge=0)
    paused_strata: tuple[PausedStratumReport, ...]

    @field_validator("coverage_rate")
    @classmethod
    def quantize_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


class RoundAwareManifestReport(_FrozenReport):
    aggregate: ManifestCoverageReport
    rounds: tuple[DiscoveryRoundReport, ...]


class ManifestReportService:
    """Build an observation-only census without inferring collection completeness."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        manifest_version: str,
        *,
        code_commit: str,
        config_fingerprint: str,
    ) -> ManifestCoverageReport:
        if _CODE_COMMIT_PATTERN.fullmatch(code_commit) is None:
            raise ManifestReportError("code commit is invalid")
        manifest = self.session.get(CompanyManifest, manifest_version)
        if manifest is None:
            raise ManifestReportError("manifest does not exist")

        manifest_companies = self.session.scalar(
            select(func.count())
            .select_from(CompanyManifestMember)
            .where(CompanyManifestMember.manifest_version == manifest_version)
        )
        assert manifest_companies is not None
        if manifest_companies != manifest.member_count:
            raise ManifestReportError("manifest member count is inconsistent")

        stored_observations = tuple(
            self.session.scalars(
                select(EntryDiscoveryObservation)
                .where(EntryDiscoveryObservation.manifest_version == manifest_version)
                .order_by(EntryDiscoveryObservation.id)
            )
        )
        quarantined_ids = _quarantined_observation_ids(self.session, stored_observations)
        observations = tuple(
            observation
            for observation in stored_observations
            if observation.id not in quarantined_ids
        )
        observed_status_counts = Counter(observation.status for observation in observations)
        status_counts = {
            status: observed_status_counts.get(status, 0) for status in DiscoveryStatus
        }
        discovered_companies = len({observation.company_id for observation in observations})

        accepted_observations = tuple(
            observation
            for observation in observations
            if observation.status is DiscoveryStatus.ACCEPTED
            and observation.job_entry_id is not None
        )
        platform_counts = Counter(
            observation.platform or "unknown" for observation in accepted_observations
        )
        platform_entry_counts = dict(sorted(platform_counts.items()))
        accepted_entries = len(accepted_observations)
        entry_companies = len({observation.company_id for observation in accepted_observations})
        self_hosted_entries = platform_entry_counts.get("self_hosted", 0)

        return ManifestCoverageReport(
            manifest_version=manifest_version,
            code_commit=code_commit,
            config_fingerprint=config_fingerprint,
            manifest_companies=manifest_companies,
            discovered_companies=discovered_companies,
            discovery_company_denominator=manifest_companies,
            discovery_coverage_rate=_rate(discovered_companies, manifest_companies),
            status_counts=status_counts,
            accepted_entries=accepted_entries,
            entry_companies=entry_companies,
            entry_company_denominator=manifest_companies,
            entry_coverage_rate=_rate(entry_companies, manifest_companies),
            entries_per_company=_rate(accepted_entries, manifest_companies),
            platform_entry_counts=platform_entry_counts,
            platform_entry_denominator=accepted_entries,
            self_hosted_entries=self_hosted_entries,
            self_hosted_rate=_rate(self_hosted_entries, accepted_entries),
        )

    def build_round_aware(
        self,
        manifest_version: str,
        *,
        code_commit: str,
        config_fingerprint: str,
    ) -> RoundAwareManifestReport:
        """Return the legacy aggregate plus independently denominated round censuses."""

        aggregate = self.build(
            manifest_version,
            code_commit=code_commit,
            config_fingerprint=config_fingerprint,
        )
        rounds = tuple(
            self.session.scalars(
                select(EntryDiscoveryRound)
                .where(EntryDiscoveryRound.manifest_version == manifest_version)
                .order_by(EntryDiscoveryRound.started_at, EntryDiscoveryRound.id)
            )
        )
        if not rounds:
            return RoundAwareManifestReport(aggregate=aggregate, rounds=())

        round_ids = tuple(discovery_round.id for discovery_round in rounds)
        observations = tuple(
            self.session.scalars(
                select(EntryDiscoveryObservation)
                .where(EntryDiscoveryObservation.discovery_round_id.in_(round_ids))
                .order_by(EntryDiscoveryObservation.id)
            )
        )
        quarantined_ids = _quarantined_observation_ids(self.session, observations)
        observations = tuple(
            observation for observation in observations if observation.id not in quarantined_ids
        )
        observations_by_round: defaultdict[UUID, list[EntryDiscoveryObservation]] = defaultdict(
            list
        )
        for observation in observations:
            assert observation.discovery_round_id is not None
            observations_by_round[observation.discovery_round_id].append(observation)

        samples = tuple(
            self.session.scalars(
                select(EntryEvidenceAuditSample)
                .where(EntryEvidenceAuditSample.discovery_round_id.in_(round_ids))
                .order_by(EntryEvidenceAuditSample.id)
            )
        )
        samples_by_round: defaultdict[UUID, list[EntryEvidenceAuditSample]] = defaultdict(list)
        sample_round_by_id: dict[UUID, UUID] = {}
        for sample in samples:
            samples_by_round[sample.discovery_round_id].append(sample)
            sample_round_by_id[sample.id] = sample.discovery_round_id

        findings = (
            tuple(
                self.session.scalars(
                    select(EntryEvidenceAuditFinding)
                    .where(EntryEvidenceAuditFinding.audit_sample_id.in_(tuple(sample_round_by_id)))
                    .order_by(EntryEvidenceAuditFinding.id)
                )
            )
            if sample_round_by_id
            else ()
        )
        findings_by_round: defaultdict[UUID, list[EntryEvidenceAuditFinding]] = defaultdict(list)
        sample_by_id = {sample.id: sample for sample in samples}
        for finding in findings:
            findings_by_round[sample_round_by_id[finding.audit_sample_id]].append(finding)

        round_reports: list[DiscoveryRoundReport] = []
        for discovery_round in rounds:
            round_observations = observations_by_round[discovery_round.id]
            status_counter = Counter(observation.status for observation in round_observations)
            status_counts = {status: status_counter.get(status, 0) for status in DiscoveryStatus}
            accepted = tuple(
                observation
                for observation in round_observations
                if observation.status is DiscoveryStatus.ACCEPTED
                and observation.job_entry_id is not None
            )
            round_findings = findings_by_round[discovery_round.id]
            severe_findings = tuple(finding for finding in round_findings if finding.severe_error)
            paused = {
                (
                    sample_by_id[finding.audit_sample_id].source_id,
                    sample_by_id[finding.audit_sample_id].platform,
                )
                for finding in severe_findings
            }
            processed_companies = len(
                {observation.company_id for observation in round_observations}
            )
            round_reports.append(
                DiscoveryRoundReport(
                    round_id=discovery_round.id,
                    name=discovery_round.name,
                    predecessor_round_id=discovery_round.predecessor_round_id,
                    config_fingerprint=discovery_round.config_fingerprint,
                    model_fingerprint=discovery_round.model_fingerprint,
                    started_at=discovery_round.started_at,
                    company_denominator=aggregate.manifest_companies,
                    processed_companies=processed_companies,
                    coverage_rate=_rate(processed_companies, aggregate.manifest_companies),
                    status_counts=status_counts,
                    accepted_entries=len(accepted),
                    entry_companies=len({observation.company_id for observation in accepted}),
                    audit_samples=len(samples_by_round[discovery_round.id]),
                    audited_samples=len(round_findings),
                    severe_errors=len(severe_findings),
                    paused_strata=tuple(
                        PausedStratumReport(source_id=source_id, platform=platform)
                        for source_id, platform in sorted(paused)
                    ),
                )
            )
        return RoundAwareManifestReport(
            aggregate=aggregate,
            rounds=tuple(round_reports),
        )


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def _quarantined_observation_ids(
    session: Session,
    observations: tuple[EntryDiscoveryObservation, ...],
) -> frozenset[UUID]:
    observation_ids = tuple(observation.id for observation in observations)
    if not observation_ids:
        return frozenset()
    return frozenset(
        session.scalars(
            select(EntryEvidenceQuarantine.observation_id).where(
                EntryEvidenceQuarantine.observation_id.in_(observation_ids)
            )
        )
    )
