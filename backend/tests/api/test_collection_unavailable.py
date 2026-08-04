import pytest
from fastapi.testclient import TestClient

from app.collection.router import get_collection_service
from app.core.config import settings
from app.main import create_app


@pytest.fixture(autouse=True)
def enable_collection_for_validation_tests(monkeypatch) -> None:
    monkeypatch.setattr(settings, "collection_enabled", True)


def test_collection_request_query_is_validated_before_service_creation() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/collection-requests", json={"query": " "})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_collection_request_requires_normalized_query_within_length_bounds() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/collection-requests", json={"query": f" {'a' * 101} "}
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_collection_request_status_rejects_malformed_uuid() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/collection-requests/not-a-uuid")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_collection_disabled_rejects_valid_submission_without_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(settings, "collection_enabled", False)
    app = create_app()
    dependency_calls = 0

    def fail_if_service_is_resolved() -> None:
        nonlocal dependency_calls
        dependency_calls += 1
        raise AssertionError("disabled collection constructed its service")

    app.dependency_overrides[get_collection_service] = fail_if_service_is_resolved

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-requests", json={"query": "Example Technologies"}
        )

    assert response.status_code == 503
    assert dependency_calls == 0
    assert response.json() == {
        "error": {
            "code": "collection_unavailable",
            "message": "Collection service is unavailable.",
        }
    }
