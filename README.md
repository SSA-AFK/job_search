# AI Company Search

Local web foundation for searching seed companies, viewing company details, and opening job sources. Stage one runs entirely on SQLite; Redis, Celery, LLM credentials, and network access are not required.

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

The backend is available at `http://127.0.0.1:8000`. The included `.env.example` documents the two supported environment variables. Set them in the current PowerShell session as shown above.

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

Run backend unit, API, seed, and migration checks:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m ruff check app tests
python -m pytest -q
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
| `python -m pytest -q` | Backend unit, API, seed, and migration tests; excludes `performance` |
| `npm test -- --run` | Frontend component and API-client tests |
| `npm run build` | TypeScript and Vite production build |
| `npm run test:e2e` | Playwright mocked `desktop`/`mobile` projects plus the real Alembic/seed/FastAPI/Vite `seeded` flow |
| `python -m pytest -m performance tests/performance/test_company_queries.py -q` | 10,000-company / 100,000-job search p95 acceptance check |

## Collection availability

Collection is deliberately unavailable in stage one. With `COLLECTION_ENABLED=false`, a valid request to `POST /api/v1/collection-requests` returns:

```json
{
  "error": {
    "code": "collection_unavailable",
    "message": "Collection service is unavailable."
  }
}
```

The response status is `503`. This makes the deferred collection capability explicit while leaving local search and browsing usable.
