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
from sqlalchemy.orm import Session

from app.company_identity.contracts import IdentityReviewStatus
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.models import Base, CollectionStatus, Company, CrawlRun, RunType

BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
MAX_INPUT_BYTES = 16 * 1024 * 1024


@pytest.fixture
def cli_environment(tmp_path: Path) -> Iterator[dict[str, str]]:
    database_path = tmp_path / "identity-cli.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    yield {
        **os.environ,
        "DATABASE_URL": database_url,
        "GATE1_LIVE_DISCOVERY_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8",
    }


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


def _session(environment: dict[str, str]) -> Iterator[Session]:
    engine = create_engine(environment["DATABASE_URL"])
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


def _seed_reviews(environment: dict[str, str]) -> tuple[dict[str, UUID], dict[str, UUID]]:
    with next(_session(environment)) as session:
        run = CrawlRun(
            run_type=RunType.DISCOVERY,
            status=CollectionStatus.SUCCEEDED,
            providers_attempted=[],
            created_at=NOW,
        )
        companies = {
            "Alpha Alias": Company(
                canonical_name="Alpha Labs",
                normalized_name="alphalabs",
                funding_stage="unknown",
                scale="unknown",
            ),
            "Beta Renamed": Company(
                canonical_name="Beta Old",
                normalized_name="betaold",
                funding_stage="unknown",
                scale="unknown",
            ),
        }
        session.add(run)
        session.add_all(companies.values())
        session.flush()
        item_ids: dict[str, UUID] = {}
        for offset, name in enumerate(
            ("Alpha Alias", "Beta Renamed", "Gamma New", "Delta Rejected"), start=1
        ):
            item = CompanyIdentityReviewItem(
                stable_identity_hash=f"{offset:064x}",
                first_crawl_run_id=run.id,
                status=IdentityReviewStatus.PENDING,
                candidate_name=name,
                normalized_name=name.casefold().replace(" ", ""),
                aliases=[],
                official_website=None,
                recruitment_identity=None,
                legal_identifiers=[],
                city=None,
                public_evidence_refs=[],
                candidate_matches=[],
                review_reasons=["fuzzy_name_neighbor"],
                created_at=NOW + timedelta(seconds=offset),
                resolved_at=None,
            )
            session.add(item)
            session.flush()
            item_ids[name] = item.id
        company_ids = {name: company.id for name, company in companies.items()}
        session.commit()
        return item_ids, company_ids


def _decision(
    review_item_id: UUID,
    action: str,
    *,
    target_company_id: UUID | None = None,
    reason: str = "Reviewed public identity evidence.",
) -> dict[str, object]:
    return {
        "review_item_id": str(review_item_id),
        "action": action,
        "target_company_id": (None if target_company_id is None else str(target_company_id)),
        "reason": reason,
        "decided_at": "2026-08-07T12:30:00Z",
    }


def _assert_sorted_object(stdout: str) -> dict[str, object]:
    assert stdout.endswith("\n")
    assert stdout.count("\n") == 1
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    assert list(payload) == sorted(payload)
    return payload


def test_identity_review_export_is_atomic_sorted_and_external(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    _seed_reviews(cli_environment)
    output = tmp_path / "reviews.json"
    output.write_text("stale", encoding="utf-8")

    result = run_cli("identity-review-export", str(output), environment=cli_environment)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '{"exported":4,"output":"reviews.json"}\n'
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert [item["draft"]["identity"]["canonical_name"] for item in exported] == [
        "Alpha Alias",
        "Beta Renamed",
        "Gamma New",
        "Delta Rejected",
    ]
    assert all(item["status"] == "pending" for item in exported)
    assert list(tmp_path.glob("reviews.json.*.tmp")) == []


def test_identity_review_empty_queues_have_zero_counts(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    export_path = tmp_path / "empty-reviews.json"
    decisions_path = tmp_path / "empty-decisions.json"
    decisions_path.write_text("[]", encoding="utf-8")

    exported = run_cli("identity-review-export", str(export_path), environment=cli_environment)
    applied = run_cli("identity-review-apply", str(decisions_path), environment=cli_environment)

    assert exported.returncode == applied.returncode == 0
    assert exported.stderr == applied.stderr == ""
    assert _assert_sorted_object(exported.stdout) == {
        "exported": 0,
        "output": "empty-reviews.json",
    }
    assert _assert_sorted_object(applied.stdout) == {"applied": 0, "replayed": 0}
    assert json.loads(export_path.read_text(encoding="utf-8")) == []


def test_identity_review_apply_supports_all_actions_and_exact_replay(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    item_ids, company_ids = _seed_reviews(cli_environment)
    decisions = [
        _decision(
            item_ids["Alpha Alias"],
            "link_as_alias",
            target_company_id=company_ids["Alpha Alias"],
        ),
        _decision(
            item_ids["Beta Renamed"],
            "rename_canonical",
            target_company_id=company_ids["Beta Renamed"],
        ),
        _decision(item_ids["Gamma New"], "create_new"),
        _decision(item_ids["Delta Rejected"], "reject"),
    ]
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions), encoding="utf-8")

    first = run_cli("identity-review-apply", str(path), environment=cli_environment)
    replay = run_cli("identity-review-apply", str(path), environment=cli_environment)

    assert first.returncode == replay.returncode == 0
    assert first.stderr == replay.stderr == ""
    assert _assert_sorted_object(first.stdout) == {"applied": 4, "replayed": 0}
    assert _assert_sorted_object(replay.stdout) == {"applied": 0, "replayed": 4}
    with next(_session(cli_environment)) as session:
        assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewDecision)) == 4
        statuses = tuple(
            session.scalars(
                select(CompanyIdentityReviewItem.status).order_by(
                    CompanyIdentityReviewItem.created_at,
                    CompanyIdentityReviewItem.id,
                )
            )
        )
        assert statuses.count(IdentityReviewStatus.RESOLVED) == 3
        assert statuses.count(IdentityReviewStatus.REJECTED) == 1


def test_identity_review_apply_changed_replay_is_sanitized_conflict(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    item_ids, _ = _seed_reviews(cli_environment)
    path = tmp_path / "decisions.json"
    original = [_decision(item_ids["Delta Rejected"], "reject")]
    path.write_text(json.dumps(original), encoding="utf-8")
    assert run_cli("identity-review-apply", str(path), environment=cli_environment).returncode == 0
    hostile = "HostileSecretReason"
    changed = [_decision(item_ids["Delta Rejected"], "reject", reason=hostile)]
    path.write_text(json.dumps(changed), encoding="utf-8")

    result = run_cli("identity-review-apply", str(path), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: identity review conflict\n"
    assert hostile not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("content", "binary"),
    (
        (b"\xff", True),
        (
            json.dumps(
                [
                    {
                        **_decision(UUID(int=1), "reject"),
                        "unexpected_secret": "do-not-echo",
                    }
                ]
            ),
            False,
        ),
    ),
)
def test_identity_review_apply_rejects_non_utf8_and_extra_fields(
    cli_environment: dict[str, str],
    tmp_path: Path,
    content: bytes | str,
    binary: bool,
) -> None:
    path = tmp_path / "invalid-decisions.json"
    if binary:
        assert isinstance(content, bytes)
        path.write_bytes(content)
    else:
        assert isinstance(content, str)
        path.write_text(content, encoding="utf-8")

    result = run_cli("identity-review-apply", str(path), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: identity review input is invalid\n"
    assert "do-not-echo" not in result.stderr


def test_identity_review_apply_rejects_oversized_input_before_json_parse(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    path = tmp_path / "oversized-decisions.json"
    with path.open("wb") as output:
        output.seek(MAX_INPUT_BYTES)
        output.write(b"]")

    result = run_cli("identity-review-apply", str(path), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: identity review input is invalid\n"


def test_identity_work_paths_reject_repository_targets_through_symlinks(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    repository_link = tmp_path / "repository-link"
    try:
        repository_link.symlink_to(BACKEND_ROOT, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    output_result = run_cli(
        "company-identity-audit",
        str(repository_link / "audit.json"),
        environment=cli_environment,
    )
    input_result = run_cli(
        "identity-review-apply",
        str(repository_link / "pyproject.toml"),
        environment=cli_environment,
    )

    for result in (output_result, input_result):
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == (
            "manifest command failed: identity work path must be outside repository\n"
        )


def test_identity_work_paths_reject_direct_repository_targets(
    cli_environment: dict[str, str],
) -> None:
    output_result = run_cli(
        "company-identity-audit", str(BACKEND_ROOT), environment=cli_environment
    )
    input_result = run_cli(
        "identity-review-apply",
        str(BACKEND_ROOT / "pyproject.toml"),
        environment=cli_environment,
    )

    for result in (output_result, input_result):
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == (
            "manifest command failed: identity work path must be outside repository\n"
        )


def test_identity_output_failure_cleans_only_owned_sibling_temp(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    output_directory = tmp_path / "blocked-output"
    output_directory.mkdir()
    unrelated = tmp_path / "keep.unrelated.tmp"
    unrelated.write_text("keep", encoding="utf-8")

    result = run_cli("identity-review-export", str(output_directory), environment=cli_environment)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: artifact write failed\n"
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob("blocked-output.*.tmp")) == []


def test_company_identity_audit_writes_sorted_empty_report(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    output = tmp_path / "identity-audit.json"

    result = run_cli("company-identity-audit", str(output), environment=cli_environment)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '{"findings":0,"output":"identity-audit.json"}\n'
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report == {
        "finding_counts": {"critical": 0, "important": 0, "minor": 0},
        "findings": [],
        "scanned_aliases": 0,
        "scanned_companies": 0,
        "scanned_review_items": 0,
    }
    assert report_text == json.dumps(
        report, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def test_company_identity_audit_reports_unavailable_similarity_without_schema_change(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    with next(_session(cli_environment)) as session:
        session.add(
            Company(
                canonical_name="Alpha Labs",
                normalized_name="alphalabs",
                funding_stage="unknown",
                scale="unknown",
            )
        )
        session.commit()
    output = tmp_path / "identity-audit.json"

    result = run_cli("company-identity-audit", str(output), environment=cli_environment)

    assert result.returncode == 0
    assert result.stderr == ""
    assert _assert_sorted_object(result.stdout) == {
        "findings": 1,
        "output": "identity-audit.json",
    }
    report = json.loads(output.read_text(encoding="utf-8"))
    assert [finding["code"] for finding in report["findings"]] == ["similarity_search_unavailable"]
    assert set(report) == {
        "finding_counts",
        "findings",
        "scanned_aliases",
        "scanned_companies",
        "scanned_review_items",
    }


def test_identity_cli_database_diagnostic_redacts_url_and_traceback(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    secret = "operator-secret-password"
    environment = {
        **cli_environment,
        "DATABASE_URL": f"postgresql://operator:{secret}@127.0.0.1:1/identity",
    }

    result = run_cli(
        "company-identity-audit",
        str(tmp_path / "audit.json"),
        environment=environment,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: database unavailable\n"
    assert secret not in result.stderr
    assert "postgresql://" not in result.stderr
    assert "Traceback" not in result.stderr


def test_identity_review_service_database_failure_uses_retryable_cli_contract(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    secret = "private-database-directory"
    missing_database = tmp_path / secret / "identity.sqlite3"
    environment = {
        **cli_environment,
        "DATABASE_URL": f"sqlite:///{missing_database.as_posix()}",
    }
    result = run_cli(
        "identity-review-export",
        str(tmp_path / "reviews.json"),
        environment=environment,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: database unavailable\n"
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr


def test_manifest_cli_import_stays_offline() -> None:
    script = """
import json
import sys
import app.manifest.cli
blocked = ('celery', 'redis', 'openai', 'playwright', 'selenium')
print(json.dumps(sorted(name for name in sys.modules if name.split('.')[0] in blocked)))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == []
