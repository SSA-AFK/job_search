from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CrawlRun,
    JobCollectionSnapshot,
    JobEntry,
    JobSnapshotStatus,
    RecruitingStatus,
)


@dataclass(frozen=True)
class RecruitingCoverage:
    status: RecruitingStatus
    active_job_count: int | None
    last_checked_at: datetime | None
    last_successful_at: datetime | None
    freshness: str
    reason_code: str | None
    primary_entry_url: str | None = None
    primary_entry_platform: str | None = None


class RecruitingCoverageService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build(self, company_id: object, *, now: datetime) -> RecruitingCoverage:
        now = now.astimezone(UTC)
        entries = list(self.session.scalars(select(JobEntry).where(JobEntry.company_id == company_id)))
        if not entries:
            last_discovery_at = self.session.scalar(
                select(CrawlRun.completed_at)
                .where(CrawlRun.company_id == company_id, CrawlRun.completed_at.is_not(None))
                .order_by(CrawlRun.completed_at.desc())
                .limit(1)
            )
            return RecruitingCoverage(
                RecruitingStatus.ENTRY_DISCOVERY_PENDING,
                None,
                last_discovery_at,
                None,
                "unknown",
                "ats_entry_discovery_pending" if last_discovery_at else None,
            )
        primary_entry = next((entry for entry in entries if entry.is_primary), None)
        primary_url = primary_entry.url if primary_entry is not None else None
        primary_platform = primary_entry.platform if primary_entry is not None else None
        snapshots = list(self.session.scalars(select(JobCollectionSnapshot).where(JobCollectionSnapshot.job_entry_id.in_([entry.id for entry in entries])).order_by(JobCollectionSnapshot.completed_at.desc(), JobCollectionSnapshot.created_at.desc())))
        latest = snapshots[0] if snapshots else None
        last_successful = next((snapshot for snapshot in snapshots if snapshot.status is JobSnapshotStatus.SUCCEEDED and snapshot.pagination_complete), None)
        if latest is not None and latest.status is not JobSnapshotStatus.SUCCEEDED:
            return RecruitingCoverage(RecruitingStatus.COLLECTION_INCOMPLETE, None, latest.completed_at, last_successful.completed_at if last_successful else None, "unknown", "temporary_source_error", primary_url, primary_platform)
        if last_successful is None:
            return RecruitingCoverage(RecruitingStatus.COLLECTION_INCOMPLETE, latest.completed_at if latest else None, None, "unknown", "needs_review", primary_url, primary_platform)
        fresh = last_successful.completed_at >= now - timedelta(hours=24)
        if not fresh:
            return RecruitingCoverage(RecruitingStatus.STALE, None, latest.completed_at if latest else None, last_successful.completed_at, "stale", None, primary_url, primary_platform)
        if last_successful.empty_confirmed:
            return RecruitingCoverage(RecruitingStatus.EMPTY_CONFIRMED, 0, latest.completed_at if latest else None, last_successful.completed_at, "fresh", None, primary_url, primary_platform)
        return RecruitingCoverage(RecruitingStatus.ACTIVE_ROLES, last_successful.observed_count, latest.completed_at if latest else None, last_successful.completed_at, "fresh", None, primary_url, primary_platform)
