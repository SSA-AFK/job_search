from uuid import uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.ingestion.contracts import ProviderQuery, ProviderResult
from app.ingestion.deduplication.semantic import DuplicateDecision
from app.ingestion.extraction.schemas import CompanyCandidate, CompanyProfileCandidate, CompanyRef
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
    from app.tasks.collection import run_ingestion

    run_id = uuid4()
    seen_run_ids = []

    class FailingOrchestrator:
        async def run(self, received_run_id):
            seen_run_ids.append(received_run_id)
            raise ConnectionError("database temporarily unavailable")

    monkeypatch.setattr(
        "app.tasks.collection.build_runtime_orchestrator",
        lambda: (FailingOrchestrator(), (Session(), Session(), Session())),
    )

    retries = []

    def retry(*, exc, countdown):
        retries.append((exc, countdown))
        raise Retry()

    monkeypatch.setattr(run_ingestion, "retry", retry)

    with pytest.raises(Retry):
        run_ingestion.run(str(run_id))

    assert seen_run_ids == [run_id]
    assert isinstance(retries[0][0], ConnectionError)
    assert retries[0][1] == 1
