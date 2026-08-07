from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.contracts import ProviderQuery, ProviderResult
from app.manifest import cli as manifest_cli
from app.manifest.cli import _run_discovery, _ZhihuFallbackDiscoverer
from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    EntryDiscoveryResult,
    ManifestCompany,
)
from app.manifest.models import (
    CandidateFact,
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)
from app.models import Base, Company

BACKEND_ROOT = Path(__file__).parents[2]
MANIFEST_VERSION = "c" * 64


def _registry_document() -> dict[str, object]:
    return {
        "entries": [
            {
                "id": "official_list",
                "name": "Official public list",
                "base_url": "https://example.com/public-list",
                "source_class": "government",
                "authorization_basis": "Public list approved for Gate 1 rehearsal.",
                "robots_policy": "required",
                "roles": ["candidate_pool"],
                "requests_per_second": "1.0",
                "rehearsal_request_budget": 100,
                "enabled": True,
            }
        ]
    }


@pytest.fixture
def cli_environment(tmp_path: Path) -> Iterator[dict[str, str]]:
    database_path = tmp_path / "manifest-cli.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    registry_path = tmp_path / "source_registry.json"
    registry_path.write_text(json.dumps(_registry_document()), encoding="utf-8")
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "GATE1_SOURCE_REGISTRY_PATH": str(registry_path),
        "GATE1_LIVE_DISCOVERY_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8",
    }
    yield environment


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


def assert_sorted_json_object(stdout: str) -> dict[str, object]:
    assert stdout.endswith("\n")
    assert stdout.count("\n") == 1
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    assert list(payload) == sorted(payload)
    return payload


def _candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source_id": "official_list",
        "source_url": "https://example.com/public-list/company-1",
        "retrieved_at": "2026-08-07T08:00:00Z",
        "canonical_name": "Acme AI",
        "aliases": ["Acme"],
        "primary_category": "foundation_models",
        "official_website": "https://acme.example/about",
        "recruitment_url": "https://jobs.feishu.cn/acme",
        "evidence_summary": "Public registry evidence for Acme AI.",
    }
    value.update(overrides)
    return value


def _database_session(environment: dict[str, str]) -> Iterator[Session]:
    engine = create_engine(environment["DATABASE_URL"])
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


def _seed_resolved_candidates(environment: dict[str, str], *, accepted: int) -> None:
    with next(_database_session(environment)) as session:
        companies: list[Company] = []
        facts: list[CandidateFact] = []
        for identity in range(1, accepted + 1):
            category = tuple(AiCategory)[(identity - 1) % len(AiCategory)]
            company = Company(
                id=UUID(int=identity),
                canonical_name=f"Company {identity:04d}",
                normalized_name=f"company {identity:04d}",
            )
            companies.append(company)
            facts.append(
                CandidateFact(
                    id=UUID(int=10_000 + identity),
                    stable_evidence_id=f"{identity:064x}",
                    canonical_name=company.canonical_name,
                    normalized_name=company.normalized_name,
                    aliases=[],
                    primary_category=category,
                    official_website=f"https://company-{identity}.example/about",
                    recruitment_url=f"https://company-{identity}.example/jobs",
                    source_id="official_list",
                    source_url=f"https://example.com/public-list/{identity}",
                    retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
                    evidence_summary="Reviewed public registry evidence.",
                    confidence_tier=ConfidenceTier.HIGH,
                    confidence_reason="government source includes an official website",
                    decision_status=CandidateDecisionStatus.ACCEPTED,
                    company_id=company.id,
                )
            )
        session.add_all(companies)
        session.flush()
        session.add_all(facts)
        session.commit()


def test_registry_check_emits_one_sorted_json_object(
    cli_environment: dict[str, str],
) -> None:
    result = run_cli("registry-check", environment=cli_environment)

    assert result.returncode == 0
    assert result.stderr == ""
    assert assert_sorted_json_object(result.stdout) == {"entries": 1}


def test_candidate_import_validates_entire_external_jsonl_before_database_write(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        json.dumps(_candidate()) + "\n" + json.dumps({"source_id": "secret-token"}) + "\n",
        encoding="utf-8",
    )

    result = run_cli("candidate-import", str(candidates), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: candidate input is invalid\n"
    with next(_database_session(cli_environment)) as session:
        assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_candidate_import_rejects_paths_inside_repository(
    cli_environment: dict[str, str],
) -> None:
    result = run_cli(
        "candidate-import",
        str(Path(__file__).resolve()),
        environment=cli_environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: candidate path must be outside repository\n"


def test_candidate_import_rejects_effectively_empty_jsonl(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    candidates = tmp_path / "empty.jsonl"
    candidates.write_text(" \n\t\n", encoding="utf-8")

    result = run_cli("candidate-import", str(candidates), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: candidate input is invalid\n"


def test_candidate_import_rejects_unregistered_source_without_echoing_value(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    candidates = tmp_path / "unregistered.jsonl"
    candidates.write_text(
        json.dumps(_candidate(source_id="private_secret_source")) + "\n",
        encoding="utf-8",
    )

    result = run_cli("candidate-import", str(candidates), environment=cli_environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: candidate source is not registered\n"
    assert "private_secret_source" not in result.stderr


def test_review_export_and_apply_round_trip_with_atomic_replacement(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    candidates = tmp_path / "review-candidates.jsonl"
    candidates.write_text(
        json.dumps(
            _candidate(
                recruitment_url="https://boards.greenhouse.io/",
                source_url="https://example.com/public-list/review",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    imported = run_cli("candidate-import", str(candidates), environment=cli_environment)
    review_path = tmp_path / "pending.json"
    review_path.write_text("stale", encoding="utf-8")

    exported = run_cli("review-export", str(review_path), environment=cli_environment)

    assert imported.returncode == 0
    assert assert_sorted_json_object(imported.stdout) == {
        "auto_accepted": 0,
        "created": 1,
        "replayed": 0,
        "review_required": 1,
    }
    assert exported.returncode == 0
    assert assert_sorted_json_object(exported.stdout) == {"review_items": 1}
    review_items = json.loads(review_path.read_text(encoding="utf-8"))
    assert len(review_items) == 1
    evidence_id = review_items[0]["stable_evidence_id"]
    assert list(tmp_path.glob("pending.json.*.tmp")) == []

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            [
                {
                    "stable_evidence_id": evidence_id,
                    "action": "reject",
                    "resulting_status": "rejected",
                    "resolved_company_id": None,
                    "reason": "Recruitment identity is ambiguous.",
                    "decided_at": "2026-08-07T09:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    applied = run_cli("review-apply", str(decisions_path), environment=cli_environment)

    assert applied.returncode == 0
    assert assert_sorted_json_object(applied.stdout) == {"applied": 1, "replayed": 0}
    with next(_database_session(cli_environment)) as session:
        assert session.scalar(select(CandidateFact.decision_status)) is CandidateDecisionStatus.REJECTED


def test_atomic_write_failure_cleans_only_its_sibling_temp(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    output_directory = tmp_path / "blocked-output"
    output_directory.mkdir()
    unrelated = tmp_path / "keep.tmp"
    unrelated.write_text("keep", encoding="utf-8")

    result = run_cli("review-export", str(output_directory), environment=cli_environment)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: artifact write failed\n"
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob("blocked-output.*.tmp")) == []


def test_manifest_freeze_requires_full_denominator_and_writes_no_artifacts(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    quota_path = tmp_path / "manifest.quota.json"

    result = run_cli(
        "manifest-freeze",
        "--manifest-out",
        str(manifest_path),
        "--quota-out",
        str(quota_path),
        "--config-fingerprint",
        "a" * 64,
        environment=cli_environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "manifest command failed: manifest freeze requires at least 1500 accepted identities\n"
    )
    assert not manifest_path.exists()
    assert not quota_path.exists()


def test_manifest_freeze_and_report_replace_individual_artifacts_atomically(
    cli_environment: dict[str, str], tmp_path: Path
) -> None:
    _seed_resolved_candidates(cli_environment, accepted=1500)
    manifest_path = tmp_path / "manifest.json"
    quota_path = tmp_path / "manifest.quota.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text("stale", encoding="utf-8")
    quota_path.write_text("stale", encoding="utf-8")

    frozen = run_cli(
        "manifest-freeze",
        "--manifest-out",
        str(manifest_path),
        "--quota-out",
        str(quota_path),
        "--config-fingerprint",
        "a" * 64,
        environment=cli_environment,
    )

    assert frozen.returncode == 0
    frozen_stdout = assert_sorted_json_object(frozen.stdout)
    assert frozen_stdout["manifest_companies"] == 1000
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quota = json.loads(quota_path.read_text(encoding="utf-8"))
    assert manifest["member_count"] == quota["total"] == 1000
    assert manifest["manifest_version"] == quota["manifest_version"]

    reported = run_cli(
        "report",
        "--manifest-file",
        str(manifest_path),
        "--code-commit",
        "abc1234",
        "--output",
        str(report_path),
        environment=cli_environment,
    )

    assert reported.returncode == 0
    report_stdout = assert_sorted_json_object(reported.stdout)
    assert report_stdout["manifest_companies"] == 1000
    assert json.loads(report_path.read_text(encoding="utf-8")) == report_stdout
    assert list(tmp_path.glob("*.tmp")) == []

    rejected_fingerprint = "b" * 64
    conflicting = run_cli(
        "report",
        "--manifest-file",
        str(manifest_path),
        "--code-commit",
        "abc1234",
        "--config-fingerprint",
        rejected_fingerprint,
        environment=cli_environment,
    )

    assert conflicting.returncode == 2
    assert conflicting.stdout == ""
    assert conflicting.stderr == (
        "manifest command failed: report fingerprint conflicts with frozen manifest\n"
    )
    assert rejected_fingerprint not in conflicting.stderr


def test_discover_requires_double_opt_in(cli_environment: dict[str, str]) -> None:
    environment = {**cli_environment, "GATE1_LIVE_DISCOVERY_ENABLED": "true"}
    result = run_cli(
        "discover", "--manifest", MANIFEST_VERSION, environment=environment
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: live discovery is disabled\n"


def test_discover_still_refuses_when_only_cli_live_flag_is_set(
    cli_environment: dict[str, str],
) -> None:
    result = run_cli(
        "discover",
        "--manifest",
        MANIFEST_VERSION,
        "--live",
        environment=cli_environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: live discovery is disabled\n"


def test_database_error_is_stable_and_redacts_database_url(
    cli_environment: dict[str, str],
) -> None:
    secret_url = "postgresql://" + "operator:super-secret@127.0.0.1:1/gate1"
    environment = {**cli_environment, "DATABASE_URL": secret_url}

    result = run_cli("report", "--manifest", MANIFEST_VERSION, environment=environment)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: database unavailable\n"
    assert "super-secret" not in result.stderr


def test_configuration_error_is_stable_and_redacts_rejected_value(
    cli_environment: dict[str, str],
) -> None:
    rejected = "Bearer " + "super-secret-configuration"
    environment = {**cli_environment, "GATE1_ZHIHU_REQUEST_BUDGET": rejected}

    result = run_cli("registry-check", environment=environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "manifest command failed: configuration is invalid\n"
    assert rejected not in result.stderr


class _EmptyZhihuProvider:
    def __init__(self) -> None:
        self.queries: list[ProviderQuery] = []

    async def search(self, query: ProviderQuery) -> ProviderResult:
        self.queries.append(query)
        return ProviderResult(documents=())


class _RetryingZhihuProvider:
    def __init__(self, before_request: Callable[[], Awaitable[None]]) -> None:
        self._before_request = before_request
        self.attempts = 0

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        for _ in range(4):
            await self._before_request()
            self.attempts += 1
        return ProviderResult(documents=())


def test_zhihu_fallback_shares_start_limiter_and_stops_at_budget() -> None:
    starts = 0

    async def before_search() -> None:
        nonlocal starts
        starts += 1

    counter = manifest_cli._ZhihuRequestBudget(
        request_budget=2,
        before_request=before_search,
    )
    provider = _RetryingZhihuProvider(counter.before_request)
    subject = _ZhihuFallbackDiscoverer(
        provider,
        request_counter=counter,
    )
    company = ManifestCompany(
        company_id=UUID(int=99),
        canonical_name="Budgeted Company",
        primary_category=AiCategory.FOUNDATION_MODELS,
        official_website="https://budgeted.example/about",
    )

    result = asyncio.run(subject.discover(company))

    assert result.status is DiscoveryStatus.BLOCKED
    assert result.error_code == "request_budget_exhausted"
    assert subject.requests == 2
    assert starts == 2
    assert provider.attempts == 2


class _StaticCoordinator:
    def __init__(self, result: EntryDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def discover(self, _company: ManifestCompany) -> EntryDiscoveryResult:
        self.calls += 1
        return self.result


def _discovery_runner_fixture(
    cli_environment: dict[str, str], *, companies: int, shared_host: bool = True
) -> tuple[sessionmaker[Session], tuple[ManifestCompany, ...]]:
    engine = create_engine(cli_environment["DATABASE_URL"])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    manifest_version = "d" * 64
    values = tuple(
        ManifestCompany(
            company_id=UUID(int=20_000 + position),
            canonical_name=f"Shared Host {position}",
            primary_category=AiCategory.FOUNDATION_MODELS,
            official_website=(
                "https://shared.example/about"
                if shared_host
                else f"https://company-{position}.example/about"
            ),
            recruitment_url=(
                f"https://shared.example/jobs/{position}"
                if shared_host
                else f"https://company-{position}.example/jobs"
            ),
        )
        for position in range(1, companies + 1)
    )
    with factory() as session:
        session.add(
            CompanyManifest(
                version=manifest_version,
                config_fingerprint="a" * 64,
                member_count=companies,
                canonical_quota={"foundation_models": companies},
                frozen_at=datetime(2026, 8, 7, tzinfo=UTC),
            )
        )
        session.add_all(
            Company(
                id=company.company_id,
                canonical_name=company.canonical_name,
                normalized_name=f"shared host {position}",
            )
            for position, company in enumerate(values, start=1)
        )
        session.flush()
        session.add_all(
            CompanyManifestMember(
                manifest_version=manifest_version,
                company_id=company.company_id,
                position=position,
                canonical_name=company.canonical_name,
                primary_category=company.primary_category,
                official_website=str(company.official_website),
                recruitment_url=str(company.recruitment_url),
            )
            for position, company in enumerate(values, start=1)
        )
        session.commit()
    return factory, values


def _add_discovery_observation(
    factory: sessionmaker[Session],
    company: ManifestCompany,
    *,
    status: DiscoveryStatus,
    error_code: str,
    minute: int,
    method: str = "official_navigation",
    source_id: str | None = None,
) -> None:
    with factory() as session:
        session.add(
            EntryDiscoveryObservation(
                manifest_version="d" * 64,
                company_id=company.company_id,
                method=method,
                status=status,
                source_id=source_id,
                error_code=error_code,
                observed_at=datetime(2026, 8, 7, 1, minute, tzinfo=UTC),
            )
        )
        session.commit()


def test_resume_preserves_zhihu_source_stop_without_calling_provider(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(cli_environment, companies=1)
    _add_discovery_observation(
        factory,
        companies[0],
        status=DiscoveryStatus.BLOCKED,
        error_code="provider_auth_failed",
        minute=1,
        method="zhihu_global_search",
        source_id="zhihu_global_search",
    )
    _loaded, state = manifest_cli._load_discovery_members(factory, "d" * 64)
    starts = 0

    async def before_request() -> None:
        nonlocal starts
        starts += 1

    counter = manifest_cli._ZhihuRequestBudget(
        request_budget=2,
        before_request=before_request,
    )
    provider = _EmptyZhihuProvider()
    subject = _ZhihuFallbackDiscoverer(
        provider,
        request_counter=counter,
        stopped="zhihu_global_search" in state.stopped_source_ids,
    )

    result = asyncio.run(subject.discover(companies[0]))

    assert result.status is DiscoveryStatus.BLOCKED
    assert result.error_code == "fallback_source_stopped"
    assert provider.queries == []
    assert starts == 0


def test_resume_reprocesses_retryable_failures_and_skips_stable_observations(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(
        cli_environment,
        companies=3,
        shared_host=False,
    )
    _add_discovery_observation(
        factory,
        companies[0],
        status=DiscoveryStatus.FAILED,
        error_code="total_timeout",
        minute=1,
    )
    _add_discovery_observation(
        factory,
        companies[1],
        status=DiscoveryStatus.BLOCKED,
        error_code="provider_rate_limited",
        minute=2,
    )
    _add_discovery_observation(
        factory,
        companies[2],
        status=DiscoveryStatus.NOT_FOUND,
        error_code="recruitment_entry_not_found",
        minute=3,
    )
    environment = {**cli_environment, "GATE1_LIVE_DISCOVERY_ENABLED": "true"}

    result = run_cli(
        "discover",
        "--manifest",
        "d" * 64,
        "--resume",
        "--live",
        environment=environment,
    )

    assert result.returncode == 0
    payload = assert_sorted_json_object(result.stdout)
    assert payload["processed"] == 2
    assert payload["skipped"] == 1
    with factory() as session:
        accepted = tuple(
            session.scalars(
                select(EntryDiscoveryObservation.company_id).where(
                    EntryDiscoveryObservation.status == DiscoveryStatus.ACCEPTED
                )
            )
        )
    assert set(accepted) == {companies[0].company_id, companies[1].company_id}


def test_resume_preserves_existing_access_stop_for_same_domain(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(cli_environment, companies=2)
    _add_discovery_observation(
        factory,
        companies[0],
        status=DiscoveryStatus.BLOCKED,
        error_code="provider_access_denied",
        minute=1,
    )
    environment = {**cli_environment, "GATE1_LIVE_DISCOVERY_ENABLED": "true"}

    result = run_cli(
        "discover",
        "--manifest",
        "d" * 64,
        "--resume",
        "--live",
        environment=environment,
    )

    assert result.returncode == 0
    assert assert_sorted_json_object(result.stdout)["processed"] == 1
    with factory() as session:
        second_error = session.scalar(
            select(EntryDiscoveryObservation.error_code).where(
                EntryDiscoveryObservation.company_id == companies[1].company_id
            )
        )
    assert second_error == "source_access_stopped"


def test_resume_rebuilds_only_consecutive_rate_limit_state(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(cli_environment, companies=6)
    _add_discovery_observation(
        factory,
        companies[0],
        status=DiscoveryStatus.BLOCKED,
        error_code="provider_rate_limited",
        minute=1,
    )
    _add_discovery_observation(
        factory,
        companies[1],
        status=DiscoveryStatus.NOT_FOUND,
        error_code="recruitment_entry_not_found",
        minute=2,
    )
    _add_discovery_observation(
        factory,
        companies[2],
        status=DiscoveryStatus.BLOCKED,
        error_code="provider_rate_limited",
        minute=3,
    )
    loaded_companies, state = manifest_cli._load_discovery_members(
        factory, "d" * 64
    )
    coordinator = _StaticCoordinator(
        EntryDiscoveryResult(
            status=DiscoveryStatus.BLOCKED,
            method="official_navigation",
            error_code="provider_rate_limited",
        )
    )

    counts = asyncio.run(
        manifest_cli._run_discovery(
            SessionLocal=factory,
            manifest_version="d" * 64,
            companies=loaded_companies[3:],
            state=state,
            coordinator=coordinator,
            limit=None,
        )
    )

    assert coordinator.calls == 2
    assert counts == {DiscoveryStatus.BLOCKED: 3}


def test_discover_with_both_opt_ins_uses_evidenced_urls_and_resumes_positions(
    cli_environment: dict[str, str],
) -> None:
    factory, _companies = _discovery_runner_fixture(cli_environment, companies=2)
    environment = {**cli_environment, "GATE1_LIVE_DISCOVERY_ENABLED": "true"}

    first = run_cli(
        "discover",
        "--manifest",
        "d" * 64,
        "--limit",
        "1",
        "--live",
        environment=environment,
    )
    resumed = run_cli(
        "discover",
        "--manifest",
        "d" * 64,
        "--limit",
        "1",
        "--resume",
        "--live",
        environment=environment,
    )

    assert first.returncode == 0
    assert assert_sorted_json_object(first.stdout)["processed"] == 1
    resumed_payload = assert_sorted_json_object(resumed.stdout)
    assert resumed.returncode == 0
    assert resumed_payload["processed"] == 1
    assert resumed_payload["skipped"] == 1
    with factory() as session:
        observations = tuple(
            session.scalars(
                select(EntryDiscoveryObservation).order_by(
                    EntryDiscoveryObservation.observed_at
                )
            )
        )
    assert [observation.company_id for observation in observations] == [
        UUID(int=20_001),
        UUID(int=20_002),
    ]


def test_discovery_stops_same_domain_after_access_denial(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(cli_environment, companies=2)
    coordinator = _StaticCoordinator(
        EntryDiscoveryResult(
            status=DiscoveryStatus.BLOCKED,
            method="official_navigation",
            error_code="provider_access_denied",
        )
    )

    counts = asyncio.run(
        _run_discovery(
            SessionLocal=factory,
            manifest_version="d" * 64,
            companies=companies,
            already_observed=frozenset(),
            coordinator=coordinator,
            limit=None,
        )
    )

    assert coordinator.calls == 1
    assert counts == {DiscoveryStatus.BLOCKED: 2}
    with factory() as session:
        errors = tuple(
            session.scalars(
                select(EntryDiscoveryObservation.error_code).order_by(
                    EntryDiscoveryObservation.observed_at
                )
            )
        )
    assert set(errors) == {"provider_access_denied", "source_access_stopped"}


def test_discovery_stops_same_domain_after_three_rate_limits(
    cli_environment: dict[str, str],
) -> None:
    factory, companies = _discovery_runner_fixture(cli_environment, companies=4)
    coordinator = _StaticCoordinator(
        EntryDiscoveryResult(
            status=DiscoveryStatus.BLOCKED,
            method="official_navigation",
            error_code="provider_rate_limited",
        )
    )

    counts = asyncio.run(
        _run_discovery(
            SessionLocal=factory,
            manifest_version="d" * 64,
            companies=companies,
            already_observed=frozenset(),
            coordinator=coordinator,
            limit=None,
        )
    )

    assert coordinator.calls == 3
    assert counts == {DiscoveryStatus.BLOCKED: 4}
