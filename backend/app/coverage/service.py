"""Database-backed aggregate coverage reporting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import CoverageReport
from app.models import Company, JobCollectionSnapshot, JobEntry, JobEntryStatus, JobSnapshotStatus

_SECONDS_PER_HOUR = 3_600
_MAX_SQL_INTEGER = 2_147_483_647


class CoverageWindowError(ValueError):
    """A report time boundary cannot be represented safely."""


class CoverageReportService:
    """Build internal company-level job-list coverage metrics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        as_of: datetime,
        refresh_window: timedelta = timedelta(hours=24),
    ) -> CoverageReport:
        as_of_utc, refresh_window_hours, window_start = validate_coverage_window(
            as_of,
            refresh_window,
        )

        qualifying_snapshots = (
            select(
                JobEntry.company_id.label("company_id"),
                JobEntry.id.label("entry_id"),
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
            .cte("qualifying_snapshots")
        )
        latest_qualifying = (
            select(
                qualifying_snapshots.c.company_id,
                qualifying_snapshots.c.entry_id,
                qualifying_snapshots.c.empty_confirmed,
            )
            .where(qualifying_snapshots.c.snapshot_rank == 1)
            .cte("latest_qualifying")
        )
        active_entries = (
            select(
                JobEntry.company_id.label("company_id"),
                func.count(JobEntry.id).label("active_entry_count"),
            )
            .where(JobEntry.status == JobEntryStatus.ACTIVE)
            .group_by(JobEntry.company_id)
            .cte("active_entries")
        )
        company_qualifying = (
            select(
                latest_qualifying.c.company_id,
                func.count(latest_qualifying.c.entry_id).label(
                    "qualified_entry_count"
                ),
                func.count(
                    case(
                        (
                            latest_qualifying.c.empty_confirmed.is_(False),
                            latest_qualifying.c.entry_id,
                        )
                    )
                ).label("nonempty_entry_count"),
            )
            .group_by(latest_qualifying.c.company_id)
            .cte("company_qualifying")
        )

        target_count = select(func.count(Company.id)).scalar_subquery()
        active_entry_count = (
            select(func.count(active_entries.c.company_id))
            .scalar_subquery()
        )
        snapshot_counts = (
            select(
                func.count(company_qualifying.c.company_id).label(
                    "enumerated_companies"
                ),
                func.count(
                    case(
                        (
                            (
                                company_qualifying.c.qualified_entry_count
                                == active_entries.c.active_entry_count
                            )
                            & (company_qualifying.c.nonempty_entry_count == 0),
                            company_qualifying.c.company_id,
                        )
                    )
                ).label("confirmed_empty_companies"),
            )
            .select_from(
                company_qualifying.join(
                    active_entries,
                    active_entries.c.company_id == company_qualifying.c.company_id,
                )
            )
            .cte("snapshot_counts")
        )
        report_statement = select(
            target_count.label("target_companies"),
            active_entry_count.label("active_entry_companies"),
            snapshot_counts.c.enumerated_companies.label(
                "recently_enumerated_companies"
            ),
            snapshot_counts.c.enumerated_companies.label("complete_list_companies"),
            snapshot_counts.c.confirmed_empty_companies,
        ).select_from(snapshot_counts)
        row = self.session.execute(report_statement).one()

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


def validate_coverage_window(
    as_of: datetime,
    refresh_window: timedelta,
) -> tuple[datetime, int, datetime]:
    """Validate and normalize report bounds before any database access."""

    as_of_utc = normalize_aware_utc(as_of)
    refresh_window_hours = _whole_positive_hours(refresh_window)
    try:
        window_start = as_of_utc - refresh_window
    except OverflowError:
        raise CoverageWindowError(
            "refresh window is outside the supported datetime range"
        ) from None
    return as_of_utc, refresh_window_hours, window_start


def normalize_aware_utc(value: datetime) -> datetime:
    """Return an aware datetime in UTC without leaking arithmetic overflow."""

    if value.utcoffset() is None:
        raise CoverageWindowError("as_of must be timezone-aware")
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise CoverageWindowError("as_of is outside the supported datetime range") from None


def _whole_positive_hours(value: timedelta) -> int:
    total_seconds = value.total_seconds()
    hours, remainder = divmod(total_seconds, _SECONDS_PER_HOUR)
    if remainder != 0 or hours < 1 or hours > _MAX_SQL_INTEGER:
        raise CoverageWindowError(
            "refresh_window must be a positive whole number of hours"
        )
    return int(hours)


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)
