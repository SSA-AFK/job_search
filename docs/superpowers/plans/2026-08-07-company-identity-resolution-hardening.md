# Company Identity Resolution Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Task 10 真实数据导入前，移除旧 ingestion 的模糊自动合并和主名称覆盖，交付持久化身份审核、稳定主名称、历史只读审计及 PostgreSQL 万级有界候选召回。

**Architecture:** 新增独立 `app.company_identity` 域，resolver 只允许唯一精确名称/别名自动关联，其余相似或冲突身份生成冻结审核 draft。`NormalizedBatchBuilder` 返回显式 outcome，orchestrator 在确认 crawl claim 后记录审核项并阻止业务入库；人工决定通过独立事务创建公司、追加别名或显式重命名。

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy 2.x, Alembic, PostgreSQL `pg_trgm`, SQLite fixture tests, pytest, Ruff, mypy

## Global Constraints

- Approved baseline is commit `2143f8f` on `codex/gate1-local-benchmark-design`; Task 10 remains paused until every task and the final whole-branch review pass.
- Only a unique exact canonical name or exact alias may auto-link an existing Company. Fuzzy score, website, recruitment identity, legal identifier, city, and score margin never auto-link.
- A review-required ingestion batch writes no Company, CompanyAlias, JobPosting, RegulatoryFiling, SourceDocument, CompanySource, or cache state.
- Ordinary ingestion never changes an existing `canonical_name` or `normalized_name`; only explicit `rename_canonical` may do so, atomically preserving the old name as an alias.
- Review decisions are append-only, exact-replay idempotent, conflict rejecting, UTC timestamped, and sanitized.
- Historical audit is read-only and never auto-splits, auto-merges, renames, or repairs data.
- PostgreSQL production similarity uses bounded Top-K `pg_trgm` queries. It never reads every Company into Python; missing similarity capability fails closed to review.
- SQLite supports offline behavior and migration tests but is not the production-scale similarity implementation.
- Migration is exactly `0009_company_identity_review`, descending from `0008_gate1_manifest_discovery`; planned `job_details` and coverage indexes become `0010` and `0011`.
- Default tests perform no live HTTP, Redis, LLM, model API, browser, or job-list access.
- Never read, print, stage, commit, or log values from `66.md`, `test.env`, raw candidate extracts, review working files, or external runtime reports.
- Never recursively delete files or directories. Test cleanup targets validated individual files or isolated non-public PostgreSQL schemas without `CASCADE`.
- CLI stdout is one sorted JSON object; diagnostics never echo credentials, authorization headers, database URLs, raw bodies, rejected decision values, or tracebacks.
- Each task ends with focused tests, relevant regression tests, Ruff, mypy for changed modules, an independent spec/quality review, and one scoped commit. Review fixes are additive commits.

---

## File Map

- `backend/app/company_identity/contracts.py`: frozen identity inputs, resolution outcomes, review decisions, audit DTOs, stable enums and limits.
- `backend/app/company_identity/models.py`: immutable review items and append-only decisions.
- `backend/app/company_identity/resolver.py`: exact ownership resolution, conflict evidence and bounded fuzzy review candidates.
- `backend/app/company_identity/repository.py`: SQLAlchemy exact-owner, evidence-owner and PostgreSQL `pg_trgm` Top-K queries.
- `backend/app/company_identity/service.py`: review recording, export and decision application transactions.
- `backend/app/company_identity/audit.py`: deterministic read-only historical audit.
- `backend/app/ingestion/persistence/contracts.py`: `BatchBuildOutcome` union at the normalization boundary.
- `backend/app/ingestion/orchestrator.py`: review outcome handling and zero-business-write stop.
- `backend/app/ingestion/persistence/service.py`: stable identity fields and alias persistence.
- `backend/app/manifest/cli.py`: operator subcommands for identity review and audit.
- `backend/alembic/versions/0009_company_identity_review.py`: review schema and PostgreSQL trigram indexes.
- `backend/tests/company_identity/`: contracts, resolver, repository, service and audit tests.
- `backend/tests/performance/test_company_identity_resolution.py`: 10,000-company PostgreSQL bounded-query gate.

### Task 1: Define Company Identity Contracts

**Files:**
- Create: `backend/app/company_identity/__init__.py`
- Create: `backend/app/company_identity/contracts.py`
- Create: `backend/tests/company_identity/__init__.py`
- Create: `backend/tests/company_identity/test_contracts.py`

**Interfaces:**
- Produces string enums `IdentityResolutionKind`, `IdentityReviewStatus`, `IdentityReviewAction`, `IdentityReviewReason`, `IdentityAuditSeverity`.
- Produces frozen Pydantic DTOs `PublicEvidenceReference`, `CompanyIdentityInput`, `CompanyIdentityNameOwner`, `CompanyIdentityCandidateMatch`, `CompanyIdentityResolution`, `CompanyIdentityReviewDraft`, `IdentityReviewDecisionInput`, `IdentityReviewRecordSummary`, `IdentityReviewApplySummary`, `IdentityReviewItem`, `IdentityAuditFinding`, and `IdentityAuditReport`.
- `CompanyIdentityInput` contains canonical name, aliases, optional official website, recruitment identity, legal identifiers, city, and public evidence references. Non-name evidence is review-only.
- `CompanyIdentityReviewDraft.stable_identity_hash` is the lowercase SHA-256 of sorted-key compact ASCII JSON over normalized public identity and evidence fields; it excludes candidate match scores and timestamps.

- [ ] **Step 1: Write failing frozen-contract and canonical-hash tests**

```python
def test_identity_input_is_frozen_bounded_and_rejects_extra_fields() -> None:
    value = CompanyIdentityInput(
        canonical_name="OpenAI China",
        aliases=("OpenAI 中国",),
        official_website="https://openai.com/zh-CN/",
        recruitment_identity=None,
        legal_identifiers=(),
        city="Shanghai",
        evidence=(
            PublicEvidenceReference(
                provider="official_site",
                url="https://openai.com/about?utm_source=test",
                evidence_id="document-1",
                confidence=Decimal("0.90"),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        value.canonical_name = "changed"


def test_review_draft_hash_uses_normalized_public_identity_not_score_or_time() -> None:
    left = review_draft(match_score=Decimal("91.0"), observed_at=utc(2026, 8, 7))
    right = review_draft(match_score=Decimal("88.0"), observed_at=utc(2026, 8, 8))
    assert left.stable_identity_hash == right.stable_identity_hash
    assert len(left.stable_identity_hash) == 64
    assert left.evidence[0].url == "https://openai.com/about"
```

Also cover all enum values, NFKC/casefold/whitespace normalization through the existing `normalize_name`, alias normalization/deduplication, UTC `Z` serialization, URL userinfo rejection, query/fragment removal in public evidence, lowercase 64-hex hashes, list/text limits, mapping immutability, and hostile reason rejection without input echo.

- [ ] **Step 2: Run the contract tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_contracts.py -q
```

Expected: collection fails only because `app.company_identity.contracts` does not exist.

- [ ] **Step 3: Implement minimal contracts and canonical serialization**

Use `ConfigDict(frozen=True, extra="forbid")`, tuple fields, `MappingProxyType` only for returned mappings, existing `normalize_name`/`normalize_url`, and explicit Pydantic length/count bounds. Define these exact actions:

```python
class IdentityReviewAction(StrEnum):
    LINK_AS_ALIAS = "link_as_alias"
    CREATE_NEW = "create_new"
    RENAME_CANONICAL = "rename_canonical"
    REJECT = "reject"
```

Do not include database sessions, ORM models, RapidFuzz or CLI behavior in this task.

- [ ] **Step 4: Run focused/static checks**

```powershell
cd backend
python -m pytest tests/company_identity/test_contracts.py -q
python -m ruff check app/company_identity/contracts.py tests/company_identity/test_contracts.py
python -m mypy app/company_identity/contracts.py
```

- [ ] **Step 5: Review and commit contracts**

```powershell
git add backend/app/company_identity/__init__.py backend/app/company_identity/contracts.py backend/tests/company_identity
git diff --cached --check
git commit -m "feat: define company identity contracts"
```

### Task 2: Add Review Persistence and Migration 0009

**Files:**
- Create: `backend/app/company_identity/models.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0009_company_identity_review.py`
- Create: `backend/tests/company_identity/test_models.py`
- Modify: `backend/tests/migrations/test_migrations.py`

**Interfaces:**
- Produces ORM `CompanyIdentityReviewItem` and `CompanyIdentityReviewDecision`.
- ORM table names are exactly `company_identity_review_items` and `company_identity_review_decisions`.
- Review item includes UUID id, unique 64-hex stable hash, immutable `first_crawl_run_id` FK, status, immutable public candidate/evidence/match JSON, created/resolved timestamps.
- Decision includes UUID id, review item FK, action, target/resulting Company FKs, reason, decided timestamp and unique decision hash.
- Migration revision is `0009_company_identity_review`; PostgreSQL creates `pg_trgm` if absent and GiST `gist_trgm_ops` indexes `ix_companies_normalized_name_trgm` and `ix_company_aliases_normalized_alias_trgm`.

- [ ] **Step 1: Write failing ORM constraint and migration tests**

```python
def test_review_item_hash_is_unique_and_decision_is_append_only(session: Session) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(persisted_review_item(session, stable_hash="a" * 64))
            session.flush()
    assert session.get(CompanyIdentityReviewItem, item.id) is not None


def test_0009_postgresql_sql_contains_bounded_similarity_indexes() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0009_company_identity_review.py"
    )
    migration = runpy.run_path(str(migration_path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={"as_sql": True, "output_buffer": output},
    )
    migration["upgrade"].__globals__["op"] = Operations(context)
    migration["upgrade"]()
    sql = " ".join(output.getvalue().split())
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert "ix_companies_normalized_name_trgm" in sql
    assert "gist_trgm_ops" in sql
```

Also assert JSON/list fields are non-null, enum CHECK names are stable, FKs have explicit names/delete behavior, review status index exists, decision hash uniqueness exists, SQLite upgrade/downgrade preserves Stage 3A/3B0 rows, PostgreSQL downgrade removes only 0009 tables/indexes and contains no `CASCADE`, and the shared `pg_trgm` extension is not dropped.

- [ ] **Step 2: Run model/migration tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_models.py tests/migrations/test_migrations.py -q
```

Expected: fails because review ORM and revision `0009_company_identity_review` are absent.

- [ ] **Step 3: Implement ORM and migration**

Follow the `GUID`, `UTCDateTime`, non-native enum and named-constraint patterns from `app.manifest.models` and `0008_gate1_manifest_discovery.py`. SQLite skips trigram extension/index DDL; PostgreSQL uses explicit `op.execute` statements. Update the lazy model loader in `app.models.__init__` so `Base.metadata` includes both company identity models without importing runtime services.

- [ ] **Step 4: Run migration gates**

```powershell
cd backend
python -m pytest tests/company_identity/test_models.py tests/migrations/test_migrations.py -q
python -m alembic upgrade head --sql
python -m alembic downgrade head:0008_gate1_manifest_discovery --sql
python -m ruff check app/company_identity/models.py app/models/__init__.py alembic/versions/0009_company_identity_review.py tests/company_identity/test_models.py tests/migrations/test_migrations.py
python -m mypy app/company_identity/models.py app/models/__init__.py
```

If `TEST_POSTGRES_URL` is configured, also run the isolated PostgreSQL round-trip marker and confirm the validated test schema is absent afterward. Never print the URL.

- [ ] **Step 5: Review and commit persistence schema**

```powershell
git add backend/app/company_identity/models.py backend/app/models/__init__.py backend/alembic/versions/0009_company_identity_review.py backend/tests/company_identity/test_models.py backend/tests/migrations/test_migrations.py
git diff --cached --check
git commit -m "feat: persist company identity reviews"
```

### Task 3: Implement Exact-Only Resolution and Bounded PostgreSQL Recall

**Files:**
- Create: `backend/app/company_identity/repository.py`
- Create: `backend/app/company_identity/resolver.py`
- Modify: `backend/app/ingestion/deduplication/company.py`
- Modify: `backend/app/ingestion/repositories.py`
- Create: `backend/tests/company_identity/test_repository.py`
- Create: `backend/tests/company_identity/test_resolver.py`
- Modify: `backend/tests/ingestion/deduplication/test_company.py`
- Modify: `backend/tests/ingestion/test_repositories.py`
- Create: `backend/tests/performance/test_company_identity_resolution.py`

**Interfaces:**
- Produces protocol `CompanyIdentityRepository` with async methods `find_exact_name_owners(names: frozenset[str]) -> tuple[CompanyIdentityNameOwner, ...]`, `find_evidence_owner_ids(identity: CompanyIdentityInput) -> frozenset[UUID]`, `find_similar_names(names: frozenset[str], *, limit: int) -> tuple[CompanyIdentityCandidateMatch, ...]`, and `similarity_search_available() -> bool`.
- Produces `SqlAlchemyCompanyIdentityRepository(session: Session, *, similarity_limit: int = 20)`.
- Produces `CompanyIdentityResolver(repository).resolve(identity: CompanyIdentityInput) -> CompanyIdentityResolution`.
- `CompanyDeduplicator.resolve(candidate)` becomes a compatibility adapter that returns `existing`, `new`, or `review_required`; it never converts fuzzy results to existing.

- [ ] **Step 1: Write failing exact/fuzzy/evidence resolution tests**

```python
@pytest.mark.parametrize("candidate_name", ["Open Al", "OpenAI China", "OpenAI Group"])
def test_fuzzy_names_require_review_without_auto_link(candidate_name: str) -> None:
    repository = fake_repository(similar=(match(COMPANY_A, "OpenAI", score="88.0"),))
    result = asyncio.run(resolver(repository).resolve(identity(candidate_name)))
    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None


def test_unique_exact_alias_is_the_only_noncanonical_auto_link() -> None:
    repository = fake_repository(exact_owners={"openaichina": {COMPANY_A}})
    result = asyncio.run(resolver(repository).resolve(identity("OpenAI China")))
    assert result.kind is IdentityResolutionKind.EXISTING
    assert result.company_id == COMPANY_A
```

Cover multiple exact owners, short-name collision, tied fuzzy candidates, website/recruitment/legal identity matches that still require review, evidence owned by a different exact-name owner, no candidates => new, unavailable similarity => review, deterministic Top-K ordering, and no UUID tie-based auto selection.

- [ ] **Step 2: Write failing SQL shape and 10,000-company performance tests**

PostgreSQL query tests must prove:

```python
assert "<->" in compiled_sql
assert "LIMIT 20" in compiled_sql
assert "ORDER BY" in compiled_sql
assert "SELECT companies.id, companies.normalized_name FROM companies" not in unbounded_sql
```

The `@pytest.mark.performance` + `@pytest.mark.postgresql` test seeds exactly 10,000 Companies plus aliases in an isolated schema, runs `EXPLAIN`, asserts a trigram index appears in the plan, asserts at most 20 candidates are materialized, and confirms the resolver performs no Python full-table comparison. Skip only when `TEST_POSTGRES_URL` is unset; Task 10 cannot be unpaused while it is skipped.

- [ ] **Step 3: Run tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_repository.py tests/company_identity/test_resolver.py tests/ingestion/deduplication/test_company.py -q
```

Expected: missing resolver/repository modules and old inclusive-80 auto-match assertions fail.

- [ ] **Step 4: Implement repository and resolver**

Use one exact ownership query across canonical names and aliases, separate bounded evidence-owner queries, and PostgreSQL trigram-distance KNN queries using `column.op("<->")(candidate)` with SQL `ORDER BY` and `LIMIT`. Combine the independently bounded canonical/alias rows into one final bounded deterministic set. Recompute RapidFuzz display scores only for returned rows. On SQLite, allow a deterministic injected/fake similarity repository for unit tests; the production SQLAlchemy repository reports capability unavailable instead of scanning every Company.

Delete `_FUZZY_MATCH_THRESHOLD` auto-link semantics. The compatibility `CompanyMatch.kind` must use a string enum or literal union that includes `review_required`.

- [ ] **Step 5: Run focused/regression/performance gates**

```powershell
cd backend
python -m pytest tests/company_identity/test_repository.py tests/company_identity/test_resolver.py tests/ingestion/deduplication/test_company.py tests/ingestion/test_repositories.py -q
python -m pytest tests/performance/test_company_identity_resolution.py -m "performance and postgresql" -q
python -m ruff check app/company_identity/repository.py app/company_identity/resolver.py app/ingestion/deduplication/company.py app/ingestion/repositories.py tests/company_identity tests/ingestion/deduplication tests/performance/test_company_identity_resolution.py
python -m mypy app/company_identity/repository.py app/company_identity/resolver.py app/ingestion/deduplication/company.py app/ingestion/repositories.py
```

- [ ] **Step 6: Review and commit resolver**

```powershell
git add backend/app/company_identity/repository.py backend/app/company_identity/resolver.py backend/app/ingestion/deduplication/company.py backend/app/ingestion/repositories.py backend/tests/company_identity backend/tests/ingestion/deduplication/test_company.py backend/tests/ingestion/test_repositories.py backend/tests/performance/test_company_identity_resolution.py
git diff --cached --check
git commit -m "feat: require review for fuzzy company identities"
```

### Task 4: Build Review Recording and Decision Application

**Files:**
- Create: `backend/app/company_identity/service.py`
- Create: `backend/tests/company_identity/test_service.py`

**Interfaces:**
- Produces `record_identity_review(session: Session, *, crawl_run_id: UUID, draft: CompanyIdentityReviewDraft) -> IdentityReviewRecordSummary`.
- Produces `export_identity_review_queue(session: Session) -> tuple[IdentityReviewItem, ...]`.
- Produces `apply_identity_review_decisions(session: Session, decisions: Sequence[IdentityReviewDecisionInput]) -> IdentityReviewApplySummary`.
- Produces sanitized exceptions `IdentityReviewConflict`, `IdentityOwnerChanged`, and `IdentitySearchUnavailable` with stable public codes.

- [ ] **Step 1: Write failing review-recording and replay tests**

```python
def test_review_record_is_exact_replay_idempotent(session: Session, crawl_run: CrawlRun) -> None:
    first = record_identity_review(session, crawl_run_id=crawl_run.id, draft=review_draft())
    second = record_identity_review(session, crawl_run_id=crawl_run.id, draft=review_draft())
    assert first.review_item_id == second.review_item_id
    assert first.created is True
    assert second.created is False
    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 1
```

Also cover dirty-session rejection, supplied hash/public-value inconsistency, public evidence sanitization, bounded match snapshots, deterministic export, no raw document fields, and replay from another crawl run returning the same item without overwriting `first_crawl_run_id` or immutable content.

- [ ] **Step 2: Write failing decision tests for all four actions**

```python
def test_link_as_alias_keeps_canonical_name(session: Session) -> None:
    company = company_row(session, canonical_name="OpenAI")
    item = pending_review(session, candidate_name="OpenAI China")
    apply_identity_review_decisions(
        session,
        (decision(item, action="link_as_alias", target_company_id=company.id),),
    )
    session.refresh(company)
    assert company.canonical_name == "OpenAI"
    assert alias_owner(session, "openaichina") == company.id


def test_rename_canonical_preserves_old_name_as_alias(session: Session) -> None:
    company = company_row(session, canonical_name="Old Name")
    item = pending_review(session, candidate_name="New Name")
    apply_identity_review_decisions(
        session,
        (decision(item, action="rename_canonical", target_company_id=company.id),),
    )
    session.refresh(company)
    assert company.canonical_name == "New Name"
    assert alias_owner(session, "oldname") == company.id
```

Cover `create_new`, `reject`, global cross-table owner conflicts, owner changing after export, exact decision replay, different-decision conflict, append-only audit, fixed diagnostics without hostile reason echo, and rollback of every partial Company/Alias change.

- [ ] **Step 3: Run service tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_service.py -q
```

Expected: fails because service functions and exceptions are absent.

- [ ] **Step 4: Implement clean-session transactions and locking**

Validate every supplied Pydantic instance again. For decision application, lock the review item first, then involved Companies ordered by UUID, then alias rows ordered by normalized alias. Re-query current cross-table owners inside the same transaction. `create_new` uses a stable UUID derived from the review hash only after confirming no owner. `rename_canonical` inserts the old alias before updating identity fields and handles the new name's same-company alias without violating uniqueness.

- [ ] **Step 5: Run focused/PostgreSQL lock gates**

```powershell
cd backend
python -m pytest tests/company_identity/test_service.py -q
python -m ruff check app/company_identity/service.py tests/company_identity/test_service.py
python -m mypy app/company_identity/service.py
```

With `TEST_POSTGRES_URL`, run the two-session marker that proves one decision waits on the item lock, exact replay returns one result, conflicting concurrent decisions produce one winner and one stable conflict, and no partial aliases remain.

- [ ] **Step 6: Review and commit review service**

```powershell
git add backend/app/company_identity/service.py backend/tests/company_identity/test_service.py
git diff --cached --check
git commit -m "feat: apply audited company identity decisions"
```

### Task 5: Integrate Identity Review Into Ingestion and Stabilize Persistence

**Files:**
- Modify: `backend/app/ingestion/persistence/contracts.py`
- Modify: `backend/app/ingestion/orchestrator.py`
- Modify: `backend/app/ingestion/runtime.py`
- Modify: `backend/app/ingestion/persistence/service.py`
- Modify: `backend/tests/ingestion/test_orchestrator.py`
- Modify: `backend/tests/ingestion/test_orchestrator_builder.py`
- Modify: `backend/tests/ingestion/test_runtime.py`
- Modify: `backend/tests/ingestion/persistence/test_service.py`
- Create: `backend/tests/integration/test_company_identity_review_stop.py`

**Interfaces:**
- Produces frozen `BatchBuildOutcome` with exactly one of `batch: NormalizedBatch` or `review_draft: CompanyIdentityReviewDraft` and constructors `ready(batch)` / `review_required(draft)`.
- `NormalizedBatchBuilder.build(...) -> BatchBuildOutcome` propagates `CompanyCandidate.aliases` and supplies website/legal/public evidence to the resolver.
- Produces protocol method `IdentityReviewRecorder.record(*, crawl_run_id: UUID, draft: CompanyIdentityReviewDraft) -> IdentityReviewRecordSummary`.
- `IngestionOrchestrator` consumes the outcome, rechecks claim ownership, records review, then finishes with `CollectionStatus.FAILED` and `error_code="company_identity_review_required"` without calling persistence.
- `PersistenceService._upsert_company` never changes identity fields on an existing Company and upserts safe aliases from the candidate.

- [ ] **Step 1: Write failing builder/orchestrator zero-write tests**

```python
def test_review_required_records_review_and_writes_no_business_rows(runtime_db) -> None:
    before = business_table_counts(runtime_db)
    result = run_ingestion_with_fuzzy_company(runtime_db, "Example Artificial Intelligenc")
    after = business_table_counts(runtime_db)
    assert result.error_code == "company_identity_review_required"
    assert after == before
    assert pending_identity_review_count(runtime_db) == 1
```

Assert `PersistenceService.persist` and cache invalidation are not called, source documents from the stopped batch are absent, the review item binds the crawl run, lost claim prevents review write, and exact replay reuses the pending item.

- [ ] **Step 2: Write failing stable-name and alias-propagation tests**

```python
def test_existing_company_ingestion_keeps_identity_and_adds_candidate_aliases(session: Session) -> None:
    company = company_row(session, canonical_name="OpenAI")
    persist_existing_company_batch(
        session,
        company_id=company.id,
        candidate_name="OpenAI China",
        aliases=("OpenAI 中国",),
    )
    session.refresh(company)
    assert (company.canonical_name, company.normalized_name) == ("OpenAI", "openai")
    assert owned_aliases(session, company.id) >= {"openaichina", "openai中国"}
```

Cover alias ownership conflict rollback, old exact-name behavior, concurrent new-company insertion, non-identity profile updates, evidence fields, jobs/filings for ready batches, and no alias loss when builder reconstructs the candidate.

- [ ] **Step 3: Run integration tests and verify RED**

```powershell
cd backend
python -m pytest tests/ingestion/test_orchestrator.py tests/ingestion/persistence/test_service.py tests/integration/test_company_identity_review_stop.py -q
```

Expected: old builder returns `NormalizedBatch`, fuzzy match persists, aliases are lost, and existing identity fields drift.

- [ ] **Step 4: Implement outcome flow and persistence guard**

Keep review recording outside the builder. Inject an `IdentityReviewRecorder` protocol into `IngestionOrchestrator`. Extend `build_ingestion_orchestrator` with caller-owned `identity_review_write_session: Session`, require all four runtime sessions to be distinct, and wire the recorder to that session so the review transaction is independent from the never-started business persistence transaction. Preserve existing retryable infrastructure handling and sanitized diagnostics.

- [ ] **Step 5: Run ingestion/manifest regression gates**

```powershell
cd backend
python -m pytest tests/ingestion tests/integration/test_company_identity_review_stop.py tests/manifest/test_identity.py tests/manifest/test_service.py -q
python -m ruff check app/ingestion app/company_identity tests/ingestion tests/integration/test_company_identity_review_stop.py
python -m mypy app/ingestion app/company_identity
```

- [ ] **Step 6: Review and commit runtime integration**

```powershell
git add backend/app/ingestion/persistence/contracts.py backend/app/ingestion/orchestrator.py backend/app/ingestion/runtime.py backend/app/ingestion/persistence/service.py backend/tests/ingestion backend/tests/integration/test_company_identity_review_stop.py
git diff --cached --check
git commit -m "fix: gate ambiguous company persistence"
```

### Task 6: Add Deterministic Read-Only Historical Audit

**Files:**
- Create: `backend/app/company_identity/audit.py`
- Create: `backend/tests/company_identity/test_audit.py`

**Interfaces:**
- Produces `CompanyIdentityAuditService(session: Session, repository: CompanyIdentityRepository).build() -> IdentityAuditReport`.
- Report includes explicit denominators/counts and stable findings for cross-table name ownership, fuzzy clusters, website conflicts, incompatible recruitment identities, canonical drift signals, orphan aliases and stale pending reviews.
- Audit performs SELECT statements only and never repairs data.

- [ ] **Step 1: Write failing finding and immutability tests**

```python
def test_audit_reports_conflicts_without_mutating_database(session: Session) -> None:
    seed_cross_table_alias_conflict(session)
    seed_shared_website_conflict(session)
    before = identity_table_snapshot(session)
    report = audit_service(session).build()
    after = identity_table_snapshot(session)
    assert after == before
    assert {item.code for item in report.findings} >= {
        "cross_table_name_owner",
        "shared_website_identity",
    }
```

Also cover fuzzy name clusters, one company with incompatible ATS tenants, `normalize_name(canonical_name) != normalized_name`, accepted CandidateFact names no longer represented by canonical/aliases, orphan aliases, pending review owner changes, stable finding hashes/order, explicit scan denominators, URL query/userinfo redaction, empty database, and repeat byte-identical JSON.

- [ ] **Step 2: Run audit tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_audit.py -q
```

Expected: fails because audit module/service is absent.

- [ ] **Step 3: Implement bounded read-only audit**

Use grouped SQL queries and the repository's bounded PostgreSQL similarity operations; do not load raw SourceDocument text. Audit may read normalized public fields from Company, CompanyAlias, JobEntry, CandidateFact and pending review items. It must not call `flush`, `commit`, `delete`, or any repair helper.

- [ ] **Step 4: Run focused/static gates**

```powershell
cd backend
python -m pytest tests/company_identity/test_audit.py -q
python -m ruff check app/company_identity/audit.py tests/company_identity/test_audit.py
python -m mypy app/company_identity/audit.py
```

- [ ] **Step 5: Review and commit audit**

```powershell
git add backend/app/company_identity/audit.py backend/tests/company_identity/test_audit.py
git diff --cached --check
git commit -m "feat: audit company identity conflicts"
```

### Task 7: Add Identity Review and Audit CLI Commands

**Files:**
- Create: `backend/app/company_identity/cli.py`
- Modify: `backend/app/manifest/cli.py`
- Create: `backend/tests/company_identity/test_cli.py`
- Modify: `backend/tests/manifest/test_cli.py`

**Interfaces:**
- Adds argparse subcommands `identity-review-export OUTPUT`, `identity-review-apply DECISIONS`, and `company-identity-audit OUTPUT` to `python -m app.manifest.cli`.
- `company_identity.cli` contains pure composition functions `identity_review_export_payload(session: Session) -> tuple[IdentityReviewItem, ...]`, `identity_review_apply_payload(session: Session, decisions: Sequence[IdentityReviewDecisionInput]) -> IdentityReviewApplySummary`, and `company_identity_audit_payload(session: Session, repository: CompanyIdentityRepository) -> IdentityAuditReport`; they do not import `app.manifest.cli`, print, or write files.
- `app.manifest.cli` owns argparse, bounded file reads, external-path validation, atomic writes, sorted stdout JSON and sanitized top-level exception mapping. Dependency direction is `manifest.cli -> company_identity.cli` only.
- Outputs one sorted JSON object. Export/audit use sibling temp + `Path.replace`. Input/output work files must resolve outside repository root.

- [ ] **Step 1: Write failing subprocess contract tests**

```python
def test_identity_review_export_is_atomic_sorted_and_external(cli_environment, tmp_path) -> None:
    output = external_runtime_path(tmp_path, "reviews.json")
    result = run_cli("identity-review-export", str(output), environment=cli_environment)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '{"exported":1,"output":"reviews.json"}\n'
    assert json.loads(output.read_text("utf-8"))[0]["status"] == "pending"
```

Cover apply exact replay, changed decision conflict, all four actions, repository-contained path rejection, symlink escape, bounded/UTF-8/extra-field decision input, atomic cleanup preserving unrelated temp files, audit output, empty queues, database/config errors, invalid pg_trgm capability, no secret/traceback echo, and no network/Redis/LLM/browser imports.

- [ ] **Step 2: Run CLI tests and verify RED**

```powershell
cd backend
python -m pytest tests/company_identity/test_cli.py tests/manifest/test_cli.py -q
```

Expected: argparse rejects the three absent commands.

- [ ] **Step 3: Implement CLI composition**

In `app.manifest.cli`, reuse its existing `_json_bytes`, `_print_json`, `_atomic_write`, `_read_bounded`, settings/session construction and top-level sanitized exception mapping. Add separate validated external input/output path helpers that resolve symlinks and require the target parent outside `_REPOSITORY_ROOT`. `app.company_identity.cli` must not import those private helpers. Do not duplicate service transaction logic. Do not include target database paths or full output paths in stdout; emit only safe file names and counts.

- [ ] **Step 4: Run CLI/full offline gates**

```powershell
cd backend
python -m pytest tests/company_identity/test_cli.py tests/manifest/test_cli.py tests/integration/test_company_identity_review_stop.py -q
python -m ruff check app/company_identity/cli.py app/manifest/cli.py tests/company_identity/test_cli.py tests/manifest/test_cli.py
python -m mypy app/company_identity/cli.py app/manifest/cli.py
```

- [ ] **Step 5: Review and commit operator surface**

```powershell
git add backend/app/company_identity/cli.py backend/app/manifest/cli.py backend/tests/company_identity/test_cli.py backend/tests/manifest/test_cli.py
git diff --cached --check
git commit -m "feat: add company identity review commands"
```

### Task 8: Update Roadmaps and Run the Task 10 Release Gate

**Files:**
- Modify: `docs/dev/job-coverage-at-scale-plan.md`
- Modify: `docs/dev/migration-master-plan.md`
- Modify: `docs/superpowers/plans/2026-08-06-stage3b0-manifest-entry-discovery.md`
- Test: `backend/tests/company_identity/`
- Test: `backend/tests/performance/test_company_identity_resolution.py`
- Test: `backend/tests/integration/test_company_identity_review_stop.py`

**Interfaces:**
- Records `0009_company_identity_review`, `0010_job_details`, `0011_coverage_query_indexes` consistently in every roadmap.
- Adds the company identity audit and zero unresolved Critical/Important findings as explicit Task 10 prerequisites.
- Produces no real candidate import, live discovery, manifest artifact, or external runtime report in this task.

- [ ] **Step 1: Update iterative documents with implemented facts**

Record exact commit ids, migration id, focused/full test counts, PostgreSQL live-gate status, 10,000-company result, audit categories, remaining risks, and the unchanged Task 10 scope. Remove obsolete `0008_job_details` / `0009_coverage_query_indexes` references rather than leaving conflicting numbering.

- [ ] **Step 2: Run the complete offline regression gate**

```powershell
cd backend
python -m pytest tests/company_identity tests/ingestion tests/manifest tests/integration/test_company_identity_review_stop.py tests/migrations/test_migrations.py -q
python -m pytest tests/ingestion/providers tests/ingestion/coverage tests/coverage tests/api -q
python -m ruff check app tests alembic
python -m mypy app
```

Expected: PASS without network, Redis, LLM, model API, browser or warnings. Environment-dependent PostgreSQL tests may skip in the default offline command, but that does not unlock Task 10.

- [ ] **Step 3: Run mandatory local PostgreSQL and performance gates**

Load the approved external test environment without printing it, then run:

```powershell
cd backend
python -m pytest -m postgresql tests/migrations/test_migrations.py tests/company_identity/test_service.py -q
python -m pytest -m "performance and postgresql" tests/performance/test_company_identity_resolution.py -q
```

Expected: no skips; migration round trip, two-session decision concurrency, trigram query plan and exactly 10,000 seeded-company bounded recall pass. Confirm zero residual isolated schemas through the test's validated cleanup path; never use `CASCADE`.

- [ ] **Step 4: Run a read-only audit rehearsal on the dedicated local database**

```powershell
cd backend
python -m app.manifest.cli company-identity-audit D:\company_search_gate1_runtime\audits\pre-task10.json
```

Expected: one sanitized JSON stdout object, external atomic report, no database mutation. Do not proceed if any Critical/Important finding lacks an explicit human ruling. Do not commit the report.

- [ ] **Step 5: Run tracked-file secret and scope checks**

```powershell
git diff --check
git grep -n -I -E "(postgres(ql)?|redis)://[^[:space:]]+:[^[:space:]]+@|sk-[A-Za-z0-9_-]{12,}" -- . ":(exclude)66.md" ":(exclude)test.env"
git status --short
```

Expected: diff check passes, tracked-file secret scan prints nothing, and status contains no `66.md`, `test.env`, raw extracts, decisions, audit reports or runtime files.

- [ ] **Step 6: Review and commit roadmap/gate status**

```powershell
git add docs/dev/job-coverage-at-scale-plan.md docs/dev/migration-master-plan.md docs/superpowers/plans/2026-08-06-stage3b0-manifest-entry-discovery.md
git diff --cached --check
git commit -m "docs: gate task10 on company identity review"
```

After this task, run the mandatory whole-branch review over `2143f8f..HEAD`. Task 10 may be resumed only when the review is clean, all deferred Critical/Important findings have explicit human rulings, the local PostgreSQL/performance gates passed without skips, and the dedicated audit has no unresolved Critical/Important findings.
