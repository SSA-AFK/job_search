from uuid import uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.deduplication.semantic import DuplicateDecision
from app.ingestion.extraction.schemas import CompanyCandidate, CompanyProfileCandidate, CompanyRef
from app.ingestion.runtime import build_ingestion_orchestrator
from app.models import Base, CollectionStatus, CrawlRun


class Provider:
    name = "test"
    calls = 0

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        self.calls += 1
        return ProviderResult(documents=())


class Extractor:
    async def discover(self, _documents):
        return (CompanyCandidate(name="Acme", evidence_ids=("source",), confidence=1),)

    async def extract_profile(self, _company: CompanyRef, _documents):
        return CompanyProfileCandidate(name="Acme", evidence_ids=("source",), confidence=1)

    async def extract_jobs(self, _company: CompanyRef, _documents):
        return ()


class SemanticJudge:
    async def jobs_are_duplicates(self, _left, _right) -> DuplicateDecision:
        return DuplicateDecision(False)


class DocumentProvider:
    name = "test"

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        return ProviderResult(
            documents=(
                RawDocument(
                    provider=self.name,
                    external_id="source",
                    url="https://acme.example/jobs/1",
                    title="Engineer",
                    text="Acme engineer",
                    published_at=None,
                ),
            )
        )


def test_collection_task_reuses_same_run_and_closes_three_sessions(tmp_path, monkeypatch) -> None:
    from app.tasks.collection import RuntimeComponents, run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'task.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        _request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    class TrackingSession(Session):
        was_closed = False

        def close(self) -> None:
            self.was_closed = True
            super().close()

    created_sessions: list[TrackingSession] = []

    def session_factory() -> TrackingSession:
        session = TrackingSession(engine, expire_on_commit=False)
        created_sessions.append(session)
        return session

    monkeypatch.setattr("app.tasks.collection.SessionLocal", session_factory)
    monkeypatch.setattr(
        "app.tasks.collection.load_runtime_components",
        lambda: RuntimeComponents((Provider(),), Extractor(), SemanticJudge()),
    )

    first = run_ingestion.apply(args=[str(run.id)]).get()
    second = run_ingestion.apply(args=[str(run.id)]).get()

    assert first["run_id"] == str(run.id)
    assert second["status"] == CollectionStatus.FAILED.value
    assert len(created_sessions) == 6
    assert all(session.was_closed for session in created_sessions)
    with Session(engine) as session:
        persisted = session.get(CrawlRun, run.id)
        assert persisted is not None
        assert persisted.status is CollectionStatus.FAILED
        assert persisted.error_code == "no_documents"


def test_collection_task_marks_unconfigured_runtime_unavailable(tmp_path, monkeypatch) -> None:
    from app.tasks.collection import RuntimeUnavailableError, run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'unavailable.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    monkeypatch.setattr("app.tasks.collection.SessionLocal", lambda: Session(engine, expire_on_commit=False))

    def unavailable_factory():
        raise RuntimeUnavailableError()

    monkeypatch.setattr("app.tasks.collection.build_runtime_orchestrator", unavailable_factory)

    result = run_ingestion.apply(args=[str(run.id)]).get()

    assert result["error_code"] == "collection_unavailable"
    with Session(engine) as session:
        persisted = session.get(CrawlRun, run.id)
        assert persisted is not None
        assert persisted.status is CollectionStatus.FAILED
        assert persisted.error_code == "collection_unavailable"
        assert session.get(type(request), request.id).status is CollectionStatus.FAILED


def test_collection_task_retries_infrastructure_error_with_same_run_id(monkeypatch) -> None:
    from app.ingestion.errors import RetryableInfrastructureError
    from app.ingestion.result import IngestionResult
    from app.tasks.collection import run_ingestion

    run_id = uuid4()
    seen_run_ids = []

    class FailingOrchestrator:
        async def run(self, received_run_id):
            seen_run_ids.append(received_run_id)
            raise RetryableInfrastructureError()

        def requeue_for_retry(self, _run_id):
            return None

    monkeypatch.setattr(
        "app.tasks.collection.build_runtime_orchestrator",
        lambda: (FailingOrchestrator(), (Session(), Session(), Session())),
    )
    monkeypatch.setattr(
        "app.tasks.collection._recover_retry_state",
        lambda received_run_id, *, exhausted: IngestionResult.unknown_run(received_run_id),
    )

    retries = []

    def retry(*, exc, countdown):
        retries.append((exc, countdown))
        raise Retry()

    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(run_id))

    assert seen_run_ids == [run_id]
    assert isinstance(retries[0][0], RetryableInfrastructureError)
    assert retries[0][1] == 1


def test_collection_task_requeues_real_running_run_before_retry(tmp_path, monkeypatch) -> None:
    from app.tasks.collection import run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'retry.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    monkeypatch.setattr("app.tasks.collection.SessionLocal", lambda: Session(engine, expire_on_commit=False))

    def runtime_factory():
        sessions = tuple(Session(engine, expire_on_commit=False) for _ in range(3))
        orchestrator = build_ingestion_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            persistence_write_session=sessions[2],
            providers=(DocumentProvider(),),
            extractor=Extractor(),
            semantic_judge=SemanticJudge(),
        )

        def fail_persistence(*_args, **_kwargs):
            raise OperationalError("insert", {}, RuntimeError("database unavailable"))

        orchestrator.persistence.persist = fail_persistence
        return orchestrator, sessions

    retries = []

    def retry(*, exc, countdown):
        retries.append((exc, countdown))
        raise Retry()

    monkeypatch.setattr("app.tasks.collection.build_runtime_orchestrator", runtime_factory)
    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(run.id))

    assert retries and retries[0][1] == 1
    with Session(engine) as session:
        persisted_run = session.get(CrawlRun, run.id)
        persisted_request = session.get(type(request), request.id)
        assert persisted_run is not None and persisted_run.status is CollectionStatus.QUEUED
        assert persisted_request is not None and persisted_request.status is CollectionStatus.QUEUED


def test_collection_task_terminalizes_real_running_run_after_retry_exhaustion(
    tmp_path, monkeypatch
) -> None:
    from app.tasks.collection import run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'retry-exhausted.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    monkeypatch.setattr("app.tasks.collection.SessionLocal", lambda: Session(engine, expire_on_commit=False))

    def runtime_factory():
        sessions = tuple(Session(engine, expire_on_commit=False) for _ in range(3))
        orchestrator = build_ingestion_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            persistence_write_session=sessions[2],
            providers=(DocumentProvider(),),
            extractor=Extractor(),
            semantic_judge=SemanticJudge(),
        )

        def fail_persistence(*_args, **_kwargs):
            raise OperationalError("insert", {}, RuntimeError("database unavailable"))

        orchestrator.persistence.persist = fail_persistence
        return orchestrator, sessions

    monkeypatch.setattr("app.tasks.collection.build_runtime_orchestrator", runtime_factory)
    run_ingestion.push_request(retries=3)
    try:
        result = run_ingestion.run(str(run.id))
    finally:
        run_ingestion.pop_request()

    assert result["error_code"] == "collection_unavailable"
    with Session(engine) as session:
        persisted_run = session.get(CrawlRun, run.id)
        persisted_request = session.get(type(request), request.id)
        assert persisted_run is not None and persisted_run.status is CollectionStatus.FAILED
        assert persisted_request is not None and persisted_request.status is CollectionStatus.FAILED


def test_collection_task_closes_sessions_created_before_factory_failure(monkeypatch) -> None:
    from app.tasks.collection import build_runtime_orchestrator

    class TrackingSession(Session):
        closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    first = TrackingSession()
    calls = 0

    def session_factory() -> Session:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise RuntimeError("second session unavailable")

    monkeypatch.setattr("app.tasks.collection.SessionLocal", session_factory)

    with pytest.raises(RuntimeError, match="second session unavailable"):
        build_runtime_orchestrator()

    assert calls == 2
    assert first.closed is True


def test_collection_task_retries_real_terminal_write_infrastructure_failure(tmp_path, monkeypatch) -> None:
    from app.tasks.collection import run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'terminal-write.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    class FailingExtractor(Extractor):
        async def discover(self, _documents):
            raise ValueError("deterministic extraction failure")

    def runtime_factory():
        sessions = tuple(Session(engine, expire_on_commit=False) for _ in range(3))
        orchestrator = build_ingestion_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            persistence_write_session=sessions[2],
            providers=(DocumentProvider(),),
            extractor=FailingExtractor(),
            semantic_judge=SemanticJudge(),
        )

        def fail_terminal_write(*_args, **_kwargs):
            raise OperationalError("update crawl_runs", {}, RuntimeError("state database unavailable"))

        orchestrator.runs.finish = fail_terminal_write
        return orchestrator, sessions

    retries = []

    def retry(*, exc, countdown):
        retries.append((exc, countdown))
        raise Retry()

    monkeypatch.setattr("app.tasks.collection.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.collection.build_runtime_orchestrator", runtime_factory)
    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(run.id))

    assert retries and retries[0][1] == 1
    with Session(engine) as session:
        persisted_run = session.get(CrawlRun, run.id)
        persisted_request = session.get(type(request), request.id)
        assert persisted_run is not None and persisted_run.status is CollectionStatus.QUEUED
        assert persisted_request is not None and persisted_request.status is CollectionStatus.QUEUED


def test_collection_task_recovers_real_running_state_with_a_fresh_session(tmp_path, monkeypatch) -> None:
    from app.tasks.collection import run_ingestion

    engine = create_engine(f"sqlite:///{tmp_path / 'run-state.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()

    def runtime_factory():
        sessions = tuple(Session(engine, expire_on_commit=False) for _ in range(3))
        orchestrator = build_ingestion_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            persistence_write_session=sessions[2],
            providers=(DocumentProvider(),),
            extractor=Extractor(),
            semantic_judge=SemanticJudge(),
        )
        start = orchestrator.runs.start_or_get_terminal

        def fail_run_state(run_id):
            start(run_id)
            raise OperationalError("update crawl_runs", {}, RuntimeError("state database unavailable"))

        orchestrator.runs.start_or_get_terminal = fail_run_state
        orchestrator.runs.requeue_for_retry = lambda _run_id: pytest.fail("reused failed state session")
        return orchestrator, sessions

    retries = []

    def retry(*, exc, countdown):
        retries.append((exc, countdown))
        raise Retry()

    monkeypatch.setattr("app.tasks.collection.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.collection.build_runtime_orchestrator", runtime_factory)
    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(run.id))

    assert retries and retries[0][1] == 1
    with Session(engine) as session:
        persisted_run = session.get(CrawlRun, run.id)
        persisted_request = session.get(type(request), request.id)
        assert persisted_run is not None and persisted_run.status is CollectionStatus.QUEUED
        assert persisted_request is not None and persisted_request.status is CollectionStatus.QUEUED
