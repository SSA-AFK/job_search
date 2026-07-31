# Stage One Task 4 Report

## Status

Implemented company search, detail, and job query services plus `/api/v1/companies` REST
endpoints. No design, plan, tracker, SDD ledger, model, migration, or seed importer file was
changed.

## Implementation Summary

- Added typed Pydantic request/response contracts for company queries, job queries, list items,
  detail records, source summaries, job source links, and generic pages.
- Added SQLAlchemy-only repository queries with separate count statements, exact filters,
  escaped normalized search terms, deterministic pagination, and batched related-row loading.
- Added service mapping for company fields, aliases, filings, company source summaries, total job
  count, jobs, and provider/application URL pairs.
- Added company list, detail, and job endpoints under the existing `/api/v1` prefix.
- Added stable error envelopes for domain 404s, router 404s, and request validation 422s.
- Registered application-level error handlers in `app/main.py`; this additional modification is
  required because an `APIRouter` cannot install exception handlers for validation or route misses.

## TDD Evidence

### Initial API RED

Command (from `backend`):

```text
python -m pytest tests/api/test_companies.py -q
```

Relevant output:

```text
FFFFFFFFFFFFFFFFFFFFFFFFF                                                [100%]
E       assert 404 == 200
E       AssertionError: assert {'detail': 'Not Found'} == {'error': {...}}
25 failed, 518 warnings in 2.34s
```

The API tests collected against real SQLite fixtures and failed because every company route was
unregistered. The absent-company cases also demonstrated the default FastAPI error body instead
of the required envelope.

The brief's combined command also proved that the service package did not exist:

```text
python -m pytest tests/companies tests/api/test_companies.py -q
E   ModuleNotFoundError: No module named 'app.companies'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
61 warnings, 1 error in 1.15s
```

### Initial GREEN

```text
python -m pytest tests/companies tests/api/test_companies.py -q
...........................                                              [100%]
27 passed, 98 warnings in 2.26s
```

### Review Regression RED

Independent review identified that unknown routes retained FastAPI's default 404 response and
that nullable `posted_at` ordering differed between SQLite and PostgreSQL. Regression tests were
added first.

```text
python -m pytest tests/api/test_companies.py::test_jobs_explicitly_order_unknown_posting_dates_last tests/api/test_companies.py::test_unknown_api_route_returns_stable_404 -q
FF                                                                       [100%]
E       assert any("posted_at DESC NULLS LAST" in statement for statement in statements)
E       AssertionError: assert {'detail': 'Not Found'} == {'error': {...}}
2 failed, 621 warnings in 1.42s
```

### Review Regression GREEN

```text
python -m pytest tests/api/test_companies.py::test_jobs_explicitly_order_unknown_posting_dates_last tests/api/test_companies.py::test_unknown_api_route_returns_stable_404 -q
..                                                                       [100%]
2 passed, 93 warnings in 1.10s
```

The fix emits `posted_at DESC NULLS LAST` explicitly and maps Starlette HTTP 404s to
`not_found` / `Resource not found` without changing their status code.

## Final Verification

```text
python -m pytest tests/companies tests/api -q
..............................                                           [100%]
30 passed, 153 warnings in 2.47s

python -m pytest -q
...................................................                      [100%]
51 passed, 93 warnings in 3.17s

python -m ruff check app tests
All checks passed!

python -m mypy app/companies app/core/errors.py --disable-error-code attr-defined
Success: no issues found in 6 source files
```

Tests use the versioned seed through `import_seed` and real in-memory SQLite tables with foreign
keys enabled. No repository, service, or importer mock is used.

## Query And Ordering Decisions

- Search normalizes `q` once and uses SQLAlchemy expressions with `autoescape=True`; no SQL is
  assembled from request values.
- Relevance order is exact normalized company name, exact normalized alias, company-name prefix,
  alias prefix, company-name contains, then alias contains. Ties use canonical name and UUID.
- Correlated alias `EXISTS` predicates avoid duplicate company rows and keep the count query exact.
- `name` sorts by canonical name ascending. `updated_at` sorts descending. Without `q`, the
  default is `updated_at`; with `q`, the default is relevance.
- Job order is `posted_at DESC NULLS LAST`, title ascending, UUID ascending. Each job's sources are
  independently ordered by provider ascending and source raw ID ascending before mapping the
  provider/application URL pairs.
- Search loads only companies. Detail batch-loads aliases, filings, source summaries, and one job
  count. Jobs batch-load sources only for the requested page.

## Files Changed

- `backend/app/companies/__init__.py`
- `backend/app/companies/schemas.py`
- `backend/app/companies/repository.py`
- `backend/app/companies/service.py`
- `backend/app/companies/router.py`
- `backend/app/core/errors.py`
- `backend/app/api/router.py`
- `backend/app/main.py`
- `backend/tests/companies/test_service.py`
- `backend/tests/api/test_companies.py`
- `.superpowers/sdd/2026-07-31-company-search-web-foundation/task-4-report.md`

## Self-Review

- Every documented company filter, all three sort modes, combined filters, default sort behavior,
  page bounds, job filters, `active_only`, pagination, malformed UUIDs, absent companies, and
  unknown API routes have API coverage.
- Ranking tests cover all six exact/prefix/contains name and alias tiers with independently written
  expected orders.
- Detail tests cover aliases, filings, source summaries, total job count, Decimal serialization,
  and UTC RFC 3339 timestamps.
- Job tests keep provider/application URLs paired and verify the explicit secondary source order
  and cross-dialect NULL policy.
- Independent review found no SQL construction, separate-count, entity-error, source-pairing, or
  UTC serialization defect after the two regression fixes.
- Only explicit source/test/report paths will be staged. Generated caches and egg-info remain
  untouched and untracked.

## Concerns

- The installed pytest 7 assertion rewriter and Starlette TestClient emit Python 3.13 dependency
  deprecation warnings. The full suite reports 93 such warnings; no application warning or Ruff
  error remains.
- No live PostgreSQL server was available. Explicit `NULLS LAST` makes nullable job pagination
  portable, and the emitted SQL is covered in the SQLite API test.
- A whole-app mypy run stops at the pre-existing `app/core/database.py` cursor annotation because
  its existing ignore code does not match mypy's current `attr-defined` diagnostic. The six new
  Task 4 source files pass the targeted mypy command shown above.
