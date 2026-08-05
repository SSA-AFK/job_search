from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.repository import CoverageRepository
from app.models import (
    CollectionStatus,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobEntry,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    RunType,
)

ENTRY_FIELDS = {
    "provider": "official",
    "platform": "self_hosted",
    "requires_rendering": False,
}
NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
SHARED_URL = "https://jobs.example.com/openings"


def make_command(entry_id, run_id, *, seen_source_ids=frozenset()):
    return RecordJobSnapshot(
        entry_id=entry_id,
        crawl_run_id=run_id,
        status=JobSnapshotStatus.SUCCEEDED,
        pagination_complete=True,
        pages_fetched=1,
        started_at=NOW,
        completed_at=NOW,
        seen_source_ids=seen_source_ids,
    )


def make_run(session: Session, company_id) -> CrawlRun:
    run = CrawlRun(
        company_id=company_id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    return run


def test_ensure_entry_normalizes_url_and_reuses_company_entry(
    repository: CoverageRepository, company: Company
) -> None:
    first = repository.ensure_entry(
        company.id,
        "https://jobs.example.com/openings/?utm_source=x",
        **ENTRY_FIELDS,
    )
    second = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)

    assert second.id == first.id
    assert first.normalized_url == SHARED_URL


def test_same_url_can_belong_to_different_companies(
    repository: CoverageRepository, companies: tuple[Company, Company]
) -> None:
    left = repository.ensure_entry(companies[0].id, SHARED_URL, **ENTRY_FIELDS)
    right = repository.ensure_entry(companies[1].id, SHARED_URL, **ENTRY_FIELDS)

    assert left.id != right.id


def test_ensure_entry_rejects_non_public_or_credentialed_url(
    repository: CoverageRepository, company: Company
) -> None:
    with pytest.raises(ValueError, match="public URL"):
        repository.ensure_entry(company.id, "https://localhost/jobs", **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="credentials"):
        repository.ensure_entry(company.id, "https://user@example.com/jobs", **ENTRY_FIELDS)


def test_ensure_entry_rejects_missing_company_and_provenance_conflict(
    repository: CoverageRepository, company: Company
) -> None:
    with pytest.raises(ValueError, match="unknown company_id"):
        repository.ensure_entry(uuid4(), SHARED_URL, **ENTRY_FIELDS)

    repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="job entry provenance conflict"):
        repository.ensure_entry(
            company.id,
            SHARED_URL,
            provider="board",
            platform="self_hosted",
            requires_rendering=False,
        )


def test_insert_snapshot_requires_existing_matching_entry_and_run(
    repository: CoverageRepository, companies: tuple[Company, Company], session: Session
) -> None:
    entry = repository.ensure_entry(companies[0].id, SHARED_URL, **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="unknown crawl_run_id"):
        repository.insert_snapshot(make_command(entry.id, uuid4()))

    other_run = make_run(session, companies[1].id)
    with pytest.raises(ValueError, match="crawl run does not belong to entry company"):
        repository.insert_snapshot(make_command(entry.id, other_run.id))
    with pytest.raises(ValueError, match="unknown job_entry_id"):
        repository.insert_snapshot(make_command(uuid4(), other_run.id))


def test_insert_snapshot_persists_command_without_mutating_sources(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        job_entry_id=entry.id,
        provider="official",
        source_raw_id="source-1",
        apply_url="https://jobs.example.com/1",
        is_active=False,
        missing_complete_snapshots=1,
    )
    session.add(source)
    session.flush()

    snapshot = repository.insert_snapshot(make_command(entry.id, run.id, seen_source_ids={source.id}))

    assert snapshot.observed_count == 1
    assert snapshot.command_hash == make_command(entry.id, run.id, seen_source_ids={source.id}).command_hash()
    assert source.is_active is False
    assert source.missing_complete_snapshots == 1
    assert source.last_seen_snapshot_id is None


def test_duplicate_snapshot_is_rejected_by_database_uniqueness(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    repository.insert_snapshot(make_command(entry.id, run.id))

    with pytest.raises(ValueError, match="snapshot already exists"):
        repository.insert_snapshot(make_command(entry.id, run.id))

    assert len(session.scalars(select(JobCollectionSnapshot)).all()) == 1


def test_lock_entry_sources_returns_stable_id_order(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
    )
    session.add(posting)
    session.flush()
    ids = sorted((uuid4(), uuid4(), uuid4()))
    for index, source_id in enumerate(reversed(ids)):
        session.add(
            JobSource(
                id=source_id,
                job_posting_id=posting.id,
                job_entry_id=entry.id,
                provider="official",
                source_raw_id=f"source-{index}",
                apply_url=f"https://jobs.example.com/{index}",
            )
        )
    session.flush()

    assert tuple(source.id for source in repository.lock_entry_sources(entry.id)) == tuple(ids)


def test_recompute_job_activity_keeps_posting_active_when_any_source_is_active(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    session.add_all(
        (
            JobSource(
                job_posting_id=posting.id,
                provider="official",
                source_raw_id="inactive",
                apply_url="https://jobs.example.com/inactive",
                is_active=False,
            ),
            JobSource(
                job_posting_id=posting.id,
                provider="board",
                source_raw_id="active",
                apply_url="https://jobs.example.com/active",
                is_active=True,
            ),
        )
    )
    session.flush()

    assert repository.recompute_job_activity({posting.id}) == 1
    assert posting.is_active is True


def test_repository_flushes_without_ending_outer_transaction(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    session.commit()
    with session.begin():
        entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
        assert session.in_transaction()
        session.add(Company(canonical_name="Later", normalized_name="later"))
        assert entry.id is not None


class _PostgresUniqueViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = SimpleNamespace(constraint_name=constraint_name)


@pytest.mark.parametrize(
    "database_error",
    [
        _PostgresUniqueViolation("uq_job_entry_company_url"),
    ],
    ids=["postgresql"],
)
def test_ensure_entry_recovers_known_unique_race_and_keeps_outer_transaction(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    database_error: Exception,
) -> None:
    entry_winner = JobEntry(
        id=uuid4(),
        company_id=company.id,
        url=SHARED_URL,
        normalized_url=SHARED_URL,
        **ENTRY_FIELDS,
    )
    original_scalar = session.scalar
    scalar_calls = iter((None, entry_winner))
    original_flush = session.flush

    def scalar(statement, *args, **kwargs):
        return next(scalar_calls)

    def flush(objects=None):
        if objects is not None:
            raise IntegrityError(None, None, database_error)
        return original_flush(objects)

    monkeypatch.setattr(session, "scalar", scalar)
    monkeypatch.setattr(session, "flush", flush)
    session.commit()
    with session.begin():
        resolved = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
        assert resolved is entry_winner
        assert session.in_transaction()
    monkeypatch.setattr(session, "scalar", original_scalar)


def test_ensure_entry_rechecks_winner_provenance_after_postgresql_unique_race(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_winner = JobEntry(
        id=uuid4(),
        company_id=company.id,
        url=SHARED_URL,
        normalized_url=SHARED_URL,
        provider="board",
        platform="self_hosted",
        requires_rendering=False,
    )
    scalar_calls = iter((None, entry_winner))
    original_flush = session.flush

    monkeypatch.setattr(session, "scalar", lambda *args, **kwargs: next(scalar_calls))

    def flush(objects=None):
        if objects is not None:
            raise IntegrityError(
                None,
                None,
                _PostgresUniqueViolation("uq_job_entry_company_url"),
            )
        return original_flush(objects)

    monkeypatch.setattr(session, "flush", flush)

    with pytest.raises(ValueError, match="job entry provenance conflict"):
        repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)


@pytest.mark.parametrize(
    "database_error",
    [
        _PostgresUniqueViolation("uq_job_snapshot_entry_run"),
    ],
    ids=["postgresql"],
)
def test_insert_snapshot_converts_known_unique_race_from_savepoint(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    database_error: Exception,
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    original_flush = session.flush

    monkeypatch.setattr(repository, "get_snapshot", lambda *args: None)

    def flush(objects=None):
        if objects is not None:
            raise IntegrityError(None, None, database_error)
        return original_flush(objects)

    monkeypatch.setattr(session, "flush", flush)

    with pytest.raises(ValueError, match="snapshot already exists"):
        repository.insert_snapshot(make_command(entry.id, run.id))


def test_insert_snapshot_reraises_unrelated_integrity_error(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    original_flush = session.flush
    unrelated = IntegrityError(None, None, _PostgresUniqueViolation("some_other_constraint"))

    monkeypatch.setattr(repository, "get_snapshot", lambda *args: None)

    def flush(objects=None):
        if objects is not None:
            raise unrelated
        return original_flush(objects)

    monkeypatch.setattr(session, "flush", flush)

    with pytest.raises(IntegrityError) as raised:
        repository.insert_snapshot(make_command(entry.id, run.id))
    assert raised.value is unrelated


def test_recompute_job_activity_batches_locks_and_queries_for_a_large_expunged_batch(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    postings = [
        JobPosting(
            company_id=company.id,
            title=f"Engineer {index}",
            normalized_title=f"engineer {index}",
            city="Shanghai",
            description="Build",
            is_active=False,
        )
        for index in range(25)
    ]
    session.add_all(postings)
    session.flush()
    session.add_all(
        JobSource(
            job_posting_id=posting.id,
            provider="official",
            source_raw_id=f"activity-{index}",
            apply_url=f"https://jobs.example.com/activity-{index}",
            is_active=index % 2 == 0,
        )
        for index, posting in enumerate(postings)
    )
    session.commit()
    posting_ids = [posting.id for posting in postings]
    session.expunge_all()
    statement_count = 0

    def count_statements(*_args) -> None:
        nonlocal statement_count
        statement_count += 1

    assert session.bind is not None
    event.listen(session.bind, "before_cursor_execute", count_statements)
    try:
        result = repository.recompute_job_activity(reversed((*posting_ids, uuid4())))
    finally:
        event.remove(session.bind, "before_cursor_execute", count_statements)

    assert result == len(posting_ids)
    assert statement_count <= 4
    active_ids = set(session.scalars(select(JobPosting.id).where(JobPosting.is_active.is_(True))))
    assert active_ids == {posting.id for index, posting in enumerate(postings) if index % 2 == 0}


def test_recompute_job_activity_uses_one_exists_query_per_500_posting_batch(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    postings = [
        JobPosting(
            company_id=company.id,
            title=f"Boundary Engineer {index}",
            normalized_title=f"boundary engineer {index}",
            city="Shanghai",
            description="Build",
            is_active=False,
        )
        for index in range(501)
    ]
    session.add_all(postings)
    session.flush()
    session.add_all(
        JobSource(
            job_posting_id=posting.id,
            provider="official",
            source_raw_id=f"exists-boundary-{index}",
            apply_url=f"https://jobs.example.com/exists-boundary-{index}",
            is_active=index % 2 == 0,
        )
        for index, posting in enumerate(postings)
    )
    session.commit()
    posting_ids = tuple(posting.id for posting in postings)
    session.expunge_all()
    statements: list[str] = []

    def capture_statements(
        _connection, _cursor, statement: str, _parameters, _context, _executemany
    ) -> None:
        statements.append(statement)

    assert session.bind is not None
    event.listen(session.bind, "before_cursor_execute", capture_statements)
    try:
        result = repository.recompute_job_activity(reversed(posting_ids))
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_statements)

    select_statements = [
        statement.upper()
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    exists_statements = [
        statement for statement in select_statements if "EXISTS (SELECT" in statement
    ]
    assert result == 501
    assert len(exists_statements) == 2
    assert all("SELECT DISTINCT" not in statement for statement in select_statements)
    assert len(statements) <= 5
    active_ids = set(
        session.scalars(select(JobPosting.id).where(JobPosting.is_active.is_(True)))
    )
    assert active_ids == {
        posting.id for index, posting in enumerate(postings) if index % 2 == 0
    }


def test_active_source_exists_query_uses_posting_activity_index(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    posting = JobPosting(
        company_id=company.id,
        title="Indexed Engineer",
        normalized_title="indexed engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    session.add(
        JobSource(
            job_posting_id=posting.id,
            provider="official",
            source_raw_id="indexed-exists-source",
            apply_url="https://jobs.example.com/indexed-exists-source",
            is_active=True,
        )
    )
    session.commit()
    exists_statements: list[tuple[str, object]] = []

    def capture_exists(
        _connection, _cursor, statement: str, parameters, _context, _executemany
    ) -> None:
        if "EXISTS (SELECT" in statement.upper():
            exists_statements.append((statement, parameters))

    assert session.bind is not None
    event.listen(session.bind, "before_cursor_execute", capture_exists)
    try:
        assert repository.recompute_job_activity({posting.id}) == 1
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_exists)

    assert len(exists_statements) == 1
    exists_sql, exists_parameters = exists_statements[0]
    plan = session.connection().exec_driver_sql(
        f"EXPLAIN QUERY PLAN {exists_sql}", exists_parameters
    ).all()
    details = " ".join(str(row[3]) for row in plan)

    assert "SEARCH job_sources USING" in details
    assert (
        "ix_job_sources_posting_active (job_posting_id=? AND is_active=?)" in details
    )
    assert "SCAN job_sources" not in details


def test_ensure_entry_recovers_from_a_real_sqlite_savepoint_unique_race(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    winner = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    session.commit()
    original_scalar = session.scalar
    first_lookup = True

    def hide_first_entry_lookup(statement, *args, **kwargs):
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", hide_first_entry_lookup)
    with session.begin():
        resolved = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
        session.add(Company(canonical_name="Recovered", normalized_name="recovered"))
        session.flush()
        assert resolved.id == winner.id

    assert session.get(Company, company.id) is not None
    assert session.scalar(select(Company).where(Company.normalized_name == "recovered")) is not None


def test_insert_snapshot_recovers_from_a_real_sqlite_savepoint_unique_race(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    winner = repository.insert_snapshot(make_command(entry.id, run.id))
    session.commit()

    monkeypatch.setattr(repository, "get_snapshot", lambda *args: None)
    with session.begin():
        with pytest.raises(ValueError, match="snapshot already exists"):
            repository.insert_snapshot(make_command(entry.id, run.id))
        session.add(Company(canonical_name="Snapshot recovered", normalized_name="snapshot-recovered"))
        session.flush()

    assert session.get(JobCollectionSnapshot, winner.id) is not None
    assert session.scalar(
        select(Company).where(Company.normalized_name == "snapshot-recovered")
    ) is not None


def test_ensure_entry_reraises_constraint_name_suffix_collision(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_flush = session.flush
    collision = IntegrityError(
        None, None, Exception("constraint uq_job_entry_company_url_shadow violated")
    )

    monkeypatch.setattr(session, "scalar", lambda *args, **kwargs: None)

    def flush(objects=None):
        if objects is not None:
            raise collision
        return original_flush(objects)

    monkeypatch.setattr(session, "flush", flush)

    with pytest.raises(IntegrityError) as raised:
        repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    assert raised.value is collision


def test_recompute_job_activity_flushes_pending_source_deactivation(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=True,
    )
    session.add(posting)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        provider="official",
        source_raw_id="pending-deactivation",
        apply_url="https://jobs.example.com/pending-deactivation",
        is_active=True,
    )
    session.add(source)
    session.commit()

    with session.begin():
        source.is_active = False
        assert repository.recompute_job_activity({posting.id}) == 1
        assert posting.is_active is False

    session.expire_all()
    persisted_source = session.get(JobSource, source.id)
    persisted_posting = session.get(JobPosting, posting.id)
    assert persisted_source is not None and persisted_source.is_active is False
    assert persisted_posting is not None and persisted_posting.is_active is False


def test_recompute_job_activity_flushes_pending_source_reactivation(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        provider="official",
        source_raw_id="pending-reactivation",
        apply_url="https://jobs.example.com/pending-reactivation",
        is_active=False,
    )
    session.add(source)
    session.commit()

    with session.begin():
        source.is_active = True
        assert repository.recompute_job_activity({posting.id}) == 1
        assert posting.is_active is True

    session.expire_all()
    persisted_source = session.get(JobSource, source.id)
    persisted_posting = session.get(JobPosting, posting.id)
    assert persisted_source is not None and persisted_source.is_active is True
    assert persisted_posting is not None and persisted_posting.is_active is True


def test_ensure_entry_reraises_sqlite_column_marker_suffix_collision(
    repository: CoverageRepository,
    company: Company,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_flush = session.flush
    collision = IntegrityError(
        None,
        None,
        Exception(
            "UNIQUE constraint failed: job_entries.company_id, "
            "job_entries.normalized_url_shadow"
        ),
    )

    monkeypatch.setattr(session, "scalar", lambda *args, **kwargs: None)

    def flush(objects=None):
        if objects is not None:
            raise collision
        return original_flush(objects)

    monkeypatch.setattr(session, "flush", flush)

    with pytest.raises(IntegrityError) as raised:
        repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    assert raised.value is collision
