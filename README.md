# AI Company Search

Local web application for searching companies, viewing evidence and jobs, and optionally collecting new company data. Existing SQLite data remains searchable when Redis, Celery, an LLM, or a Provider is unavailable.

## Prerequisites

- Python 3.12 or newer
- Node.js 20 or newer with npm

## Backend setup

Run these commands from the repository root in Windows PowerShell:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:DATABASE_URL = "sqlite:///./company_search.db"
$env:COLLECTION_ENABLED = "false"
alembic upgrade head
python -m app.seed.cli data/companies.seed.json
python -m uvicorn app.main:app --reload
```

The backend is available at `http://127.0.0.1:8000`. Collection stays disabled unless its worker runtime and external services are configured explicitly.

To confirm that seed imports remain idempotent, run the import command a second time. The second output reports no newly created companies, jobs, or sources.

## Frontend setup

In a second PowerShell window, run:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/companies`. Vite proxies `/api` requests to the local backend, so search, company details, and job-source links work from the seeded SQLite database without external services.

## Tests and checks

Run backend lint, type, unit, API, seed, migration, and integration checks:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m ruff check app tests
python -m mypy app
python -m pytest -q
python -m pytest tests/integration -q
```

Run frontend unit and build checks:

```powershell
Set-Location frontend
npm test -- --run
npm run build
```

Install the browser binaries once, then run the desktop, mobile, and real seeded browser projects:

```powershell
Set-Location frontend
npx playwright install
npm run test:e2e
```

The Playwright command starts both Vite and FastAPI without requiring a shell activation of the backend virtual environment. Its seeded backend resolves `backend/.venv/Scripts/python.exe` on Windows or `backend/.venv/bin/python` on POSIX before a resolved `PATH` fallback. To intentionally use another interpreter, set `PLAYWRIGHT_PYTHON` to its absolute executable path; the launcher validates the path and preflights the backend dependencies. Its seeded project validates and replaces only the ignored `backend/.playwright-seeded.sqlite3` file, upgrades it with Alembic, imports `backend/data/companies.seed.json` twice, verifies stable row counts, and then searches and opens the real seed through Vite's `/api` proxy. It does not require Redis, Celery, LLM credentials, collection services, or external data fetches. The mocked desktop/mobile flows remain isolated and continue to cover their existing UI states.

Run the representative SQLite search performance acceptance check separately. It creates exactly 10,000 companies and 100,000 jobs using a fixed random seed, performs five warm-up requests, then measures 50 requests. The p95 must remain at or below 300 ms.

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m pytest -m performance tests/performance/test_company_queries.py -q
```

## Test matrix

| Command | Coverage |
| --- | --- |
| `python -m ruff check app tests` | Backend linting |
| `python -m mypy app` | Full backend static type check |
| `python -m pytest -q` | Backend unit, API, seed, migration, task, and mocked integration tests; excludes `performance` |
| `npm test -- --run` | Frontend component and API-client tests |
| `npm run build` | TypeScript and Vite production build |
| `npm run test:e2e` | Playwright mocked `desktop`/`mobile` projects plus the real Alembic/seed/FastAPI/Vite `seeded` flow |
| `python -m pytest -m performance tests/performance/test_company_queries.py -q` | 10,000-company / 100,000-job search p95 acceptance check |

## Collection API

With `COLLECTION_ENABLED=false`, a valid request to `POST /api/v1/collection-requests` returns:

```json
{
  "error": {
    "code": "collection_unavailable",
    "message": "Collection service is unavailable."
  }
}
```

The response status is `503`, while `GET /api/v1/companies` and company detail/job routes continue reading SQLite. With collection enabled, submit and poll a request from PowerShell:

```powershell
$body = @{ query = "Example Technologies" } | ConvertTo-Json
$request = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/collection-requests -ContentType application/json -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/collection-requests/$($request.id)"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/companies?q=Example%20Technologies"
```

Database request/run rows are the status source of truth. Terminal statuses are `succeeded`, `partial`, and `failed`; public failure codes include `collection_unavailable`, Provider codes such as `request_timeout`, and extraction `invalid_output`.

## Collection services

Start Redis from the repository root. This command binds only the local development port and persists no repository files:

```powershell
docker run --name company-search-redis --rm -p 6379:6379 redis:7-alpine
```

In separate PowerShell windows, activate the backend environment and start the API, worker, and Beat scheduler with the actual application modules:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m celery -A app.tasks.celery_app:celery_app beat --loglevel=INFO
```

Set runtime values in every API/worker/Beat process before startup:

```powershell
$env:COLLECTION_ENABLED = "true"
$env:CELERY_BROKER_URL = "redis://localhost:6379/0"
$env:CELERY_RESULT_BACKEND = "redis://localhost:6379/1"
$env:CACHE_REDIS_URL = "redis://localhost:6379/2"
$env:COLLECTION_RUNTIME_FACTORY = "your_runtime.module:create_components"
```

`COLLECTION_RUNTIME_FACTORY` must be a `module:callable` path. The callable takes no arguments and returns `app.tasks.collection.RuntimeComponents(providers, extractor, semantic_judge)`. The worker, not the factory, creates and closes three distinct SQLAlchemy sessions for run state, deduplication reads, and persistence writes. An absent, unimportable, or invalid factory terminalizes the request and run as `collection_unavailable`.

## Provider enablement

The runtime factory is the composition boundary. It reads Provider/LLM environment values, validates them, constructs only authorized Providers, and returns them in `RuntimeComponents.providers`. A disabled Provider is omitted from that sequence and therefore omitted from each run's `providers_attempted`; an enabled Provider failure is reported through the terminal/partial `error_code` and persisted run metadata.

Enable Zhihu only with an authorized Global Search API secret:

```powershell
$env:ZHIHU_PROVIDER_ENABLED = "true"
$env:ZHIHU_ACCESS_SECRET = "<authorized-secret>"
```

`ZhihuGlobalSearchProvider` refuses enabled startup without `ZHIHU_ACCESS_SECRET`, uses a 5-second connect timeout and 15-second total deadline, sends at most 20 results in one documented response, and retries only 429/5xx or transport timeouts with bounded delays. It does not perform undocumented pagination.

Configure the OpenAI-compatible extractor used by your runtime factory:

```powershell
$env:OPENAI_COMPATIBLE_BASE_URL = "https://llm.example.com/v1"
$env:OPENAI_COMPATIBLE_MODEL = "approved-model-name"
$env:OPENAI_COMPATIBLE_API_KEY = "<authorized-api-key>"
```

The repository defines the LLM protocol and validated JSON extraction boundary; the operator-supplied runtime factory must construct the compatible client, enforce its request timeout/rate limit, and pass it to `CrewExtractor`. Never enable collection with an unbounded client or persist raw model output.

Enable company-site collection only for explicitly approved websites:

```powershell
$env:COMPANY_SITE_PROVIDER_ENABLED = "true"
```

The factory must supply the approved company website/host to `CompanySiteProvider`. The Provider enforces public HTTP(S) destinations, redirect revalidation, `robots.txt`, same-host crawling, a ten-page cap, and partial page-failure warnings. Leave it disabled when ownership, authorization, or robots compliance is unclear.

Unsupported commercial job-board and company-data Providers remain explicitly disabled pending both credentials and collection authorization. Do not add them to `RuntimeComponents.providers`; disabled Providers make no network call and do not appear in `providers_attempted`.

Do not set real secrets in `.env.example`, tracked files, shell history, or test fixtures. Use process-scoped environment variables or an approved secret manager.
