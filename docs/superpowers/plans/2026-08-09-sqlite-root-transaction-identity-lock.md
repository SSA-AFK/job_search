# SQLite Root-Transaction Identity Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SQLite's expandable per-key identity locks with one root-transaction-scoped identity-writer mutex while leaving PostgreSQL advisory locking unchanged.

**Architecture:** `serialized_company_identities(...)` keeps its public interface. PostgreSQL continues deriving and acquiring sorted granular advisory keys. SQLite resolves one mutex per SQLAlchemy bind, records ownership against the root `SessionTransaction`, reuses it for repeated calls, and releases it exactly once from the root `after_transaction_end` event.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x, SQLite, PostgreSQL advisory locks, pytest, Ruff, mypy

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-08-09-sqlite-root-transaction-identity-lock-design.md` at commit `4623525`.
- Implementation baseline is commit `4623525` on `codex/gate1-local-benchmark-design`.
- SQLite uses one process-wide identity-writer mutex per database bind and holds it through root commit or rollback.
- Repeated identity calls in one root transaction reuse ownership and never expand a keyed SQLite lock set.
- Nested savepoint completion never releases the SQLite mutex.
- PostgreSQL sorted, domain-separated transaction advisory locks remain behaviorally unchanged.
- Automatic manifest writes and all manual actions, including `ACCEPT` and `REJECT`, continue using the existing shared lock service boundary.
- Do not add a website uniqueness constraint, lock timeout, silent retry, recursive deletion, or `CASCADE`.
- Task 10 remains paused. Do not run live candidate import, discovery, external data, or external reports.
- Never read, print, stage, commit, or log `66.md`, `test.env`, database URLs, secrets, raw candidates, decisions, audit reports, or runtime reports.

---

### Task 1: Hold One SQLite Identity Mutex Through The Root Transaction

**Files:**
- Modify: `backend/app/company_identity/service.py`
- Modify: `backend/tests/company_identity/test_service.py`
- Modify: `backend/tests/manifest/test_identity.py`

**Interfaces:**
- Preserve: `serialized_company_identities(session: Session, names: Sequence[str], *, official_websites: Sequence[str] = ()) -> Iterator[None]`.
- Preserve: `serialized_company_identity_names(session: Session, names: Sequence[str], *, official_website: str | None = None) -> Iterator[None]`.
- Produce internally: one weakly bind-scoped SQLite `Lock` and root-transaction ownership state released by `after_transaction_end`.
- PostgreSQL continues executing `SELECT pg_advisory_xact_lock(:lock_key)` for every sorted derived key.

- [ ] **Step 1: Write bounded failing tests for the SQLite deadlock and lifetime contract**

Add a direct service regression using two file-backed SQLite Sessions and two caller-owned root transactions. Root A first calls `serialized_company_identities(...)` with a lexically high identity, root B attempts a low identity, and each root then requests the other's identity. Use `Event` objects and `Future.result(timeout=...)`; a test-owned fallback event/rollback in `finally` must unblock both workers even when the assertion fails.

The desired assertions are:

```python
assert root_a_first_acquired.wait(timeout=5)
assert not root_b_first_acquired.wait(timeout=0.2)
root_a.commit()
assert root_b_first_acquired.wait(timeout=5)
assert root_b_repeated_call_completed.wait(timeout=5)
```

Parameterize root A completion over `commit` and `rollback`. Also assert that a nested savepoint end does not unblock root B, while a new root transaction on a reused Session acquires fresh ownership.

Extend the manifest helper regression so automatic resolution and manual `REJECT` both use the same bounded cleanup pattern:

```python
observer_future = pool.submit(observe_after_identity_lock)
try:
    assert not observer_acquired.wait(timeout=0.2)
    outer_transaction.commit()  # parameterized with rollback
    observed = observer_future.result(timeout=15)
finally:
    if writer.in_transaction():
        writer.rollback()
    fallback_release.set()
```

- [ ] **Step 2: Run the new tests against `4623525` and verify RED without hanging**

Run:

```powershell
cd backend
python -m pytest tests/company_identity/test_service.py -k "sqlite and root and identity" -q
python -m pytest tests/manifest/test_identity.py -k "sqlite and identity_lock and caller_root" -q
```

Expected: the opposite-order test demonstrates the expandable keyed-lock deadlock or the second root enters before root completion. Every failing test must terminate within its explicit timeout and execute its fallback cleanup.

- [ ] **Step 3: Implement the bind-scoped SQLite root-transaction mutex**

In `app.company_identity.service`, replace SQLite per-key transaction ownership with one bind mutex. Keep the PostgreSQL branch of `_serialized_lock_keys(...)` unchanged.

Use this internal shape, adapting names only when required by existing typing:

```python
_LOCAL_IDENTITY_MUTEXES: WeakKeyDictionary[object, Lock] = WeakKeyDictionary()


class _LocalTransactionLocks:
    def __init__(self) -> None:
        self.by_transaction: dict[SessionTransaction, Lock] = {}


def _local_identity_mutex(session: Session) -> Lock:
    bind = session.get_bind()
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_IDENTITY_MUTEXES.setdefault(bind, Lock())
```

For SQLite/no-transaction lexical use, acquire and release the mutex around `yield`. For a root transaction, reuse `state.by_transaction[transaction]` when present; otherwise acquire the bind mutex, store it against the root transaction, and do not release in the context manager. `_release_local_transaction_locks(...)` ignores nested transactions, pops the root mutex, and releases it once.

The non-PostgreSQL branch must not derive or acquire per-key locks:

```python
transaction = session.get_transaction()
mutex = _local_identity_mutex(session)
if transaction is None:
    with mutex:
        yield
    return

state = _local_transaction_locks(session)
if transaction not in state.by_transaction:
    mutex.acquire()
    state.by_transaction[transaction] = mutex
try:
    yield
except BaseException:
    raise
```

If acquisition or listener setup raises, release any newly acquired mutex before propagating. Do not release on normal context exit while the root transaction remains active.

- [ ] **Step 4: Run focused GREEN and existing lifetime regressions**

Run:

```powershell
cd backend
python -m pytest tests/company_identity/test_service.py -k "sqlite or serialized_company" -q
python -m pytest tests/manifest/test_identity.py -k "sqlite and identity_lock" -q
python -m pytest tests/ingestion/persistence/test_service.py -k "lock and transaction" -q
```

Expected: all new opposite-order, commit, rollback, nested savepoint, Session reuse, automatic, and manual `REJECT` tests pass. Existing Task 5 SQLite lock-lifetime coverage remains green.

- [ ] **Step 5: Run affected, full, static, PostgreSQL, and safety gates**

Run from `backend`:

```powershell
python -m pytest tests/company_identity tests/manifest tests/ingestion/persistence tests/integration/test_company_identity_review_stop.py -q
python -m pytest -q
python -m ruff check app tests alembic
python -m mypy app
python -m pytest -m postgresql tests/company_identity/test_service.py tests/manifest/test_identity.py tests/ingestion/persistence/test_service.py -q
python -m pytest -m performance -q
```

PostgreSQL commands must use the approved loopback test database through an in-process environment value without printing or persisting the URL. Expected: no selected PostgreSQL/performance skip, `pg_trgm` remains in `public`, zero residual isolated schemas, approved secret baseline remains exactly six unchanged metadata entries, changed range has zero secret metadata entries, and `git diff --check` passes.

- [ ] **Step 6: Independently review and commit the scoped fix**

The reviewer must inspect root transaction ownership, listener idempotence, acquisition-failure cleanup, nested transaction handling, repeated-call behavior, test termination, and unchanged PostgreSQL SQL shape. Critical/Important findings enter the normal fix loop.

After approval:

```powershell
git add backend/app/company_identity/service.py backend/tests/company_identity/test_service.py backend/tests/manifest/test_identity.py
git diff --cached --check
git commit -m "fix: serialize sqlite identity root transactions"
```

Append RED/GREEN evidence, test counts, review verdict, commit SHA, and residual risks to this plan's SDD ledger. Verify the tracked worktree is clean. Task 10 remains paused until the controller's final scoped re-review and branch disposition.
