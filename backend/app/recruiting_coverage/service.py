from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobCollectionSnapshot, JobEntry, JobSnapshotStatus, RecruitingStatus


@dataclass(frozen=True)
class RecruitingCoverage:
    status: RecruitingStatus
    active_job_count: int | None
    last_checked_at: datetime | None
    last_successful_at: datetime | None
    freshness: str
    reason_code: str | None


class RecruitingCoverageService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build(self, company_id: object, *, now: datetime) -> RecruitingCoverage:
        now = now.astimezone(UTC)
        entries = list(self.session.scalars(select(JobEntry).where(JobEntry.company_id == company_id)))
        if not entries:
            return RecruitingCoverage(RecruitingStatus.ENTRY_DISCOVERY_PENDING, None, None, None, "unknown", None)
        snapshots = list(self.session.scalars(select(JobCollectionSnapshot).where(JobCollectionSnapshot.job_entry_id.in_([entry.id for entry in entries])).order_by(JobCollectionSnapshot.completed_at.desc(), JobCollectionSnapshot.created_at.desc())))
        latest = snapshots[0] if snapshots else None
        last_successful = next((snapshot for snapshot in snapshots if snapshot.status is JobSnapshotStatus.SUCCEEDED and snapshot.pagination_complete), None)
        if latest is not None and latest.status is not JobSnapshotStatus.SUCCEEDED:
            return RecruitingCoverage(RecruitingStatus.COLLECTION_INCOMPLETE, None, latest.completed_at, last_successful.completed_at if last_successful else None, "unknown", "temporary_source_error")
        if last_successful is None:
            return RecruitingCoverage(RecruitingStatus.COLLECTION_INCOMPLETE, latest.completed_at if latest else None, None, "unknown", "needs_review")
        fresh = last_successful.completed_at >= now - timedelta(hours=24)
        if not fresh:
            return RecruitingCoverage(RecruitingStatus.STALE, None, latest.completed_at if latest else None, last_successful.completed_at, "stale", None)
        if last_successful.empty_confirmed:
            return RecruitingCoverage(RecruitingStatus.EMPTY_CONFIRMED, 0, latest.completed_at if latest else None, last_successful.completed_at, "fresh", None)
        return RecruitingCoverage(RecruitingStatus.ACTIVE_ROLES, last_successful.observed_count, latest.completed_at if latest else None, last_successful.completed_at, "fresh", None)
