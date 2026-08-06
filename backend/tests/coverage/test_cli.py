import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobSnapshotStatus,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli_environment(tmp_path: Path) -> dict[str, str]:
    database_path = tmp_path / "coverage.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = Company(canonical_name="CLI Company", normalized_name="cli-company")
        session.add(company)
        session.flush()
        entry = JobEntry(
            company_id=company.id,
            url="https://jobs.example.com/cli",
            normalized_url="https://jobs.example.com/cli",
            provider="official",
            platform="custom",
            status=JobEntryStatus.ACTIVE,
        )
        session.add(entry)
        session.flush()
        completed_at = datetime(2026, 8, 5, 11, tzinfo=UTC)
        session.add(
            JobCollectionSnapshot(
                job_entry_id=entry.id,
                status=JobSnapshotStatus.SUCCEEDED,
                lifecycle_applied=True,
                pagination_complete=True,
                empty_confirmed=False,
                observed_count=1,
                pages_fetched=1,
                command_hash="1" * 64,
                started_at=completed_at - timedelta(minutes=1),
                completed_at=completed_at,
            )
        )
        session.commit()
    engine.dispose()
    return {**os.environ, "DATABASE_URL": f"sqlite:///{database_path.as_posix()}"}


def _run_cli(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.coverage.cli", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_prints_one_sorted_json_object_with_strings_and_utc_timestamp(
    cli_environment: dict[str, str],
) -> None:
    result = _run_cli(
        "--as-of",
        "2026-08-05T20:00:00+08:00",
        environment=cli_environment,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    payload = json.loads(result.stdout)
    assert list(payload) == sorted(payload)
    assert payload["as_of"] == "2026-08-05T12:00:00Z"
    assert payload["entry_coverage_rate"] == "1.0000"
    assert payload["enumeration_rate"] == "1.0000"
    assert payload["completeness_rate"] == "1.0000"
    assert payload["refresh_slo_rate"] == "1.0000"
    assert payload["refresh_window_hours"] == 24


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (("--as-of", "2026-08-05T12:00:00"), "timezone-aware"),
        (("--as-of", "not-a-date"), "valid ISO-8601"),
        (("--refresh-hours", "0"), "positive"),
    ],
)
def test_cli_rejects_invalid_inputs_without_stdout(
    cli_environment: dict[str, str], arguments: tuple[str, ...], diagnostic: str
) -> None:
    result = _run_cli(*arguments, environment=cli_environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert diagnostic in result.stderr


def test_cli_sanitizes_database_failures(tmp_path: Path) -> None:
    secret = "super-secret-password"
    missing_parent = tmp_path / secret / "coverage.db"
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{missing_parent.as_posix()}",
    }

    result = _run_cli(environment=environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "coverage report failed: database unavailable\n"
    assert secret not in result.stderr
    assert "SELECT" not in result.stderr.upper()


def test_cli_sanitizes_unrelated_settings_validation_failures(
    cli_environment: dict[str, str],
) -> None:
    secret = "settings-secret-sentinel"
    environment = {
        **cli_environment,
        "COLLECTION_ENABLED": f"not-a-boolean-{secret}",
    }

    result = _run_cli(environment=environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "coverage report failed: database unavailable\n"
    assert secret not in result.stderr
    assert "INPUT_VALUE" not in result.stderr.upper()
    assert "TRACEBACK" not in result.stderr.upper()
    assert environment["DATABASE_URL"] not in result.stderr
    assert "SELECT" not in result.stderr.upper()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--as-of", "0001-01-01T00:00:00Z"),
        ("--refresh-hours", "2147483647"),
        ("--refresh-hours", "999999999999999"),
    ],
)
def test_cli_rejects_unrepresentable_windows_before_database_access(
    arguments: tuple[str, ...],
) -> None:
    secret = "super-secret-password"
    environment = {
        **os.environ,
        "DATABASE_URL": f"missing-driver://user:{secret}@database.example/coverage",
    }

    result = _run_cli(*arguments, environment=environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "coverage report failed: refresh window is outside the supported datetime range\n"
    )
    assert secret not in result.stderr
    assert "TRACEBACK" not in result.stderr.upper()
    assert "SELECT" not in result.stderr.upper()


@pytest.mark.parametrize(
    "as_of",
    [
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59.999999-14:00",
    ],
)
def test_cli_rejects_as_of_values_that_overflow_utc_normalization_before_database_access(
    as_of: str,
) -> None:
    secret = "super-secret-password"
    environment = {
        **os.environ,
        "DATABASE_URL": f"missing-driver://user:{secret}@database.example/coverage",
    }

    result = _run_cli("--as-of", as_of, environment=environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "coverage report failed: as_of is outside the supported datetime range\n"
    )
    assert secret not in result.stderr
    assert "TRACEBACK" not in result.stderr.upper()
    assert "SELECT" not in result.stderr.upper()
