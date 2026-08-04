from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.collection.router import get_collection_service
from app.collection.service import CollectionService
from app.core.config import settings
from app.main import create_app
from app.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_collection_service] = lambda: CollectionService(
        session, lambda _run_id: "celery-task-123"
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def enable_collection(monkeypatch) -> None:
    monkeypatch.setattr(settings, "collection_enabled", True)


def test_create_collection_request_returns_accepted(client: TestClient) -> None:
    response = client.post("/api/v1/collection-requests", json={"query": "  示例 科技 "})

    assert response.status_code == 202
    assert response.json()["query"] == "示例 科技"
    assert response.json()["normalized_query"] == "示例科技"
    assert response.json()["status"] == "queued"


@pytest.mark.parametrize("query", [" ", " a ", f" {'a' * 101} "])
def test_create_collection_request_validates_normalized_query_length(
    client: TestClient, query: str
) -> None:
    response = client.post("/api/v1/collection-requests", json={"query": query})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_collection_request_returns_database_status(client: TestClient) -> None:
    submitted = client.post(
        "/api/v1/collection-requests", json={"query": "Example Technologies"}
    ).json()

    response = client.get(f"/api/v1/collection-requests/{submitted['id']}")

    assert response.status_code == 200
    assert response.json() == submitted


def test_get_collection_request_rejects_malformed_uuid(client: TestClient) -> None:
    response = client.get("/api/v1/collection-requests/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_collection_request_returns_not_found_for_absent_request(client: TestClient) -> None:
    response = client.get(f"/api/v1/collection-requests/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "collection_request_not_found"


def test_dispatch_failure_returns_discoverable_failed_request(session: Session) -> None:
    app = create_app()

    def dispatch_collection(_run_id: UUID) -> str:
        raise RuntimeError("broker unavailable")

    app.dependency_overrides[get_collection_service] = lambda: CollectionService(
        session, dispatch_collection
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-requests", json={"query": "Example Technologies"}
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "collection_unavailable"
