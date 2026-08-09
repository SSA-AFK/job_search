# Entry Evidence Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, model-assisted, auditable entry-evidence regeneration for frozen manifest members.

**Architecture:** Isolate public evidence extraction, LLM classification, acceptance policy, audit sampling, and discovery-round persistence. Existing observations remain immutable.

**Tech Stack:** Python, Pydantic, SQLAlchemy, Alembic, pytest, DashScope-compatible client.

## Global Constraints

- Public registered sources only; robots, budgets, exact hosts, and 1 QPS remain mandatory.
- Never send secrets, database URLs, raw responses, or local paths to the model.
- Model output is never sole acceptance evidence.
- Automatically accepted results receive a 5% source/platform-stratified audit; one severe error pauses the stratum.
- No job-list request, login, CAPTCHA handling, or observation deletion.

### Task 1: Evidence and Model Contracts

**Files:** Create `backend/app/manifest/evidence.py`, `backend/tests/manifest/test_evidence.py`; modify `backend/app/core/config.py`.

- [ ] Write failing tests for public-only payload projection, strict structured model output, threshold validation, and secret redaction.
- [ ] Implement immutable evidence/model DTOs and a disabled-by-default DashScope client.
- [ ] Run `python -m pytest tests/manifest/test_evidence.py -q`, Ruff, and mypy.
- [ ] Commit `feat: add entry evidence contracts`.

### Task 2: Deterministic Acceptance and Audit Policy

**Files:** Create `backend/app/manifest/evidence_policy.py`, `backend/tests/manifest/test_evidence_policy.py`.

- [ ] Write failing tests for hard-rule rejection, high-confidence acceptance, review routing, deterministic 5% stratified selection, and severe-error stratum pause.
- [ ] Implement policy requiring HTTPS, registered source, robots approval, ownership evidence, and model threshold.
- [ ] Run focused tests, Ruff, and mypy.
- [ ] Commit `feat: add evidence acceptance policy`.

### Task 3: Immutable Discovery Rounds

**Files:** Modify `backend/app/manifest/models.py`, `backend/alembic/versions/`, `backend/app/manifest/service.py`; add migration and persistence tests.

- [ ] Write migration and service tests proving prior observations cannot be overwritten and a new round links predecessor evidence.
- [ ] Add named round and audit persistence with foreign keys and indexes.
- [ ] Run migration matrix and service tests.
- [ ] Commit `feat: persist entry evidence rounds`.

### Task 4: CLI and Reports

**Files:** Modify `backend/app/manifest/cli.py`, `backend/app/manifest/reporting.py`; add CLI/report integration tests.

- [ ] Write failing tests for explicit model opt-in, dry-run, sanitized diagnostics, paused-stratum behavior, and round-separated reports.
- [ ] Implement `evidence-regenerate`, `evidence-audit`, and round-aware reporting commands.
- [ ] Run offline integration suite, Ruff, and mypy.
- [ ] Commit `feat: add evidence regeneration operator flow`.

### Task 5: Controlled Live Gate

**Files:** Modify `docs/dev/job-coverage-at-scale-plan.md` and `docs/dev/migration-master-plan.md`.

- [ ] Run registry validation, all offline tests, secret scan, and a bounded 20-member live smoke with explicit model opt-in.
- [ ] Review the audit sample and stop any severe-error stratum before resume.
- [ ] Resume only eligible members, generate sanitized round report, update measured facts, and commit canonical documentation.
