# AI Company Search Global Iteration Tracker

This is the living delivery document for the AI Company Search project. It is the single global index for product scope, implementation progress, decisions, risks, and verification evidence. Detailed requirements remain in the design specification and detailed execution steps remain in the two implementation plans.

## Document Contract

- Update this document only after a task review reaches an approved terminal result or a blocker changes project direction.
- Keep historical decisions and iteration records append-only; correct an old entry with a newer superseding entry.
- A task is `Complete` only when implementation, focused tests, full relevant tests, commit, and independent task review are complete.
- `.superpowers/sdd/<plan>/progress.md` is the execution recovery ledger. This document is the durable human-facing summary.
- Test counts and commit hashes must be copied from fresh command output or the reviewed SDD report, never estimated.

## Source Documents

| Document | Purpose | Status |
|----------|---------|--------|
| [`specs/2026-07-31-ai-company-search-agent-design.md`](specs/2026-07-31-ai-company-search-agent-design.md) | Product, architecture, contracts, data model, and acceptance criteria | Approved |
| [`plans/2026-07-31-company-search-web-foundation.md`](plans/2026-07-31-company-search-web-foundation.md) | Stage one implementation plan | Executed and integrated locally into `main` |
| [`plans/2026-07-31-company-search-ingestion-pipeline.md`](plans/2026-07-31-company-search-ingestion-pipeline.md) | Stage two implementation plan | Executed and integrated locally into `main` |
| [`../dev/job-coverage-at-scale-plan.md`](../dev/job-coverage-at-scale-plan.md) | Stage 3 coverage design and scale gates | Stage 3A implemented; Task 7/final review pending |
| [`../dev/migration-master-plan.md`](../dev/migration-master-plan.md) | Stage 3 migration and approval sequence | Stage 3A implemented; later stages awaiting separate plans |
| [`plans/2026-08-05-company-search-stage3a-coverage-observability.md`](plans/2026-08-05-company-search-stage3a-coverage-observability.md) | Stage 3A detailed implementation plan | Tasks 1–6 reviewed; Task 7/final review pending |
| [`../zhihu.md`](../../zhihu.md) | Supplied Zhihu Global Search API contract | Reference |

## Current Snapshot

| Field | Value |
|-------|-------|
| Overall status | Stage one and two complete on `main`; Stage 3A implemented on isolated branch with final reviews pending |
| Current stage | Stage 3A coverage observability completion gate |
| Current task | Task 7 independent review, then whole-branch review |
| Execution method | Subagent-Driven Development |
| Active branch/worktree | `codex/company-search-stage3a-coverage-observability` in its isolated worktree; main-workspace WIP untouched |
| Stage one progress | 8/8 tasks complete; completion gate passed |
| Stage two progress | 12/12 tasks complete; completion gate passed |
| Stage 3A progress | Tasks 1–6 reviewed; Task 7 implemented and current matrix passed; Task 7/final reviews pending |
| Last verified artifact state | Stage 3A branch: Ruff clean; mypy 79 files; backend 513 passed/1 skipped/2 deselected; integration 13; migration/seed 34 passed/1 skipped; performance 2 passed at 13.0 ms p95; live PostgreSQL marker 1 passed with no schema residue |
| Next action | Complete Task 7 and whole-branch reviews; Stage 3B awaits a separate implementation plan and approval |

## Delivery Sequence

```text
Approved design
  -> Stage one: searchable local Web application
  -> Stage one completion gate
  -> Stage two: asynchronous ingestion pipeline
  -> Stage two completion gate
  -> Whole-product verification
  -> Integration decision
  -> Local integration into main
  -> Stage 3A: coverage observability foundation
  -> Stage 3A completion gate and integration decision
  -> Separate Stage 3B plan and approval
```

Stage two must not begin until every stage one gate in the plan passes. Within a stage, tasks run sequentially because later task briefs consume interfaces and commits from earlier tasks.

## Stage One Tracker

Plan: [`plans/2026-07-31-company-search-web-foundation.md`](plans/2026-07-31-company-search-web-foundation.md)

| Task | Deliverable | Status | Commit(s) | Review | Verification |
|------|-------------|--------|-----------|--------|--------------|
| 1 | Backend application bootstrap | Complete | `6ab6b06` | Approved, no findings | pytest 1 passed; Ruff passed; 93 third-party Python 3.13 deprecation warnings documented |
| 2 | Normalized database schema and migration | Complete | `1635a5a`, `c6e4bd0` | Approved after fix round 1 | full pytest 11 passed; Ruff passed; migration parity verified; SQLite UTC round trip covered |
| 3 | Normalization and idempotent seed import | Complete | `961bc75` | Approved, no findings | focused pytest 10 passed; full pytest 21 passed; Ruff passed; repeated CLI idempotent |
| 4 | Company query services and REST endpoints | Complete | `06fedd8`, `4d74880` | Approved after artifact-only fix round 1; one deferred Minor | focused pytest 30 passed; full pytest 51 passed; Ruff passed |
| 5 | Stage-one collection API contract | Complete | `3530d72` | Approved, no findings | focused pytest 5 passed; full pytest 56 passed; OpenAPI smoke and Ruff passed |
| 6 | Search workspace frontend | Complete | `ba7b44c`, `50ef731` | Approved after fix round 1; two deferred Minors | Vitest 13 passed; build passed; contrast and responsive checks passed |
| 7 | Company detail, empty state, and browser flows | Complete | `43b442f`, `4a26e0c` | Approved after fix round 1 | focused Vitest 8 passed; full Vitest 23 passed; build passed; Playwright desktop 3 passed and mobile 3 passed; responsive visual checks passed |
| 8 | Performance verification and developer runbook | Complete | `9170d5e`, `eb0a575` | Approved after fix round 1; one deferred Minor | Ruff passed; normal pytest 56 passed/2 deselected; Vitest 23 passed; build passed; Playwright 6 passed; performance 2 passed at 22.9 ms p95; migration/seed/503/artifact checks passed |

### Stage One Gate

- [x] Backend Ruff and pytest suites pass with pristine output.
- [x] Frontend Vitest, build, and Playwright suites pass.
- [x] Alembic upgrades an empty SQLite database.
- [x] Repeated seed import does not increase unique entity counts.
- [x] Seeded company search and details work without external services.
- [x] Collection endpoint returns the documented `503 collection_unavailable` response.
- [x] The generated 10,000-company/100,000-job dataset query p95 is at most 300 ms.
- [x] No credentials, database files, virtual environments, build outputs, or SDD scratch artifacts are tracked.

## Stage Two Tracker

Plan: [`plans/2026-07-31-company-search-ingestion-pipeline.md`](plans/2026-07-31-company-search-ingestion-pipeline.md)

| Task | Deliverable | Status | Commit(s) | Review | Verification |
|------|-------------|--------|-----------|--------|--------------|
| 1 | Ingestion configuration and collection requests | Complete | `0395040` | Approved; no Critical/Important; one deferred Minor | focused collection/API/migration 15 passed; full pytest 87 passed/2 deselected; Ruff passed |
| 2 | Provider contracts and safe HTTP infrastructure | Complete | `584a0e1`, `f3452ad` | Approved after security fix round 1 | provider tests 28 passed; full pytest 115 passed/2 deselected; Ruff passed |
| 3 | Zhihu Global Search Provider | Complete | `8f13e83`, `fab12c2` | Approved after boundary fix round 1 | focused 16 passed; provider tests 44 passed; full pytest 131 passed/2 deselected; mypy and Ruff passed |
| 4 | Allowlisted company website Provider | Complete | `ea3d3b2`, `7db5c10`, `2f09d4f` | Approved after security fix rounds 1-2; two deferred Minors | focused company-site 16 passed; provider tests 63 passed; full pytest 150 passed/2 deselected; mypy and Ruff passed |
| 5 | Structured CrewAI extraction adapters | Complete | `cf22a3a`, `a70706e` | Approved after fix round 1; two deferred Minors | extraction tests 11 passed; full pytest 161 passed/2 deselected; mypy and Ruff passed |
| 6 | Deterministic normalization and deduplication | Complete | `e0252bd`, `816d969`, `567e4f5` | Approved after fix rounds 1-2; one deferred Minor | focused normalization/deduplication 23 passed; full pytest 188 passed/2 deselected; mypy and Ruff passed |
| 7 | Transactional idempotent persistence | Complete | `6a404ca`, `18726ab`, `fdadd26`, `90132b4`, `b9da944` | Approved after fix rounds 1-4 | Task 6/7 focused 125 passed; full pytest 247 passed/2 deselected; mypy and Ruff passed |
| 8 | Orchestrator and terminal-state classification | Complete | `102b154..5c593da` | Approved after fix rounds 1-4; one deferred Minor | focused builder/runtime 14 passed; ingestion/collection 197 passed; full pytest 280 passed/2 deselected; mypy and Ruff passed |
| 9 | Celery tasks, daily refresh, and job expiration | Complete | `f253f17`, `1e94c0e`, `bcaf218` | Approved after fix rounds 1-2; three deferred Minors | tasks/collection 24 passed; ingestion 190 passed; full pytest 297 passed/2 deselected; Ruff passed; scoped mypy retains one pre-existing core error |
| 10 | Optional Redis caching and invalidation | Complete | `d49ba63`, `7c2dd72` | Approved after fix round 1 | cache 12 passed; cache/company/persistence 54 passed; full pytest 309 passed/2 deselected; Ruff and scoped mypy passed |
| 11 | Frontend collection polling | Complete | `9e4bd03`, `514eedd`, `73be699`, `c7326b4` | Approved after fix rounds 1-3 | focused Vitest 26 passed; full Vitest 54 passed; build passed; desktop/mobile Playwright collection flow passed |
| 12 | End-to-end failure verification and runbook | Complete | `22f3c6a`, `b6589d1`, `aca6f75` | Approved after fix rounds 1-2; two deferred Minors | Ruff passed; mypy 69 files passed; full pytest 347 passed/2 deselected; integration 12 passed; performance 2 passed; migrations/seed 19 passed; Vitest 54 passed; build passed; Playwright 9 passed |

### Stage Two Gate

- [x] A mocked collection request reaches a terminal state and successful data becomes searchable.
- [x] Duplicate requests, Celery delivery, and Provider documents remain idempotent.
- [x] Zhihu requests match the supplied contract and never use undocumented pagination.
- [x] Unsafe URLs and invalid LLM output cannot reach persistence.
- [x] Refresh uses `last_collected_at < now - 24 hours`.
- [x] Expiration uses `last_seen_at < now - 30 days` while retaining jobs with another active source.
- [x] Search remains readable when Redis, Celery, the LLM, or any Provider is unavailable.
- [x] Every enabled Provider declares credentials, compliance limits, timeouts, rate limits, and mock coverage.

## Stage 3A Tracker

Plan: [`plans/2026-08-05-company-search-stage3a-coverage-observability.md`](plans/2026-08-05-company-search-stage3a-coverage-observability.md)

| Task | Deliverable | Status | Commit(s) | Review |
|------|-------------|--------|-----------|--------|
| 1 | Coverage enums and immutable commands | Complete | `758078b`, `2bc5a45`, `1d15f46` | Approved after fix rounds 1–2 |
| 2 | Migration `0006` | Complete | `5067b4c`, `218fffd`, `11a4a11` | Approved after fix rounds 1–2 |
| 3 | Migration `0007` | Complete | `c4f090b`, `f778ec1` | Approved after fix round 1 |
| 4 | Transaction-neutral coverage repository | Complete | `b1fea8e`, `bd4f5ec`, `b7ffe3d`, `a0e5b65` | Approved after fix rounds 1–3 |
| 5 | Atomic snapshot/source lifecycle | Complete | `2d3f166`, `5936b41`, `eae6055` | Approved after fix rounds 1–2 |
| 6 | Coverage reports and JSON CLI | Complete | `809bafa`, `3b208ae`, `5c30753` | Approved after fix rounds 1–2 |
| 7 | Integration acceptance and completion gate | Review pending | Current gate commit | Controller review pending |

### Stage 3A Gate

- [x] Empty, failed, partial, complete, replay and source/posting lifecycle behavior has database-backed coverage.
- [x] SQLite migrations cover empty database, `0005`, downgrade and legacy-source preservation.
- [x] Live PostgreSQL upgrades through head and cleans its isolated schema without `CASCADE`.
- [x] Internal JSON reports distinguish confirmed empty companies from failures with bounded query count.
- [x] Current Providers do not write false complete-list snapshots; default tests use no live network, browser, Redis or LLM.
- [ ] Task 7 independent specification and quality review.
- [ ] Final whole-branch review and current-HEAD matrix after review findings.
- [ ] Integration decision. Stage 3B remains unapproved.

## Quality Gates Per Task

Every task follows this sequence:

1. Extract a task brief from the approved plan.
2. Dispatch a fresh implementer using TDD.
3. Capture RED and GREEN evidence in the task report.
4. Commit the task implementation.
5. Generate a review package from the recorded task base to its head.
6. Dispatch an independent reviewer for specification compliance and code quality.
7. Resolve Critical or Important findings through bounded fix/re-review rounds.
8. Update the SDD ledger and this tracker only after the task gate closes.

The whole stage receives a separate broad review after all of its task reviews pass.

## Stable Product Invariants

- Search reads only local persisted data and never waits on external collection.
- CrewAI and the LLM produce candidate data only; deterministic services own validation, normalization, deduplication, state changes, and persistence.
- `job_sources` preserves the provider, platform raw id, and apply URL relationship.
- `collection_requests` and `crawl_runs` are the source of truth for asynchronous state.
- Provider integrations default to disabled and cannot bypass access controls or service terms.
- Redis and external integrations may improve or refresh results but cannot make existing search data unreadable.
- All timestamps are UTC in storage and RFC 3339 in the API.

## Decision Log

| Date | Decision | Rationale | Supersedes |
|------|----------|-----------|------------|
| 2026-07-31 | Split delivery into Web foundation and ingestion pipeline | Produces an independently runnable product before adding external-system risk | Initial five-Agent single-stage design |
| 2026-07-31 | Restrict CrewAI to discovery and extraction roles | Deduplication, persistence, and state transitions require deterministic and testable behavior | Five sequential Agents including persistence |
| 2026-07-31 | Normalize job source records into `job_sources` | Maintains provider/raw-id/apply-URL identity and idempotency | Parallel source and URL arrays |
| 2026-07-31 | Use database task rows as asynchronous truth | Celery state alone cannot support durable polling and replay | Log-only crawl tracking |
| 2026-07-31 | Execute with Subagent-Driven Development | Provides a fresh implementer and independent review gate per task | Inline execution option |
| 2026-07-31 | Resolve stage-one test preflight in favor of behavioral assertions | Keep user-facing error copy changeable, define deterministic source ordering, and allow only the minimal frontend test harness before RED | Ambiguous plan test details |
| 2026-07-31 | Keep `JobSource` without `TimestampMixin` | `first_seen_at` and `last_seen_at` already define source lifecycle; the approved design does not include redundant creation/update fields | Conflicting Task 2 code example |
| 2026-07-31 | Ignore frontend generated artifacts when the frontend is created | `npm install` and browser tooling generate large local directories before Task 8; guarding them in Task 6 prevents accidental staging | Task 8-only `.gitignore` timing |
| 2026-08-01 | Permit `queued -> failed` only for pre-worker dispatch failure and make Task 6 deduplication resolution async | Prevent undiscoverable queued rows while preserving the normal worker state machine; align deduplication with the async semantic judge | Conflicting Task 1 transition text and synchronous Task 6 examples |
| 2026-08-01 | Assign Provider contract field evolution to Tasks 3 and 4 | The original File Maps created immutable base contracts in Task 2 but omitted ownership for fields explicitly consumed by the Zhihu and company-site Providers | Under-specified downstream ProviderQuery/ProviderResult evolution |

## Active Risks

| Risk | Impact | Mitigation | State |
|------|--------|------------|-------|
| External Provider credentials or authorization unavailable | Some data sources cannot ship | Providers default off; stage one uses versioned seed data; stage two starts with supplied Zhihu contract | Controlled |
| SQLite/PostgreSQL behavior diverges | Later production migration may fail | Live PostgreSQL marker plus SQLAlchemy types, named constraints, Alembic, and dialect-aware tests | Controlled through `0007`; reopen for later migrations |
| LLM output is malformed or follows hostile page text | Incorrect or unsafe data could be written | Bounded plain-text evidence, fixed roles, no tools, Pydantic validation, evidence-id checks | Open until stage two |
| Duplicate task delivery or source replay | Duplicate entities and unstable status | Database unique constraints, run idempotency, repeated-delivery integration tests | Open until stage two |
| Search performance degrades at target volume | User-visible latency exceeds target | Indexed query paths and a 10k/100k p95 gate in stage one | Controlled at 20.5 ms p95 in final verification |

## Verification Evidence

| Date | Scope | Command or evidence | Result |
|------|-------|---------------------|--------|
| 2026-07-31 | Design and implementation plans | Structural script checked required terms, obsolete terms, placeholders, trailing whitespace, balanced fences, required headers, and five steps per task | PASS: stage one 8 tasks/40 steps; stage two 12 tasks/60 steps |
| 2026-07-31 | Stage one Task 8 acceptance matrix | Ruff, normal pytest, Vitest, build, desktop/mobile Playwright, empty migration, repeated seed, collection 503, artifact hygiene, and 10k/100k performance fixture | PASS: p95 22.9 ms; one deferred dependency deprecation warning |
| 2026-07-31 | Stage one final review and controller verification | Independent whole-branch review, one final fix wave, scoped re-review, then fresh backend/frontend/browser/performance commands | PASS: Ruff clean; pytest 75 passed/2 deselected with no warnings; p95 20.5 ms; Vitest 28 passed; build passed; resolver 6 passed; Playwright 7 passed; tracked generated artifacts 0 |
| 2026-08-01 | Finishing mobile E2E stabilization | Diagnosed a WebKit first-load timeout from a loading-state snapshot; added a user-visible detail-ready wait without changing product code; independent scoped review | PASS: focused mobile 1 passed; resolver 6 passed; Playwright 7 passed; Vitest 28 passed; build passed; review approved |
| 2026-08-01 | Finishing mobile search readiness | Applied the same local 20-second readiness allowance to the two remaining mocked mobile flows and removed a fixed sleep; all navigation and collection assertions remain | PASS: focused mobile 2 passed; resolver 6 passed; Playwright 7 passed; Vitest 28 passed; build passed; scoped follow-up approved |
| 2026-08-01 | Final mobile result-transition stabilization and local integration | Waited for the initial mocked result before filtering, used distinct initial and filtered companies, independently approved the scoped test change, then merged the stage-one branch into `main` | PASS: Ruff clean; pytest 75 passed/2 deselected; performance 2 passed at 19.3 ms p95; Vitest 28 passed; build passed; resolver 6 passed; Playwright 7 passed; final reviewed commit `af63ec0` |
| 2026-08-01 | Stage two baseline | Isolated worktree at `ed7755f`; backend and frontend baseline commands | PASS: Ruff clean; pytest 75 passed/2 deselected; Vitest 28 passed; build passed |
| 2026-08-01 | Stage two Task 1 | Database-backed collection request implementation and independent task review | PASS: focused 15 passed; full pytest 87 passed/2 deselected; Ruff clean; review approved with one deferred concurrency-test Minor |
| 2026-08-01 | Stage two Task 2 | Provider contracts, SSRF-safe HTTP boundary, and scoped security re-review | PASS: provider tests 28 passed; full pytest 115 passed/2 deselected; Ruff clean; DNS pinning, compressed-body bound, and media-type findings addressed |
| 2026-08-03 | Stage two Task 3 | Zhihu Global Search Provider and scoped boundary re-review | PASS: focused 16 passed; provider tests 44 passed; full pytest 131 passed/2 deselected; mypy/Ruff clean; timeout, allowlist, and malformed-response findings addressed |
| 2026-08-03 | Stage two Task 4 | Allowlisted company website Provider and two scoped security re-reviews | PASS: focused company-site 16 passed; provider tests 63 passed; full pytest 150 passed/2 deselected; mypy/Ruff clean; redirect policy, origin cache, seed canonicalization, and explicit port-zero findings addressed; two Minors deferred |
| 2026-08-03 | Stage two Task 5 | Structured extraction adapters and scoped fix re-review | PASS: extraction tests 11 passed; full pytest 161 passed/2 deselected; mypy/Ruff clean; company scoping, complete prompt cap, and collision-free evidence IDs verified; two Minors deferred |
| 2026-08-03 | Stage two Task 6 | Deterministic normalization/deduplication and two scoped fix re-reviews | PASS: focused 23 passed; full pytest 188 passed/2 deselected; mypy/Ruff clean; cross-company source isolation, exact salary precision, and explicit employment compatibility verified; one Minor deferred |
| 2026-08-03 | Stage two Task 7 | Transactional/idempotent persistence and four scoped fix re-reviews | PASS: Task 6/7 focused 125 passed; full pytest 247 passed/2 deselected; mypy/Ruff clean; race-safe savepoints, fallback identity index, stale ordering, deep immutability, DB bounds, and audited rollback verified |
| 2026-08-03 | Stage two Task 8 | Traceable orchestration and four scoped fix re-reviews | PASS: focused builder/runtime 14 passed; ingestion/collection 197 passed; full pytest 280 passed/2 deselected; mypy/Ruff clean; terminal synchronization, discovery, warnings, evidence provenance, isolated sessions, and fresh-worker idempotency verified; one Minor deferred |
| 2026-08-03 | Stage two Task 9 | Celery tasks, daily refresh, job expiration, and two scoped fix re-reviews | PASS: tasks/collection 24 passed; ingestion 190 passed; full pytest 297 passed/2 deselected; Ruff clean; Redis transport, fresh-worker registration, normalized-query race recovery, fresh-session retry recovery, same-run retry, and exhaustion terminalization verified; three Minors deferred |
| 2026-08-04 | Stage two Task 10 | Optional Redis cache-aside queries and transactional invalidation with scoped fix re-review | PASS: cache 12 passed; cache/company/persistence 54 passed; full pytest 309 passed/2 deselected; Ruff/scoped mypy clean; exact TTLs, Pydantic serialization, bounded degraded mode, atomic version-token writes, and post-commit invalidation verified |
| 2026-08-04 | Stage two Task 11 | Frontend collection polling with three scoped fix re-reviews | PASS: focused Vitest 26 passed; full Vitest 54 passed; build passed; desktop/mobile Playwright passed; exact cadence, independent deadline, StrictMode single submission, Unicode-normalized bounded sessions, manual recovery, and responsive layout verified |
| 2026-08-04 | Stage two Task 12 | End-to-end failure verification, runbook, and two scoped fix re-reviews | PASS: Ruff clean; mypy 69 files; full pytest 347 passed/2 deselected; integration 12 passed; performance 2 passed; migrations/seed 19 passed; Vitest 54 passed; build passed; Playwright 9 passed; opt-in collection, deterministic conflicts, bounded Zhihu responses, two-phase company-site collection, and operator-owned host authorization verified; two Minors deferred |
| 2026-08-04 | Stage two final review fix wave | Ten Important findings plus scoped concurrency/security follow-ups | BLOCKED: implementation gates pass (Ruff, mypy 71 files, backend 398 passed/2 deselected, integration 12, performance 2, migrations/seed 21, Vitest 54, build, Playwright 9), but official scoped review found a reconciliation race and employment-type loss |
| 2026-08-04 | Stage two authorized targeted repair | Atomic reconciliation, employment-type preservation, and two scoped review rounds | PASS: Ruff clean; mypy 71 files; backend 414 passed/2 deselected; integration 12; performance 2; migrations/seed 24; Vitest 55; build passed; Playwright 9; official scoped re-review found no Critical/Important |
| 2026-08-05 | Stage 3A Tasks 1–7 implementation matrix | Coverage contracts, migrations, lifecycle, reports, integration acceptance, Provider audit, and live PostgreSQL round trip | PASS before final reviews: Ruff clean; mypy 79 files; backend 513 passed/1 skipped/2 deselected; integration 13; migration/seed 34 passed/1 skipped; performance 2 at 13.0 ms p95; PostgreSQL 1 passed and zero residual schemas; one known Pydantic negative-test warning |

## Iteration History

| Date | Iteration | Outcome | Next |
|------|-----------|---------|------|
| 2026-07-31 | Design review | Found reversed time predicates, unnormalized job sources, missing task/API contracts, and non-deterministic persistence role | Revise design |
| 2026-07-31 | Design revision | Added two-stage architecture, normalized models, REST contracts, Provider boundaries, task states, and acceptance criteria | Write plans |
| 2026-07-31 | Planning | Produced and structurally verified both stage plans | Establish global tracker and begin stage one |
| 2026-07-31 | Stage one Task 1 | Bootstrapped FastAPI, typed settings, SQLAlchemy session factory, SQLite foreign keys, and `/api/v1/health`; independent review approved with no findings | Implement normalized schema and migration |
| 2026-07-31 | Stage one Task 2 | Added nine normalized tables and Alembic migration; fixed SQLite UTC normalization in review round 1; scoped re-review approved | Add normalization and idempotent seed import |
| 2026-07-31 | Stage one Task 3 | Added strict seed schemas, Unicode/URL normalization, five-company seed data, and one-transaction idempotent import; review approved with no findings | Implement company query APIs |
| 2026-07-31 | Stage one Task 4 | Added company search, detail, jobs, filters, ranking, pagination, stable errors, and source ordering; removed accidentally tracked SDD report in review round 1 | Establish disabled collection API contract |
| 2026-07-31 | Stage one Task 5 | Added disabled collection POST/GET routes with normalized validation, stable 503/422 errors, and OpenAPI coverage; review approved with no findings | Build search workspace frontend |
| 2026-07-31 | Stage one Task 6 | Built URL-driven responsive search workspace; fixed out-of-range recovery, focus contrast, and live status in review round 1 | Add detail and browser workflows |
| 2026-07-31 | Stage one Task 7 | Added company details, paginated active jobs, provider-paired application links, terminal empty-query collection handling, and desktop/mobile browser flows; added HTTP(S)-only logo validation in review round 1 | Add performance verification and developer runbook |
| 2026-07-31 | Stage one Task 8 | Added deterministic 10k-company/100k-job performance verification, a complete PowerShell runbook, sample environment, generated-artifact policy, and the full acceptance matrix; restored explicit frontend ignores in review round 1 | Run whole-branch review and close the stage-one completion gate |
| 2026-07-31 | Stage one final review | Corrected enum contracts, sanitized correlated 500 handling, URL boundaries, warning cleanliness, queryless relevance, and E2E interpreter resolution; added real seeded browser-to-backend coverage; scoped re-review approved all findings | Choose integration path, then begin stage two Task 1 |
| 2026-08-01 | Stage one finishing verification | Stabilized the mocked mobile detail test at the visible loaded boundary after a reproducible slow WebKit first render; source-link assertions remain unchanged and scoped review approved | Run current-HEAD verification and choose integration path |
| 2026-08-01 | Stage one finishing verification follow-up | Unified mocked mobile search readiness waits after current-HEAD verification exposed the same host-specific WebKit latency in the remaining flows; removed arbitrary sleep and preserved behavioral assertions | Run final current-HEAD verification and choose integration path |
| 2026-08-01 | Stage one final stabilization and local integration | Prevented stale-result false positives by waiting for initial readiness and distinguishing initial `Moonshot AI` from filtered `DeepSeek`; independent review approved `af63ec0`; merged locally into `main` and removed the completed feature branch/worktree | Begin stage two Task 1 from the accepted `main` baseline |
| 2026-08-01 | Stage two execution start | Created the isolated ingestion-pipeline worktree, verified the Stage 1 baseline, and resolved preflight state-machine and async-interface conflicts with user approval | Implement stage two Task 1 with TDD and independent review |
| 2026-08-01 | Stage two Task 1 | Activated POST/GET collection requests, normalized active-request reuse, atomic request/run creation, post-commit dispatch tracking, discoverable dispatch failure, and the cross-dialect partial unique index; independent review approved | Implement provider contracts and safe HTTP infrastructure |
| 2026-08-01 | Stage two Task 2 | Added immutable Provider contracts and defensive HTTP fetching; security review found DNS rebinding and decompression-bound gaps, fixed by validated-address connection pinning, proxy bypass prevention, and identity-only content encoding; scoped re-review approved | Implement the Zhihu Global Search Provider |
| 2026-08-03 | Stage two Task 3 | Added exact single-page Zhihu Global Search requests, authentication, supported host filters, bounded retries, structured response mapping, and disabled-by-default configuration; fixed wall-clock timeout, forbidden-only allowlist broadening, and malformed response handling in review round 1 | Implement the allowlisted company website Provider |
| 2026-08-03 | Stage two Task 4 | Added bounded same-origin website crawling with robots enforcement, eligible-path filtering, canonicalized breadth-first discovery, partial-result warnings, and challenge detection; two review rounds closed redirect-policy, per-origin cache, seed canonicalization, and explicit port-zero defects | Implement structured CrewAI extraction adapters |
| 2026-08-03 | Stage two Task 5 | Added strict candidate schemas, evidence-reference validation, fixed extraction roles behind an application protocol, bounded untrusted-text prompts, and invalid-output classification; review round 1 added target-company scoping, complete prompt budgeting, and collision-free evidence IDs | Implement deterministic normalization and deduplication |
| 2026-08-03 | Stage two Task 6 | Added exact-first company/job deduplication, bounded fuzzy and semantic matching, company isolation, candidate source contracts, and deterministic salary normalization; two review rounds corrected cross-company source matches, employment compatibility, and precision-safe RMB conversion | Implement transactional idempotent persistence |
| 2026-08-03 | Stage two Task 7 | Added persistence-ready immutable batch contracts and one-transaction upserts for documents, evidence, companies, jobs, sources, and filings; four review rounds added race-safe savepoint recovery, null-external document uniqueness, stale-write protection, deep bounds, and audited numeric overflow handling | Build the orchestrator and terminal-state classification |
| 2026-08-03 | Stage two Task 8 | Added discovery-first provider orchestration, validated batch composition, synchronized terminal state, deterministic partial/failure classification, SQLAlchemy dedup repositories, and distinct-session runtime composition; four review rounds closed transaction ownership, provenance, warning, discovery, and fresh-worker idempotency gaps | Wire Celery tasks and daily maintenance |
| 2026-08-03 | Stage two Task 9 | Added Redis-capable Celery wiring, registered collection and maintenance tasks, exact daily refresh/expiration rules, normalized-query race recovery, bounded same-run retries, and synchronized exhaustion failure; two review rounds closed deployment registration, session cleanup, and infrastructure-state recovery gaps | Add optional Redis query caching and transactional invalidation |
| 2026-08-04 | Stage two Task 10 | Added optional cache-aside list/detail/job responses, canonical versioned keys, exact TTLs, warning-only Redis degradation, and post-commit invalidation; review round 1 added bounded timeouts, atomic version-token writes, and partial-failure-safe invalidation ordering | Implement frontend collection polling |
| 2026-08-04 | Stage two Task 11 | Added typed collection polling, exact capped backoff, public status UI, manual refresh, and success navigation; three review rounds added independent deadlines, durable StrictMode-safe request sessions, full Unicode normalization, capacity/TTL policy, transport recovery, and terminal manual persistence | Verify end-to-end failure modes and update the runbook |
| 2026-08-04 | Stage two Task 12 | Added real API-to-worker-to-persistence acceptance coverage and operating documentation; two review rounds closed opt-in gating, production company-site reachability, response bounds, deterministic conflict recovery, local URL aliases, and LLM-controlled outbound authorization | Run the stage two whole-branch review and completion gate |
| 2026-08-04 | Stage two whole-branch review | Implemented the single approved final fix wave and passed all current-HEAD verification; official scoped re-review closed nine original findings but retained one reconciliation atomicity defect and found employment-type loss in production deduplication | Await authorization for an additional targeted repair; do not merge `5152155` as-is |
| 2026-08-04 | Stage two authorized targeted repair | Added atomic run-to-request reconciliation locking and first-class part-time/temporary job types; the second scoped round corrected SQLite FK-preserving migration, PostgreSQL lock order, and frontend contracts | Completion review approved; run final current-HEAD verification and choose integration path |
| 2026-08-05 | Stage two local integration | Fast-forwarded approved stage-two HEAD `5ff344a` into `main`, repeated merged-result verification, restored and hash-verified pre-existing user work, and removed the merged feature branch/worktree using validated per-file cleanup | Define and approve the next bounded stage before implementation |
| 2026-08-05 | Stage 3A Tasks 1–7 implementation | Added formal entry/snapshot facts, replay-safe lifecycle, safe two-absence source deactivation, bounded internal coverage reporting, real database acceptance, and PostgreSQL revision-column compatibility; Tasks 1–6 reviews passed | Run Task 7 independent review and final whole-branch review; do not begin Stage 3B without a separate approved plan |

## Update Template

Append one row to Iteration History after each approved task and update Current Snapshot plus the corresponding task row.

```text
Date:
Stage / task:
Status: Complete | Blocked
Commit range:
Focused verification:
Broader verification:
Review result:
Deferred findings or risks:
Next task:
```
