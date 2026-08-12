from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.job_enumeration.contracts import JobEnumerationResult, JobEnumerationStatus
from app.job_enumeration.jobhunt import JobHuntCli, JobHuntError
from app.models import JobCollectionSnapshot, JobEntry, JobSnapshotStatus


class JobEnumerationService:
    def __init__(
        self,
        session: Session,
        *,
        jobhunt: JobHuntCli,
        site_mapping: Mapping[UUID, str],
        freshness_hours: int = 24,
    ) -> None:
        self._session = session
        self._jobhunt = jobhunt
        self._site_mapping = site_mapping
        self._freshness = timedelta(hours=freshness_hours)

    async def enumerate_if_stale(
        self, company_id: UUID, *, now: datetime
    ) -> JobEnumerationResult:
        now = now.astimezone(UTC)
        latest = self._latest_complete(company_id)
        if latest is not None and latest.completed_at >= now - self._freshness:
            return JobEnumerationResult(status=JobEnumerationStatus.FRESH_DATABASE_HIT)
        site = self._site_mapping.get(company_id)
        if site is None:
            return JobEnumerationResult(status=JobEnumerationStatus.SOURCE_UNSUPPORTED)
        try:
            sites = await self._jobhunt.sites()
            natures = tuple(sorted(sites.get(site, frozenset())))
            if not natures:
                return JobEnumerationResult(
                    status=JobEnumerationStatus.SOURCE_UNSUPPORTED,
                    source_key=site,
                )
            return await self._jobhunt.enumerate(site=site, natures=natures)
        except JobHuntError as error:
            return JobEnumerationResult(
                status=JobEnumerationStatus.SOURCE_FAILED,
                source_key=site,
                error_code=error.code,
            )

    def _latest_complete(self, company_id: UUID) -> JobCollectionSnapshot | None:
        return self._session.scalar(
            select(JobCollectionSnapshot)
            .join(JobEntry, JobEntry.id == JobCollectionSnapshot.job_entry_id)
            .where(
                JobEntry.company_id == company_id,
                JobCollectionSnapshot.status == JobSnapshotStatus.SUCCEEDED,
                JobCollectionSnapshot.pagination_complete.is_(True),
            )
            .order_by(JobCollectionSnapshot.completed_at.desc())
            .limit(1)
        )
