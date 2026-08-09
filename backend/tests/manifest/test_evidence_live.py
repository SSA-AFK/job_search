import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.ingestion.errors import ExtractionError
from app.manifest import cli as manifest_cli
from app.manifest import service as manifest_service
from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    DiscoveryStatus,
    SourceClass,
    SourceRegistry,
    SourceRegistryEntry,
    SourceRole,
)
from app.manifest.evidence_live import (
    EvidenceBatchGate,
    EvidenceCandidateInput,
    EvidenceInputError,
    evaluate_evidence_batch,
    parse_evidence_candidates,
)
from app.manifest.evidence_policy import AuditStratum, EvidenceAcceptancePolicy
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    EntryDiscoveryRound,
    EntryEvidenceAuditSample,
)
from app.models import Company
from app.models.base import Base

MANIFEST_VERSION = "c" * 64


class FakeLlm:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def candidate(**overrides: object) -> EvidenceCandidateInput:
    values: dict[str, object] = {
        "company_id": UUID(int=101),
        "source_url": "https://association.example.org/members/acme",
        "candidate_url": "https://jobs.acme.example/careers",
        "title": "Acme AI careers",
        "summary": "Acme AI builds foundation models.",
        "anchor": "Join Acme",
        "source_id": "public_registry",
        "platform": "moka",
        "source_registered": True,
        "robots_approved": True,
        "ownership_evidence": "official_navigation_anchor:Careers",
    }
    values.update(overrides)
    return EvidenceCandidateInput(**values)


def enabled_gate(**overrides: object) -> EvidenceBatchGate:
    values: dict[str, object] = {
        "cli_live": True,
        "cli_model": True,
        "config_live": True,
        "config_model": True,
        "dry_run": False,
    }
    values.update(overrides)
    return EvidenceBatchGate(**values)


def assessment_json(**overrides: object) -> str:
    values: dict[str, object] = {
        "confidence": "0.96",
        "rationale": "The public evidence consistently identifies the company entry.",
        "risk_labels": [],
    }
    values.update(overrides)
    return json.dumps(values)


def test_evidence_input_is_strict_and_bounded_to_twenty_items() -> None:
    serialized = candidate().model_dump(mode="json")
    serialized["unexpected"] = "not allowed"

    with pytest.raises(EvidenceInputError, match="evidence input is invalid"):
        parse_evidence_candidates(json.dumps([serialized]).encode())

    serialized.pop("unexpected")
    with pytest.raises(EvidenceInputError, match="evidence input exceeds item limit"):
        parse_evidence_candidates(json.dumps([serialized] * 21).encode())


def test_evidence_input_rejects_secret_bearing_public_urls() -> None:
    with pytest.raises(ValueError, match="sensitive"):
        candidate(candidate_url="https://jobs.acme.example/careers?token=super-secret")


def test_evidence_input_rejects_sensitive_ownership_unknown_platform_and_duplicate_url() -> None:
    with pytest.raises(ValueError, match="public diagnostics"):
        candidate(ownership_evidence="api_key=super-secret")
    with pytest.raises(ValueError, match="known platform"):
        candidate(platform="unknown")

    duplicate_url = candidate(company_id=UUID(int=102)).model_dump(mode="json")
    with pytest.raises(EvidenceInputError, match="evidence input is invalid"):
        parse_evidence_candidates(
            json.dumps([candidate().model_dump(mode="json"), duplicate_url]).encode()
        )


@pytest.mark.parametrize(
    "gate",
    (
        enabled_gate(cli_live=False),
        enabled_gate(cli_model=False),
        enabled_gate(config_live=False),
        enabled_gate(config_model=False),
    ),
)
def test_disabled_execution_gate_never_calls_model(gate: EvidenceBatchGate) -> None:
    llm = FakeLlm(assessment_json())

    with pytest.raises(PermissionError, match="live evidence execution is disabled"):
        asyncio.run(
            evaluate_evidence_batch(
                (candidate(),),
                llm_client=llm,
                policy=EvidenceAcceptancePolicy(),
                gate=gate,
            )
        )

    assert llm.prompts == []


def test_dry_run_never_calls_model_even_when_every_opt_in_is_enabled() -> None:
    llm = FakeLlm(assessment_json())

    evaluations = asyncio.run(
        evaluate_evidence_batch(
            (candidate(),),
            llm_client=llm,
            policy=EvidenceAcceptancePolicy(),
            gate=enabled_gate(dry_run=True),
        )
    )

    assert evaluations == ()
    assert llm.prompts == []


def test_model_receives_only_the_public_projection_and_returns_strict_assessment() -> None:
    llm = FakeLlm(assessment_json())

    evaluations = asyncio.run(
        evaluate_evidence_batch(
            (candidate(),),
            llm_client=llm,
            policy=EvidenceAcceptancePolicy(confidence_threshold=Decimal("0.90")),
            gate=enabled_gate(),
        )
    )

    assert len(evaluations) == 1
    assert evaluations[0].decision.status is CandidateDecisionStatus.ACCEPTED
    assert evaluations[0].decision.reason_code == "accepted"
    assert evaluations[0].model_called is True
    prompt = json.loads(llm.prompts[0])
    assert prompt["evidence"] == {
        "anchor_text": "Join Acme",
        "candidate_url": "https://jobs.acme.example/careers",
        "page_title": "Acme AI careers",
        "source_url": "https://association.example.org/members/acme",
        "visible_summary": "Acme AI builds foundation models.",
    }
    assert "ownership_evidence" not in llm.prompts[0]
    assert "source_registered" not in llm.prompts[0]


def test_hard_rejection_and_paused_stratum_do_not_call_model() -> None:
    llm = FakeLlm(assessment_json(), assessment_json())
    policy = EvidenceAcceptancePolicy(
        paused_strata=frozenset({AuditStratum(source_id="paused_registry", platform="moka")})
    )

    evaluations = asyncio.run(
        evaluate_evidence_batch(
            (
                candidate(robots_approved=False),
                candidate(source_id="paused_registry"),
            ),
            llm_client=llm,
            policy=policy,
            gate=enabled_gate(),
        )
    )

    assert [item.decision.reason_code for item in evaluations] == [
        "robots_disallowed",
        "audit_stratum_paused",
    ]
    assert [item.model_called for item in evaluations] == [False, False]
    assert llm.prompts == []


@pytest.mark.parametrize(
    ("response", "reason_code"),
    (
        ("DATABASE_URL=postgresql://operator:super-secret@db/jobs", "model_invalid_output"),
        (
            ExtractionError(
                code="model_unavailable",
                detail="api_key=super-secret",
            ),
            "model_unavailable",
        ),
    ),
)
def test_model_failures_are_reduced_to_sanitized_reason_codes(
    response: str | Exception, reason_code: str
) -> None:
    llm = FakeLlm(response)

    evaluations = asyncio.run(
        evaluate_evidence_batch(
            (candidate(),),
            llm_client=llm,
            policy=EvidenceAcceptancePolicy(),
            gate=enabled_gate(),
        )
    )

    serialized = evaluations[0].model_dump_json()
    assert evaluations[0].decision.status is CandidateDecisionStatus.REJECTED
    assert evaluations[0].decision.reason_code == reason_code
    assert "super-secret" not in serialized
    assert "postgresql://" not in serialized


@pytest.fixture
def live_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sessionmaker, Path, SimpleNamespace]:
    database_path = tmp_path / "evidence-live.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        company = Company(
            id=UUID(int=101),
            canonical_name="Acme AI",
            normalized_name="acme-ai",
        )
        session.add(company)
        session.add(
            CompanyManifest(
                version=MANIFEST_VERSION,
                config_fingerprint="a" * 64,
                member_count=1,
                canonical_quota={"foundation_models": 1},
                frozen_at=datetime(2026, 8, 9, 8, tzinfo=UTC),
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
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps([candidate().model_dump(mode="json")]), encoding="utf-8")
    settings = SimpleNamespace(
        entry_evidence_model_enabled=True,
        entry_evidence_model_name="qwen-plus",
        entry_evidence_model_confidence_threshold=Decimal("0.90"),
        gate1_live_discovery_enabled=True,
        gate1_source_registry_path="unused.json",
        openai_compatible_api_key="test-secret",
        openai_compatible_base_url="https://dashscope.example/v1",
        openai_request_timeout_seconds=5.0,
    )
    registry = SourceRegistry(
        entries=(
            SourceRegistryEntry(
                id="public_registry",
                name="Public association registry",
                base_url="https://association.example.org/members/acme",
                source_class=SourceClass.ASSOCIATION,
                authorization_basis="Public association member evidence page.",
                robots_policy="required",
                roles=frozenset({SourceRole.CANDIDATE_POOL}),
                requests_per_second="0.5",
                rehearsal_request_budget=20,
            ),
        )
    )
    monkeypatch.setattr(manifest_cli, "_load_settings", lambda: settings)
    monkeypatch.setattr(manifest_cli, "_session_factory", lambda: factory)
    monkeypatch.setattr(manifest_cli, "_load_registry", lambda _path: registry)
    yield factory, evidence_path, settings
    engine.dispose()


def operator_args(evidence_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "manifest": MANIFEST_VERSION,
        "manifest_file": None,
        "round_name": "entry-evidence-live-01",
        "model": True,
        "live": True,
        "dry_run": False,
        "evidence_input": evidence_path,
        "limit": 20,
        "registry": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_cli_parser_requires_explicit_bounded_live_evidence_arguments() -> None:
    parsed = manifest_cli._parser().parse_args(
        [
            "evidence-regenerate",
            "--manifest",
            MANIFEST_VERSION,
            "--round-name",
            "entry-evidence-live-01",
            "--evidence-input",
            "D:/external/evidence.json",
            "--limit",
            "20",
            "--live",
            "--model",
        ]
    )

    assert parsed.live is True
    assert parsed.model is True
    assert parsed.limit == 20
    assert parsed.evidence_input == Path("D:/external/evidence.json")


def test_live_operator_persists_observation_and_audit_then_honors_pause(
    live_operator: tuple[sessionmaker, Path, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, evidence_path, _settings = live_operator
    first_llm = FakeLlm(assessment_json())
    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", lambda _settings: first_llm)

    first = manifest_cli._evidence_regenerate(operator_args(evidence_path))

    assert first["processed"] == 1
    assert first["model_calls"] == 1
    assert first["audit_samples"] == 1
    assert first["status_counts"] == {"accepted": 1}
    assert "test-secret" not in json.dumps(first, default=str)
    with factory() as session:
        first_round = session.scalar(
            select(EntryDiscoveryRound).where(EntryDiscoveryRound.name == "entry-evidence-live-01")
        )
        observation = session.scalar(
            select(EntryDiscoveryObservation).where(
                EntryDiscoveryObservation.discovery_round_id == first_round.id
            )
        )
        sample = session.scalar(
            select(EntryEvidenceAuditSample).where(
                EntryEvidenceAuditSample.discovery_round_id == first_round.id
            )
        )
    assert observation.status is DiscoveryStatus.ACCEPTED
    assert observation.method == "entry_evidence_model"
    assert sample.observation_id == observation.id

    manifest_service.record_evidence_audit_finding(
        factory(),
        audit_sample_id=sample.id,
        severe_error=True,
        reason="The sampled URL belongs to another legal entity.",
        audited_at=sample.selected_at,
    )
    with factory.begin() as session:
        stored_predecessor = session.get(EntryDiscoveryObservation, observation.id)
        assert stored_predecessor is not None
        stored_predecessor.observed_at = datetime(
            2030, 1, 1, tzinfo=UTC, microsecond=999_999
        )
    paused_llm = FakeLlm(assessment_json())
    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", lambda _settings: paused_llm)

    second = manifest_cli._evidence_regenerate(
        operator_args(evidence_path, round_name="entry-evidence-live-02")
    )

    assert second["processed"] == 1
    assert second["model_calls"] == 0
    assert second["audit_samples"] == 0
    assert second["status_counts"] == {"review_required": 1}
    assert paused_llm.prompts == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EntryDiscoveryRound)) == 2
        assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 2
        observations = tuple(
            session.scalars(
                select(EntryDiscoveryObservation).order_by(EntryDiscoveryObservation.observed_at)
            )
        )
    assert observations[0].status is DiscoveryStatus.ACCEPTED
    assert observations[1].status is DiscoveryStatus.REVIEW_REQUIRED
    assert observations[1].predecessor_observation_id == observations[0].id


def test_cli_live_gate_failure_does_not_construct_client_or_create_round(
    live_operator: tuple[sessionmaker, Path, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, evidence_path, settings = live_operator
    settings.gate1_live_discovery_enabled = False
    constructed = False

    def forbidden_client(_settings: object) -> FakeLlm:
        nonlocal constructed
        constructed = True
        return FakeLlm(assessment_json())

    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", forbidden_client)

    with pytest.raises(
        manifest_cli.ManifestCommandError, match="live evidence execution is disabled"
    ):
        manifest_cli._evidence_regenerate(operator_args(evidence_path))

    assert constructed is False
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EntryDiscoveryRound)) == 0


def test_cli_dry_run_does_not_construct_client_or_write_database(
    live_operator: tuple[sessionmaker, Path, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, evidence_path, _settings = live_operator

    def forbidden_client(_settings: object) -> FakeLlm:
        raise AssertionError("dry-run must not construct a model client")

    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", forbidden_client)

    result = manifest_cli._evidence_regenerate(operator_args(evidence_path, dry_run=True))

    assert result["would_process"] == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EntryDiscoveryRound)) == 0
        assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 0


def test_unregistered_source_is_blocked_before_model_call(
    live_operator: tuple[sessionmaker, Path, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, evidence_path, _settings = live_operator
    evidence_path.write_text(
        json.dumps(
            [
                candidate(
                    source_url="https://association.example.org/unregistered/acme"
                ).model_dump(mode="json")
            ]
        ),
        encoding="utf-8",
    )
    llm = FakeLlm(assessment_json())
    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", lambda _settings: llm)

    result = manifest_cli._evidence_regenerate(operator_args(evidence_path))

    assert result["model_calls"] == 0
    assert result["status_counts"] == {"blocked": 1}
    assert llm.prompts == []
    with factory() as session:
        observation = session.scalar(select(EntryDiscoveryObservation))
    assert observation.error_code == "unregistered_source"
    assert observation.method == "entry_evidence_policy"


def test_malicious_model_output_persists_only_sanitized_failure(
    live_operator: tuple[sessionmaker, Path, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, evidence_path, _settings = live_operator
    secret_output = "DATABASE_URL=postgresql://operator:super-secret@db/jobs"
    llm = FakeLlm(secret_output)
    monkeypatch.setattr(manifest_cli, "_evidence_llm_client", lambda _settings: llm)

    result = manifest_cli._evidence_regenerate(operator_args(evidence_path))

    serialized = json.dumps(result, default=str)
    assert result["status_counts"] == {"failed": 1}
    assert "super-secret" not in serialized
    assert "postgresql://" not in serialized
    with factory() as session:
        observation = session.scalar(select(EntryDiscoveryObservation))
    assert observation.error_code == "model_invalid_output"
    assert observation.ownership_evidence == "official_navigation_anchor:Careers"
