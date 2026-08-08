# Stage 3B0 Manifest and Entry Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable, deterministic pipeline that imports public candidate facts, resolves independent recruiting identities, freezes an exactly 1,000-company manifest, discovers owned public recruitment entries, and reports an ATS census without collecting job lists.

**Architecture:** Add an isolated `app.manifest` domain beside the existing ingestion and coverage domains. PostgreSQL stores candidate facts, append-only review decisions, immutable manifest membership, and entry-discovery observations; canonical JSON artifacts make the frozen denominator reproducible. Live entry discovery is an explicit opt-in adapter over the existing `SafeHttpClient`, `RobotsPolicy`, and Zhihu provider, while every default test uses fixtures and no network.

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy 2.x, Alembic, SQLite fixture tests, PostgreSQL migration gates, pytest, httpx/respx, Ruff, mypy

## Global Constraints

- Implement only after explicit execution approval in an isolated worktree created from the approved baseline; do not modify or stage `66.md`, `test.env`, or protected WIP.
- Stage 3B0 includes candidate intake, deterministic recruiting-identity decisions, manual-review import/export, manifest freeze, official recruitment-entry discovery, and an observed ATS census.
- Stage 3B0 excludes job-list enumeration, job-detail enrichment, Playwright, LLM calls, Celery scale-out, the 20/100/1,000 full benchmark runner, public deployment, and remote push.
- Count one company by independent recruiting identity. Merge aliases that share an inseparable recruiting inventory; separate a subsidiary only when its entry and inventory are independently attributable.
- Require at least 1,500 `accepted` candidates after review before freezing exactly 1,000 manifest members; `review_required` records never enter quota inputs.
- Allocate 400 floor seats and 600 proportional seats across the nine approved AI categories exactly as specified in the design. A category floor shortage stops the freeze.
- Use only registered public or explicitly authorized sources. Do not log in, solve CAPTCHAs, rotate proxies, evade fingerprints, bypass access controls, or collect employee personal information.
- All network requests reuse `SafeHttpClient` and `RobotsPolicy`, revalidate redirects, allow only exact approved hosts, and start at no more than one request per domain per second.
- Zhihu is a cached discovery fallback only, limited to one request per second and at most 200 requests for the entire rehearsal. It is never job-list evidence.
- The model API key is never loaded by any Stage 3B0 module or command.
- Candidate import files and raw responses live outside Git. Checked-in artifacts contain only normalized public facts, evidence references, the source registry, the quota result, and the canonical frozen manifest.
- `manifest_version` is the lowercase SHA-256 of canonical JSON with sorted keys and deterministic member order. Frozen membership is immutable.
- Accepted entries persist through the existing `JobEntry` model. Discovery observations do not write `JobCollectionSnapshot` and never claim pagination completeness or confirmed emptiness.
- New Alembic revision is exactly `0008_gate1_manifest_discovery`, descending from `0007_job_source_snapshot_lifecycle`. Company identity hardening owns `0009_company_identity_review`; planned `job_details` and coverage-index revisions move to `0010` and `0011` in the migration roadmap.
- SQLite and PostgreSQL migration gates must preserve all Stage 3A rows and foreign keys. Default tests perform no live HTTP, Redis, LLM, or browser access.
- Never recursively delete files or directories. Test cleanup targets validated individual files or non-public isolated PostgreSQL schemas without `CASCADE`.
- Every CLI emits sanitized diagnostics: no credentials, authorization headers, database URLs, raw response bodies, or tracebacks.
- Each task ends with focused tests, relevant regression tests, Ruff, mypy for changed modules, an independent review gate, and one scoped commit.

---

## File Map

- `backend/app/manifest/contracts.py`: source registry, candidate fact, review, manifest, discovery, and census DTOs.
- `backend/app/manifest/registry.py`: load and validate the checked-in source registry.
- `backend/app/manifest/models.py`: candidate facts, review decisions, manifests, members, and discovery observations.
- `backend/app/manifest/candidates.py`: idempotent JSONL candidate import and deterministic normalization.
- `backend/app/manifest/identity.py`: exact auto-resolution and append-only manual review application.
- `backend/app/manifest/allocation.py`: category floors, largest remainder, diversity ordering, canonical JSON, and hash.
- `backend/app/manifest/discovery.py`: official-site link discovery and stable ATS census classification.
- `backend/app/manifest/service.py`: clean-session transaction boundaries for freeze and accepted entry persistence.
- `backend/app/manifest/reporting.py`: database-backed pool, review, manifest, entry, and ATS census reports.
- `backend/app/manifest/cli.py`: offline-by-default operator commands and explicit live discovery switch.
- `backend/data/gate1/source_registry.json`: reviewed public-source allowlist and budgets.
- `backend/data/gate1/manifest.json`: canonical frozen public manifest, created only after the live data gate.
- `backend/data/gate1/manifest.quota.json`: canonical allocation inputs and outputs for the same manifest.
- `backend/alembic/versions/0008_gate1_manifest_discovery.py`: Stage 3B0 persistence schema.
- `backend/tests/manifest/`: contract, registry, import, identity, allocation, discovery, service, report, and CLI tests.
- `backend/tests/integration/test_manifest_entry_discovery.py`: offline end-to-end Stage 3B0 acceptance flow.
- `docs/dev/job-coverage-at-scale-plan.md`: global iterative delivery status.
- `docs/dev/migration-master-plan.md`: migration numbering and Stage 3B0 status.

### Task 1: Define Source Registry and Domain Contracts

**Files:**
- Create: `backend/app/manifest/__init__.py`
- Create: `backend/app/manifest/contracts.py`
- Create: `backend/app/manifest/registry.py`
- Create: `backend/data/gate1/source_registry.json`
- Create: `backend/tests/manifest/__init__.py`
- Create: `backend/tests/manifest/test_contracts.py`
- Create: `backend/tests/manifest/test_registry.py`

**Interfaces:**
- Produces: `AiCategory`, `ConfidenceTier`, `CandidateDecisionStatus`, `ReviewAction`, `DiscoveryStatus`, `SourceClass`, and `SourceRole` string enums.
- Produces: frozen `SourceRegistry`, `SourceRegistryEntry`, `CandidateFactInput`, `ReviewDecisionInput`, `ManifestCompany`, `ManifestMemberData`, `AtsClassification`, `EntryDiscoveryResult`, `RecordDiscoveryCommand`, and `AtsCensus` Pydantic DTOs.
- Produces: `load_source_registry(path: Path) -> SourceRegistry`, rejecting duplicate ids, credential-bearing URLs, disabled references, rates above 1.0 QPS, and invalid budgets. Multiple reviewed registry entries may share a host but retain independent ids and budgets.

- [ ] **Step 1: Write failing contract and registry tests**

Cover all nine `AiCategory` values, UTC timestamp normalization, public credential-free URLs, bounded text/list fields, extra-field rejection, and immutable DTOs. Add these registry invariants:

```python
def test_registry_rejects_unsafe_rate_and_duplicate_source_id(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path,
        entries=[registry_entry(id="official-list", qps=1.1), registry_entry(id="official-list")],
    )
    with pytest.raises(ValueError, match="source registry is invalid"):
        load_source_registry(path)


def test_registry_contains_only_discovery_fallback_zhihu_initially() -> None:
    registry = load_source_registry(GATE1_REGISTRY_PATH)
    zhihu = registry.require("zhihu_global_search")
    assert str(zhihu.base_url) == "https://developer.zhihu.com/api/v1/content/global_search"
    assert zhihu.roles == frozenset({SourceRole.ENTRY_DISCOVERY_FALLBACK})
    assert zhihu.requests_per_second == Decimal("1.0")
    assert zhihu.rehearsal_request_budget == 200
```

The initial checked-in registry contains only the already authorized Zhihu endpoint. Candidate-pool sources are added to this file in Task 10 and committed before their extracts are imported; no unregistered evidence id is accepted.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/manifest/test_contracts.py tests/manifest/test_registry.py -q
```

Expected: FAIL because `app.manifest` and the registry do not exist.

- [ ] **Step 3: Implement the exact enums and registry boundary**

Use the approved category identifiers:

```python
class AiCategory(StrEnum):
    FOUNDATION_MODELS = "foundation_models"
    AI_CLOUD_MODEL_PLATFORMS = "ai_cloud_model_platforms"
    AI_CHIPS_COMPUTE = "ai_chips_compute"
    AUTONOMOUS_DRIVING_TRANSPORT = "autonomous_driving_transport"
    ROBOTICS_EMBODIED_AI = "robotics_embodied_ai"
    COMPUTER_VISION_IMAGING = "computer_vision_imaging"
    SPEECH_LANGUAGE_TECHNOLOGY = "speech_language_technology"
    ENTERPRISE_VERTICAL_AI = "enterprise_vertical_ai"
    DATA_INFRASTRUCTURE_MLOPS = "data_infrastructure_mlops"


class SourceRegistryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    name: str = Field(min_length=1, max_length=100)
    base_url: DocumentUrl
    source_class: SourceClass
    authorization_basis: str = Field(min_length=10, max_length=500)
    robots_policy: Literal["required", "api_contract"]
    roles: frozenset[SourceRole] = Field(min_length=1)
    requests_per_second: Decimal = Field(gt=0, le=Decimal("1.0"))
    rehearsal_request_budget: int | None = Field(default=None, ge=1, le=100_000)
    enabled: bool = True
```

The loader wraps Pydantic/JSON/path errors as `SourceRegistryError("source registry is invalid")` without including input content.

- [ ] **Step 4: Run focused tests and static checks**

```powershell
cd backend
python -m pytest tests/manifest/test_contracts.py tests/manifest/test_registry.py -q
python -m ruff check app/manifest tests/manifest
python -m mypy app/manifest
```

Expected: all commands PASS.

- [ ] **Step 5: Review and commit the registry boundary**

```powershell
git add backend/app/manifest backend/data/gate1/source_registry.json backend/tests/manifest
git commit -m "feat: define gate1 manifest contracts"
```

### Task 2: Add Stage 3B0 ORM Models

**Files:**
- Create: `backend/app/manifest/models.py`
- Modify: `backend/app/models/job_entry.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/tests/manifest/test_models.py`

**Interfaces:**
- Produces: `CandidateFact`, `CandidateReview`, `CompanyManifest`, `CompanyManifestMember`, and `EntryDiscoveryObservation` ORM models.
- Consumes: existing `Company` and `JobEntry`; Task 1 enums.

- [ ] **Step 1: Write failing ORM tests**

Assert table names, defaults, uniqueness, UTC fields, enum values, and these ownership rules:

```python
def test_manifest_membership_is_unique_by_position_and_company(session: Session) -> None:
    manifest = persisted_manifest(session)
    company = persisted_company(session)
    session.add_all([
        manifest_member(manifest, company, position=1),
        manifest_member(manifest, company, position=2),
    ])
    with pytest.raises(IntegrityError):
        session.flush()


def test_discovery_observation_cannot_own_another_company_entry(session: Session) -> None:
    observation = discovery_observation_for_company_a(linked_entry=entry_for_company_b(session))
    session.add(observation)
    with pytest.raises(IntegrityError):
        session.flush()
```

Enforce cross-company ownership with a composite foreign key from `(job_entry_id, company_id)` to a new unique constraint on `job_entries(id, company_id)`; do not rely only on service validation.

- [ ] **Step 2: Run model tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_models.py -q
```

Expected: FAIL because the models do not exist.

- [ ] **Step 3: Implement focused models**

Use these stable identities and constraints:

```python
class CandidateFact(Base, TimestampMixin):
    __tablename__ = "candidate_facts"
    __table_args__ = (
        UniqueConstraint("stable_evidence_id", name="uq_candidate_fact_evidence"),
        Index("ix_candidate_facts_decision_category", "decision_status", "primary_category"),
    )


class CompanyManifestMember(Base):
    __tablename__ = "company_manifest_members"
    __table_args__ = (
        UniqueConstraint("manifest_version", "position", name="uq_manifest_member_position"),
        UniqueConstraint("manifest_version", "company_id", name="uq_manifest_member_company"),
    )
```

`CandidateFact` stores normalized public fields, aliases as JSON, evidenced official-website and recruitment-URL candidates, `source_id`, `source_url`, `retrieved_at`, evidence summary, confidence tier/reason, decision status, and nullable resolved `company_id`. `CandidateReview` is append-only and records prior status, action, resulting status/company, reason, and `decided_at`. `CompanyManifest` stores the 64-character version, config fingerprint, member count, canonical quota JSON, and frozen timestamp. `EntryDiscoveryObservation` stores manifest/company, method, status, candidate/normalized URL, source id, ownership evidence, platform, rendering flag, stable error code, optional linked entry, and observation time.

- [ ] **Step 4: Run focused tests and static checks**

```powershell
cd backend
python -m pytest tests/manifest/test_models.py -q
python -m ruff check app/manifest/models.py app/models/__init__.py tests/manifest/test_models.py
python -m mypy app/manifest/models.py app/models/__init__.py
```

- [ ] **Step 5: Review and commit ORM models**

```powershell
git add backend/app/manifest/models.py backend/app/models/job_entry.py backend/app/models/__init__.py backend/tests/manifest/test_models.py
git commit -m "feat: add manifest discovery models"
```

### Task 3: Add the `0008` Migration

**Files:**
- Create: `backend/alembic/versions/0008_gate1_manifest_discovery.py`
- Modify: `backend/tests/migrations/test_migrations.py`

**Interfaces:**
- Produces: database schema matching Task 2 and revision `0008_gate1_manifest_discovery`.
- Preserves: all Stage 3A company, job entry, snapshot, and source rows across upgrade/downgrade.

- [ ] **Step 1: Write failing SQLite, offline DDL, and PostgreSQL tests**

Extend expected tables with:

```python
EXPECTED_TABLES |= {
    "candidate_facts",
    "candidate_reviews",
    "company_manifests",
    "company_manifest_members",
    "entry_discovery_observations",
}
```

Add a `0007 -> 0008 -> 0007` round trip that inserts Stage 3A rows first, exercises every new uniqueness/FK constraint, downgrades, and confirms the original rows remain. Extend the existing isolated PostgreSQL test using a schema named `stage3a_test_` followed by exactly 32 lowercase hexadecimal characters and the existing non-cascading cleanup helpers.

- [ ] **Step 2: Run migration tests and verify RED**

```powershell
cd backend
python -m pytest tests/migrations/test_migrations.py -q
```

Expected: FAIL because revision `0008_gate1_manifest_discovery` is absent.

- [ ] **Step 3: Implement migration with named constraints**

Set:

```python
revision = "0008_gate1_manifest_discovery"
down_revision = "0007_job_source_snapshot_lifecycle"
```

Use `GUID`, `UTCDateTime`, non-native named enum checks, named foreign keys, and explicit indexes matching Task 2. Add `uq_job_entries_id_company` before creating the composite observation foreign key; drop it only after dropping the observation table on downgrade. Do not rebuild or mutate existing Stage 3A data.

- [ ] **Step 4: Run migration matrix**

```powershell
cd backend
python -m pytest tests/migrations/test_migrations.py -q
python -m alembic upgrade head --sql > $null
python -m alembic downgrade 0007_job_source_snapshot_lifecycle:head --sql > $null
python -m ruff check alembic/versions/0008_gate1_manifest_discovery.py tests/migrations/test_migrations.py
python -m mypy app/manifest/models.py
```

Expected: all offline commands PASS. Run the opt-in PostgreSQL marker only when `TEST_POSTGRES_URL` points to the dedicated local Gate 1 database; its cleanup remains schema-scoped and non-recursive.

- [ ] **Step 5: Review and commit the migration**

```powershell
git add backend/alembic/versions/0008_gate1_manifest_discovery.py backend/tests/migrations/test_migrations.py
git commit -m "feat: migrate gate1 manifest data"
```

### Task 4: Import Candidate Facts Idempotently

**Files:**
- Create: `backend/app/manifest/candidates.py`
- Create: `backend/tests/manifest/test_candidates.py`

**Interfaces:**
- Produces: frozen `CandidateImportSummary(created: int, replayed: int)`.
- Produces: `canonical_candidate_fact(input: CandidateFactInput) -> bytes`.
- Produces: `stable_evidence_id(input: CandidateFactInput) -> str`.
- Produces: `classify_candidate_confidence(input: CandidateFactInput, source: SourceRegistryEntry) -> tuple[ConfidenceTier, str]`.
- Produces: `import_candidate_facts(session: Session, facts: Iterable[CandidateFactInput], registry: SourceRegistry) -> CandidateImportSummary`.
- Guarantees: exact replay is side-effect free; conflicting evidence id raises `CandidateEvidenceConflict` and rolls back the whole input batch.

- [ ] **Step 1: Write failing import tests**

```python
def test_exact_candidate_replay_is_idempotent(session: Session, registry: SourceRegistry) -> None:
    first = import_candidate_facts(session, [candidate_fact()], registry)
    second = import_candidate_facts(session, [candidate_fact()], registry)
    assert first.created == 1
    assert second == CandidateImportSummary(created=0, replayed=1)


def test_unregistered_source_rolls_back_batch(session: Session, registry: SourceRegistry) -> None:
    with pytest.raises(UnregisteredSourceError):
        import_candidate_facts(session, [candidate_fact(), candidate_fact(source_id="unknown")], registry)
    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0
```

Also test Unicode/ASCII name normalization, alias sorting/deduplication, URL normalization, UTC conversion, input bound enforcement, and conflict rollback.

- [ ] **Step 2: Run import tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_candidates.py -q
```

- [ ] **Step 3: Implement canonical import**

Hash canonical public evidence only:

```python
def stable_evidence_id(value: CandidateFactInput) -> str:
    return sha256(canonical_candidate_fact(value)).hexdigest()


def import_candidate_facts(
    session: Session,
    facts: Iterable[CandidateFactInput],
    registry: SourceRegistry,
) -> CandidateImportSummary:
    with session.begin():
        return CandidateImporter(session, registry).import_all(tuple(facts))
```

Normalize names through `app.core.normalization.normalize_name` and URLs through `normalize_url`; never use fuzzy similarity to auto-merge identities. Imports always start as `review_required`; source input cannot declare itself accepted. Derive confidence deterministically: high for a government/exchange/association/industrial-park record with an evidenced official website, medium for those source classes without a website or for an official-company-site record, and low for an authorized-API fallback. Store the generated reason rather than trusting an input reason.

- [ ] **Step 4: Run focused and regression checks**

```powershell
cd backend
python -m pytest tests/manifest/test_candidates.py tests/core/test_normalization.py -q
python -m ruff check app/manifest/candidates.py tests/manifest/test_candidates.py
python -m mypy app/manifest/candidates.py
```

- [ ] **Step 5: Review and commit candidate import**

```powershell
git add backend/app/manifest/candidates.py backend/tests/manifest/test_candidates.py
git commit -m "feat: import auditable candidate facts"
```

### Task 5: Resolve Recruiting Identities and Manual Reviews

**Files:**
- Create: `backend/app/manifest/identity.py`
- Create: `backend/tests/manifest/test_identity.py`

**Interfaces:**
- Produces: frozen `IdentityResolutionSummary`, `CandidateReviewItem`, and `ReviewSummary` result types.
- Produces: `auto_resolve_candidates(session: Session) -> IdentityResolutionSummary`.
- Produces: `export_review_queue(session: Session) -> tuple[CandidateReviewItem, ...]`.
- Produces: `apply_review_decisions(session: Session, decisions: Sequence[ReviewDecisionInput]) -> ReviewSummary`.
- Guarantees: only exact normalized-name/alias or exact evidenced recruitment-entry URL/tenant identity auto-merges; a shared ATS hostname alone never merges companies. All conflicting category, group/subsidiary, host ownership, and fuzzy-only matches remain `review_required`.

- [ ] **Step 1: Write failing identity tests**

Test exact alias merge, shared inseparable recruiting host merge, independently attributable subsidiary separation, fuzzy-only non-merge, conflicting decisions, replay, and append-only audit:

```python
def test_fuzzy_name_match_requires_review(session: Session) -> None:
    persist_candidates(session, "北京示例智能科技", "示例智能")
    summary = auto_resolve_candidates(session)
    assert summary.auto_accepted == 0
    assert summary.review_required == 2


def test_review_replay_is_idempotent_but_conflict_is_rejected(session: Session) -> None:
    decision = accept_as_new_identity(candidate_id=CANDIDATE_ID, canonical_name="示例智能")
    assert apply_review_decisions(session, [decision]).applied == 1
    assert apply_review_decisions(session, [decision]).replayed == 1
    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(session, [decision.model_copy(update={"action": ReviewAction.REJECT})])
```

- [ ] **Step 2: Run identity tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_identity.py -q
```

- [ ] **Step 3: Implement conservative identity resolution**

Create or link existing `Company` rows only inside a transaction. Auto-accept a singleton or exact-identity group only when every fact agrees on primary category and no evidence claims separable recruiting inventories; otherwise require review. Use `rapidfuzz.fuzz.ratio >= 90` only to flag near-name pairs for review, never to merge them. A merge decision also upserts globally unique `CompanyAlias` rows after proving they are unowned or already owned by the same company. Review exports contain public evidence and stable ids but no raw bodies or secrets. Reject any decision that references a non-reviewable candidate or would move an alias between companies without an explicit `MERGE` action.

- [ ] **Step 4: Run focused and company regression checks**

```powershell
cd backend
python -m pytest tests/manifest/test_identity.py tests/companies tests/seed/test_importer.py -q
python -m ruff check app/manifest/identity.py tests/manifest/test_identity.py
python -m mypy app/manifest/identity.py
```

- [ ] **Step 5: Review and commit identity resolution**

```powershell
git add backend/app/manifest/identity.py backend/tests/manifest/test_identity.py
git commit -m "feat: resolve recruiting identities"
```

### Task 6: Freeze the Deterministic 1,000-Company Manifest

**Files:**
- Create: `backend/app/manifest/allocation.py`
- Create: `backend/app/manifest/service.py`
- Create: `backend/tests/manifest/test_allocation.py`
- Create: `backend/tests/manifest/test_service.py`

**Interfaces:**
- Produces: frozen `ResolvedCandidate`, `QuotaAllocation`, and `FrozenManifest` result types used by the selection and persistence functions.
- Produces: `allocate_quotas(counts: Mapping[AiCategory, int], total: int = 1000) -> QuotaAllocation`.
- Produces: `select_manifest_members(candidates: Sequence[ResolvedCandidate], allocation: QuotaAllocation) -> tuple[ManifestMemberData, ...]`.
- Produces: `canonical_manifest_bytes(members: Sequence[ManifestMemberData]) -> bytes`.
- Produces: `freeze_manifest(session: Session, *, config_fingerprint: str) -> FrozenManifest`.
- Guarantees: exactly 1,000 members, immutable hash/membership, deterministic prefixes, and no unresolved candidate.

- [ ] **Step 1: Write failing quota, ordering, and freeze tests**

```python
def test_mixed_allocation_is_exact_and_deterministic() -> None:
    counts = {category: 200 + index for index, category in enumerate(AiCategory)}
    allocation = allocate_quotas(counts)
    assert sum(allocation.final.values()) == 1000
    assert sum(allocation.floor.values()) == 400
    assert sum(allocation.proportional.values()) == 600
    assert allocate_quotas(dict(reversed(tuple(counts.items())))) == allocation


def test_freeze_requires_1500_accepted_and_no_floor_shortage(session: Session) -> None:
    seed_resolved_candidates(session, accepted=1499)
    with pytest.raises(ManifestFreezeError, match="at least 1500 accepted"):
        freeze_manifest(session, config_fingerprint="a" * 64)


def test_frozen_manifest_replay_matches_and_conflict_fails(session: Session) -> None:
    first = freeze_manifest(session, config_fingerprint="a" * 64)
    assert freeze_manifest(session, config_fingerprint="a" * 64) == first
    mutate_candidate_pool(session)
    with pytest.raises(ManifestFreezeConflict):
        freeze_manifest(session, config_fingerprint="a" * 64)
```

Also prove the four extra floor seats go to the largest categories with enum-id tie breaking, proportional seats use largest remainder after floor selection, and diversity selection round-robins lexically ordered `(scale_or_unknown, city_or_unknown)` buckets within confidence tiers.

- [ ] **Step 2: Run allocation/service tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_allocation.py tests/manifest/test_service.py -q
```

- [ ] **Step 3: Implement exact arithmetic and canonical freeze**

Use integer quotient/remainder arithmetic, never binary floating point:

```python
share_numerator = remaining_count * 600
base, remainder = divmod(share_numerator, total_remaining)
```

Canonical JSON uses UTF-8, `ensure_ascii=False`, `sort_keys=True`, compact separators, UTC `Z` timestamps, and member order by `position`. Persist manifest and members in one transaction, then return bytes for exact writes to `backend/data/gate1/manifest.json` and `manifest.quota.json`; the service itself does not write files.

- [ ] **Step 4: Run focused and deterministic replay checks**

```powershell
cd backend
python -m pytest tests/manifest/test_allocation.py tests/manifest/test_service.py -q
python -m ruff check app/manifest/allocation.py app/manifest/service.py tests/manifest
python -m mypy app/manifest/allocation.py app/manifest/service.py
```

- [ ] **Step 5: Review and commit manifest freeze**

```powershell
git add backend/app/manifest/allocation.py backend/app/manifest/service.py backend/tests/manifest/test_allocation.py backend/tests/manifest/test_service.py
git commit -m "feat: freeze deterministic company manifest"
```

### Task 7: Discover and Classify Recruitment Entries Offline First

**Files:**
- Create: `backend/app/manifest/discovery.py`
- Modify: `backend/app/ingestion/providers/http.py`
- Create: `backend/tests/manifest/fixtures/official_careers.html`
- Create: `backend/tests/manifest/fixtures/no_careers.html`
- Create: `backend/tests/manifest/test_discovery.py`
- Modify: `backend/tests/ingestion/providers/test_http.py`

**Interfaces:**
- Produces: `classify_recruitment_url(url: str, official_host: str) -> AtsClassification`.
- Produces: `OfficialEntryDiscoverer.discover(company: ManifestCompany) -> EntryDiscoveryResult`.
- Produces: `EntryDiscoveryCoordinator.discover(company: ManifestCompany) -> EntryDiscoveryResult`, applying the approved priority order and returning unresolved fallback results for review.
- Produces: `DomainStartLimiter.wait(url: str) -> Awaitable[None]` and an optional `SafeHttpClient(before_request=...)` request-start hook shared by robots, page, and redirect requests.
- Consumes: `SafeHttpClient`, `RobotsPolicy`, exact official host, and Task 1 contracts.
- Guarantees: no job-list fetch or completeness claim; classification is census metadata only.

- [ ] **Step 1: Write failing fixture-based discovery tests**

Cover an already evidenced recruitment URL taking priority; official navigation labels `招聘`, `社会招聘`, `校园招聘`, `加入我们`, `人才招聘`, `careers`, and `jobs`; relative/absolute URLs; cross-host ATS links; Zhihu fallback review; unsafe redirects; credentials; robots denial; 401/403 stop; 429 budget stop; login/CAPTCHA classification; duplicate normalized URLs; bounded page/request counts; and one-second start spacing shared by robots, redirects, and pages.

```python
@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://jobs.feishu.cn/acme", "feishu"),
        ("https://app.mokahr.com/social-recruitment/acme", "moka"),
        ("https://tenant.beisen.cn/recruit", "beisen"),
        ("https://jobs.acme.cn/careers", "self_hosted"),
    ],
)
def test_classifier_uses_hostname_not_substring(url: str, platform: str) -> None:
    assert classify_recruitment_url(url, "acme.cn").platform == platform
```

Do not classify general job-board domains as company-owned entries without tenant/company ownership evidence.

- [ ] **Step 2: Run discovery tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_discovery.py -q
```

- [ ] **Step 3: Implement bounded discovery**

Validate an already evidenced recruitment URL first. Otherwise fetch the evidenced official website root, then at most the normalized same-host career candidates found in navigation. Every request uses the exact official host allowlist and robots check. Accept an ATS host only when the anchor text/path names recruitment and the official page retains the tenant relationship as ownership evidence. Invoke Zhihu only after those paths fail; a Zhihu result without independent ownership evidence is `review_required`, never accepted. Unknown, blocked, ambiguous, and unsafe results return stable non-success statuses rather than an empty success.

Extend `SafeHttpClient` so an optional async request-start hook executes immediately before every actual request, including redirects. Preserve existing behavior when the hook is absent. Map 401/403 to `provider_access_denied`, 429 to `provider_rate_limited`, and other error responses to the existing `http_status` code so stop policies never parse diagnostic strings.

Reference `codex/local-wip-before-stage3a:backend/app/ingestion/providers/ats_classifier.py` only for known platform host patterns; reimplement with `urlsplit().hostname` and tests, and do not copy its broad job-board ownership assumptions.

- [ ] **Step 4: Run provider security regression checks**

```powershell
cd backend
python -m pytest tests/manifest/test_discovery.py tests/ingestion/providers/test_http.py tests/ingestion/providers/test_security.py tests/ingestion/providers/test_robots.py -q
python -m ruff check app/manifest/discovery.py app/ingestion/providers/http.py tests/manifest/test_discovery.py tests/ingestion/providers/test_http.py
python -m mypy app/manifest/discovery.py app/ingestion/providers/http.py
```

- [ ] **Step 5: Review and commit entry discovery**

```powershell
git add backend/app/manifest/discovery.py backend/app/ingestion/providers/http.py backend/tests/manifest/fixtures backend/tests/manifest/test_discovery.py backend/tests/ingestion/providers/test_http.py
git commit -m "feat: discover public recruitment entries"
```

### Task 8: Persist Discovery Results and Build the ATS Census

**Files:**
- Modify: `backend/app/manifest/service.py`
- Create: `backend/app/manifest/reporting.py`
- Create: `backend/tests/manifest/test_reporting.py`
- Modify: `backend/tests/manifest/test_service.py`

**Interfaces:**
- Produces: frozen `DiscoveryRecordSummary` and `ManifestCoverageReport` result types.
- Produces: `record_discovery_result(session: Session, command: RecordDiscoveryCommand) -> DiscoveryRecordSummary`.
- Produces: `ManifestReportService.build(manifest_version: str, *, code_commit: str, config_fingerprint: str) -> ManifestCoverageReport`.
- Guarantees: accepted owned results upsert `JobEntry`; replays are idempotent; conflicting observations fail; no snapshot rows are written.

- [ ] **Step 1: Write failing persistence and census tests**

```python
def test_accepted_discovery_upserts_owned_entry_without_snapshot(session: Session) -> None:
    result = record_discovery_result(session, accepted_discovery_command())
    assert result.entry_created is True
    assert session.get(JobEntry, result.job_entry_id).company_id == COMPANY_ID
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0


def test_report_has_explicit_denominators_and_platform_counts(session: Session) -> None:
    report = ManifestReportService(session).build(
        MANIFEST_VERSION,
        code_commit="abc1234",
        config_fingerprint="a" * 64,
    )
    assert report.manifest_companies == 1000
    assert report.entry_company_denominator == 1000
    assert sum(report.platform_entry_counts.values()) == report.accepted_entries
```

Also test cross-company rejection, normalized URL replay/conflict, review-required results, multiple entries per company, undefined-rate serialization, and no raw response/secret fields in reports.

- [ ] **Step 2: Run service/report tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_service.py tests/manifest/test_reporting.py -q
```

- [ ] **Step 3: Implement transactional persistence and report queries**

Only `DiscoveryStatus.ACCEPTED` may create/update `JobEntry`. Set `provider="official_entry_discovery"`, the classifier platform, and `requires_rendering` from the classification; leave `status=JobEntryStatus.UNKNOWN` until Stage 3B/3C proves collection success. Reports bind to manifest hash, current code/config fingerprints supplied by the caller, explicit denominators, discovery status counts, entry coverage, entries per company, and platform/self-hosted distribution.

- [ ] **Step 4: Run focused and Stage 3A regression checks**

```powershell
cd backend
python -m pytest tests/manifest/test_service.py tests/manifest/test_reporting.py tests/ingestion/coverage tests/coverage -q
python -m ruff check app/manifest/service.py app/manifest/reporting.py tests/manifest
python -m mypy app/manifest/service.py app/manifest/reporting.py
```

- [ ] **Step 5: Review and commit persistence/reporting**

```powershell
git add backend/app/manifest/service.py backend/app/manifest/reporting.py backend/tests/manifest
git commit -m "feat: record entry discovery census"
```

### Task 9: Add Offline-by-Default Operator CLI and Integration Gate

**Files:**
- Create: `backend/app/manifest/cli.py`
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`
- Create: `backend/tests/manifest/test_cli.py`
- Create: `backend/tests/integration/test_manifest_entry_discovery.py`

**Interfaces:**
- Produces commands: `registry-check`, `candidate-import`, `review-export`, `review-apply`, `manifest-freeze`, `discover`, and `report`.
- Adds settings: `gate1_live_discovery_enabled: bool = False`, `gate1_source_registry_path: str = "data/gate1/source_registry.json"`, `gate1_zhihu_request_budget: int = 200`, and `gate1_domain_min_interval_seconds: float = 1.0`.
- Guarantees: `discover` refuses network unless both the setting and `--live` are present; all other commands are offline.

- [ ] **Step 1: Write failing subprocess and integration tests**

Test sorted one-object JSON stdout, stable exit codes, atomic artifact writes, secret-redacted database/config errors, external candidate paths, registry rejection, freeze prerequisites, review round trip, and live double opt-in:

```python
def test_discover_requires_double_opt_in(cli_environment: dict[str, str]) -> None:
    result = run_cli("discover", "--manifest", MANIFEST_VERSION, environment=cli_environment)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: live discovery is disabled\n"


def test_offline_integration_freezes_and_reports_full_fixture(session: Session) -> None:
    import_fixture_candidates(session, accepted=1500)
    frozen = freeze_manifest(session, config_fingerprint="a" * 64)
    persist_fixture_discoveries(session, frozen)
    report = ManifestReportService(session).build(
        frozen.manifest_version,
        code_commit="abc1234",
        config_fingerprint="a" * 64,
    )
    assert report.manifest_companies == 1000
    assert report.accepted_entries == 1000
```

The freeze service and CLI are both hard-coded to 1,000. Unit tests may call `allocate_quotas(..., total=...)` for arithmetic examples, but no persistence interface accepts a smaller manifest denominator.

- [ ] **Step 2: Run CLI/integration tests and verify RED**

```powershell
cd backend
python -m pytest tests/manifest/test_cli.py tests/integration/test_manifest_entry_discovery.py -q
```

- [ ] **Step 3: Implement commands and bounded live composition**

Use `argparse` subcommands. `candidate-import` reads UTF-8 JSONL from an explicit path outside the repository and validates every row before opening a transaction. `review-export`, `manifest-freeze`, and `report` use write-to-sibling-temp plus `Path.replace()` for atomic individual-file writes; they never recursively clean directories. `discover` processes deterministic manifest positions, skips terminal observations on resume, enforces per-domain start intervals and the shared Zhihu counter, and stops the affected source on 401/403 or concentrated 429 results.

Do not import `app.ingestion.production`, extraction clients, or any OpenAI settings. Build only `SafeHttpClient`, `RobotsPolicy`, `OfficialEntryDiscoverer`, and the already tested Zhihu provider when fallback is explicitly enabled.

- [ ] **Step 4: Run the complete offline Stage 3B0 gate**

```powershell
cd backend
python -m pytest tests/manifest tests/integration/test_manifest_entry_discovery.py tests/migrations/test_migrations.py -q
python -m pytest tests/ingestion/providers tests/ingestion/coverage tests/coverage -q
python -m ruff check app tests alembic/versions/0008_gate1_manifest_discovery.py
python -m mypy app
```

Expected: all commands PASS with no network, Redis, browser, or LLM access.

- [ ] **Step 5: Review and commit the operator surface**

```powershell
git add backend/app/manifest/cli.py backend/app/core/config.py .env.example backend/tests/manifest/test_cli.py backend/tests/integration/test_manifest_entry_discovery.py
git commit -m "feat: add manifest discovery commands"
```

### Company Identity Resolution Hardening Gate Before Task 10

The approved execution baseline was the user override `5d6f2cf`; the mandatory final whole-branch review remains `2143f8f..HEAD`. Implemented commits are Task 1 `64d1a3e`, `2427ecf`; Task 2 `104ef3d`, `23de812`; Task 3 `906d3b4`, `7d7415a`, `bab4ab9`, `ff922fc`, `d7730ae`; Task 4 `b0890fd`, `6ef0235`, `53792cd`; Task 5 `cb2c0b6`, `d9d98d1`, `ca01958`; Task 6 `ad1ddb9`, `99c4cb4`; and Task 7 `c4ec697`, `4efb39b`, `249c32d`.

The migration sequence is `0008_gate1_manifest_discovery`, `0009_company_identity_review`, `0010_job_details`, and `0011_coverage_query_indexes`. No obsolete numbering remains valid.

The audit categories are Critical: `cross_table_name_owner`, `shared_website_identity`, `incompatible_recruitment_identities`, `audit_findings_truncated`; Important: `accepted_candidate_name_unrepresented`, `fuzzy_name_cluster`, `orphan_alias`, `pending_review_owner_changed`, `similarity_search_unavailable`; Minor: `canonical_name_normalized_drift`, `alias_normalized_drift`, `filing_number_normalized_drift`, `website_normalized_drift`. The Task 6 human ruling keeps narrow pending-owner semantics: report only provable new current-ownership conflicts or cardinality transitions, without a prior exact-owner UUID schema expansion. The Task 7 human ruling treats POSIX output directories as trusted and operator-controlled; Windows retains stronger pinned native handles, while the same-privilege POSIX namespace race remains an explicit runtime risk.

Task 8 evidence on 2026-08-08 is not a release pass. The first complete offline group reported `728 passed, 7 skipped, 1 warning`; the warning is the pre-existing intentional non-integer `salary_months` Pydantic serializer warning. The second group reported `220 passed, 1 failed`; `test_company_detail_includes_aliases_filings_sources_and_job_count` proves that the API-visible filing number is incorrectly lowercased by identity normalization. Ruff passed and mypy passed for 98 source files. Default tests did not enable network, Redis, model API, browser, or job-list providers. The required tracked-file secret pattern scan was also not clean: it found four API-key patterns in a tracked agent skill document and two credentialed-connection-URI patterns in CLI/reporting redaction tests. Only file, line, and category were reported; no matched value was printed.

`TEST_POSTGRES_URL` was absent, so the PostgreSQL migration/service markers, two-session concurrency cases, trigram query plan, exact 10,000-company performance marker, and test-owned residual-schema validation were not run. The approved dedicated audit database configuration was also absent, so no read-only audit CLI or external audit report was produced and zero unresolved Critical/Important findings cannot be claimed.

Task 10 stays paused until all of the following are true: both offline groups have zero failures; the existing warning is removed or explicitly asserted; the tracked-file secret pattern scan is clean; PostgreSQL migration/service and `performance and postgresql` markers pass with zero skips and test-owned non-`CASCADE` cleanup proves zero residual schemas; the dedicated read-only audit has zero unresolved Critical/Important findings or an explicit human ruling for each; and the `2143f8f..HEAD` whole-branch review is clean. Deferred Minor risks remain tracked: advisory lock keys are not sorted/deduplicated against a theoretical 64-bit hash collision; the seed importer direct `RegulatoryFiling` writer is outside Task 3 persistence locking; audit chunk common evidence can displace a display label; no project-equipped POSIX runtime exercised the POSIX writer; the Windows symlink test lacks account privilege; and the human-approved same-privilege POSIX race remains.

### Task 10: Update Roadmaps and Execute the Authorized Stage 3B0 Data Gate

**Files:**
- Modify: `backend/data/gate1/source_registry.json`
- Create after successful freeze: `backend/data/gate1/manifest.json`
- Create after successful freeze: `backend/data/gate1/manifest.quota.json`
- Modify: `docs/dev/job-coverage-at-scale-plan.md`
- Modify: `docs/dev/migration-master-plan.md`
- Test: `backend/tests/manifest/test_registry.py`
- Test: `backend/tests/integration/test_manifest_entry_discovery.py`

**Interfaces:**
- Consumes: reviewed public source extracts outside Git and Tasks 1-9 commands.
- Produces: a committed source registry, exactly 1,000-member canonical manifest, quota artifact, review audit in PostgreSQL, and sanitized ATS census report.
- Stops: before any job-list request; Stage 3B remains separately planned and approved.

- [ ] **Step 1: Add and review each real candidate-pool source before use**

For every official public list, add one concrete registry entry containing its exact name, base URL, source class, authorization basis, robots policy, candidate-pool role, QPS at or below 1.0, and finite rehearsal budget. Run:

```powershell
cd backend
python -m app.manifest.cli registry-check --registry data/gate1/source_registry.json
python -m pytest tests/manifest/test_registry.py -q
```

Expected: PASS before any source extract is downloaded or imported. Reject sources requiring login, CAPTCHA, personal data, bypass behavior, or unclear authorization.

Commit the reviewed registry before using any newly added source:

```powershell
git add backend/data/gate1/source_registry.json
git diff --cached --check
git commit -m "data: register gate1 candidate sources"
```

- [ ] **Step 2: Import sanitized external JSONL extracts and clear the review queue**

Store raw downloads and normalized candidate JSONL outside the repository. For each reviewed file:

```powershell
cd backend
python -m app.manifest.cli candidate-import D:\company_search_gate1_runtime\candidates\source.jsonl
python -m app.manifest.cli review-export D:\company_search_gate1_runtime\reviews\pending.json
```

Apply only evidence-backed human decisions:

```powershell
python -m app.manifest.cli review-apply D:\company_search_gate1_runtime\reviews\decisions.json
python -m app.manifest.cli report --format json
```

Expected before freeze: at least 1,500 accepted identities, zero unresolved records selected for allocation, and every category able to fill its floor. The actual filenames may be split per registered source; the CLI contract and runtime root remain fixed.

- [ ] **Step 3: Freeze and verify the canonical artifacts**

```powershell
cd backend
python -m app.manifest.cli manifest-freeze --manifest-out data/gate1/manifest.json --quota-out data/gate1/manifest.quota.json
python -m app.manifest.cli report --manifest-file data/gate1/manifest.json --format json
```

Expected: exactly 1,000 unique recruiting identities, nine category allocations summing to 1,000, both artifacts bound to the same `manifest_version`, and a second freeze producing byte-identical artifacts.

- [ ] **Step 4: Run entry discovery with explicit live authorization and stop rules**

Load only the external runtime environment containing the dedicated local database and authorized Zhihu secret; do not load model variables. Start with a 20-member discovery smoke, review its destinations and sanitized report, then resume the same manifest for the remaining members:

```powershell
cd backend
python -m app.manifest.cli discover --manifest-file data/gate1/manifest.json --limit 20 --live
python -m app.manifest.cli report --manifest-file data/gate1/manifest.json --format json
python -m app.manifest.cli discover --manifest-file data/gate1/manifest.json --resume --live
python -m app.manifest.cli report --manifest-file data/gate1/manifest.json --format json
```

Stop immediately on unauthorized destinations, credential leakage, cross-company ownership, unbounded resources, source-wide 401/403, concentrated 429, or a broken request budget. Do not request or enumerate a job list.

- [ ] **Step 5: Update the global iterative documents with measured facts**

Record Stage 3B0 commit ids, migrations `0008` and `0009`, manifest hash, accepted/review/rejected counts, category/city/scale distributions, entry coverage, discovery status distribution, platform census, Zhihu usage, and remaining Stage 3B risks. Keep planned `job_details` at `0010` and coverage indexes at `0011`; leave Stage 3B as awaiting its own implementation plan and approval.

- [ ] **Step 6: Run final verification and secret scan**

```powershell
cd backend
python -m pytest -q
python -m pytest -m performance -q
python -m ruff check app tests alembic
python -m mypy app
git diff --check
git grep -n -I -E "(postgres(ql)?|redis)://[^[:space:]]+:[^[:space:]]+@|sk-[A-Za-z0-9_-]{12,}" -- . ":(exclude)66.md" ":(exclude)test.env"
```

Expected: all suites/checks PASS and the tracked-file secret scan prints nothing. Task 10 additionally requires the isolated PostgreSQL migration/service marker and the exact 10,000-company `performance and postgresql` marker to pass with zero skips, followed by test-owned validation of zero residual schemas without `CASCADE`. Run the dedicated local read-only company identity audit and require zero unresolved Critical/Important findings before any real candidate import or live discovery. The `2143f8f..HEAD` whole-branch review must also be clean; none of these requirements is optional when local configuration is absent.

- [ ] **Step 7: Review and commit Stage 3B0 artifacts and roadmap status**

Stage only the canonical manifest/quota artifacts and roadmap changes; the public source registry was committed before intake in Step 1. Confirm `66.md`, `test.env`, raw extracts, review working files, and reports are absent from `git status` staging before committing:

```powershell
git add backend/data/gate1/manifest.json backend/data/gate1/manifest.quota.json docs/dev/job-coverage-at-scale-plan.md docs/dev/migration-master-plan.md
git diff --cached --check
git commit -m "docs: record gate1 manifest discovery baseline"
```

Do not merge, push, start Stage 3B, or run the 1,000-company job-list benchmark in this task.
