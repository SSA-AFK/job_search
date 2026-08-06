# Local 1,000-Company Gate 1 Benchmark Design

> Status: Approved section by section; awaiting final written-spec review
> Date: 2026-08-06
> Scope: One local, deterministic, real-company coverage rehearsal
> Base: Stage 3A merged locally at `881a484`

## 1. Objective

Run one end-to-end benchmark on the operator's Windows machine using a frozen manifest of exactly 1,000 real mainland-China AI-related recruiting entities. The rehearsal measures company recruitment-entry discovery, complete job-list enumeration, source lifecycle safety, queue capacity, runtime resource usage, and coverage-report reproducibility.

This rehearsal is not the seven-day Gate 1 acceptance run. It establishes the first real baseline and validates the path needed before a later continuous run.

The execution expands through the same frozen manifest in three stages:

1. 20-company smoke run;
2. 100-company canary;
3. 1,000-company single pass.

The 100- and 1,000-company stages may start only after every blocking condition from the preceding stage is clear.

## 2. Fixed Decisions

- Use the Manifest-first deterministic approach.
- Count one company by independent recruiting identity, not by every legal entity or brand alias.
- Target mainland-China AI-related companies.
- Use mixed stratification: 40% category floors and 60% candidate-pool-proportional allocation.
- Use only public, authorized sources. Do not access login-only content, bypass controls, solve CAPTCHAs, or use unapproved commercial data sources.
- Use deterministic extraction for the normal path. Do not enable or call an LLM during this rehearsal.
- Send ambiguous identities, group relationships, duplicates, and low-confidence recruiting entries to manual review.
- Use `66.md` only as an untracked local runtime-secret source. Never copy its secret values into Git, logs, reports, fixtures, prompts, or documentation.
- Use Zhihu only as a low-frequency discovery fallback, never as a complete job-list source.
- Keep PostgreSQL as the task, coverage, entry, snapshot, and lifecycle source of truth. Redis is transport, result storage, and cache only.

## 3. Scope Boundaries

### Included

- auditable candidate-pool construction;
- deterministic company normalization and recruiting-identity deduplication;
- versioned 1,000-company manifest generation;
- official website and recruitment-entry discovery;
- ATS classification and authorized list adapters;
- complete-list pagination where the endpoint can be proven;
- Stage 3A snapshot and source-lifecycle writes;
- staged Celery scheduling, backpressure, stop/resume, and idempotency;
- sanitized JSON and Markdown benchmark reports;
- local PostgreSQL, Redis, API, Worker, Beat, and optional bounded browser processes.

### Excluded

- job-description detail enrichment;
- LLM-based extraction or semantic fallback;
- public deployment, public domain, HTTPS termination, Docker, or Kubernetes;
- employee personal information;
- login-only systems, CAPTCHA or anti-bot bypass;
- unapproved commercial job boards or company databases;
- seven-day, 3,000-company, or 10,000-company acceptance;
- production push or remote deployment.

## 4. Delivery Decomposition

The objective is too broad for one implementation plan. One design governs four sequential implementation plans:

1. **Stage 3B0 - Manifest and entry discovery**
   Build the candidate pool, deduplicate recruiting identities, apply deterministic stratification, operate the manual-review queue, freeze the manifest, discover official websites and recruitment entries, and produce an observed ATS census.

2. **Stage 3B - ATS runtime integration**
   Integrate authorized self-hosted and ATS tenant pages into the current Provider, security, transaction, and snapshot boundaries. The protected pre-Stage-3A WIP branch is reference material only; every reused component requires tests and review.

3. **Stage 3C - Complete enumeration**
   Implement provable page-number, cursor, public-JSON, scroll, or bounded browser completion semantics for prioritized platforms. Unknown termination, truncation, CAPTCHA, and unverified counts are never complete.

4. **Stage 3D - Local benchmark runner**
   Add local bootstrap, process control, staged batching, backpressure, metrics, stop/resume, report generation, and the 20/100/1,000 rehearsal workflow.

No external 1,000-company collection starts until these plans are separately approved, implemented, tested, and reviewed in order.

## 5. Architecture And Data Flow

```text
approved public source registry
        |
        v
raw candidate facts (name + evidence + timestamp)
        |
        v
deterministic normalization and recruiting-identity deduplication
        |
        +----> manual review queue for ambiguous records
        |
        v
mixed stratification and immutable manifest membership
        |
        v
official site and recruitment-entry discovery
        |
        v
ATS classification and authorized list enumeration
        |
        v
RecordJobSnapshot -> Stage 3A transaction/lifecycle service
        |
        v
PostgreSQL coverage facts -> sanitized benchmark reports
```

The manifest membership denominator is immutable for a benchmark version. Entry discovery, snapshots, and job sources are mutable database facts linked to that frozen membership; they do not rewrite the manifest.

## 6. Candidate And Manifest Model

### 6.1 Candidate Facts

Each candidate fact contains:

- original name;
- normalized name;
- aliases;
- proposed primary AI category;
- city and scale bucket when evidenced;
- official-website candidate when evidenced;
- source type and public source URL;
- retrieval timestamp;
- evidence summary;
- deterministic confidence tier;
- deterministic confidence reason;
- decision status: `accepted`, `review_required`, or `rejected`.

Raw response bodies, browser profiles, and credentials are runtime artifacts and are not committed. The accepted manifest stores only normalized public facts and evidence references needed to reproduce membership.

### 6.2 Recruiting-Identity Rules

- Merge legal entities, former names, and aliases that recruit through one inseparable recruiting identity.
- Count a subsidiary or sub-brand separately only when it owns an independent recruitment entry and separable job inventory.
- Count a group as one company when its shared recruitment entry cannot truthfully assign jobs to subsidiaries.
- Reject unresolved duplicates from the frozen denominator until manual review decides them.

### 6.3 Primary Categories

Every accepted company receives exactly one primary category:

1. foundation models;
2. AI cloud and model platforms;
3. AI chips and compute;
4. autonomous driving and intelligent transport;
5. robotics and embodied AI;
6. computer vision and imaging;
7. speech and language technology;
8. enterprise or vertical AI applications;
9. data infrastructure and MLOps.

Secondary tags may be recorded but do not affect quota allocation.

### 6.4 Deterministic Mixed Stratification

The manifest builder requires at least 1,500 `accepted` candidates after duplicate and ambiguity review is complete. `review_required` candidates never count toward this threshold or the allocation inputs.

- Reserve 400 positions for category floors.
- Assign 44 floor positions to each of the nine categories (396 total).
- Assign the remaining four floor positions to the four categories with the largest accepted candidate pools; ties resolve by category identifier.
- Allocate the remaining 600 positions in proportion to the accepted candidate counts remaining after floor selection, using the largest-remainder method; ties resolve by category identifier.
- Within each category, select by confidence tier first. Within a confidence tier, round-robin across the lexically ordered `(scale_bucket_or_unknown, city_or_unknown)` diversity buckets; order records inside each bucket by normalized name and stable evidence identifier.
- A committed quota artifact records the inputs, candidate counts, floor allocation, proportional allocation, and final count of exactly 1,000.

If any category cannot fill its floor, stop manifest freezing and require an explicit design amendment. Do not silently reallocate the deficit.

### 6.5 Manifest Identity

The canonical JSON manifest uses sorted keys and deterministic member ordering. Its SHA-256 is the `manifest_version`. Every 20-, 100-, and 1,000-company run records the same hash, code commit, and configuration fingerprint.

## 7. Public Source And Access Policy

Allowed source classes are:

- government public filings and public announcements;
- exchange, industry-association, industrial-park, and other official public company lists;
- official company websites and public career pages;
- public ATS tenant pages that permit access;
- the authorized Zhihu API only for discovery evidence.

Before implementation, Stage 3B0 commits an explicit source registry containing source name, base URL, source class, authorization basis, robots policy, rate budget, and parser owner. A source not in that registry is disabled.

Runtime restrictions:

- HTTP(S) only;
- public destinations only, with redirect revalidation;
- exact approved-host boundary for company-site crawling;
- obey `robots.txt` and service terms;
- single-domain request start rate at most one per second;
- no login, CAPTCHA, proxy rotation, fingerprint evasion, or access-control bypass;
- no employee personal information;
- Zhihu call budget at most 200 requests for the entire rehearsal, with caching and QPS at most one.

401/403 responses stop that source. Concentrated 429 responses stop expansion and require budget review.

## 8. Recruitment Entry And List Semantics

Entry discovery priority is:

1. already evidenced official career URL;
2. official website navigation and bounded same-host career paths;
3. deterministic ATS-domain recognition;
4. low-frequency Zhihu discovery fallback;
5. manual review.

An entry is accepted only when its ownership relationship to the manifest company is evidenced. Redirects, tenant identifiers, and normalized URLs are persisted with the evidence.

List results obey Stage 3A contracts:

- `succeeded + pagination_complete=true` only when the adapter proves the list endpoint and termination condition;
- `empty_confirmed=true` only for a successful complete list with zero observed sources and a compatible reported total;
- timeout, unknown termination, truncation, CAPTCHA, parse ambiguity, or unsupported rendering becomes `partial` or `failed`;
- partial and failed snapshots never increment complete-absence counters or deactivate sources;
- a source deactivates only after two newly-created consecutive applied complete absences for its retained entry;
- identical replay is side-effect free and conflicting replay is rejected;
- a posting remains active while any source remains active.

## 9. Local Runtime And Secret Handling

### 9.1 Machine Baseline

The approved local baseline is Windows with:

- AMD Ryzen 7 8845H, 8 cores and 16 logical processors;
- approximately 31 GB RAM;
- PostgreSQL 18 on localhost;
- Redis on localhost port 6379;
- at least 100 GB free space required before a run.

### 9.2 Secret Bootstrap

`66.md` and `test.env` remain untracked and are never modified or committed.

The bootstrap command:

1. reads the approved local connection values from `66.md` without logging them;
2. rejects non-loopback PostgreSQL or Redis destinations;
3. uses the local PostgreSQL administrator connection only to create database `company_search_gate1` and role `company_search_gate1` with a new random password;
4. writes a runtime environment file outside the repository, readable only by the current Windows user;
5. configures Redis database 0 for Broker, 1 for Result Backend, and 2 for Cache;
6. does not load the model key;
7. makes the Zhihu secret available only to the budgeted discovery process;
8. redacts credential-bearing URLs and secret fields from all diagnostics.

The local single rehearsal may use the values currently recorded in `66.md` because the operator explicitly authorized them. Any continuous or non-local run requires rotating every exposed credential first.

### 9.3 Process Commands

The local control surface provides:

- `preflight`: dependencies, ports, PostgreSQL, Redis, disk, migrations, and credential presence; no external calls;
- `bootstrap`: dedicated database/role and external runtime environment creation;
- `start`, `status`, `stop`: API, Worker, and Beat lifecycle;
- `run --limit 20|100|1000`: execute a stage from the frozen manifest;
- `resume`: enqueue only unfinished, eligible members for the same run;
- `report`: regenerate sanitized JSON and Markdown reports from PostgreSQL.

No command recursively deletes files. Cleanup, when needed, targets validated individual runtime files or stops processes without deleting data.

## 10. Scheduling And Capacity Controls

Initial limits are deliberately conservative:

- Celery worker concurrency: 4;
- deterministic HTTP global concurrency: 8;
- per-domain request-start rate: at most 1 per second;
- browser pool: at most 2 pages;
- provider-specific limits may be lower but never higher than these defaults;
- Redis submissions use bounded batches rather than enqueuing all 1,000 at once.

Each stage has a distinct `benchmark_run_id` but references the same manifest hash. The 20-company subset is a prefix of the 100-company subset, which is a prefix of the 1,000-company manifest under deterministic ordering.

Resume reads database terminal state. It does not infer completion from Redis or Celery results. Duplicate delivery must preserve run claims, snapshot idempotency, and canonical row uniqueness.

## 11. Stage Gates And Stop Conditions

### 11.1 Twenty-Company Smoke

Required before expansion:

- import, entry discovery, classification, enumeration, snapshot recording, and report generation all execute;
- every task reaches a database terminal state;
- no unhandled exception, leaked transaction, false complete snapshot, false confirmed-empty snapshot, or secret-bearing log;
- no unauthorized network destination.

Any failure blocks the 100-company stage.

### 11.2 One-Hundred-Company Canary

Required before expansion:

- all work terminalizes as succeeded, partial, or failed;
- retries and provider budgets remain bounded;
- no partial or failed snapshot deactivates a source;
- no sustained queue growth or stale running tasks;
- no browser-process or page leak;
- CPU or memory does not remain above 85% for five minutes;
- PostgreSQL and Redis remain available;
- no source-wide 401/403 or concentrated 429 condition.

### 11.3 One-Thousand-Company Single Pass

- Submit bounded batches only after the first two gates pass.
- Terminalize every manifest member within 24 hours. A safety stop must reconcile owned work to an explicit stopped terminal state, but the stopped run does not pass this capacity gate.
- Permit interruption and resume without duplicate facts or lifecycle changes.
- Do not treat the 24-hour boundary as a predicted collection duration; it is the rehearsal stop boundary.

Immediate stop conditions at any stage:

- false complete or confirmed-empty facts;
- source deactivation caused by partial or failed observations;
- cross-company or cross-entry source ownership corruption;
- unauthorized network access;
- credentials in logs, artifacts, or reports;
- non-idempotent replay;
- task state that cannot terminalize or recover;
- unbounded queue, browser, memory, connection, or disk growth.

## 12. Benchmark Metrics

Every report binds to manifest hash, benchmark run, code commit, start/end time, and sanitized configuration fingerprint.

### 12.1 Company And Entry Metrics

- manifest count and category, scale, and city distribution;
- processed, accepted, review-required, rejected, and unfinished counts;
- official website confirmation rate;
- recruitment-entry company count and coverage rate;
- entries per company;
- ATS and self-hosted platform distribution.

### 12.2 Job-List Metrics

- successful complete, partial, and failed snapshot counts;
- complete-list company coverage rate;
- confirmed-empty company count;
- active posting and source totals;
- jobs per company: minimum, p50, p90, p95, and maximum;
- title, location, employment type, and application-link completeness;
- duplicate posting, duplicate source, and ownership-conflict counts;
- failure-code distribution with explicit denominators.

### 12.3 Capacity And Stability Metrics

- total and per-stage duration;
- queue-depth peak, queue wait, and task-duration distributions;
- HTTP request, retry, 429, 401/403, timeout, and byte totals;
- browser starts, peak pages, render ratio, and leak count;
- PostgreSQL connections, writes, lock waits, and slow queries;
- Redis connections, memory, and key counts by logical database;
- CPU, memory, disk, and process-restart peaks;
- Zhihu calls, cache hits, and budget status.

## 13. Single-Pass Acceptance

The rehearsal passes only when:

- the frozen manifest contains exactly 1,000 evidence-backed members;
- the 20- and 100-company stages pass their gates;
- all 1,000 members reach a non-stopped terminal state within 24 hours;
- unauthorized requests, secret leaks, false complete facts, false empty facts, and unsafe source deactivations are all zero;
- queue, database, browser, memory, disk, and connection growth remain bounded;
- duplicate-delivery audit creates no duplicate company, entry, posting source, snapshot side effect, or lifecycle transition;
- the report can be regenerated from PostgreSQL with identical core counts.

Entry coverage and complete enumeration rates are baseline observations in this first single pass. They are not pass thresholds. The measured distributions will inform the later seven-day Gate 1 targets.

## 14. Error Handling

- Configuration and preflight errors fail before an external call.
- Provider errors use stable sanitized codes and never store credentials or raw model output.
- Every queued work item must reach a database terminal state or remain eligible for bounded reconciliation.
- A provider outage does not make existing local search data unavailable.
- PostgreSQL commit is the boundary for persisted facts and cache invalidation.
- Redis loss degrades queue/cache behavior but cannot redefine coverage truth.
- Browser failure closes owned pages/processes and records a partial/failed observation; it never fabricates an empty list.
- Manual-review records retain evidence and reason codes without counting toward accepted coverage until resolved.

## 15. Testing And Review

Each implementation plan uses TDD and independent specification and quality reviews.

Default tests remain offline:

- candidate normalization, deduplication, quota, and manifest-hash fixtures;
- source-registry and authorization validation;
- official-site and ATS response fixtures for every adapter;
- pagination termination, truncation, and empty-list cases;
- snapshot replay, ownership, rollback, ordering, and lifecycle cases;
- scheduler batching, backpressure, stop/resume, and duplicate delivery;
- report denominator, reproducibility, and secret-redaction tests;
- SQLite behavioral and PostgreSQL migration/static/live gates;
- browser pool cleanup and bounded-resource tests without live sites.

Live access is opt-in, uses explicit budgets, and runs only after the corresponding implementation plan is approved and its offline suite passes. A live failure never weakens the offline contract.

## 16. Handoff

After this design is approved in written form, the next artifact is the Stage 3B0 Manifest and Entry Discovery implementation plan. Stage 3B, Stage 3C, and Stage 3D plans follow only after the preceding stage is implemented and reviewed. No 1,000-company external run is authorized by this design document alone.
