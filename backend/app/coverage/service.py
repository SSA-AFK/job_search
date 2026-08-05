"""Database-backed aggregate coverage reporting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import CoverageReport
from app.models import Company, JobCollectionSnapshot, JobEntry, JobEntryStatus, JobSnapshotStatus

_SECONDS_PER_HOUR = 3_600
_MAX_SQL_INTEGER = 2_147_483_647


class CoverageReportService:
    """Build internal company-level job-list coverage metrics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        as_of: datetime,
        refresh_window: timedelta = timedelta(hours=24),
    ) -> CoverageReport:
        as_of_utc = _require_aware_utc(as_of)
        refresh_window_hours = _whole_positive_hours(refresh_window)
        window_start = as_of_utc - refresh_window

        qualifying_snapshots = (
            select(
                JobEntry.company_id.label("company_id"),
                JobCollectionSnapshot.empty_confirmed.label("empty_confirmed"),
                func.row_number()
                .over(
                    partition_by=JobCollectionSnapshot.job_entry_id,
                    order_by=(
                        JobCollectionSnapshot.completed_at.desc(),
                        JobCollectionSnapshot.created_at.desc(),
                        JobCollectionSnapshot.id.desc(),
                    ),
                )
                .label("snapshot_rank"),
            )
            .join(JobEntry, JobEntry.id == JobCollectionSnapshot.job_entry_id)
            .where(
                JobEntry.status == JobEntryStatus.ACTIVE,
                JobCollectionSnapshot.status == JobSnapshotStatus.SUCCEEDED,
                JobCollectionSnapshot.pagination_complete.is_(True),
                JobCollectionSnapshot.completed_at >= window_start,
                JobCollectionSnapshot.completed_at <= as_of_utc,
            )
            .subquery("qualifying_snapshots")
        )
        latest_qualifying = (
            select(
                qualifying_snapshots.c.company_id,
                qualifying_snapshots.c.empty_confirmed,
            )
            .where(qualifying_snapshots.c.snapshot_rank == 1)
            .subquery("latest_qualifying")
        )

        target_count = select(func.count(Company.id)).scalar_subquery()
        active_entry_count = (
            select(func.count(func.distinct(JobEntry.company_id)))
            .where(JobEntry.status == JobEntryStatus.ACTIVE)
            .scalar_subquery()
        )
        enumerated_count = select(
            func.count(func.distinct(latest_qualifying.c.company_id))
        ).scalar_subquery()
        empty_count = (
            select(func.count(func.distinct(latest_qualifying.c.company_id)))
            .where(latest_qualifying.c.empty_confirmed.is_(True))
            .scalar_subquery()
        )
        row = self.session.execute(
            select(
                target_count.label("target_companies"),
                active_entry_count.label("active_entry_companies"),
                enumerated_count.label("recently_enumerated_companies"),
                enumerated_count.label("complete_list_companies"),
                empty_count.label("confirmed_empty_companies"),
            )
        ).one()

        return CoverageReport(
            as_of=as_of_utc,
            refresh_window_hours=refresh_window_hours,
            target_companies=row.target_companies,
            active_entry_companies=row.active_entry_companies,
            recently_enumerated_companies=row.recently_enumerated_companies,
            complete_list_companies=row.complete_list_companies,
            confirmed_empty_companies=row.confirmed_empty_companies,
            entry_coverage_rate=_rate(row.active_entry_companies, row.target_companies),
            enumeration_rate=_rate(
                row.recently_enumerated_companies, row.active_entry_companies
            ),
            completeness_rate=_rate(
                row.complete_list_companies, row.recently_enumerated_companies
            ),
            refresh_slo_rate=_rate(row.recently_enumerated_companies, row.target_companies),
        )


def _require_aware_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)


def _whole_positive_hours(value: timedelta) -> int:
    total_seconds = value.total_seconds()
    hours, remainder = divmod(total_seconds, _SECONDS_PER_HOUR)
    if remainder != 0 or hours < 1 or hours > _MAX_SQL_INTEGER:
        raise ValueError("refresh_window must be a positive whole number of hours")
    return int(hours)


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)
