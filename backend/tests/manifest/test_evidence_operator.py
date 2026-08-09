from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.manifest.contracts import AiCategory, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    EntryDiscoveryRound,
    EntryEvidenceAuditFinding,
    EntryEvidenceAuditSample,
)
from app.models import Company, JobEntry
from app.models.base import Base

BACKEND_ROOT = Path(__file__).parents[2]
MANIFEST_VERSION = "c" * 64
STARTED_AT = datetime(2026, 8, 9, 8, tzinfo=UTC)


@pytest.fixture
def operator_environment(tmp_path: Path) -> Iterator[tuple[dict[str, str], str]]:
    database_path = tmp_path / "operator.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    company = Company(
        id=UUID(int=91_001),
        canonical_name="Operator Company",
        normalized_name="operator-company",
    )
    with factory.begin() as session:
        session.add(company)
        session.add(
            CompanyManifest(
                version=MANIFEST_VERSION,
                config_fingerprint="a" * 64,
                member_count=1,
                canonical_quota={"foundation_models": 1},
                frozen_at=STARTED_AT,
            )
        )
        session.add(
            CompanyManifestMember(
                manifest_version=MANIFEST_VERSION,
                company_id=company.id,
                position=1,
                canonical_name=company.canonical_name,
                primary_category=AiCategory.FOUNDATION_MODELS,
            )
        )
    engine.dispose()
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "ENTRY_EVIDENCE_MODEL_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8",
    }
    yield environment, database_url


def run_cli(
    *arguments: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.manifest.cli", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_evidence_regenerate_dry_run_never_requires_or_creates_model_round(
    operator_environment: tuple[dict[str, str], str],
) -> None:
    environment, database_url = operator_environment

    result = run_cli(
        "evidence-regenerate",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-09",
        "--dry-run",
        environment=environment,
    )

    assert result.returncode == 0
    assert payload(result) == {
        "dry_run": True,
        "eligible_members": 1,
        "manifest_version": MANIFEST_VERSION,
        "model_enabled": False,
        "paused_strata": [],
        "round_name": "entry-evidence-2026-08-09",
        "would_create_round": True,
    }
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EntryDiscoveryRound)) == 0
    engine.dispose()


def test_evidence_regenerate_rejects_hostile_round_name_without_echoing_it(
    operator_environment: tuple[dict[str, str], str],
) -> None:
    environment, _database_url = operator_environment
    hostile_name = "postgresql://operator:super-secret@database/jobs"

    result = run_cli(
        "evidence-regenerate",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        hostile_name,
        "--dry-run",
        environment=environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: invalid arguments\n"
    assert hostile_name not in result.stderr
    assert "super-secret" not in result.stderr


@pytest.mark.parametrize(
    ("environment_enabled", "cli_enabled"),
    ((False, True), (True, False)),
)
def test_evidence_regenerate_requires_configuration_and_explicit_model_opt_in(
    operator_environment: tuple[dict[str, str], str],
    environment_enabled: bool,
    cli_enabled: bool,
) -> None:
    environment, database_url = operator_environment
    environment = {
        **environment,
        "ENTRY_EVIDENCE_MODEL_ENABLED": str(environment_enabled).lower(),
    }
    arguments = [
        "evidence-regenerate",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-09",
    ]
    if cli_enabled:
        arguments.append("--model")

    result = run_cli(*arguments, environment=environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: evidence model is disabled\n"
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EntryDiscoveryRound)) == 0
    engine.dispose()


def test_evidence_regenerate_creates_only_the_named_round_after_double_opt_in(
    operator_environment: tuple[dict[str, str], str],
) -> None:
    environment, database_url = operator_environment
    environment = {**environment, "ENTRY_EVIDENCE_MODEL_ENABLED": "true"}

    result = run_cli(
        "evidence-regenerate",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-09",
        "--model",
        environment=environment,
    )

    assert result.returncode == 0
    result_payload = payload(result)
    assert result_payload["dry_run"] is False
    assert result_payload["eligible_members"] == 1
    assert result_payload["model_enabled"] is True
    assert result_payload["round_created"] is True
    assert result_payload["round_name"] == "entry-evidence-2026-08-09"
    assert result_payload["paused_strata"] == []
    assert set(result_payload) == {
        "dry_run",
        "eligible_members",
        "manifest_version",
        "model_enabled",
        "paused_strata",
        "round_created",
        "round_id",
        "round_name",
    }
    engine = create_engine(database_url)
    with engine.connect() as connection:
        stored = connection.execute(select(EntryDiscoveryRound)).one()
    engine.dispose()
    assert stored.name == "entry-evidence-2026-08-09"


def _seed_audit_sample(database_url: str) -> tuple[UUID, UUID]:
    engine = create_engine(database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    round_id = UUID(int=92_001)
    observation_id = UUID(int=92_002)
    entry_id = UUID(int=92_003)
    sample_id = UUID(int=92_004)
    with factory.begin() as session:
        session.add(
            EntryDiscoveryRound(
                id=round_id,
                manifest_version=MANIFEST_VERSION,
                name="entry-evidence-2026-08-09",
                config_fingerprint="a" * 64,
                model_fingerprint="b" * 64,
                started_at=STARTED_AT + timedelta(minutes=1),
            )
        )
        session.add(
            JobEntry(
                id=entry_id,
                company_id=UUID(int=91_001),
                url="https://jobs.example/a",
                normalized_url="https://jobs.example/a",
                provider="official_entry_discovery",
                platform="moka",
                requires_rendering=False,
            )
        )
        session.add(
            EntryDiscoveryObservation(
                id=observation_id,
                manifest_version=MANIFEST_VERSION,
                discovery_round_id=round_id,
                company_id=UUID(int=91_001),
                method="entry_evidence_model",
                status=DiscoveryStatus.ACCEPTED,
                candidate_url="https://jobs.example/a",
                normalized_url="https://jobs.example/a",
                source_id="public_registry",
                ownership_evidence="official website careers anchor",
                platform="moka",
                requires_rendering=False,
                job_entry_id=entry_id,
                observed_at=STARTED_AT + timedelta(minutes=2),
            )
        )
        session.add(
            EntryEvidenceAuditSample(
                id=sample_id,
                discovery_round_id=round_id,
                observation_id=observation_id,
                source_id="public_registry",
                platform="moka",
                selected_at=STARTED_AT + timedelta(minutes=3),
            )
        )
    engine.dispose()
    return round_id, sample_id


def test_evidence_audit_records_severe_finding_and_pauses_stratum(
    operator_environment: tuple[dict[str, str], str], tmp_path: Path
) -> None:
    environment, database_url = operator_environment
    _round_id, sample_id = _seed_audit_sample(database_url)
    findings = tmp_path / "findings.json"
    findings.write_text(
        json.dumps(
            [
                {
                    "audit_sample_id": str(sample_id),
                    "severe_error": True,
                    "reason": "The entry belongs to another legal entity.",
                    "audited_at": "2026-08-09T08:04:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "evidence-audit",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-09",
        "--findings",
        str(findings),
        environment=environment,
    )

    assert result.returncode == 0
    assert payload(result) == {
        "audited": 1,
        "dry_run": False,
        "manifest_version": MANIFEST_VERSION,
        "paused_strata": [{"platform": "moka", "source_id": "public_registry"}],
        "round_name": "entry-evidence-2026-08-09",
        "severe_errors": 1,
    }
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EntryEvidenceAuditFinding)) == 1
    engine.dispose()

    dry_run = run_cli(
        "evidence-regenerate",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-10",
        "--dry-run",
        environment=environment,
    )
    assert payload(dry_run)["paused_strata"] == [
        {"platform": "moka", "source_id": "public_registry"}
    ]


def test_evidence_audit_rejects_sensitive_diagnostics_without_echoing_input(
    operator_environment: tuple[dict[str, str], str], tmp_path: Path
) -> None:
    environment, database_url = operator_environment
    _round_id, sample_id = _seed_audit_sample(database_url)
    secret = "postgresql://operator:super-secret@database/jobs"
    findings = tmp_path / "findings.json"
    findings.write_text(
        json.dumps(
            [
                {
                    "audit_sample_id": str(sample_id),
                    "severe_error": True,
                    "reason": secret,
                    "audited_at": "2026-08-09T08:04:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "evidence-audit",
        "--manifest",
        MANIFEST_VERSION,
        "--round-name",
        "entry-evidence-2026-08-09",
        "--findings",
        str(findings),
        environment=environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: input is invalid\n"
    assert secret not in result.stderr
    assert "super-secret" not in result.stderr


def test_report_include_rounds_exposes_separate_round_census(
    operator_environment: tuple[dict[str, str], str],
) -> None:
    environment, database_url = operator_environment
    _seed_audit_sample(database_url)

    result = run_cli(
        "report",
        "--manifest",
        MANIFEST_VERSION,
        "--code-commit",
        "abc1234",
        "--include-rounds",
        environment=environment,
    )

    assert result.returncode == 0
    result_payload = payload(result)
    aggregate = result_payload["aggregate"]
    rounds = result_payload["rounds"]
    assert isinstance(aggregate, dict)
    assert isinstance(rounds, list)
    assert aggregate["manifest_companies"] == 1
    assert aggregate["accepted_entries"] == 1
    assert len(rounds) == 1
    assert isinstance(rounds[0], dict)
    assert rounds[0]["name"] == "entry-evidence-2026-08-09"
    assert rounds[0]["company_denominator"] == 1
    assert rounds[0]["accepted_entries"] == 1
