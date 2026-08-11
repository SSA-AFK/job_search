# Company Recruiting Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a desktop-first recruiting radar that imports a deterministic 20-company Tianyancha cohort into a new database, enriches approved public recruitment data, and presents traceable company and recruiting status in the existing UI.

**Architecture:** Add an auditable import-batch boundary, then derive a read-only recruiting-coverage DTO from job entries and snapshots. The company API owns filtering, ordering, status/freshness rules and safe failure copy; the React client renders those server-provided facts in the existing search and detail surfaces. No recruiting state is client-derived or persisted as a second source of truth.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy, Alembic, SQLite (isolated pilot), Celery/Redis for optional collection dispatch, React, TypeScript, Vite, Vitest, Playwright.

## Global Constraints

- Use a new SQLite database for the pilot; never read from or write to an existing development or production database.
- Import exactly worksheet `高级搜索` rows 3–22 from the supplied workbook; record workbook filename, sheet and source row for each item.
- Preserve uncertain semantics: only `active_roles` and `empty_confirmed` are conclusive; missing, partial, failed, blocked and stale data must remain distinct.
- Access only approved public recruitment entries; stop on robots exclusions, login, CAPTCHA or verification barriers.
- Desktop presentation is in scope. Do not add or test mobile-specific layouts in this change.
- Preserve all unrelated dirty-worktree changes. Stage and commit only files changed by each completed task.

---

## File structure

| File | Responsibility |
| --- | --- |
| `backend/app/models/import_batch.py` | Import-run and row-level provenance models. |
| `backend/alembic/versions/0016_import_batches.py` | Schema for isolated cohort audit records. |
| `backend/app/imports/xlsx_staging.py` | Read, validate and select rows 3–22 without uploading the workbook. |
| `backend/app/imports/service.py` | Persist a batch and idempotently create companies for the selected rows. |
| `backend/app/imports/cli.py` | Operator-only CLI to create the pilot database and import the cohort. |
| `backend/app/recruiting_coverage/service.py` | Derive coverage status, freshness, count and safe reason from records. |
| `backend/app/companies/schemas.py` | Query parameters and API DTOs. |
| `backend/app/companies/repository.py` | Batch-scoped search, coverage projection and ordering. |
| `backend/app/companies/service.py` | Compose company DTOs with coverage and profile completeness. |
| `backend/app/companies/router.py` | Expose validated query options. |
| `frontend/src/api/types.ts` | Mirror API DTOs and query vocabulary. |
| `frontend/src/search/search-params.ts` | Serialize and parse new search filters/sorts. |
| `frontend/src/search/Filters.tsx` | Desktop filter controls. |
| `frontend/src/search/CompanyResults.tsx` | Compact, textual recruiting and profile summary. |
| `frontend/src/company/CompanyDetailPage.tsx` | Recruiting coverage panel and complete profile presentation. |
| `frontend/src/styles.css` | Desktop-only visual treatment using existing design tokens. |

### Task 1: Create an isolated, auditable 20-company import

**Files:**
- Create: `backend/alembic/versions/0016_import_batches.py`
- Create: `backend/app/models/import_batch.py`
- Create: `backend/app/imports/__init__.py`
- Create: `backend/app/imports/xlsx_staging.py`
- Create: `backend/app/imports/service.py`
- Create: `backend/app/imports/cli.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/imports/test_xlsx_staging.py`
- Test: `backend/tests/imports/test_service.py`

**Interfaces:**
- Consumes: `Company`, `normalize_name`, `Session`, `Base`, and an operator-supplied database URL.
- Produces: `ImportBatch`, `ImportItem`, `read_tianyancha_cohort(workbook_path) -> tuple[StagedCompany, ...]`, and `import_cohort(session, workbook_path) -> ImportSummary`.

- [ ] **Step 1: Write failing workbook-selection tests**

```python
def test_reader_returns_exactly_rows_3_through_22(tmp_path: Path) -> None:
    workbook = make_workbook(tmp_path, sheet="高级搜索", values=["声明", "公司名称", *[f"公司{i}" for i in range(1, 22)]])
    cohort = read_tianyancha_cohort(workbook)
    assert [item.source_row for item in cohort] == list(range(3, 23))
    assert [item.canonical_name for item in cohort] == [f"公司{i}" for i in range(1, 21)]

def test_reader_rejects_missing_sheet_or_blank_selected_company(tmp_path: Path) -> None:
    with pytest.raises(CohortWorkbookError, match="高级搜索"):
        read_tianyancha_cohort(make_workbook(tmp_path, sheet="Sheet1", values=[]))
```

- [ ] **Step 2: Run the selection tests and confirm failure**

Run: `python -m pytest tests/imports/test_xlsx_staging.py -q` from `backend`.

Expected: FAIL because `app.imports.xlsx_staging` does not exist.

- [ ] **Step 3: Implement workbook parsing with a fixed cohort boundary**

```python
@dataclass(frozen=True)
class StagedCompany:
    canonical_name: str
    normalized_name: str
    source_row: int

def read_tianyancha_cohort(workbook_path: Path) -> tuple[StagedCompany, ...]:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    worksheet = workbook["高级搜索"]
    worksheet.reset_dimensions()
    rows = tuple(worksheet.iter_rows(min_row=3, max_row=22, max_col=1, values_only=True))
    if len(rows) != 20 or any(not isinstance(row[0], str) or not row[0].strip() for row in rows):
        raise CohortWorkbookError("rows 3-22 must contain twenty company names")
    return tuple(StagedCompany(row[0].strip(), normalize_name(row[0]), index) for index, row in enumerate(rows, start=3))
```

Use the bundled local workbook runtime only. Do not log cell values beyond the selected company names and source-row metadata.

- [ ] **Step 4: Write failing persistence and isolation tests**

```python
def test_import_persists_source_provenance_and_is_idempotent(session: Session, workbook: Path) -> None:
    first = import_cohort(session, workbook)
    second = import_cohort(session, workbook)
    assert first.created_companies == 20
    assert second.created_companies == 0
    assert session.scalar(select(func.count()).select_from(ImportItem)) == 20
    assert {item.source_row for item in session.scalars(select(ImportItem))} == set(range(3, 23))
```

- [ ] **Step 5: Implement schema, service and CLI**

Create `import_batches` with `id`, `workbook_filename`, `worksheet_name`, `created_at`; create `import_items` with `id`, `import_batch_id`, `company_id`, `source_row`, `source_name`, `normalized_source_name`, `created_at`, and a unique constraint on `(import_batch_id, source_row)`. Add foreign keys to `companies` and cascade only when deleting an explicitly targeted import batch.

`import_cohort` must normalize names, create no duplicate company for an existing exact normalized name, and link every selected row to its company. The CLI accepts `--database-url` and `--workbook`; it creates tables through Alembic before import, rejects a database URL that points to the configured default database, and prints only JSON summary fields `batch_id`, `companies_created`, `companies_matched`, and `items_imported`.

- [ ] **Step 6: Run import tests and migration checks**

Run: `python -m pytest tests/imports -q; python -m alembic upgrade head; python -m alembic downgrade 0015_funding_events; python -m alembic upgrade head` from `backend`.

Expected: PASS; the temporary test database has exactly 20 `ImportItem` records after a repeated import.

- [ ] **Step 7: Commit the import boundary**

```powershell
git add backend/alembic/versions/0016_import_batches.py backend/app/models/import_batch.py backend/app/models/__init__.py backend/app/imports backend/tests/imports
git commit -m "feat: add isolated company cohort import"
```

### Task 2: Derive recruiting coverage and profile completeness on the server

**Files:**
- Create: `backend/app/recruiting_coverage/__init__.py`
- Create: `backend/app/recruiting_coverage/service.py`
- Modify: `backend/app/companies/schemas.py`
- Modify: `backend/app/companies/repository.py`
- Modify: `backend/app/companies/service.py`
- Test: `backend/tests/recruiting_coverage/test_service.py`
- Test: `backend/tests/api/test_companies.py`

**Interfaces:**
- Consumes: `JobEntry`, `JobCollectionSnapshot`, `JobPosting`, `CompanyProfileField`, `Company`, and current time passed as `now` to the coverage service.
- Produces: `RecruitingCoverage`, `ProfileCompleteness`, `build_recruiting_coverage(company_id, now)`, and list/detail DTO fields with the same names.

- [ ] **Step 1: Write failing status-contract tests**

```python
@pytest.mark.parametrize(
    ("snapshot_status", "observed_count", "expected"),
    [(JobSnapshotStatus.SUCCEEDED, 2, "active_roles"), (JobSnapshotStatus.SUCCEEDED, 0, "empty_confirmed")],
)
def test_completed_snapshots_are_the_only_conclusive_states(
    service: RecruitingCoverageService, company_id: UUID
) -> None:
    coverage = service.build(company_id, now=NOW)
    assert coverage.status is expected

def test_failed_latest_snapshot_keeps_prior_evidence_but_is_incomplete(
    service: RecruitingCoverageService, company_id: UUID
) -> None:
    assert service.build(company_id, now=NOW).status == "collection_incomplete"
```

Include explicit tests for `entry_discovery_pending`, `stale`, a partial snapshot, and a non-public raw error mapped to `Temporary source error`.

- [ ] **Step 2: Run coverage tests and confirm failure**

Run: `python -m pytest tests/recruiting_coverage/test_service.py -q` from `backend`.

Expected: FAIL because the coverage service and DTOs do not exist.

- [ ] **Step 3: Define DTOs and minimal server-owned rules**

```python
class RecruitingStatus(StrEnum):
    ACTIVE_ROLES = "active_roles"
    EMPTY_CONFIRMED = "empty_confirmed"
    ENTRY_DISCOVERY_PENDING = "entry_discovery_pending"
    COLLECTION_INCOMPLETE = "collection_incomplete"
    STALE = "stale"

class RecruitingCoverage(BaseModel):
    status: RecruitingStatus
    active_job_count: int | None
    last_checked_at: datetime | None
    last_successful_at: datetime | None
    freshness: Literal["fresh", "stale", "unknown"]
    reason_code: str | None
    entries: list[RecruitingEntrySummary]
```

Use the current 24-hour refresh policy as the fresh boundary. Set `active_job_count` only for a fresh successful complete snapshot. Never expose raw provider exception text.

- [ ] **Step 4: Add batch-scoped query filters and deterministic sorts**

Extend `CompanyQuery` with `recruiting_status`, `has_active_roles`, `profile_complete`, and sort values `last_checked_at` and `active_job_count`. The repository must use correlated subqueries or aggregate joins that preserve one row per company, apply filters before pagination, and use `Company.canonical_name, Company.id` as stable tie-breakers.

- [ ] **Step 5: Add API response tests**

```python
def test_search_filters_active_roles_and_serializes_coverage(client: TestClient) -> None:
    response = client.get("/api/v1/companies", params={"recruiting_status": "active_roles"})
    assert response.status_code == 200
    assert response.json()["items"][0]["recruiting_coverage"]["status"] == "active_roles"

def test_company_detail_reports_missing_profile_fields(client: TestClient, deepseek_id: UUID) -> None:
    assert "website" in client.get(f"/api/v1/companies/{deepseek_id}").json()["profile_completeness"]["missing_fields"]
```

- [ ] **Step 6: Run focused backend checks**

Run: `python -m pytest tests/recruiting_coverage tests/api/test_companies.py -q; python -m ruff check app tests; python -m mypy app` from `backend`.

Expected: PASS with API payloads containing both new DTOs in list and detail responses.

- [ ] **Step 7: Commit coverage API**

```powershell
git add backend/app/recruiting_coverage backend/app/companies backend/tests/recruiting_coverage backend/tests/api/test_companies.py
git commit -m "feat: expose recruiting coverage summaries"
```

### Task 3: Add desktop search filtering and company-row summaries

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/search/search-params.ts`
- Modify: `frontend/src/search/Filters.tsx`
- Modify: `frontend/src/search/CompanyResults.tsx`
- Modify: `frontend/src/search/SearchPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: API `recruiting_coverage` and `profile_completeness` fields from Task 2.
- Produces: URL-backed filters and a `RecruitingCoverageSummary` rendered once per company result.

- [ ] **Step 1: Write failing UI tests**

```tsx
it("serializes a recruiting-status filter and renders a conclusive status", async () => {
  renderSearch({ recruiting_coverage: { status: "active_roles", active_job_count: 3, last_checked_at: "2026-08-11T00:00:00Z", freshness: "fresh", reason_code: null, entries: [] } });
  await userEvent.selectOptions(screen.getByLabelText("招聘状态"), "active_roles");
  expect(requestUrl()).toContain("recruiting_status=active_roles");
  expect(screen.getByText("正在招聘 · 3 个职位")).toBeInTheDocument();
});

it("does not call an unknown status zero open roles", () => {
  renderResults({ recruiting_coverage: { status: "entry_discovery_pending", active_job_count: null, freshness: "unknown", entries: [] } });
  expect(screen.getByText("招聘入口待发现")).toBeInTheDocument();
  expect(screen.queryByText("暂无职位")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the frontend unit tests and confirm failure**

Run: `npm test -- --run src/search/SearchPage.test.tsx` from `frontend`.

Expected: FAIL because the controls, client types and status copy do not exist.

- [ ] **Step 3: Add client contract and URL parsing**

Add literal TypeScript unions matching Task 2 exactly. Extend `CompanySearchParams` and `parseSearchParams` to round-trip only validated `recruiting_status`, `has_active_roles`, `profile_complete`, `last_checked_at`, and `active_job_count` values; leave existing query parameters unchanged.

- [ ] **Step 4: Implement the desktop controls and result summary**

Add filter labels `招聘状态`, `有在招职位`, and `资料完整度`; add sort labels `最近核验` and `在招职位最多`. Render one textual badge for each status, `最近核验：YYYY-MM-DD` for a timestamp, a role count only for `active_roles`, and `资料：present/target`. Use the existing green semantic token for positive evidence and neutral/error tokens plus textual reasons for non-conclusive states.

- [ ] **Step 5: Run frontend tests and build**

Run: `npm test -- --run src/search/SearchPage.test.tsx; npm run build` from `frontend`.

Expected: PASS and TypeScript accepts the exact backend DTO fields.

- [ ] **Step 6: Commit the search surface**

```powershell
git add frontend/src/api frontend/src/search frontend/src/styles.css
git commit -m "feat: show recruiting status in company search"
```

### Task 4: Add a traceable recruiting and profile detail view

**Files:**
- Modify: `frontend/src/company/CompanyDetailPage.tsx`
- Modify: `frontend/src/company/JobList.tsx`
- Modify: `frontend/src/company/CompanyDetailPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `CompanyDetail.recruiting_coverage`, `CompanyDetail.profile_completeness`, `profile_fields`, `sources`, and the paginated active job list.
- Produces: A desktop-only coverage panel that explains whether and why the user can trust the displayed job count.

- [ ] **Step 1: Write failing detail tests for all user-visible states**

```tsx
const companyWithCoverage = (status: RecruitingStatus): CompanyDetail => ({
  ...company,
  recruiting_coverage: coverage(status),
});
const companyWithMissingProfileFacts = (): CompanyDetail => ({
  ...company,
  website: null,
  headquarters: null,
});

it.each([
  ["active_roles", "正在招聘", "3 个在招职位"],
  ["empty_confirmed", "已核验暂无职位", "最近核验"],
  ["collection_incomplete", "招聘信息待复查", "临时来源错误"],
])("renders %s without an unsupported conclusion", async (status, label, detail) => {
  renderCompanyDetail(companyWithCoverage(status));
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(screen.getByText(detail)).toBeInTheDocument();
});

it("labels unavailable facts as pending enrichment", async () => {
  renderCompanyDetail(companyWithMissingProfileFacts());
  expect(await screen.findAllByText("待补全")).not.toHaveLength(0);
});
```

- [ ] **Step 2: Run the detail tests and confirm failure**

Run: `npm test -- --run src/company/CompanyDetailPage.test.tsx` from `frontend`.

Expected: FAIL because the coverage panel is absent.

- [ ] **Step 3: Implement the coverage panel and evidence links**

Place the panel directly below the identity header. It contains status text, concise explanation, last checked/successful date, trustworthy active-role count, and a compact list of entry platform links. For `collection_incomplete` and `stale`, retain prior successful date but state that current coverage needs review. Render existing company facts in the approved order and leave missing values as `待补全`.

- [ ] **Step 4: Preserve job source traceability**

In `JobList.tsx`, render provider name, verified/pending indicator, and each existing application URL. Do not create an apply button for a role missing an `apply_url`; the backend must already exclude it from the displayed count.

- [ ] **Step 5: Run frontend verification**

Run: `npm test -- --run src/company/CompanyDetailPage.test.tsx; npm run build` from `frontend`.

Expected: PASS for all coverage states, profile gaps and job-source links.

- [ ] **Step 6: Commit the detail surface**

```powershell
git add frontend/src/company frontend/src/styles.css
git commit -m "feat: explain recruiting coverage on company detail"
```

### Task 5: Run the isolated cohort and verify the desktop flow

**Files:**
- Create: `backend/tests/integration/test_pilot_cohort.py`
- Modify: `frontend/tests/seeded-flow.spec.ts`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: Task 1 import CLI, Task 2 API filters, and Task 3–4 UI.
- Produces: A reproducible pilot command, summary report, and one desktop browser acceptance test against a seeded isolated database.

- [ ] **Step 1: Write the failing cohort boundary test**

```python
def test_pilot_database_contains_only_selected_cohort(pilot_database_url: str, workbook: Path) -> None:
    summary = run_import_cli(pilot_database_url, workbook)
    assert summary["items_imported"] == 20
    assert company_count(pilot_database_url) == 20
    assert import_item_rows(pilot_database_url) == list(range(3, 23))
```

- [ ] **Step 2: Add a desktop browser acceptance test**

```ts
test("user filters the pilot cohort and inspects recruiting evidence", async ({ page }) => {
  await page.goto("/companies");
  await page.getByLabel("招聘状态").selectOption("active_roles");
  await page.getByRole("link", { name: /.+/ }).first().click();
  await expect(page.getByText("招聘覆盖")).toBeVisible();
});
```

- [ ] **Step 3: Implement the test database command and safety guard**

Document a command that requires an explicit absolute `DATABASE_URL` pointing to `company_search_pilot_20.sqlite3`, runs Alembic, imports the workbook, and starts collection only after the operator explicitly enables approved providers. The guard must refuse the configured default database URL and a database with existing non-pilot `ImportBatch` records.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest tests/imports tests/recruiting_coverage tests/api/test_companies.py tests/integration/test_pilot_cohort.py -q; python -m ruff check app tests; python -m mypy app` from `backend`, then `npm test -- --run; npm run build; npm run test:e2e -- --project=seeded-desktop` from `frontend`.

Expected: PASS. Record the 20-company import, identity-review, entry-coverage, completed-list and active-role metrics without claiming a platform-wide coverage rate.

- [ ] **Step 5: Commit pilot documentation and acceptance checks**

```powershell
git add backend/tests/integration/test_pilot_cohort.py frontend/tests/seeded-flow.spec.ts README.md .env.example
git commit -m "test: verify isolated recruiting radar pilot"
```

## Plan self-review

- Spec coverage: Tasks 1 and 5 implement the isolated 20-company test database; Task 2 implements the five recruiting states and profile completeness; Tasks 3 and 4 implement desktop list/detail presentation and traceability; Task 5 covers measurable verification.
- Ambiguity resolved: the deterministic cohort is workbook rows 3–22, and freshness uses the existing 24-hour policy.
- Guardrails covered: raw provider errors are mapped server-side, missing/failed collection is non-conclusive, and collection only targets the pilot cohort.
- Scope check: the plan excludes mobile, public data redistribution, bypass mechanisms, recommendations and notifications.
