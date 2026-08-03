from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, CollectionRequest, CollectionStatus, Company, CrawlRun, RunType


def company(name: str, collected_at: datetime | None) -> Company:
    return Company(
        id=uuid4(), canonical_name=name, normalized_name=name.lower(), funding_stage="unknown",
        scale="unknown", last_collected_at=collected_at,
    )


def test_refresh_selects_only_never_or_older_than_24_hours(monkeypatch) -> None:
    from app.tasks.schedule import enqueue_stale_companies

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fresh = company("Fresh", now - timedelta(hours=23, minutes=59))
        boundary = company("Boundary", now - timedelta(hours=24))
        stale = company("Stale", now - timedelta(hours=24, seconds=1))
        never = company("Never", None)
        stale_id, never_id = stale.id, never.id
        session.add_all((never, stale, boundary, fresh))
        session.commit()

    dispatched: list[str] = []
    monkeypatch.setattr("app.tasks.schedule.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.schedule.utc_now", lambda: now)
    class AsyncResult:
        id = "task-1"

    def delay(run_id: str) -> AsyncResult:
        dispatched.append(run_id)
        return AsyncResult()

    monkeypatch.setattr("app.tasks.schedule.run_ingestion", type("Task", (), {"delay": staticmethod(delay)})())

    result = enqueue_stale_companies.apply().get()

    assert result["enqueued"] == 2
    with Session(engine) as session:
        created = list(
            session.query(CrawlRun)
            .join(CollectionRequest, CrawlRun.collection_request_id == CollectionRequest.id)
            .order_by(CollectionRequest.query)
        )
        assert [str(run.id) for run in created] == dispatched
        assert {run.company_id for run in created} == {stale_id, never_id}
        assert all(run.run_type is RunType.COMPANY_REFRESH for run in created)


def test_refresh_skips_company_with_active_request(monkeypatch) -> None:
    from app.tasks.schedule import enqueue_stale_companies

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        stale = company("Acme", now - timedelta(days=2))
        session.add(stale)
        session.flush()
        request = CollectionRequest(
            query="Acme", normalized_query="acme", company_id=stale.id,
            status=CollectionStatus.RUNNING,
        )
        session.add(request)
        session.commit()

    monkeypatch.setattr("app.tasks.schedule.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.schedule.utc_now", lambda: now)
    monkeypatch.setattr("app.tasks.schedule.run_ingestion", type("Task", (), {"delay": lambda *_: None})())

    assert enqueue_stale_companies.apply().get() == {"enqueued": 0, "skipped_active": 1}
    with Session(engine) as session:
        assert session.query(CrawlRun).count() == 0


def test_celery_uses_json_and_runs_both_maintenance_tasks_at_shanghai_two_am() -> None:
    from app.tasks.celery_app import celery_app

    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert celery_app.conf.task_acks_late is True
    schedules = celery_app.conf.beat_schedule
    assert set(schedules) == {"enqueue-stale-companies", "expire-stale-job-sources"}
    assert all(item["schedule"].hour == {2} and item["schedule"].minute == {0} for item in schedules.values())
