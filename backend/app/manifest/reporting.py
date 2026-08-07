"""Database-backed reporting for a frozen manifest and its entry census."""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field, field_validator
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.manifest.contracts import AtsCensus, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)

_RATE_QUANTUM = Decimal("0.0001")


class ManifestReportError(ValueError):
    """Raised when persisted manifest census data is inconsistent."""


class ManifestCoverageReport(AtsCensus):
    """Immutable, explicitly denominated manifest discovery census."""

    code_commit: str
    config_fingerprint: str
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

        grouped_statuses = self.session.execute(
            select(
                EntryDiscoveryObservation.status,
                func.count(EntryDiscoveryObservation.id),
            )
            .where(EntryDiscoveryObservation.manifest_version == manifest_version)
            .group_by(EntryDiscoveryObservation.status)
        ).all()
        observed_status_counts: dict[DiscoveryStatus, int] = {
            status: int(count) for status, count in grouped_statuses
        }
        status_counts = {
            status: int(observed_status_counts.get(status, 0))
            for status in DiscoveryStatus
        }
        discovered_companies = self.session.scalar(
            select(func.count(distinct(EntryDiscoveryObservation.company_id))).where(
                EntryDiscoveryObservation.manifest_version == manifest_version
            )
        )
        assert discovered_companies is not None

        grouped_platforms = self.session.execute(
            select(
                EntryDiscoveryObservation.platform,
                func.count(EntryDiscoveryObservation.id),
            )
            .where(
                EntryDiscoveryObservation.manifest_version == manifest_version,
                EntryDiscoveryObservation.status == DiscoveryStatus.ACCEPTED,
                EntryDiscoveryObservation.job_entry_id.is_not(None),
            )
            .group_by(EntryDiscoveryObservation.platform)
            .order_by(EntryDiscoveryObservation.platform)
        ).all()
        platform_entry_counts = {
            "unknown" if platform is None else platform: int(count)
            for platform, count in grouped_platforms
        }
        accepted_entries = sum(platform_entry_counts.values())
        entry_companies = self.session.scalar(
            select(func.count(distinct(EntryDiscoveryObservation.company_id))).where(
                EntryDiscoveryObservation.manifest_version == manifest_version,
                EntryDiscoveryObservation.status == DiscoveryStatus.ACCEPTED,
                EntryDiscoveryObservation.job_entry_id.is_not(None),
            )
        )
        assert entry_companies is not None
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
