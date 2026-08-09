"""Database-backed reporting for a frozen manifest and its entry census."""

import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.manifest.contracts import AtsCensus, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
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

        observations = tuple(
            self.session.scalars(
                select(EntryDiscoveryObservation)
                .where(
                    EntryDiscoveryObservation.manifest_version == manifest_version
                )
                .order_by(EntryDiscoveryObservation.id)
            )
        )
        observed_status_counts = Counter(
            observation.status for observation in observations
        )
        status_counts = {
            status: observed_status_counts.get(status, 0)
            for status in DiscoveryStatus
        }
        discovered_companies = len(
            {observation.company_id for observation in observations}
        )

        accepted_observations = tuple(
            observation
            for observation in observations
            if observation.status is DiscoveryStatus.ACCEPTED
            and observation.job_entry_id is not None
        )
        platform_counts = Counter(
            observation.platform or "unknown"
            for observation in accepted_observations
        )
        platform_entry_counts = dict(sorted(platform_counts.items()))
        accepted_entries = len(accepted_observations)
        entry_companies = len(
            {observation.company_id for observation in accepted_observations}
        )
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


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)
