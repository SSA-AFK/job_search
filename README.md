# AI Company Search

Local web application for searching companies, viewing evidence and jobs, and optionally collecting new company data. Existing SQLite data remains searchable when Redis, Celery, an LLM, or a Provider is unavailable.

## Recruiting data workflow

The production collection endpoint now performs bounded recruiting-entry verification for an
existing company. It checks existing entries first, then the company's `/careers` URL, and finally
at most one general Serper query. A run validates at most three candidates and five HTTP requests;
the first verified public entry stops the run. This stage never parses or writes jobs.

External job enumeration is separate and opt-in. JobHunt-CLI must be installed by the operator at
an absolute path, pinned to `JOBHUNT_EXPECTED_VERSION`, and enabled with an explicitly reviewed
`backend/data/jobhunt_sites.json` company-to-site mapping. The application never installs or updates
JobHunt-CLI, and JobHunt failure never falls back to BOSS automatically.

BOSS data is accepted only as a manually captured local JSON file. Import it into an explicit,
non-default database with:

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m app.imports.boss_cli `
  --database-url sqlite:///./review-copy.sqlite3 `
  --input C:\path\to\boss_jobs.json
```

The importer never starts Chrome, logs in, creates companies, writes complete-list snapshots, or
deactivates old jobs. Unmatched company names are counted and skipped.

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

The backend is available at `http://127.0.0.1:8011`. The frontend is available at `http://127.0.0.1:5174`. Collection stays disabled unless its worker runtime and external services are configured explicitly.

To confirm that seed imports remain idempotent, run the import command a second time. The second output reports no newly created companies, jobs, or sources.

## Frontend setup

In a second PowerShell window, run:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5174/companies`. Vite proxies `/api` requests to the local backend, so search, company details, and job-source links work from the seeded SQLite database without external services.

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

## Stage 3A coverage operations

Upgrade the configured database and print the internal 24-hour coverage report as JSON:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
python -m app.coverage.cli --refresh-hours 24
```

The report is an offline database read. Stage 3A is Complete at `83a8f14`: all seven planned tasks and Task 7 reviews passed, with final specification review PASS and quality review APPROVED after round 4/5 and no open findings. Current Providers do not write Stage 3A complete-list snapshots; Stage 3B is awaiting separate implementation plan and approval.

`JobSource.lifecycle_managed` records durable source-level lifecycle ownership. Legacy sources and sources linked only to partial/failed snapshots retain the 30-day age fallback; sources processed by an applied complete snapshot use the two-consecutive-complete-absences rule while their entry remains present.

The Stage 3A rollback target is migration `0005_extend_job_type_values`:

```powershell
python -m alembic downgrade 0005_extend_job_type_values
```

This downgrade is allowed only before Stage 3B writes production sources linked to job entries or collection snapshots. Back up the database and stop writers before any production migration. `TEST_POSTGRES_URL` is an opt-in test-runner input for the PostgreSQL migration gate, not an application setting; do not add it to application configuration or `.env.example`.

The verified Stage 3A closure matrix at `83a8f14` is: backend `539 passed / 2 skipped / 2 deselected`, integration `13 passed`, performance `2 passed / 541 deselected`, Ruff clean, mypy clean across 79 files, offline Alembic upgrade/downgrade clean, live PostgreSQL `2 passed / 17 deselected`, and zero residual `stage3a_test_*` schemas. The full backend run retains one intentional Pydantic serializer warning from the existing non-integer `salary_months` negative test.

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
$request = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8011/api/v1/collection-requests -ContentType application/json -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8011/api/v1/collection-requests/$($request.id)"
Invoke-RestMethod -Uri "http://127.0.0.1:8011/api/v1/companies?q=Example%20Technologies"
```

Database request/run rows are the status source of truth. Terminal statuses are `succeeded`, `partial`, and `failed`; public failure codes include `collection_unavailable`, Provider codes such as `request_timeout`, `provider_auth_failed`, and `provider_rate_limited`, plus extraction `invalid_output`. `crawl_runs.error_detail` stores sanitized JSON issues with stage, code, and optional provider attribution; it never stores credentials or raw model output.

## Collection services

Start Redis from the repository root. This command binds only the local development port and persists no repository files:

```powershell
docker run --name company-search-redis --rm -p 6379:6379 redis:7-alpine
```

In separate PowerShell windows, activate the backend environment and start the API, worker, and Beat scheduler with the actual application modules:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
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
$env:OPENAI_COMPATIBLE_BASE_URL = "https://llm.example.com/v1"
$env:OPENAI_COMPATIBLE_MODEL = "approved-model-name"
$env:OPENAI_COMPATIBLE_API_KEY = "<authorized-api-key>"
$env:ZHIHU_PROVIDER_ENABLED = "true"
$env:ZHIHU_ACCESS_SECRET = "<authorized-secret>"
```

The default checked-in factory is `app.ingestion.production.create_runtime_components`. It constructs the OpenAI-compatible extraction client, semantic duplicate judge, and enabled Providers directly from settings. It fails fast unless the endpoint, model, API key, and at least one authorized Provider are configured; invalid configuration terminalizes the request and run as `collection_unavailable` without making an external call.

`COLLECTION_RUNTIME_FACTORY` is an optional `module:callable` override for deployments that need another composition. The callable takes no arguments and returns `app.ingestion.runtime.RuntimeComponents(providers, extractor, semantic_judge)`. The worker creates and closes three distinct SQLAlchemy sessions for run state, deduplication reads, and persistence writes. An unimportable or invalid override also maps to `collection_unavailable`.

Beat runs `redispatch_stale_queued_runs` every minute. It requeues abandoned `running` rows and redispatches committed `queued` rows that have no task id, using `started_at` only for the `COLLECTION_STALE_RUNNING_SECONDS` cutoff and `COLLECTION_STALE_QUEUED_SECONDS` for queued rows. Each atomic claim gets a new UUID `claim_token`; retry, reconciliation, persistence, and terminal compare-and-set updates must carry that exact token. A duplicate delivery that observes `running` is an in-progress no-op. Persistence conditionally locks the paired run/request token inside its own transaction and holds ownership through commit. Migration `0004` backfills a unique token for running rows created by an older release. Ambiguous broker failures can terminalize only a paired request/run that remains `queued`, so they cannot overwrite a worker that already claimed or completed the run.

## Provider enablement

The checked-in production composition reads Provider/LLM environment values, validates them, constructs only authorized Providers, and returns them in `RuntimeComponents.providers`. A disabled Provider is omitted from that sequence and therefore omitted from each run's `providers_attempted`; an enabled Provider failure is reported through the terminal/partial `error_code` and persisted run metadata.

Every enabled Provider is wrapped in process-shared concurrency and start-rate controls. Configure `PROVIDER_MAX_CONCURRENCY` (default `2`) and `PROVIDER_MIN_INTERVAL_SECONDS` (default `0.25`); invalid values fail runtime composition before collection starts.

Enable Zhihu only with an authorized Global Search API secret:

```powershell
$env:ZHIHU_PROVIDER_ENABLED = "true"
$env:ZHIHU_ACCESS_SECRET = "<authorized-secret>"
```

`ZhihuGlobalSearchProvider` refuses enabled startup without `ZHIHU_ACCESS_SECRET`, uses a 5-second connect timeout and 15-second total deadline, sends at most 20 results in one documented response, and retries only 429/5xx or transport timeouts with bounded delays. Exhausted 429 responses use `provider_rate_limited`; 401/403 responses use `provider_auth_failed`. It does not perform undocumented pagination.

Configure the OpenAI-compatible extractor and semantic judge used by the default runtime:

```powershell
$env:OPENAI_COMPATIBLE_BASE_URL = "https://llm.example.com/v1"
$env:OPENAI_COMPATIBLE_MODEL = "approved-model-name"
$env:OPENAI_COMPATIBLE_API_KEY = "<authorized-api-key>"
$env:OPENAI_REQUEST_TIMEOUT_SECONDS = "30"
```

The repository constructs a bounded chat-completions client and passes it to `CrewExtractor` and `LlmSemanticDuplicateJudge`. The client requests identity encoding, rejects compressed responses, rejects oversized declared lengths, and stops streamed bodies at a fixed byte cap before JSON parsing. Each extraction role states its root arrays, required/optional fields, and supported enums in the prompt; all outputs still cross validated JSON schemas. Never persist raw model output.

Enable company-site collection only for explicitly approved websites:

```powershell
$env:COMPANY_SITE_PROVIDER_ENABLED = "true"
$env:COMPANY_SITE_APPROVED_HOSTS = "www.example.com,careers.example.com"
```

The orchestrator runs Providers in two phases. Discovery Providers first identify a candidate website for the selected company. The checked-in composition parses `COMPANY_SITE_APPROVED_HOSTS` as exact hostnames and passes that trusted set to `CompanySiteProvider`; it refuses enabled startup when the set is empty or invalid. The orchestrator invokes a website-dependent Provider only when the candidate host exactly matches that Provider's operator-approved set, and passes only the matched host through `ProviderQuery.allowed_hosts`. `CompanySiteProvider` enforces both authorization checks again before robots or HTTP work, then enforces public HTTP(S) destinations, redirect revalidation, `robots.txt`, same-host crawling, a ten-page cap, and partial page-failure warnings. LLM output never expands the allowlist. Leave this phase disabled when ownership, authorization, or robots compliance is unclear.

Unsupported commercial job-board and company-data Providers remain explicitly disabled pending both credentials and collection authorization. Do not add them to `RuntimeComponents.providers`; disabled Providers make no network call and do not appear in `providers_attempted`.

Do not set real secrets in `.env.example`, tracked files, shell history, or test fixtures. Use process-scoped environment variables or an approved secret manager.
# AI 求职公司榜（内部校准）

首批 AI 公司榜使用天眼查 Excel 导出作为本地基线，只补充融资、AI 发明专利、中标验证和重大风险四类缺口。基础工商、企业规模和完整历史不会重复调用。天眼返回仅用于内部结构化评分；用户可见结论必须来自官网、政府或审核后的公开来源。

在 `backend` 目录运行，且必须显式使用隔离 SQLite 数据库：

```powershell
$env:PYTHONPATH='.'
python -m app.rankings.cli `
  --database-url sqlite:///company_ranking_pilot_v2.sqlite3 `
  --workbook 'C:\path\to\companies.xlsx' `
  --sample-size 100 `
  --seed ai-ranking-pilot-v1 `
  --collect-tyc `
  --report
```

命令可重复执行：成功且未过期的类别会跳过；认证或额度耗尽时批次快速停止，恢复后从未完成类别继续。首轮每家公司最多四个数据类别；专利与软著、中标与资质分别需要两个底层工具，因此完整覆盖最多六个工具调用。输出同时报告 `logical_calls` 与 `tool_calls`，不得将网络批量请求数解释为供应商额度次数。

内部报告包含发展阶段、五维原始分、阶段百分位、证据覆盖和资格原因。AI 相关性由
Excel 经营范围的严格 AI 术语命中，或近三年 AI 专利/软件著作权自动判定；经营范围原文
仅在导入内存中使用，数据库只保存派生分类、命中数量和不可逆指纹。官网不参与默认
评分和入榜门槛，仅用于榜单前 20 家或争议公司的可选复核。

2026-08-12 首批校准批次使用规则 `tyc-ranking-v2` 与评分规则
`ai-long-term-v2`：100 家、400/400 个数据类别成功、最多 600 个底层工具调用；
重复运行验证为 0 调用。官网证据采集成功 16 家、无官网 37 家、关闭失败 47 家；
这些官网结果只保留为可选复核材料，不影响自动榜单。最终 98 家自动入榜，2 家因经营
范围和 AI 知识产权均无明确 AI 证据留在观察池。

临时 API Key 只允许写入本机 CLI 凭据存储，禁止写入仓库、数据库、报告或命令示例；
批次结束后应由密钥所有者在天眼账户侧立即废止。

## AI 榜单页面

应用默认使用现有 `backend/company_ranking_pilot_v2.sqlite3` 作为同一业务数据库，不创建或
联查第二套榜单数据库。若系统环境中已有旧 `DATABASE_URL`，启动后端时必须显式覆盖：

```powershell
cd backend
$env:DATABASE_URL='sqlite:///./company_ranking_pilot_v2.sqlite3'
$env:PYTHONPATH='.'
python -m uvicorn app.main:app --reload
```

- `/list`：98 家正式 AI 榜单与 2 家观察池。
- `/companies`：只检索本期固定 100 家公司。
- `/companies/{id}`：公司公开资料、榜单评分和白名单评分依据；职位区仅保留占位。

页面运行不访问天眼 API 或公司官网。榜单刷新通过离线采集与重评分命令完成，前端只读取
数据库快照。
