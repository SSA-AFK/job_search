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
| [`plans/2026-07-31-company-search-web-foundation.md`](plans/2026-07-31-company-search-web-foundation.md) | Stage one implementation plan | Approved, ready to execute |
| [`plans/2026-07-31-company-search-ingestion-pipeline.md`](plans/2026-07-31-company-search-ingestion-pipeline.md) | Stage two implementation plan | Approved, waits for stage one gate |
| [`../zhihu.md`](../../zhihu.md) | Supplied Zhihu Global Search API contract | Reference |

## Current Snapshot

| Field | Value |
|-------|-------|
| Overall status | Stage one complete; stage two ready |
| Current stage | Stage one complete - Web search foundation |
| Current task | Integration decision before stage two |
| Execution method | Subagent-Driven Development |
| Active branch/worktree | `codex/company-search-web-foundation` at `.worktrees/codex-company-search-web-foundation` |
| Stage one progress | 8/8 tasks complete; completion gate passed |
| Stage two progress | 0/12 tasks complete |
| Last verified artifact state | Stage one implementation and finishing stabilization reviewed through `d8c7a9d` |
| Next action | Choose how to integrate the stage-one branch, then begin stage two Task 1 from the accepted baseline |

## Delivery Sequence

```text
Approved design
  -> Stage one: searchable local Web application
  -> Stage one completion gate
  -> Stage two: asynchronous ingestion pipeline
  -> Stage two completion gate
  -> Whole-product verification
  -> Integration decision
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
| 1 | Ingestion configuration and collection requests | Ready | - | - | - |
| 2 | Provider contracts and safe HTTP infrastructure | Pending Task 1 | - | - | - |
| 3 | Zhihu Global Search Provider | Pending Task 2 | - | - | - |
| 4 | Allowlisted company website Provider | Pending Task 3 | - | - | - |
| 5 | Structured CrewAI extraction adapters | Pending Task 4 | - | - | - |
| 6 | Deterministic normalization and deduplication | Pending Task 5 | - | - | - |
| 7 | Transactional idempotent persistence | Pending Task 6 | - | - | - |
| 8 | Orchestrator and terminal-state classification | Pending Task 7 | - | - | - |
| 9 | Celery tasks, daily refresh, and job expiration | Pending Task 8 | - | - | - |
| 10 | Optional Redis caching and invalidation | Pending Task 9 | - | - | - |
| 11 | Frontend collection polling | Pending Task 10 | - | - | - |
| 12 | End-to-end failure verification and runbook | Pending Task 11 | - | - | - |

### Stage Two Gate

- [ ] A mocked collection request reaches a terminal state and successful data becomes searchable.
- [ ] Duplicate requests, Celery delivery, and Provider documents remain idempotent.
- [ ] Zhihu requests match the supplied contract and never use undocumented pagination.
- [ ] Unsafe URLs and invalid LLM output cannot reach persistence.
- [ ] Refresh uses `last_collected_at < now - 24 hours`.
- [ ] Expiration uses `last_seen_at < now - 30 days` while retaining jobs with another active source.
- [ ] Search remains readable when Redis, Celery, the LLM, or any Provider is unavailable.
- [ ] Every enabled Provider declares credentials, compliance limits, timeouts, rate limits, and mock coverage.

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

## Active Risks

| Risk | Impact | Mitigation | State |
|------|--------|------------|-------|
| External Provider credentials or authorization unavailable | Some data sources cannot ship | Providers default off; stage one uses versioned seed data; stage two starts with supplied Zhihu contract | Controlled |
| SQLite/PostgreSQL behavior diverges | Later production migration may fail | Use SQLAlchemy types, named constraints, Alembic, and dialect-aware index tests | Open |
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
