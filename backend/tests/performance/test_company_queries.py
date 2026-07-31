"""Performance acceptance tests for company search against a representative SQLite dataset."""

import statistics
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models import Base, Company, JobPosting
from tests.performance.generate_dataset import seed_performance_dataset


@pytest.fixture(scope="module")
def performance_database() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    seed_performance_dataset(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def performance_client(performance_database: Engine) -> Iterator[TestClient]:
    app = create_app()

    def get_performance_session() -> Iterator[Session]:
        with Session(performance_database, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = get_performance_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def timed_get(client: TestClient, path: str) -> float:
    started_at = time.perf_counter()
    response = client.get(path)
    duration = time.perf_counter() - started_at

    assert response.status_code == 200
    assert response.json()["total"] > 0
    return duration


@pytest.mark.performance
def test_performance_dataset_has_exact_company_and_job_counts(
    performance_database: Engine,
) -> None:
    with Session(performance_database) as session:
        company_count = session.scalar(select(func.count(Company.id)))
        job_count = session.scalar(select(func.count(JobPosting.id)))

    assert company_count == 10_000
    assert job_count == 100_000


@pytest.mark.performance
def test_seeded_company_search_p95_is_below_300_ms(performance_client: TestClient) -> None:
    for _ in range(5):
        timed_get(performance_client, "/api/v1/companies?q=ai&page_size=20")

    durations = [
        timed_get(performance_client, "/api/v1/companies?q=ai&page_size=20")
        for _ in range(50)
    ]
    p95 = statistics.quantiles(durations, n=100)[94]

    print(f"company search p95: {p95 * 1_000:.1f} ms")
    assert p95 <= 0.300
