from fastapi.testclient import TestClient

from app.main import create_app


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
