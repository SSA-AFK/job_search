import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import ORMExecuteState, Session

from app.coverage.service import CoverageReportService
from app.models import (
    Base,
    Company,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobSnapshotStatus,
)

AS_OF = datetime(2026, 8, 5, 12, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    value = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(value)
    yield value
    value.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as value:
        yield value


def _company(session: Session, name: str) -> Company:
    company = Company(canonical_name=name, normalized_name=name.lower().replace(" ", "-"))
    session.add(company)
    session.flush()
    return company


def _entry(
    session: Session,
    company: Company,
    suffix: str,
    *,
    status: JobEntryStatus = JobEntryStatus.ACTIVE,
) -> JobEntry:
    url = f"https://jobs.example.com/{suffix}"
    entry = JobEntry(
        company_id=company.id,
        url=url,
        normalized_url=url,
        provider="official",
        platform="custom",
        status=status,
    )
    session.add(entry)
    session.flush()
    return entry


def _snapshot(
    session: Session,
    entry: JobEntry,
    *,
    completed_at: datetime,
    status: JobSnapshotStatus = JobSnapshotStatus.SUCCEEDED,
    complete: bool = True,
    empty: bool = False,
    command_digit: int = 1,
) -> JobCollectionSnapshot:
    snapshot = JobCollectionSnapshot(
        job_entry_id=entry.id,
        status=status,
        pagination_complete=complete,
        empty_confirmed=empty,
        reported_total=0 if empty else None,
        observed_count=0 if empty else 1,
        pages_fetched=0 if status is JobSnapshotStatus.FAILED else 1,
        command_hash=f"{command_digit:064x}",
        error_code=None if status is JobSnapshotStatus.SUCCEEDED else "collection_error",
        started_at=completed_at - timedelta(minutes=1),
        completed_at=completed_at,
    )
    session.add(snapshot)
    return snapshot


def test_build_reports_exact_company_level_coverage_rates(session: Session) -> None:
    _company(session, "No Entry")

    empty_company = _company(session, "Empty List")
    empty_entry = _entry(session, empty_company, "empty")
    _snapshot(
        session,
        empty_entry,
        completed_at=AS_OF - timedelta(hours=23),
        empty=True,
        command_digit=1,
    )
    # Multiple entries and snapshots for one company must not inflate company counts.
    duplicate_entry = _entry(session, empty_company, "empty-secondary")
    _snapshot(
        session,
        duplicate_entry,
        completed_at=AS_OF - timedelta(hours=2),
        empty=True,
        command_digit=2,
    )

    nonempty_company = _company(session, "Nonempty List")
    nonempty_entry = _entry(session, nonempty_company, "nonempty")
    _snapshot(
        session,
        nonempty_entry,
        completed_at=AS_OF - timedelta(hours=12),
        empty=True,
        command_digit=3,
    )
    _snapshot(
        session,
        nonempty_entry,
        completed_at=AS_OF - timedelta(hours=1),
        empty=False,
        command_digit=4,
    )

    stale_company = _company(session, "Stale Entry")
    stale_entry = _entry(session, stale_company, "stale", status=JobEntryStatus.STALE)
    _snapshot(
        session,
        stale_entry,
        completed_at=AS_OF - timedelta(hours=1),
        command_digit=5,
    )

    failed_company = _company(session, "Failed Entry")
    failed_entry = _entry(session, failed_company, "failed")
    _snapshot(
        session,
        failed_entry,
        completed_at=AS_OF - timedelta(minutes=30),
        status=JobSnapshotStatus.FAILED,
        complete=False,
        command_digit=6,
    )
    session.commit()

    report = CoverageReportService(session).build(as_of=AS_OF)

    assert report.target_companies == 5
    assert report.active_entry_companies == 3
    assert report.recently_enumerated_companies == 2
    assert report.complete_list_companies == 2
    assert report.confirmed_empty_companies == 1
    assert report.entry_coverage_rate == Decimal("0.6000")
    assert report.enumeration_rate == Decimal("0.6667")
    assert report.completeness_rate == Decimal("1.0000")
    assert report.refresh_slo_rate == Decimal("0.4000")
    assert report.as_of == AS_OF
    assert report.refresh_window_hours == 24


def test_build_returns_undefined_rates_for_empty_database(session: Session) -> None:
    report = CoverageReportService(session).build(as_of=AS_OF)

    assert report.target_companies == 0
    assert report.active_entry_companies == 0
    assert report.recently_enumerated_companies == 0
    assert report.complete_list_companies == 0
    assert report.confirmed_empty_companies == 0
    assert report.entry_coverage_rate is None
    assert report.enumeration_rate is None
    assert report.completeness_rate is None
    assert report.refresh_slo_rate is None


def test_build_includes_both_refresh_window_boundaries(session: Session) -> None:
    company = _company(session, "Boundary")
    lower_entry = _entry(session, company, "lower")
    upper_entry = _entry(session, company, "upper")
    future_entry = _entry(session, company, "future")
    _snapshot(session, lower_entry, completed_at=AS_OF - timedelta(hours=24), command_digit=1)
    _snapshot(session, upper_entry, completed_at=AS_OF, command_digit=2)
    _snapshot(
        session,
        future_entry,
        completed_at=AS_OF + timedelta(microseconds=1),
        command_digit=3,
    )
    session.commit()

    report = CoverageReportService(session).build(as_of=AS_OF)

    assert report.recently_enumerated_companies == 1


def test_build_uses_latest_qualifying_snapshot_per_entry(session: Session) -> None:
    company = _company(session, "Latest Qualifying")
    entry = _entry(session, company, "latest-qualifying")
    _snapshot(
        session,
        entry,
        completed_at=AS_OF - timedelta(hours=3),
        empty=True,
        command_digit=1,
    )
    _snapshot(
        session,
        entry,
        completed_at=AS_OF - timedelta(hours=2),
        status=JobSnapshotStatus.PARTIAL,
        complete=False,
        command_digit=2,
    )
    _snapshot(
        session,
        entry,
        completed_at=AS_OF - timedelta(hours=1),
        status=JobSnapshotStatus.FAILED,
        complete=False,
        command_digit=3,
    )
    session.commit()

    report = CoverageReportService(session).build(as_of=AS_OF)

    assert report.recently_enumerated_companies == 1
    assert report.confirmed_empty_companies == 1


@pytest.mark.parametrize(
    "refresh_window",
    [timedelta(0), -timedelta(hours=1), timedelta(minutes=90)],
)
def test_build_rejects_refresh_windows_that_cannot_be_reported(
    session: Session, refresh_window: timedelta
) -> None:
    with pytest.raises(ValueError, match="positive whole number of hours"):
        CoverageReportService(session).build(as_of=AS_OF, refresh_window=refresh_window)


def test_build_rejects_naive_as_of(session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CoverageReportService(session).build(as_of=AS_OF.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("as_of", "refresh_window"),
    [
        (datetime.min.replace(tzinfo=UTC), timedelta(hours=24)),
        (AS_OF, timedelta(hours=2_147_483_647)),
    ],
)
def test_build_rejects_unrepresentable_window_before_executing_sql(
    engine: Engine,
    session: Session,
    as_of: datetime,
    refresh_window: timedelta,
) -> None:
    statements = 0

    def count_statement(
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal statements
        statements += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        with pytest.raises(ValueError, match="outside the supported datetime range"):
            CoverageReportService(session).build(
                as_of=as_of,
                refresh_window=refresh_window,
            )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert statements == 0


def test_build_compiles_one_snapshot_window_scan_for_postgresql(session: Session) -> None:
    compiled_statements: list[str] = []

    def capture_statement(orm_execute_state: ORMExecuteState) -> None:
        compiled_statements.append(
            str(
                orm_execute_state.statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            ).lower()
        )

    event.listen(session, "do_orm_execute", capture_statement)
    try:
        CoverageReportService(session).build(as_of=AS_OF)
    finally:
        event.remove(session, "do_orm_execute", capture_statement)

    assert len(compiled_statements) == 1
    compiled_sql = compiled_statements[0]
    assert compiled_sql.count("row_number() over") == 1
    assert len(re.findall(r"\bfrom\s+job_collection_snapshots\b", compiled_sql)) == 1


def test_build_statement_count_is_bounded_independent_of_company_count(
    engine: Engine, session: Session
) -> None:
    for index in range(30):
        company = _company(session, f"Company {index}")
        entry = _entry(session, company, f"company-{index}")
        _snapshot(
            session,
            entry,
            completed_at=AS_OF - timedelta(hours=1),
            command_digit=index + 1,
        )
    session.commit()
    statements = 0

    def count_statement(
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal statements
        statements += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        report = CoverageReportService(session).build(as_of=AS_OF)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert report.recently_enumerated_companies == 30
    assert statements == 1
