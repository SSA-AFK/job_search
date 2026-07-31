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
| Overall status | Implementation in progress |
| Current stage | Stage one - Web search foundation |
| Current task | Stage one Task 1 - Bootstrap the Backend Application |
| Execution method | Subagent-Driven Development |
| Active branch/worktree | `codex/company-search-web-foundation` at `.worktrees/codex-company-search-web-foundation` |
| Stage one progress | 0/8 tasks complete |
| Stage two progress | 0/12 tasks complete |
| Last verified artifact state | Design and both plans passed structural verification on 2026-07-31 |
| Next action | Initialize the stage-one SDD ledger, then dispatch stage one Task 1 |

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
| 1 | Backend application bootstrap | Ready | - | - | - |
| 2 | Normalized database schema and migration | Pending | - | - | - |
| 3 | Normalization and idempotent seed import | Pending | - | - | - |
| 4 | Company query services and REST endpoints | Pending | - | - | - |
| 5 | Stage-one collection API contract | Pending | - | - | - |
| 6 | Search workspace frontend | Pending | - | - | - |
| 7 | Company detail, empty state, and browser flows | Pending | - | - | - |
| 8 | Performance verification and developer runbook | Pending | - | - | - |

### Stage One Gate

- [ ] Backend Ruff and pytest suites pass with pristine output.
- [ ] Frontend Vitest, build, and Playwright suites pass.
- [ ] Alembic upgrades an empty SQLite database.
- [ ] Repeated seed import does not increase unique entity counts.
- [ ] Seeded company search and details work without external services.
- [ ] Collection endpoint returns the documented `503 collection_unavailable` response.
- [ ] The generated 10,000-company/100,000-job dataset query p95 is at most 300 ms.
- [ ] No credentials, database files, virtual environments, build outputs, or SDD scratch artifacts are tracked.

## Stage Two Tracker

Plan: [`plans/2026-07-31-company-search-ingestion-pipeline.md`](plans/2026-07-31-company-search-ingestion-pipeline.md)

| Task | Deliverable | Status | Commit(s) | Review | Verification |
|------|-------------|--------|-----------|--------|--------------|
| 1 | Ingestion configuration and collection requests | Blocked by stage one gate | - | - | - |
| 2 | Provider contracts and safe HTTP infrastructure | Blocked by stage one gate | - | - | - |
| 3 | Zhihu Global Search Provider | Blocked by stage one gate | - | - | - |
| 4 | Allowlisted company website Provider | Blocked by stage one gate | - | - | - |
| 5 | Structured CrewAI extraction adapters | Blocked by stage one gate | - | - | - |
| 6 | Deterministic normalization and deduplication | Blocked by stage one gate | - | - | - |
| 7 | Transactional idempotent persistence | Blocked by stage one gate | - | - | - |
| 8 | Orchestrator and terminal-state classification | Blocked by stage one gate | - | - | - |
| 9 | Celery tasks, daily refresh, and job expiration | Blocked by stage one gate | - | - | - |
| 10 | Optional Redis caching and invalidation | Blocked by stage one gate | - | - | - |
| 11 | Frontend collection polling | Blocked by stage one gate | - | - | - |
| 12 | End-to-end failure verification and runbook | Blocked by stage one gate | - | - | - |

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

## Active Risks

| Risk | Impact | Mitigation | State |
|------|--------|------------|-------|
| External Provider credentials or authorization unavailable | Some data sources cannot ship | Providers default off; stage one uses versioned seed data; stage two starts with supplied Zhihu contract | Controlled |
| SQLite/PostgreSQL behavior diverges | Later production migration may fail | Use SQLAlchemy types, named constraints, Alembic, and dialect-aware index tests | Open |
| LLM output is malformed or follows hostile page text | Incorrect or unsafe data could be written | Bounded plain-text evidence, fixed roles, no tools, Pydantic validation, evidence-id checks | Open until stage two |
| Duplicate task delivery or source replay | Duplicate entities and unstable status | Database unique constraints, run idempotency, repeated-delivery integration tests | Open until stage two |
| Search performance degrades at target volume | User-visible latency exceeds target | Indexed query paths and a 10k/100k p95 gate in stage one | Open until stage one Task 8 |

## Verification Evidence

| Date | Scope | Command or evidence | Result |
|------|-------|---------------------|--------|
| 2026-07-31 | Design and implementation plans | Structural script checked required terms, obsolete terms, placeholders, trailing whitespace, balanced fences, required headers, and five steps per task | PASS: stage one 8 tasks/40 steps; stage two 12 tasks/60 steps |

## Iteration History

| Date | Iteration | Outcome | Next |
|------|-----------|---------|------|
| 2026-07-31 | Design review | Found reversed time predicates, unnormalized job sources, missing task/API contracts, and non-deterministic persistence role | Revise design |
| 2026-07-31 | Design revision | Added two-stage architecture, normalized models, REST contracts, Provider boundaries, task states, and acceptance criteria | Write plans |
| 2026-07-31 | Planning | Produced and structurally verified both stage plans | Establish global tracker and begin stage one |

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
