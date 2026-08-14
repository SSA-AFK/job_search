from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.ingestion.persistence.result import PersistenceResult
from app.models import Base, CollectionRequest, CollectionStatus, CrawlRun


def test_checked_in_runtime_factory_composes_a_worker_without_operator_code() -> None:
    from app.ingestion.production import create_runtime_components

    config = SimpleNamespace(
        openai_compatible_base_url="https://llm.example/v1",
        openai_compatible_model="model",
        openai_compatible_api_key="secret",
        openai_request_timeout_seconds=30.0,
        zhihu_provider_enabled=True,
        zhihu_access_secret="zhihu-secret",
        serper_provider_enabled=False,
        serper_api_key=None,
        serper_gl="cn",
        serper_hl="zh-cn",
        company_site_provider_enabled=False,
        company_site_approved_hosts="",
        ats_provider_enabled=False,
        ats_feishu_enabled=False,
        ats_moka_enabled=False,
        ats_liepin_enabled=False,
        ats_lagou_enabled=False,
        ats_approved_hosts="jobs.feishu.cn,app.mokahr.com",
        playwright_pool_size=2,
        playwright_page_timeout_seconds=30.0,
        provider_max_concurrency=2,
        provider_min_interval_seconds=0.0,
        ymicp_provider_enabled=False,
        ymicp_base_url="http://127.0.0.1:16181",
        ymicp_timeout_seconds=30.0,
        tianyancha_provider_enabled=False,
        tianyancha_cli_executable="npx",
        tianyancha_call_budget=100,
    )

    components = create_runtime_components(config)

    assert [provider.name for provider in components.providers] == [
        "zhihu_global_search"
    ]
    assert components.extractor is None


def test_checked_in_runtime_factory_does_not_require_llm_configuration() -> None:
    from app.ingestion.production import (
        ProductionRuntimeConfigurationError,
        create_runtime_components,
    )

    config = SimpleNamespace(
        openai_compatible_base_url=None,
        openai_compatible_model=None,
        openai_compatible_api_key=None,
        openai_request_timeout_seconds=30.0,
        zhihu_provider_enabled=False,
        zhihu_access_secret=None,
        serper_provider_enabled=False,
        serper_api_key=None,
        serper_gl="cn",
        serper_hl="zh-cn",
        company_site_provider_enabled=False,
        company_site_approved_hosts="",
        ats_provider_enabled=False,
        ats_feishu_enabled=False,
        ats_moka_enabled=False,
        ats_liepin_enabled=False,
        ats_lagou_enabled=False,
        ats_approved_hosts="jobs.feishu.cn,app.mokahr.com",
        playwright_pool_size=2,
        playwright_page_timeout_seconds=30.0,
        provider_max_concurrency=2,
        provider_min_interval_seconds=0.0,
        ymicp_provider_enabled=False,
        ymicp_base_url="http://127.0.0.1:16181",
        ymicp_timeout_seconds=30.0,
        tianyancha_provider_enabled=False,
        tianyancha_cli_executable="npx",
        tianyancha_call_budget=100,
    )

    with pytest.raises(ProductionRuntimeConfigurationError, match="at least one Provider"):
        create_runtime_components(config)


def test_duplicate_worker_delivery_observes_running_without_terminalizing(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'claim.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        request, run = CollectionRepository(setup).create_request("Acme", "acme")
        setup.commit()
        run_id = run.id
        request_id = request.id

    with Session(engine, expire_on_commit=False) as first:
        first_claim = CollectionRepository(first).claim_queued(run_id)
    with Session(engine, expire_on_commit=False) as duplicate:
        duplicate_claim = CollectionRepository(duplicate).claim_queued(run_id)

    assert first_claim is not None and first_claim.claimed is True
    assert duplicate_claim is not None and duplicate_claim.claimed is False
    assert duplicate_claim.run.status is CollectionStatus.RUNNING
    with Session(engine) as verification:
        assert verification.get(CrawlRun, run_id).status is CollectionStatus.RUNNING
        assert (
            verification.get(CollectionRequest, request_id).status
            is CollectionStatus.RUNNING
        )


def test_terminal_write_does_not_overwrite_state_after_claim_was_reconciled(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'terminal-cas.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as owner:
        request, run = CollectionRepository(owner).create_request("Acme", "acme")
        owner.commit()
        request_id = request.id
        run_id = run.id
        claim = CollectionRepository(owner).claim_queued(run.id)
        assert claim is not None and claim.claimed
        claim_token = claim.claim_token
        assert claim_token is not None

        with Session(engine, expire_on_commit=False) as reconciler:
            CollectionRepository(reconciler).requeue_for_retry(
                run.id, expected_claim_token=claim_token
            )

        with Session(engine, expire_on_commit=False) as new_owner:
            new_claim = CollectionRepository(new_owner).claim_queued(run.id)
            assert new_claim is not None and new_claim.claimed

        result = CollectionRepository(owner).finish(
            claim.run,
            expected_claim_token=claim_token,
            status=CollectionStatus.SUCCEEDED,
            providers_attempted=("test",),
            documents_found=1,
            jobs_found=0,
            persistence=PersistenceResult(
                company_id=uuid4(),
                documents_written=1,
                jobs_written=0,
                warnings=(),
            ),
            error_code=None,
            error_detail=None,
        )

    assert result.status is CollectionStatus.RUNNING
    with Session(engine) as verification:
        assert verification.get(CrawlRun, run_id).status is CollectionStatus.RUNNING
        assert verification.get(CollectionRequest, request_id).status is CollectionStatus.RUNNING


def test_claim_generation_is_unique_when_successive_claim_times_are_identical(
    tmp_path, monkeypatch
) -> None:
    fixed_time = datetime(2026, 8, 4, 12, tzinfo=UTC)
    monkeypatch.setattr("app.collection.repository.utc_now", lambda: fixed_time)
    engine = create_engine(f"sqlite:///{tmp_path / 'claim-token.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as owner:
        request, run = CollectionRepository(owner).create_request("Acme", "acme")
        owner.commit()
        run_id = run.id
        request_id = request.id
        repository = CollectionRepository(owner)
        first_claim = repository.claim_queued(run_id)
        assert first_claim is not None and first_claim.claimed
        first_token = first_claim.claim_token
        assert first_token is not None
        repository.requeue_for_retry(
            run_id, expected_claim_token=first_token
        )
        second_claim = repository.claim_queued(run_id)
        assert second_claim is not None and second_claim.claimed
        assert second_claim.claim_token != first_token

        observed = repository.finish(
            first_claim.run,
            expected_claim_token=first_token,
            status=CollectionStatus.SUCCEEDED,
            providers_attempted=("test",),
            documents_found=1,
            jobs_found=0,
            persistence=PersistenceResult(
                company_id=uuid4(),
                documents_written=1,
                jobs_written=0,
                warnings=(),
            ),
            error_code=None,
            error_detail=None,
        )

    assert observed.status is CollectionStatus.RUNNING
    with Session(engine) as verification:
        stored = verification.get(CrawlRun, run_id)
        assert stored is not None and stored.status is CollectionStatus.RUNNING
        assert stored.claim_token == second_claim.claim_token
        assert (
            verification.get(CollectionRequest, request_id).status
            is CollectionStatus.RUNNING
        )


def test_retry_exhaustion_does_not_overwrite_a_new_claim_generation(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'exhausted-claim.sqlite3'}")
    Base.metadata.create_all(engine)
    claim_times = iter(
        (
            datetime(2026, 8, 4, 10, tzinfo=UTC),
            datetime(2026, 8, 4, 11, tzinfo=UTC),
        )
    )
    monkeypatch.setattr("app.collection.repository.utc_now", lambda: next(claim_times))
    with Session(engine, expire_on_commit=False) as setup:
        request, run = CollectionRepository(setup).create_request("Acme", "acme")
        setup.commit()
        run_id = run.id
        request_id = request.id

    with Session(engine, expire_on_commit=False) as worker:
        repository = CollectionRepository(worker)
        claim = repository.claim_queued(run_id)
        assert claim is not None and claim.claimed is True
        first_token = claim.claim_token
        assert first_token is not None
        requeued = repository.requeue_for_retry(
            run_id, expected_claim_token=first_token
        )
        assert requeued is not None and requeued.status is CollectionStatus.QUEUED
        new_claim = repository.claim_queued(run_id)
        assert new_claim is not None and new_claim.claimed is True
        assert new_claim.claim_token != first_token

        observed = repository.fail_retry_exhausted(
            run_id, expected_claim_token=first_token
        )

    assert observed is not None and observed.status is CollectionStatus.RUNNING
    with Session(engine) as verification:
        assert verification.get(CrawlRun, run_id).status is CollectionStatus.RUNNING
        assert verification.get(CollectionRequest, request_id).status is CollectionStatus.RUNNING


def test_retry_is_scheduled_even_when_immediate_state_recovery_fails(monkeypatch) -> None:
    from app.ingestion.errors import RetryableInfrastructureError
    from app.tasks.collection import run_ingestion

    claim_token = str(uuid4())

    class FailingOrchestrator:
        async def run(self, _run_id):
            raise RetryableInfrastructureError(claim_token=claim_token)

    sessions = (Session(), Session(), Session())
    monkeypatch.setattr(
        "app.tasks.collection.build_runtime_orchestrator",
        lambda: (FailingOrchestrator(), sessions),
    )

    def recovery_fails(_run_id, *, exhausted, expected_claim_token):
        assert expected_claim_token == claim_token
        raise OSError("database unavailable")

    monkeypatch.setattr("app.tasks.collection._recover_retry_state", recovery_fails)
    attempted_retries: list[int] = []

    def retry(*, exc, countdown, args):
        assert isinstance(exc, RetryableInfrastructureError)
        assert args[1] == claim_token
        attempted_retries.append(countdown)
        raise Retry()

    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(uuid4()))

    assert attempted_retries == [1]


def test_later_retry_reconciles_state_before_running_pipeline(monkeypatch) -> None:
    from app.ingestion.result import IngestionResult
    from app.tasks.collection import run_ingestion

    run_id = uuid4()
    claim_token = str(uuid4())
    events: list[str] = []

    class Orchestrator:
        async def run(self, received_run_id):
            assert received_run_id == run_id
            events.append("run")
            return IngestionResult.unknown_run(received_run_id)

    monkeypatch.setattr(
        "app.tasks.collection._recover_retry_state",
        lambda _run_id, *, exhausted, expected_claim_token: events.append("reconcile"),
    )
    monkeypatch.setattr(
        "app.tasks.collection.build_runtime_orchestrator",
        lambda: (Orchestrator(), (Session(), Session(), Session())),
    )

    run_ingestion.push_request(retries=1)
    try:
        run_ingestion.run(str(run_id), claim_token)
    finally:
        run_ingestion.pop_request()

    assert events == ["reconcile", "run"]


def test_stale_queued_dispatcher_recovers_commit_before_broker_gap(tmp_path, monkeypatch) -> None:
    from app.tasks.schedule import redispatch_stale_queued_runs

    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{tmp_path / 'stale-queued.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        _request, run = CollectionRepository(setup).create_request("Acme", "acme")
        run.created_at = now - timedelta(minutes=10)
        setup.commit()
        run_id = run.id

    dispatched: list[str] = []

    class Result:
        id = "redispatched-task"

    def delay(value: str) -> Result:
        dispatched.append(value)
        return Result()

    monkeypatch.setattr(
        "app.tasks.schedule.SessionLocal",
        lambda: Session(engine, expire_on_commit=False),
    )
    monkeypatch.setattr("app.tasks.schedule.utc_now", lambda: now)
    monkeypatch.setattr(
        "app.tasks.schedule.run_ingestion",
        SimpleNamespace(delay=delay),
    )

    assert redispatch_stale_queued_runs.apply().get() == {
        "redispatched": 1,
        "requeued": 0,
    }
    assert dispatched == [str(run_id)]
    with Session(engine) as verification:
        stored = verification.scalar(select(CrawlRun).where(CrawlRun.id == run_id))
        assert stored is not None and stored.celery_task_id == "redispatched-task"


def test_stale_running_reconciliation_cannot_requeue_a_new_claim_generation(
    tmp_path, monkeypatch
) -> None:
    from app.tasks.schedule import redispatch_stale_queued_runs

    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{tmp_path / 'stale-running-race.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        request, run = CollectionRepository(setup).create_request("Acme", "acme")
        setup.commit()
        claim = CollectionRepository(setup).claim_queued(run.id)
        assert claim is not None and claim.claimed
        claim.run.started_at = now - timedelta(hours=2)
        claim.run.created_at = now - timedelta(hours=3)
        setup.commit()
        run_id = run.id
        request_id = request.id
        stale_claim_token = claim.claim_token

    original_requeue = CollectionRepository.requeue_for_retry
    raced = False

    def requeue_after_new_owner_claims(
        repository, received_run_id, *, expected_claim_token=None
    ):
        nonlocal raced
        assert expected_claim_token == stale_claim_token
        if not raced:
            raced = True
            with Session(engine, expire_on_commit=False) as reconciler:
                old_run = reconciler.get(CrawlRun, run_id)
                assert old_run is not None and old_run.claim_token is not None
                original_requeue(
                    CollectionRepository(reconciler),
                    run_id,
                    expected_claim_token=old_run.claim_token,
                )
            with Session(engine, expire_on_commit=False) as new_owner:
                new_claim = CollectionRepository(new_owner).claim_queued(run_id)
                assert new_claim is not None and new_claim.claimed
        return original_requeue(
            repository,
            received_run_id,
            expected_claim_token=expected_claim_token,
        )

    monkeypatch.setattr(
        CollectionRepository, "requeue_for_retry", requeue_after_new_owner_claims
    )
    monkeypatch.setattr(
        "app.tasks.schedule.SessionLocal",
        lambda: Session(engine, expire_on_commit=False),
    )
    monkeypatch.setattr("app.tasks.schedule.utc_now", lambda: now)
    monkeypatch.setattr(
        "app.tasks.schedule.run_ingestion",
        SimpleNamespace(delay=lambda _run_id: pytest.fail("new owner was redispatched")),
    )

    assert redispatch_stale_queued_runs.apply().get() == {
        "redispatched": 0,
        "requeued": 0,
    }
    with Session(engine) as verification:
        assert verification.get(CrawlRun, run_id).status is CollectionStatus.RUNNING
        assert (
            verification.get(CollectionRequest, request_id).status
            is CollectionStatus.RUNNING
        )


def test_retry_recovery_lost_cas_preserves_work_requeued_between_pair_locks(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'paired-recovery-race.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        request, run = CollectionRepository(setup).create_request("Acme", "acme")
        setup.commit()
        claim = CollectionRepository(setup).claim_queued(run.id)
        assert claim is not None and claim.claim_token is not None
        run_id = run.id
        request_id = request.id
        claim_token = claim.claim_token

    with Session(engine, expire_on_commit=False) as first_recovery:
        repository = CollectionRepository(first_recovery)
        raced = False

        @event.listens_for(first_recovery, "do_orm_execute", retval=True)
        def requeue_after_run_lock(state):
            nonlocal raced
            result = state.invoke_statement()
            if not state.is_select:
                return result
            sql = " ".join(
                str(state.statement.compile(dialect=postgresql.dialect())).split()
            )
            if (
                not raced
                and state.is_select
                and "FROM crawl_runs" in sql
                and "JOIN" not in sql
                and "FOR UPDATE" in sql
            ):
                frozen = result.freeze()
                raced = True
                with Session(engine, expire_on_commit=False) as second_recovery:
                    second = CollectionRepository(second_recovery).requeue_for_retry(
                        run_id, expected_claim_token=claim_token
                    )
                    assert second is not None
                    assert second.status is CollectionStatus.QUEUED
                return frozen()
            return result

        observed = repository.requeue_for_retry(
            run_id, expected_claim_token=claim_token
        )

    assert raced is True
    assert observed is not None and observed.status is CollectionStatus.QUEUED
    with Session(engine) as verification:
        stored_run = verification.get(CrawlRun, run_id)
        stored_request = verification.get(CollectionRequest, request_id)
        assert stored_run is not None and stored_run.status is CollectionStatus.QUEUED
        assert stored_run.claim_token is None
        assert stored_run.error_code is None
        assert stored_request is not None
        assert stored_request.status is CollectionStatus.QUEUED
        assert stored_request.error_code is None


def test_retry_recovery_locks_run_then_request_in_postgresql_order(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'paired-recovery-lock.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        _request, run = CollectionRepository(setup).create_request("Acme", "acme")
        setup.commit()
        claim = CollectionRepository(setup).claim_queued(run.id)
        assert claim is not None and claim.claim_token is not None
        run_id = run.id
        claim_token = claim.claim_token

    statements = []
    with Session(engine, expire_on_commit=False) as recovery:
        @event.listens_for(recovery, "do_orm_execute")
        def capture_statement(state) -> None:
            if state.is_select:
                statements.append(state.statement)

        CollectionRepository(recovery).requeue_for_retry(
            run_id, expected_claim_token=claim_token
        )

    compiled = [
        " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
        for statement in statements
    ]
    lock_statements = [sql for sql in compiled if "FOR UPDATE" in sql]

    assert len(lock_statements) == 2
    assert "FROM crawl_runs" in lock_statements[0]
    assert "JOIN" not in lock_statements[0]
    assert "FROM collection_requests" in lock_statements[1]
    assert "JOIN" not in lock_statements[1]
