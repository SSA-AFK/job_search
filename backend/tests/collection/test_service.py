from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.collection.service import CollectionService
from app.models import Base, CollectionRequest, CollectionStatus, CrawlRun


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def dispatched() -> list[UUID]:
    return []


@pytest.fixture
def service(session: Session, dispatched: list[UUID]) -> CollectionService:
    def dispatch_collection(run_id: UUID) -> str:
        dispatched.append(run_id)
        return "celery-task-123"

    return CollectionService(session, dispatch_collection)


def mark_failed(session: Session, request_id: UUID, error_code: str) -> None:
    request = session.get(CollectionRequest, request_id)
    assert request is not None
    request.status = CollectionStatus.FAILED
    request.error_code = error_code
    session.commit()


def test_submit_reuses_active_normalized_query(session: Session, service: CollectionService) -> None:
    first = service.submit("  示例 科技 ")
    second = service.submit("示例科技")

    assert second.id == first.id
    assert second.status == CollectionStatus.QUEUED
    assert session.scalar(select(func.count(CollectionRequest.id))) == 1
    assert session.scalar(select(func.count(CrawlRun.id))) == 1


def test_terminal_request_does_not_block_new_submission(
    session: Session, service: CollectionService
) -> None:
    first = service.submit("示例科技")
    mark_failed(session, first.id, "provider_timeout")

    second = service.submit("示例科技")

    assert second.id != first.id


def test_concurrent_submissions_reuse_active_request(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'collection.sqlite3'}")
    Base.metadata.create_all(engine)
    dispatch_started = Event()
    allow_dispatch_to_finish = Event()

    def dispatch_collection(_run_id: UUID) -> str:
        dispatch_started.set()
        assert allow_dispatch_to_finish.wait(timeout=5)
        return "celery-task-123"

    def submit() -> UUID:
        with Session(engine, expire_on_commit=False) as database_session:
            return CollectionService(database_session, dispatch_collection).submit("Example Tech").id

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_submission = executor.submit(submit)
        assert dispatch_started.wait(timeout=5)
        second_submission = submit()
        allow_dispatch_to_finish.set()
        first_request_id = first_submission.result(timeout=5)

    with Session(engine) as database_session:
        assert second_submission == first_request_id
        assert database_session.scalar(select(func.count(CollectionRequest.id))) == 1
        assert database_session.scalar(select(func.count(CrawlRun.id))) == 1


def test_submit_records_dispatch_task_id(
    session: Session, dispatched: list[UUID], service: CollectionService
) -> None:
    submitted = service.submit("Example Technologies")
    run = session.scalar(select(CrawlRun).where(CrawlRun.collection_request_id == submitted.id))

    assert run is not None
    assert dispatched == [run.id]
    assert run.celery_task_id == "celery-task-123"


def test_dispatch_failure_marks_request_and_run_failed(session: Session) -> None:
    def dispatch_collection(_run_id: UUID) -> str:
        raise RuntimeError("broker unavailable")

    submitted = CollectionService(session, dispatch_collection).submit("Example Technologies")
    run = session.scalar(select(CrawlRun).where(CrawlRun.collection_request_id == submitted.id))

    assert submitted.status == CollectionStatus.FAILED
    assert submitted.error_code == "collection_unavailable"
    assert run is not None
    assert run.status == CollectionStatus.FAILED
    assert run.error_code == "collection_unavailable"


def test_get_returns_persisted_request(service: CollectionService) -> None:
    submitted = service.submit("Example Technologies")

    found = service.get(submitted.id)

    assert found == submitted
