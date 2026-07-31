from fastapi.testclient import TestClient

from app.main import create_app


def test_collection_is_explicitly_unavailable_without_worker() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/collection-requests", json={"query": "示例公司"})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "collection_unavailable"
    assert isinstance(error["message"], str)
    assert error["message"].strip()


def test_collection_request_query_is_validated_before_availability_check() -> None:
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


def test_collection_request_status_is_unavailable_for_valid_uuid() -> None:
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/collection-requests/00000000-0000-0000-0000-000000000000"
        )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "collection_unavailable"
    assert isinstance(error["message"], str)
    assert error["message"].strip()


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
