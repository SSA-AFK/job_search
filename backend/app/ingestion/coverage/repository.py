"""Transaction-neutral persistence helpers for job-list coverage."""

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.normalization import normalize_url
from app.ingestion.contracts import DocumentUrl
from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.models import Company, CrawlRun, JobCollectionSnapshot, JobEntry, JobPosting, JobSource

_PUBLIC_URL = TypeAdapter(DocumentUrl)
_ENTRY_UNIQUE_CONSTRAINT = "uq_job_entry_company_url"
_ENTRY_UNIQUE_MARKER = "job_entries.company_id, job_entries.normalized_url"
_SNAPSHOT_UNIQUE_CONSTRAINT = "uq_job_snapshot_entry_run"
_SNAPSHOT_UNIQUE_MARKER = "job_collection_snapshots.job_entry_id, job_collection_snapshots.crawl_run_id"
_JOB_ACTIVITY_BATCH_SIZE = 500


class CoverageRepository:
    """Reads and writes coverage models without owning the outer transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_entry(
        self,
        company_id: UUID,
        url: str,
        *,
        provider: str,
        platform: str,
        requires_rendering: bool,
    ) -> JobEntry:
        self._require_company(company_id)
        normalized_url = _normalize_entry_url(url)
        statement = select(JobEntry).where(
            JobEntry.company_id == company_id,
            JobEntry.normalized_url == normalized_url,
        )
        entry = self.session.scalar(statement)
        if entry is None:
            candidate = JobEntry(
                company_id=company_id,
                url=normalized_url,
                normalized_url=normalized_url,
                provider=provider,
                platform=platform,
                requires_rendering=requires_rendering,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(candidate)
                    self.session.flush([candidate])
            except IntegrityError as exc:
                if not _is_known_unique_constraint(
                    exc, _ENTRY_UNIQUE_CONSTRAINT, _ENTRY_UNIQUE_MARKER
                ):
                    raise
                entry = self.session.scalar(statement.execution_options(populate_existing=True))
                if entry is None:
                    raise ValueError("job entry uniqueness conflict had no winner") from exc
            else:
                entry = candidate

        self._require_matching_provenance(
            entry,
            provider=provider,
            platform=platform,
            requires_rendering=requires_rendering,
        )
        return entry

    def lock_entry(self, entry_id: UUID) -> JobEntry:
        statement = (
            select(JobEntry)
            .where(JobEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        entry = self.session.scalar(statement)
        if entry is None:
            raise ValueError("unknown job_entry_id")
        return entry

    def get_snapshot(
        self, entry_id: UUID, crawl_run_id: UUID
    ) -> JobCollectionSnapshot | None:
        return self.session.scalar(
            select(JobCollectionSnapshot).where(
                JobCollectionSnapshot.job_entry_id == entry_id,
                JobCollectionSnapshot.crawl_run_id == crawl_run_id,
            )
        )

    def insert_snapshot(
        self, command: RecordJobSnapshot, *, lifecycle_applied: bool = False
    ) -> JobCollectionSnapshot:
        entry = self._require_entry(command.entry_id)
        run = self._require_run(command.crawl_run_id)
        if run.company_id != entry.company_id:
            raise ValueError("crawl run does not belong to entry company")
        if self.get_snapshot(command.entry_id, command.crawl_run_id) is not None:
            raise ValueError("snapshot already exists")

        snapshot = JobCollectionSnapshot(
            job_entry_id=command.entry_id,
            crawl_run_id=command.crawl_run_id,
            status=command.status,
            lifecycle_applied=lifecycle_applied,
            pagination_complete=command.pagination_complete,
            empty_confirmed=command.empty_confirmed,
            reported_total=command.reported_total,
            observed_count=len(command.seen_source_ids),
            pages_fetched=command.pages_fetched,
            content_fingerprint=command.content_fingerprint,
            command_hash=command.command_hash(),
            error_code=command.error_code,
            started_at=command.started_at,
            completed_at=command.completed_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(snapshot)
                self.session.flush([snapshot])
        except IntegrityError as exc:
            if _is_known_unique_constraint(
                exc, _SNAPSHOT_UNIQUE_CONSTRAINT, _SNAPSHOT_UNIQUE_MARKER
            ):
                raise ValueError("snapshot already exists") from exc
            raise
        return snapshot

    def lock_entry_sources(self, entry_id: UUID) -> tuple[JobSource, ...]:
        statement = (
            select(JobSource)
            .where(JobSource.job_entry_id == entry_id)
            .order_by(JobSource.id)
            .with_for_update()
        )
        return tuple(self.session.scalars(statement))

    def recompute_job_activity(self, job_ids: Iterable[UUID]) -> int:
        """Lock requested postings in UUID order before deriving activity from sources."""

        self.session.flush()
        requested_ids = tuple(sorted(set(job_ids)))
        count = 0
        with self.session.no_autoflush:
            for start in range(0, len(requested_ids), _JOB_ACTIVITY_BATCH_SIZE):
                batch = requested_ids[start : start + _JOB_ACTIVITY_BATCH_SIZE]
                postings = tuple(
                    self.session.scalars(
                        select(JobPosting)
                        .where(JobPosting.id.in_(batch))
                        .order_by(JobPosting.id)
                        .with_for_update()
                    )
                )
                active_job_ids = set(
                    self.session.scalars(
                        select(JobPosting.id)
                        .where(
                            JobPosting.id.in_(batch),
                            exists(
                                select(JobSource.id).where(
                                    JobSource.job_posting_id == JobPosting.id,
                                    JobSource.is_active.is_(True),
                                )
                            ),
                        )
                    )
                )
                for posting in postings:
                    posting.is_active = posting.id in active_job_ids
                count += len(postings)
        self.session.flush()
        return count

    def _require_company(self, company_id: UUID) -> Company:
        company = self.session.get(Company, company_id)
        if company is None:
            raise ValueError("unknown company_id")
        return company

    def _require_entry(self, entry_id: UUID) -> JobEntry:
        entry = self.session.get(JobEntry, entry_id)
        if entry is None:
            raise ValueError("unknown job_entry_id")
        return entry

    def _require_run(self, run_id: UUID) -> CrawlRun:
        run = self.session.get(CrawlRun, run_id)
        if run is None:
            raise ValueError("unknown crawl_run_id")
        return run

    @staticmethod
    def _require_matching_provenance(
        entry: JobEntry,
        *,
        provider: str,
        platform: str,
        requires_rendering: bool,
    ) -> None:
        if (
            entry.provider != provider
            or entry.platform != platform
            or entry.requires_rendering != requires_rendering
        ):
            raise ValueError("job entry provenance conflict")


def _normalize_entry_url(url: str) -> str:
    """Validate a public URL, then remove only tracking parameters from its identity."""

    _PUBLIC_URL.validate_python(url)
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")],
        doseq=True,
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _is_known_unique_constraint(
    error: IntegrityError, constraint_name: str, sqlite_marker: str
) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    reported_name = getattr(diagnostic, "constraint_name", None)
    if isinstance(reported_name, str):
        return reported_name == constraint_name

    message = str(error.orig)
    name_pattern = rf"(?<![A-Za-z0-9_]){re.escape(constraint_name)}(?![A-Za-z0-9_])"
    marker_pattern = rf"(?<![A-Za-z0-9_]){re.escape(sqlite_marker)}(?![A-Za-z0-9_])"
    return re.search(name_pattern, message) is not None or re.search(marker_pattern, message) is not None
