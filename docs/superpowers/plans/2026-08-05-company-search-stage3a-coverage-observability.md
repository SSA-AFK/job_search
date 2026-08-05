# Company Search Stage 3A Coverage Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-backed recruitment entries, list snapshots, safe source lifecycle rules, and internal coverage reports so the system can distinguish a complete empty list from collection failure before ATS expansion begins.

**Architecture:** Stage 3A adds an isolated coverage domain beside the existing ingestion pipeline. A validated command records one immutable entry-level list snapshot and applies source lifecycle changes in the same transaction; only a newly created, complete successful snapshot may increment missing counters or deactivate sources. Reporting reads database rows directly and exposes an offline JSON CLI for Gate evidence, while current Stage 2 Providers remain unchanged until Stage 3B supplies truthful list-enumeration metadata.

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy 2.x, Alembic, SQLite for deterministic tests, PostgreSQL DDL verification, pytest, Ruff, mypy

## Global Constraints

- Begin implementation only after explicit execution approval and create an isolated Git worktree from the approved `main` baseline; do not modify or stage the restored user WIP in the main workspace.
- Stage 3A excludes ATS runtime registration, Playwright, live HTTP, external LLM calls, frontend work, `job_details`, scheduling scale-out, and 1k/3k/10k data collection.
- Existing Stage 2 Providers do not claim list completeness and do not write Stage 3A snapshots; Stage 3B will connect truthful ATS/list enumeration outputs to the interfaces defined here.
- New Alembic revisions are exactly `0006_job_entries_and_snapshots` and `0007_job_source_snapshot_lifecycle`, both descending from the checked-in `0005_extend_job_type_values` chain.
- Database rows, not Celery task state or Redis, are the source of truth for recruitment entries, snapshots, and coverage reports.
- A `partial` or `failed` snapshot never increments missing counters and never deactivates a source.
- A source is deactivated only after it is absent from two newly created consecutive `succeeded` snapshots with `pagination_complete=true` for the same entry.
- A job posting stays active while any of its sources is active; a seen source is reactivated and resets its missing counter to zero.
- Snapshot replay for the same `(job_entry_id, crawl_run_id)` is idempotent only when its persisted payload matches; a different payload raises a stable conflict and performs no lifecycle changes.
- External URLs must remain public, credential-free HTTP(S) URLs and use the existing normalization/security boundaries.
- All tests are offline by default. No task in this plan accesses a real Provider, browser, Redis server, LLM, or external website.
- SQLite migrations must preserve foreign keys and existing data; PostgreSQL-specific DDL must compile in tests.
- Do not recursively delete files or directories. Generated test databases are removed only through validated per-file cleanup when cleanup is required.
- Each task ends with focused tests, relevant broader tests, Ruff, mypy for changed modules, an independent review gate, and one scoped commit.

---

## File Map

- `backend/app/ingestion/coverage/contracts.py`: immutable commands, results, and report DTOs.
- `backend/app/ingestion/coverage/repository.py`: transaction-neutral SQLAlchemy reads, inserts, and locks.
- `backend/app/ingestion/coverage/service.py`: clean-session transaction boundary and idempotent snapshot recording.
- `backend/app/coverage/service.py`: aggregate coverage queries.
- `backend/app/coverage/cli.py`: offline JSON report command.
- `backend/app/models/job_entry.py`: recruitment entry and immutable list snapshot ORM models.
- `backend/app/models/job.py`: source-to-entry/snapshot lifecycle fields.
- `backend/alembic/versions/0006_job_entries_and_snapshots.py`: entry/snapshot tables.
- `backend/alembic/versions/0007_job_source_snapshot_lifecycle.py`: nullable legacy-safe source lifecycle columns.
- `backend/tests/ingestion/coverage/`: contract, repository, service, and lifecycle tests.
- `backend/tests/coverage/`: report and CLI tests.
- `backend/tests/integration/test_job_coverage_lifecycle.py`: Stage 3A end-to-end database acceptance flow.

### Task 1: Define Coverage Enums and Immutable Commands

**Files:**
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/ingestion/coverage/__init__.py`
- Create: `backend/app/ingestion/coverage/contracts.py`
- Create: `backend/tests/ingestion/coverage/__init__.py`
- Create: `backend/tests/ingestion/coverage/test_contracts.py`

**Interfaces:**
- Produces: `JobEntryStatus`, with values `unknown`, `active`, `stale`, and `disabled`.
- Produces: `JobSnapshotStatus`, with values `succeeded`, `partial`, and `failed`.
- Produces: immutable `RecordJobSnapshot(entry_id, crawl_run_id, status, pagination_complete, empty_confirmed, reported_total, pages_fetched, content_fingerprint, error_code, started_at, completed_at, seen_source_ids)`.
- Produces: `RecordJobSnapshot.command_hash() -> str`, a lowercase SHA-256 over canonical JSON with sorted source ids and UTC timestamps.
- Produces: immutable `SnapshotRecordResult(snapshot_id, created, sources_reactivated, sources_missing_incremented, sources_deactivated, jobs_recomputed)`.
- Produces: immutable `CoverageReport` with integer counters and decimal rates used by Task 6.

- [ ] **Step 1: Write failing validation tests**

Create tests with exact invariants:

```python
def test_success_requires_complete_pagination_and_no_error() -> None:
    with pytest.raises(ValidationError, match="successful snapshot must be complete"):
        snapshot_command(status="succeeded", pagination_complete=False)


def test_partial_requires_error_and_cannot_confirm_empty() -> None:
    with pytest.raises(ValidationError, match="partial snapshot requires error_code"):
        snapshot_command(status="partial", error_code=None)
    with pytest.raises(ValidationError, match="empty confirmation requires successful complete snapshot"):
        snapshot_command(status="partial", error_code="page_timeout", empty_confirmed=True)


def test_confirmed_empty_has_no_seen_sources_and_zero_reported_total() -> None:
    with pytest.raises(ValidationError, match="confirmed empty snapshot cannot contain sources"):
        snapshot_command(empty_confirmed=True, seen_source_ids={uuid4()})
    with pytest.raises(ValidationError, match="confirmed empty reported_total must be zero"):
        snapshot_command(empty_confirmed=True, reported_total=1)


def test_snapshot_bounds_and_time_order() -> None:
    with pytest.raises(ValidationError, match="completed_at must not precede started_at"):
        snapshot_command(started_at=LATER, completed_at=EARLIER)
    with pytest.raises(ValidationError, match="content_fingerprint"):
        snapshot_command(content_fingerprint="not-sha256")
```

Also assert that `failed` requires an error, contains no seen sources, has `pages_fetched=0`, and cannot be complete; `reported_total`, `pages_fetched`, and `seen_source_ids` are bounded to non-negative SQL-safe values and at most 20,000 source ids.

- [ ] **Step 2: Run the focused contract tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage/test_contracts.py -q
```

Expected: FAIL because the coverage enums and contracts do not exist.

- [ ] **Step 3: Implement the minimal enums and Pydantic contracts**

Use frozen DTOs and one model-level invariant function:

```python
class JobEntryStatus(StrEnum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    STALE = "stale"
    DISABLED = "disabled"


class JobSnapshotStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RecordJobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: UUID
    crawl_run_id: UUID
    status: JobSnapshotStatus
    pagination_complete: bool = False
    empty_confirmed: bool = False
    reported_total: int | None = Field(default=None, ge=0, le=2_147_483_647)
    pages_fetched: int = Field(ge=0, le=32_767)
    content_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, min_length=1, max_length=50)
    started_at: AwareDatetime
    completed_at: AwareDatetime
    seen_source_ids: frozenset[UUID] = Field(default_factory=frozenset, max_length=20_000)
```

`CoverageReport` rates are `Decimal` values quantized to four decimal places and are `None` when their denominator is zero; do not represent an undefined ratio as zero.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage/test_contracts.py -q
python -m ruff check app/models/enums.py app/ingestion/coverage tests/ingestion/coverage
python -m mypy app/models/enums.py app/ingestion/coverage
```

Expected: all commands PASS.

- [ ] **Step 5: Review and commit the coverage contracts**

Review that Task 1 changes contain no ORM or transaction behavior. Then run:

```powershell
git add backend/app/models/enums.py backend/app/models/__init__.py backend/app/ingestion/coverage backend/tests/ingestion/coverage
git commit -m "feat: define job coverage contracts"
```

### Task 2: Add Recruitment Entry and Snapshot Tables

**Files:**
- Create: `backend/app/models/job_entry.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/alembic/versions/0006_job_entries_and_snapshots.py`
- Modify: `backend/tests/migrations/test_migrations.py`
- Create: `backend/tests/models/test_job_entry.py`

**Interfaces:**
- Produces: `JobEntry`, uniquely identified by `(company_id, normalized_url)`.
- Produces: `JobCollectionSnapshot`, uniquely identified by `(job_entry_id, crawl_run_id)`.
- Consumes: Task 1 enums and existing `GUID`, `UTCDateTime`, `Company`, and `CrawlRun` tables.

- [ ] **Step 1: Write failing ORM and migration tests**

Add model tests for uniqueness, enum values, defaults, and foreign-key behavior. Extend the migration round trip with:

```python
EXPECTED_TABLES |= {"job_entries", "job_collection_snapshots"}

assert {column["name"] for column in inspector.get_columns("job_entries")} >= {
    "id", "company_id", "url", "normalized_url", "provider", "platform",
    "requires_rendering", "status", "failure_count", "last_checked_at",
    "last_success_at", "created_at", "updated_at",
}
assert {column["name"] for column in inspector.get_columns("job_collection_snapshots")} >= {
    "id", "job_entry_id", "crawl_run_id", "status", "pagination_complete",
    "empty_confirmed", "reported_total", "observed_count", "pages_fetched",
    "content_fingerprint", "command_hash", "error_code", "started_at",
    "completed_at", "created_at",
}
```

Add a dedicated test that upgrades to `0005`, inserts one legacy company/run, upgrades to `0006`, inserts an entry and snapshot, verifies uniqueness/FKs, downgrades to `0005`, and confirms legacy rows remain.

- [ ] **Step 2: Run model/migration tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/models/test_job_entry.py tests/migrations/test_migrations.py -q
```

Expected: FAIL because `0006` and the ORM models do not exist.

- [ ] **Step 3: Implement models and `0006`**

Use these table-level constraints and indexes:

```python
class JobEntry(Base, TimestampMixin):
    __tablename__ = "job_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_url", name="uq_job_entry_company_url"),
        Index("ix_job_entries_status_checked", "status", "last_checked_at"),
        Index("ix_job_entries_platform_status", "platform", "status"),
    )


class JobCollectionSnapshot(Base):
    __tablename__ = "job_collection_snapshots"
    __table_args__ = (
        UniqueConstraint("job_entry_id", "crawl_run_id", name="uq_job_snapshot_entry_run"),
        Index("ix_job_snapshots_entry_completed", "job_entry_id", "completed_at"),
        Index("ix_job_snapshots_status_completed", "status", "completed_at"),
    )
```

Required column behavior:

- Entry deletion cascades snapshots; company deletion cascades entries.
- Crawl run deletion sets `crawl_run_id` to null only if a future retention policy deletes runs; the `(entry, run)` uniqueness constraint applies to non-null run ids.
- `failure_count`, `observed_count`, and `pages_fetched` have application and server defaults of zero.
- URLs use `String(2000)`, provider/platform/error use `String(50)`, and both fingerprint and `command_hash` use `String(64)`.
- `command_hash` is the SHA-256 of the canonical validated command, including sorted `seen_source_ids`; it is required and exists only to make replay comparison exact.
- Snapshot rows are immutable after insertion at the service boundary; the ORM does not expose an update helper.

Set `down_revision = "0005_extend_job_type_values"`. Use named enum constraints with `native_enum=False` so SQLite and PostgreSQL share values.

Add a `postgresql` pytest marker to `backend/pyproject.toml` and a migration test that reads `TEST_POSTGRES_URL`, creates an isolated database schema, upgrades `0005 → head`, exercises both new tables, downgrades to `0005`, and drops only that isolated schema. The test skips when the variable is absent from normal local runs, but Task 7 requires it to run once before completion.

The PostgreSQL test must use a validated schema name with prefix `stage3a_test_`, set `search_path` only for its own connection, downgrade to `base`, verify the schema is empty, and use `DROP SCHEMA <validated_name>` without `CASCADE`. It must never target `public` or a caller-supplied schema name.

- [ ] **Step 4: Verify migrations, models, and PostgreSQL DDL**

Run:

```powershell
cd backend
python -m pytest tests/models/test_job_entry.py tests/migrations/test_migrations.py -q
python -m alembic upgrade head --sql *> $env:TEMP\stage3a-upgrade.sql
python -m ruff check app/models alembic/versions/0006_job_entries_and_snapshots.py tests/models tests/migrations
python -m mypy app/models/job_entry.py
```

Expected: tests and checks PASS; offline SQL generation exits zero and contains both new table names.

- [ ] **Step 5: Review and commit `0006`**

Confirm downgrade drops only the two new tables and their enum constraints. Then run:

```powershell
git add backend/app/models backend/pyproject.toml backend/alembic/versions/0006_job_entries_and_snapshots.py backend/tests/models/test_job_entry.py backend/tests/migrations/test_migrations.py
git commit -m "feat: add job entry and snapshot schema"
```

### Task 3: Extend Job Sources with Snapshot Lifecycle State

**Files:**
- Modify: `backend/app/models/job.py`
- Create: `backend/alembic/versions/0007_job_source_snapshot_lifecycle.py`
- Modify: `backend/tests/migrations/test_migrations.py`
- Create: `backend/tests/models/test_job_source_lifecycle.py`

**Interfaces:**
- Extends: `JobSource.job_entry_id: UUID | None`.
- Extends: `JobSource.last_seen_snapshot_id: UUID | None`.
- Extends: `JobSource.missing_complete_snapshots: int`, default 0.
- Consumes: Task 2 `job_entries` and `job_collection_snapshots`.

- [ ] **Step 1: Write failing legacy-preservation and FK tests**

Add a migration test that creates a company, posting, and source at revision `0006` with no entry metadata, upgrades to `0007`, and asserts:

```python
row = connection.execute(text(
    "SELECT job_entry_id, last_seen_snapshot_id, missing_complete_snapshots "
    "FROM job_sources WHERE id = :id"
), {"id": source_id}).one()
assert row == (None, None, 0)
assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
```

Then insert valid entry/snapshot references, reject cross-table missing references, downgrade to `0006`, and verify the legacy source row and all original `job_sources` fields remain.

- [ ] **Step 2: Run migration/model tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/migrations/test_migrations.py tests/models/test_job_source_lifecycle.py -q
```

Expected: FAIL because the lifecycle columns and `0007` do not exist.

- [ ] **Step 3: Implement nullable legacy-safe lifecycle columns**

Add the ORM fields and index:

```python
job_entry_id: Mapped[UUID | None] = mapped_column(
    ForeignKey("job_entries.id", ondelete="SET NULL")
)
last_seen_snapshot_id: Mapped[UUID | None] = mapped_column(
    ForeignKey("job_collection_snapshots.id", ondelete="SET NULL")
)
missing_complete_snapshots: Mapped[int] = mapped_column(
    Integer, default=0, server_default="0", nullable=False
)
```

Add `Index("ix_job_sources_entry_active", "job_entry_id", "is_active")`. Keep both new FKs nullable because Stage 2 sources do not yet have a trustworthy recruitment entry. `down_revision` is exactly `0006_job_entries_and_snapshots`.

- [ ] **Step 4: Verify both dialect paths and full migration round trip**

Run:

```powershell
cd backend
python -m pytest tests/migrations/test_migrations.py tests/models -q
python -m alembic upgrade head --sql *> $env:TEMP\stage3a-upgrade.sql
python -m ruff check app/models/job.py alembic/versions/0007_job_source_snapshot_lifecycle.py tests/migrations tests/models
python -m mypy app/models/job.py
```

Expected: all commands PASS and offline SQL contains `job_entry_id`, `last_seen_snapshot_id`, and `missing_complete_snapshots`.

- [ ] **Step 5: Review and commit `0007`**

Confirm the SQLite upgrade/downgrade keeps all `job_sources` dependents and the PostgreSQL DDL uses named foreign keys/indexes. Then run:

```powershell
git add backend/app/models/job.py backend/alembic/versions/0007_job_source_snapshot_lifecycle.py backend/tests/migrations/test_migrations.py backend/tests/models/test_job_source_lifecycle.py
git commit -m "feat: track job source snapshot lifecycle"
```

### Task 4: Add Transaction-Neutral Coverage Repository

**Files:**
- Create: `backend/app/ingestion/coverage/repository.py`
- Create: `backend/tests/ingestion/coverage/conftest.py`
- Create: `backend/tests/ingestion/coverage/test_repository.py`

**Interfaces:**
- Produces: `CoverageRepository.ensure_entry(company_id, url, provider, platform, requires_rendering) -> JobEntry`.
- Produces: `CoverageRepository.get_snapshot(entry_id, crawl_run_id) -> JobCollectionSnapshot | None`.
- Produces: `CoverageRepository.insert_snapshot(command) -> JobCollectionSnapshot`.
- Produces: `CoverageRepository.lock_entry_sources(entry_id) -> tuple[JobSource, ...]`.
- Produces: `CoverageRepository.recompute_job_activity(job_ids) -> int`.
- Constraint: repository methods flush but never begin, commit, or roll back the caller's outer transaction.

- [ ] **Step 1: Write failing entry and repository tests**

Cover normalized idempotency and ownership:

```python
def test_ensure_entry_normalizes_url_and_reuses_company_entry(repository, company) -> None:
    first = repository.ensure_entry(
        company.id, "https://jobs.example.com/openings/?utm_source=x",
        provider="official", platform="self_hosted", requires_rendering=False,
    )
    second = repository.ensure_entry(
        company.id, "https://jobs.example.com/openings",
        provider="official", platform="self_hosted", requires_rendering=False,
    )
    assert second.id == first.id


def test_same_url_can_belong_to_different_companies(repository, companies) -> None:
    left = repository.ensure_entry(companies[0].id, SHARED_URL, **ENTRY_FIELDS)
    right = repository.ensure_entry(companies[1].id, SHARED_URL, **ENTRY_FIELDS)
    assert left.id != right.id
```

Also test public credential-free URL validation, missing company/run rejection, entry-company versus run-company mismatch rejection, duplicate snapshot uniqueness, stable source lock ordering, and recomputing a posting to active while any source remains active.

- [ ] **Step 2: Run repository tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage/test_repository.py -q
```

Expected: FAIL because `CoverageRepository` does not exist.

- [ ] **Step 3: Implement repository helpers without transaction ownership**

Reuse `normalize_url` and the public URL validator. Use stable ordering for locks:

```python
def lock_entry_sources(self, entry_id: UUID) -> tuple[JobSource, ...]:
    statement = (
        select(JobSource)
        .where(JobSource.job_entry_id == entry_id)
        .order_by(JobSource.id)
        .with_for_update()
    )
    return tuple(self.session.scalars(statement))
```

`ensure_entry` may use a nested savepoint to recover the named `uq_job_entry_company_url` race, then re-read the winner. It must reject a reused entry if caller-supplied provider/platform/rendering metadata conflicts instead of silently rewriting provenance.

`insert_snapshot` requires `CrawlRun.company_id == JobEntry.company_id`, stores `observed_count=len(command.seen_source_ids)` and `command_hash=command.command_hash()`, but does not change any sources. Task 5 owns lifecycle mutation.

- [ ] **Step 4: Verify repository behavior and surrounding persistence tests**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage/test_repository.py tests/ingestion/persistence -q
python -m ruff check app/ingestion/coverage/repository.py tests/ingestion/coverage
python -m mypy app/ingestion/coverage/repository.py
```

Expected: all commands PASS and the existing persistence suite remains unchanged.

- [ ] **Step 5: Review and commit the repository**

Verify no repository method calls `commit()` or starts an outer transaction. Then run:

```powershell
git add backend/app/ingestion/coverage/repository.py backend/tests/ingestion/coverage
git commit -m "feat: add job coverage repository"
```

### Task 5: Record Snapshots and Apply Safe Source Lifecycle Atomically

**Files:**
- Create: `backend/app/ingestion/coverage/service.py`
- Modify: `backend/app/ingestion/coverage/__init__.py`
- Create: `backend/tests/ingestion/coverage/test_service.py`

**Interfaces:**
- Produces: `JobCoverageService.record(command: RecordJobSnapshot) -> SnapshotRecordResult`.
- Consumes: Task 1 commands/results and Task 4 repository.
- Transaction contract: the service requires a clean SQLAlchemy session, owns one outer transaction, locks the entry and its sources, inserts the snapshot, applies lifecycle changes, and commits once.

- [ ] **Step 1: Write failing atomic lifecycle tests**

Cover all state transitions:

```python
def test_two_complete_absences_deactivate_source_and_posting(service, source, posting) -> None:
    first = service.record(complete_snapshot(seen_source_ids=set(), run_id=uuid4()))
    assert first.sources_missing_incremented == 1
    assert source.missing_complete_snapshots == 1
    assert source.is_active is True

    second = service.record(complete_snapshot(seen_source_ids=set(), run_id=uuid4()))
    assert second.sources_deactivated == 1
    assert source.missing_complete_snapshots == 2
    assert source.is_active is False
    assert posting.is_active is False


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_noncomplete_snapshot_never_changes_sources(service, source, status) -> None:
    service.record(noncomplete_snapshot(status=status))
    assert source.missing_complete_snapshots == 0
    assert source.is_active is True


def test_seen_source_reactivates_and_resets_counter(service, inactive_source, posting) -> None:
    result = service.record(complete_snapshot(seen_source_ids={inactive_source.id}))
    assert result.sources_reactivated == 1
    assert inactive_source.is_active is True
    assert inactive_source.missing_complete_snapshots == 0
    assert posting.is_active is True
```

Also test: another active source keeps the posting active; a seen source from another entry is rejected; a legacy source with `job_entry_id=None` is untouched; duplicate identical replay returns `created=False` and does not increment twice; duplicate conflicting replay raises `CoverageConflict(code="snapshot_conflict")`; injected flush failure rolls back snapshot and lifecycle changes.

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage/test_service.py -q
```

Expected: FAIL because `JobCoverageService` does not exist.

- [ ] **Step 3: Implement the single-transaction lifecycle service**

Use the same clean-session rule as `PersistenceService`:

```python
def record(self, command: RecordJobSnapshot) -> SnapshotRecordResult:
    if self.session.in_transaction():
        raise CoverageConflict(code="active_session_transaction")
    with self.session.begin():
        self.repository.lock_entry(command.entry_id)
        existing = self.repository.get_snapshot(command.entry_id, command.crawl_run_id)
        if existing is not None:
            return self._replay(existing, command)
        snapshot = self.repository.insert_snapshot(command)
        if command.status is JobSnapshotStatus.SUCCEEDED and command.pagination_complete:
            counters = self._apply_complete_snapshot(snapshot, command.seen_source_ids)
        else:
            counters = _LifecycleCounters()
        jobs_recomputed = self.repository.recompute_job_activity(counters.affected_job_ids)
    return SnapshotRecordResult(snapshot_id=snapshot.id, created=True, **counters.payload(), jobs_recomputed=jobs_recomputed)
```

Persist and compare the deterministic `command_hash` introduced in Task 2. The signature includes status, completeness, empty flag, counts, fingerprint, error, timestamps, and sorted source ids. Replay with the same hash returns `created=False`; a different hash raises `snapshot_conflict` before any state mutation.

Lock sources in UUID order. Update seen sources first: set `last_seen_snapshot_id`, set `last_seen_at=command.completed_at`, reset `missing_complete_snapshots=0`, and reactivate the source. Then increment only unseen sources attached to the same entry. Recompute only affected postings using an `EXISTS` query over active sources.

For every newly created snapshot, set `JobEntry.last_checked_at=command.completed_at`. A successful complete snapshot sets the entry to `active`, resets `failure_count=0`, and updates `last_success_at`; a partial or failed snapshot increments `failure_count` but does not automatically mark the entry stale in Stage 3A. Replay does not update entry health a second time.

- [ ] **Step 4: Verify focused, expiration, and persistence behavior**

Run:

```powershell
cd backend
python -m pytest tests/ingestion/coverage tests/tasks/test_expiration.py tests/ingestion/persistence -q
python -m ruff check app/ingestion/coverage tests/ingestion/coverage
python -m mypy app/ingestion/coverage
```

Expected: all commands PASS; the existing 30-day expiration behavior remains valid for legacy and non-complete sources.

- [ ] **Step 5: Review and commit atomic lifecycle recording**

Review transaction ownership, replay equality, lock order, and rollback tests. Then run:

```powershell
git add backend/app/ingestion/coverage backend/tests/ingestion/coverage
git commit -m "feat: record safe job list snapshots"
```

### Task 6: Add Internal Coverage Reports and JSON CLI

**Files:**
- Create: `backend/app/coverage/__init__.py`
- Create: `backend/app/coverage/service.py`
- Create: `backend/app/coverage/cli.py`
- Create: `backend/tests/coverage/__init__.py`
- Create: `backend/tests/coverage/test_service.py`
- Create: `backend/tests/coverage/test_cli.py`

**Interfaces:**
- Produces: `CoverageReportService.build(as_of: datetime, refresh_window: timedelta = timedelta(hours=24)) -> CoverageReport`.
- Produces: `python -m app.coverage.cli [--as-of ISO-8601] [--refresh-hours 24]`, which prints one JSON object and exits nonzero for invalid input/database failure.
- Consumes: `Company`, `JobEntry`, `JobCollectionSnapshot`, Task 1 `CoverageReport`, and existing `SessionLocal`.

- [ ] **Step 1: Write failing denominator and CLI tests**

Build fixtures with: one company without an entry, one with an active entry and recent complete empty snapshot, one with an active entry and recent complete non-empty snapshot, one stale entry, and one active entry with only a failed snapshot.

Assert exact counters and denominators:

```python
report = service.build(as_of=AS_OF)
assert report.target_companies == 5
assert report.active_entry_companies == 3
assert report.recently_enumerated_companies == 2
assert report.complete_list_companies == 2
assert report.confirmed_empty_companies == 1
assert report.entry_coverage_rate == Decimal("0.6000")
assert report.enumeration_rate == Decimal("0.6667")
assert report.completeness_rate == Decimal("1.0000")
assert report.refresh_slo_rate == Decimal("0.4000")
```

Add empty-database tests asserting all counters are zero and all denominator-free rates are `None`. CLI tests parse stdout JSON, verify decimal values are strings, verify UTC ISO timestamps, and reject naive `--as-of` values.

- [ ] **Step 2: Run report tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/coverage -q
```

Expected: FAIL because the coverage report package does not exist.

- [ ] **Step 3: Implement aggregate queries and CLI**

Use `COUNT(DISTINCT company_id)` subqueries so multiple entries/snapshots cannot inflate company counts. Select only each entry's latest qualifying snapshot inside the refresh window. Definitions are exact:

```python
entry_coverage_rate = active_entry_companies / target_companies
enumeration_rate = recently_enumerated_companies / active_entry_companies
completeness_rate = complete_list_companies / recently_enumerated_companies
refresh_slo_rate = recently_enumerated_companies / target_companies
```

A recently enumerated company has at least one `succeeded`, complete snapshot inside the window; a complete confirmed empty snapshot counts as successful enumeration. `partial` and `failed` do not count.

The CLI uses `json.dumps(report.model_dump(mode="json"), sort_keys=True)` and writes diagnostics to stderr without credentials or SQL text.

- [ ] **Step 4: Verify reports, query count, and static checks**

Run:

```powershell
cd backend
python -m pytest tests/coverage -q
python -m ruff check app/coverage tests/coverage
python -m mypy app/coverage
```

Add a SQLAlchemy event counter test and require `CoverageReportService.build` to use a bounded number of SQL statements independent of company count. Expected: all commands PASS.

- [ ] **Step 5: Review and commit internal reporting**

Confirm the task adds no public API or frontend surface and does not expose company/source details. Then run:

```powershell
git add backend/app/coverage backend/tests/coverage
git commit -m "feat: report job coverage metrics"
```

### Task 7: Add Stage 3A Integration Acceptance and Close the Gate

**Files:**
- Create: `backend/tests/integration/test_job_coverage_lifecycle.py`
- Modify: `backend/tests/migrations/test_migrations.py`
- Modify: `README.md`
- Modify: `docs/dev/job-coverage-at-scale-plan.md`
- Modify: `docs/dev/migration-master-plan.md`
- Modify: `docs/superpowers/company-search-iteration.md`

**Interfaces:**
- Verifies: migrations → entry registration → snapshot recording → source lifecycle → canonical posting activity → coverage report.
- Documents: offline report command, schema upgrade, rollback boundary, and Stage 3B handoff.

- [ ] **Step 1: Write the failing Stage 3A acceptance flow**

Create one database-backed test that:

```python
def test_stage3a_coverage_lifecycle_and_report(session, coverage_service, report_service) -> None:
    # Company A has two sources on one entry; Company B has a confirmed empty entry.
    coverage_service.record(complete_snapshot(company_a_entry, seen={source_a.id, source_b.id}))
    coverage_service.record(complete_snapshot(company_b_entry, seen=set(), empty_confirmed=True))
    coverage_service.record(complete_snapshot(company_a_entry, seen={source_b.id}))
    coverage_service.record(complete_snapshot(company_a_entry, seen={source_b.id}))

    session.expire_all()
    assert source_a.is_active is False
    assert source_b.is_active is True
    assert posting.is_active is True
    report = report_service.build(as_of=AS_OF)
    assert report.recently_enumerated_companies == 2
    assert report.confirmed_empty_companies == 1
```

Add a failed snapshot between the two complete absences and prove it neither increments nor resets the missing counter. Replay every successful command once and prove counts/lifecycle remain unchanged.

- [ ] **Step 2: Run the integration test and verify its pre-documentation state**

Run:

```powershell
cd backend
python -m pytest tests/integration/test_job_coverage_lifecycle.py -q
```

Expected: PASS only after Tasks 1–6; if it fails, fix the owning task before editing documentation.

- [ ] **Step 3: Document exact operations and approval state**

Add these commands to the existing root `README.md`:

```powershell
python -m alembic upgrade head
python -m app.coverage.cli --refresh-hours 24
python -m alembic downgrade 0005_extend_job_type_values
```

Document that downgrade is allowed only before Stage 3B writes production snapshot-linked sources. `TEST_POSTGRES_URL` is a test-runner input, not an application setting; do not add it to Settings or `.env.example`.

Update both approved Stage 3 documents and the global iteration tracker with actual commit ids, test counts, review findings, and Stage 3A status. Leave Stage 3B as “awaiting separate implementation plan and approval.”

- [ ] **Step 4: Run the full Stage 3A completion matrix**

Run fresh commands from `backend/`:

```powershell
python -m ruff check app tests alembic
python -m mypy app
python -m pytest -q
python -m pytest tests/integration -q
python -m pytest tests/migrations tests/seed -q
python -m pytest -m performance -q
python -m alembic upgrade head --sql *> $env:TEMP\stage3a-upgrade.sql
if (-not $env:TEST_POSTGRES_URL) { throw "TEST_POSTGRES_URL is required for the Stage 3A completion gate" }
python -m pytest -m postgresql tests/migrations -q
```

Then verify hygiene from the repository root:

```powershell
git diff --check
git status --short
git diff --cached --name-only
```

Expected: all test/check commands PASS; offline migration SQL generation exits zero; no generated database, cache, report, or secret file is staged.

- [ ] **Step 5: Run final review and commit Stage 3A documentation**

Request independent specification-compliance and code-quality reviews across the entire Stage 3A commit range. Resolve every Critical/Important finding, rerun the completion matrix, and record any explicitly deferred Minor finding.

Stage only files actually changed by this task:

```powershell
git add backend/tests/integration/test_job_coverage_lifecycle.py backend/tests/migrations/test_migrations.py README.md docs/dev/job-coverage-at-scale-plan.md docs/dev/migration-master-plan.md docs/superpowers/company-search-iteration.md
git commit -m "docs: close stage three coverage foundation gate"
```

Include `.env.example` only if the implementation introduced a consumed setting and its test. Stop after this commit and present the integration choices; do not begin Stage 3B.

## Completion Gate

Stage 3A is complete only when all seven tasks are independently reviewed and the final current-HEAD matrix passes. The completion evidence must demonstrate:

- migrations from an empty database and from revision `0005`;
- legacy job sources preserved with nullable entry/snapshot links;
- complete empty lists distinguished from failures;
- partial/failed snapshots cannot deactivate sources;
- two complete absences deactivate only the missing entry source;
- another active source keeps the canonical posting active;
- identical replay is side-effect free and conflicting replay is rejected;
- report denominators do not count successful empty lists as failures;
- report query count remains bounded as company count grows;
- no current Provider writes a false complete snapshot;
- no live network, browser, Redis, or LLM dependency enters the default suite;
- no main-workspace user WIP is staged or altered.

After this gate, write a separate Stage 3B ATS integration implementation plan and request new user approval.
