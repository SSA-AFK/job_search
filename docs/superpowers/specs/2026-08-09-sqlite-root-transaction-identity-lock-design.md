# SQLite Root-Transaction Identity Lock Design

## Status And Scope

This design closes the final SQLite-only concurrency gap found during the scoped
review of company identity hardening. It changes only the cooperating-writer lock
strategy used by SQLite. PostgreSQL keeps its existing sorted, domain-separated
transaction advisory locks. Task 10 remains paused until implementation, verification,
and scoped re-review pass.

## Problem

The current SQLite implementation retains keyed identity locks until the SQLAlchemy
root transaction ends. A caller-owned root transaction may invoke identity operations
more than once and expand its lock set. Two roots can then deadlock permanently:

1. root A holds a high derived key;
2. root B holds a low derived key;
3. root A requests the low key while root B requests the high key.

Sorting each individual acquisition does not impose a global order across repeated
calls because neither root knows its future lock set. SQLite has no advisory-lock
deadlock detector or timeout to break this cycle.

## Decision

SQLite uses one process-wide identity-writer mutex per database scope, held by the
SQLAlchemy root `SessionTransaction` from first identity-lock acquisition through the
root commit or rollback.

- The first identity operation in a root transaction acquires the mutex.
- Later identity operations in the same root transaction reuse ownership and never
  reacquire or expand keyed locks.
- Nested savepoint completion does not release the mutex.
- Root commit, rollback, or failed transaction termination releases it exactly once.
- A different root transaction blocks before reading lock-protected identity owners
  and proceeds only after the prior root has completed.
- Automatic manifest writes and every manual action, including `ACCEPT` and `REJECT`,
  use the same mutex through the existing identity-lock service boundary.

SQLite already serializes database writes and is used for offline behavior rather than
production-scale concurrency. Serializing cooperating identity writers therefore
removes a deadlock class without weakening a supported production throughput claim.

## Components And Data Flow

`app.company_identity.service` owns the SQLite mutex and root-transaction ownership
metadata. Callers continue using `serialized_company_identities(...)`; no manifest or
persistence caller receives a SQLite-specific API.

For SQLite, the service resolves the current root transaction, checks whether that root
already owns the mutex, acquires it when necessary, and registers one root
`after_transaction_end` release. Name, alias, and normalized-website inputs remain
validated for parity and diagnostics but do not create per-key SQLite lock objects.

For PostgreSQL, the service follows the unchanged advisory-lock path and continues to
derive, deduplicate, sort, and acquire granular transaction keys.

## Failure Handling

- Exceptions before root completion do not release the mutex early; the caller must
  commit or roll back the root transaction.
- Root rollback releases the mutex even when application work failed.
- Duplicate SQLAlchemy transaction-end events and cleanup paths are idempotent.
- Session reuse after a completed root transaction acquires fresh ownership for the
  next root.
- The implementation must not add lock timeouts, silent retries, a website uniqueness
  constraint, recursive cleanup, or `CASCADE`.

## Verification

TDD regressions must first fail against `06555db` and then pass:

1. two caller-owned SQLite roots acquire opposite initial key orders and make repeated
   identity calls without AB/BA deadlock;
2. the second root remains blocked until the first root commits;
3. rollback releases the mutex and lets the waiter proceed;
4. automatic manifest resolution and manual `ACCEPT`/`REJECT` retain serialization
   through their root transaction;
5. nested savepoint completion does not release ownership;
6. a new root on a reused Session does not inherit stale ownership;
7. failure-path tests use an explicit test-owned fallback release/event so a regression
   fails with a timeout instead of hanging executor shutdown;
8. PostgreSQL advisory-lock SQL shape and two-session behavior remain unchanged.

The final gate includes affected and full backend tests, Ruff, mypy, PostgreSQL and
performance markers without selected skips, secret-baseline verification, diff checks,
and zero residual isolated schemas.

## Residual Risk

The mutex coordinates cooperating in-process SQLite writers only. A separate process
or direct database writer can bypass it. That is consistent with SQLite's offline role;
production identity concurrency remains PostgreSQL-backed.
