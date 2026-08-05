from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from alembic import command as alembic_command
from app.coverage.service import CoverageReportService
from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.repository import CoverageRepository
from app.ingestion.coverage.service import JobCoverageService
from app.models import (
    CollectionStatus,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    RunType,
)

AS_OF = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _new_run(session: Session, company_id: UUID) -> CrawlRun:
    run = CrawlRun(
        company_id=company_id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    return run


def _complete_snapshot(
    *,
    entry_id: UUID,
    run_id: UUID,
    completed_at: datetime,
    seen_source_ids: frozenset[UUID] = frozenset(),
    empty_confirmed: bool = False,
) -> RecordJobSnapshot:
    return RecordJobSnapshot(
        entry_id=entry_id,
        crawl_run_id=run_id,
        status=JobSnapshotStatus.SUCCEEDED,
        pagination_complete=True,
        empty_confirmed=empty_confirmed,
        reported_total=len(seen_source_ids),
        pages_fetched=1,
        started_at=completed_at - timedelta(minutes=1),
        completed_at=completed_at,
        seen_source_ids=seen_source_ids,
    )


def test_stage3a_coverage_lifecycle_and_report(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'stage3a-acceptance.sqlite3').as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: object, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        with Session(engine, expire_on_commit=False) as session:
            company_a = Company(canonical_name="Company A", normalized_name="company-a")
            company_b = Company(canonical_name="Company B", normalized_name="company-b")
            session.add_all((company_a, company_b))
            session.commit()

            repository = CoverageRepository(session)
            company_a_entry = repository.ensure_entry(
                company_a.id,
                "https://company-a.example/jobs",
                provider="official",
                platform="custom",
                requires_rendering=False,
            )
            company_b_entry = repository.ensure_entry(
                company_b.id,
                "https://company-b.example/jobs",
                provider="official",
                platform="custom",
                requires_rendering=False,
            )
            session.commit()

            posting = JobPosting(
                company_id=company_a.id,
                title="Platform Engineer",
                normalized_title="platform engineer",
                city="Shanghai",
                description="Build the platform",
            )
            session.add(posting)
            session.flush()
            source_a = JobSource(
                job_posting_id=posting.id,
                job_entry_id=company_a_entry.id,
                provider="official",
                source_raw_id="company-a-source-a",
                apply_url="https://company-a.example/jobs/a",
            )
            source_b = JobSource(
                job_posting_id=posting.id,
                job_entry_id=company_a_entry.id,
                provider="official",
                source_raw_id="company-a-source-b",
                apply_url="https://company-a.example/jobs/b",
            )
            session.add_all((source_a, source_b))
            session.flush()

            runs = [_new_run(session, company_a.id) for _ in range(4)]
            empty_run = _new_run(session, company_b.id)
            session.commit()

            successful_commands = (
                _complete_snapshot(
                    entry_id=company_a_entry.id,
                    run_id=runs[0].id,
                    completed_at=AS_OF - timedelta(hours=5),
                    seen_source_ids=frozenset((source_a.id, source_b.id)),
                ),
                _complete_snapshot(
                    entry_id=company_b_entry.id,
                    run_id=empty_run.id,
                    completed_at=AS_OF - timedelta(hours=4),
                    empty_confirmed=True,
                ),
                _complete_snapshot(
                    entry_id=company_a_entry.id,
                    run_id=runs[1].id,
                    completed_at=AS_OF - timedelta(hours=3),
                    seen_source_ids=frozenset((source_b.id,)),
                ),
                _complete_snapshot(
                    entry_id=company_a_entry.id,
                    run_id=runs[3].id,
                    completed_at=AS_OF - timedelta(hours=1),
                    seen_source_ids=frozenset((source_b.id,)),
                ),
            )
            failed_command = RecordJobSnapshot(
                entry_id=company_a_entry.id,
                crawl_run_id=runs[2].id,
                status=JobSnapshotStatus.FAILED,
                error_code="request_failed",
                pages_fetched=0,
                started_at=AS_OF - timedelta(hours=2, minutes=1),
                completed_at=AS_OF - timedelta(hours=2),
            )
            coverage_service = JobCoverageService(session)

            for coverage_command in successful_commands[:3]:
                assert coverage_service.record(coverage_command).created is True
            assert source_a.missing_complete_snapshots == 1

            assert coverage_service.record(failed_command).created is True
            assert source_a.missing_complete_snapshots == 1
            assert source_a.is_active is True

            assert coverage_service.record(successful_commands[3]).created is True
            session.expire_all()
            assert source_a.missing_complete_snapshots == 2
            assert source_a.is_active is False
            assert source_b.missing_complete_snapshots == 0
            assert source_b.is_active is True
            assert posting.is_active is True

            lifecycle_before_replay = (
                source_a.missing_complete_snapshots,
                source_a.is_active,
                source_b.missing_complete_snapshots,
                source_b.is_active,
                posting.is_active,
            )
            snapshot_count_before_replay = session.scalar(
                select(func.count()).select_from(JobCollectionSnapshot)
            )
            session.rollback()

            replay_results = [
                coverage_service.record(coverage_command)
                for coverage_command in successful_commands
            ]
            assert all(result.created is False for result in replay_results)
            session.expire_all()
            assert session.scalar(
                select(func.count()).select_from(JobCollectionSnapshot)
            ) == snapshot_count_before_replay
            assert (
                source_a.missing_complete_snapshots,
                source_a.is_active,
                source_b.missing_complete_snapshots,
                source_b.is_active,
                posting.is_active,
            ) == lifecycle_before_replay

            report = CoverageReportService(session).build(as_of=AS_OF)
            assert report.target_companies == 2
            assert report.active_entry_companies == 2
            assert report.recently_enumerated_companies == 2
            assert report.complete_list_companies == 2
            assert report.confirmed_empty_companies == 1
    finally:
        engine.dispose()
