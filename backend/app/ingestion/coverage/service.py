"""Atomic recording and lifecycle updates for job-list coverage snapshots."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import RecordJobSnapshot, SnapshotRecordResult
from app.ingestion.coverage.repository import CoverageRepository
from app.models import (
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobSnapshotStatus,
    JobSource,
)

_DEACTIVATION_MISSING_THRESHOLD = 2


class CoverageConflict(Exception):
    """Reject a snapshot command that cannot be applied without ambiguity."""

    def __init__(self, *, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class _LifecycleCounters:
    sources_reactivated: int = 0
    sources_missing_incremented: int = 0
    sources_deactivated: int = 0
    affected_job_ids: set[UUID] = field(default_factory=set)


class JobCoverageService:
    """Own the transaction that records one job-list coverage observation."""

    def __init__(
        self,
        session: Session,
        *,
        repository: CoverageRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or CoverageRepository(session)

    def record(self, command: RecordJobSnapshot) -> SnapshotRecordResult:
        if self.session.in_transaction():
            raise CoverageConflict(code="active_session_transaction")

        with self.session.begin():
            self._materialize_outer_transaction()
            entry = self.repository.lock_entry(command.entry_id)
            existing = self.repository.get_snapshot(command.entry_id, command.crawl_run_id)
            if existing is not None:
                snapshot_id = self._replay_snapshot_id(existing, command)
                created = False
                counters = _LifecycleCounters()
                jobs_recomputed = 0
            else:
                sources = self.repository.lock_entry_sources(command.entry_id)
                self._validate_seen_source_ownership(command, sources)
                lifecycle_applied = self._is_newer_complete_lifecycle_snapshot(
                    entry, command
                )
                snapshot = self.repository.insert_snapshot(
                    command, lifecycle_applied=lifecycle_applied
                )
                snapshot_id = snapshot.id
                created = True
                if (
                    entry.last_checked_at is not None
                    and command.completed_at <= entry.last_checked_at
                ):
                    counters = _LifecycleCounters()
                    jobs_recomputed = 0
                else:
                    self._update_entry_health(entry, command)
                    if lifecycle_applied:
                        counters = self._apply_complete_snapshot(snapshot, command, sources)
                    else:
                        counters = _LifecycleCounters()
                    jobs_recomputed = self.repository.recompute_job_activity(
                        counters.affected_job_ids
                    )

        return SnapshotRecordResult(
            snapshot_id=snapshot_id,
            created=created,
            sources_reactivated=counters.sources_reactivated,
            sources_missing_incremented=counters.sources_missing_incremented,
            sources_deactivated=counters.sources_deactivated,
            jobs_recomputed=jobs_recomputed,
        )

    def _materialize_outer_transaction(self) -> None:
        connection = self.session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")

    @staticmethod
    def _replay_snapshot_id(
        snapshot: JobCollectionSnapshot, command: RecordJobSnapshot
    ) -> UUID:
        if snapshot.command_hash != command.command_hash():
            raise CoverageConflict(code="snapshot_conflict")
        return snapshot.id

    def _apply_complete_snapshot(
        self,
        snapshot: JobCollectionSnapshot,
        command: RecordJobSnapshot,
        sources: tuple[JobSource, ...],
    ) -> _LifecycleCounters:
        counters = _LifecycleCounters(
            affected_job_ids={source.job_posting_id for source in sources}
        )
        for source in sources:
            if source.id not in command.seen_source_ids:
                continue
            source.lifecycle_managed = True
            if not source.is_active:
                counters.sources_reactivated += 1
            source.last_seen_snapshot_id = snapshot.id
            source.last_seen_at = command.completed_at
            source.missing_complete_snapshots = 0
            source.is_active = True

        for source in sources:
            if source.id in command.seen_source_ids:
                continue
            source.lifecycle_managed = True
            source.missing_complete_snapshots += 1
            counters.sources_missing_incremented += 1
            if (
                source.is_active
                and source.missing_complete_snapshots >= _DEACTIVATION_MISSING_THRESHOLD
            ):
                source.is_active = False
                counters.sources_deactivated += 1
        return counters

    @staticmethod
    def _validate_seen_source_ownership(
        command: RecordJobSnapshot, sources: tuple[JobSource, ...]
    ) -> None:
        if not command.seen_source_ids.issubset({source.id for source in sources}):
            raise CoverageConflict(code="source_entry_conflict")

    @staticmethod
    def _is_newer_complete_lifecycle_snapshot(
        entry: JobEntry, command: RecordJobSnapshot
    ) -> bool:
        return (
            command.status is JobSnapshotStatus.SUCCEEDED
            and command.pagination_complete
            and (
                entry.last_checked_at is None
                or command.completed_at > entry.last_checked_at
            )
        )

    @staticmethod
    def _update_entry_health(entry: JobEntry, command: RecordJobSnapshot) -> None:
        entry.last_checked_at = command.completed_at
        if (
            command.status is JobSnapshotStatus.SUCCEEDED
            and command.pagination_complete
        ):
            entry.status = JobEntryStatus.ACTIVE
            entry.failure_count = 0
            entry.last_success_at = command.completed_at
        else:
            entry.failure_count += 1
