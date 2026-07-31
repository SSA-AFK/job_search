import logging
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_session
from app.main import create_app


def test_unexpected_dependency_failure_is_sanitized_and_correlated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_exception_text = "SECRET_TOKEN SELECT * FROM companies"

    def failing_session() -> None:
        raise RuntimeError(secret_exception_text)

    app = create_app()
    app.dependency_overrides[get_session] = failing_session
    caplog.set_level(logging.ERROR, logger="app.errors")

    with TestClient(app) as client:
        try:
            response = client.get("/api/v1/companies")
        except RuntimeError:
            pytest.fail("unexpected exception escaped the error boundary")

    request_id = response.json()["error"]["request_id"]
    assert UUID(request_id).version == 4
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": request_id,
        }
    }
    assert response.headers["x-request-id"] == request_id

    error_records = [record for record in caplog.records if record.name == "app.errors"]
    assert len(error_records) == 1
    record = error_records[0]
    assert record.getMessage() == "Unhandled request failure"
    assert record.request_id == request_id  # type: ignore[attr-defined]
    assert record.error_code == "internal_error"  # type: ignore[attr-defined]
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.route == "/companies"  # type: ignore[attr-defined]
    assert record.exc_info is None

    logged = caplog.text + " ".join(record.getMessage() for record in error_records)
    assert secret_exception_text not in logged
    assert "RuntimeError" not in logged
    assert "Traceback" not in logged
