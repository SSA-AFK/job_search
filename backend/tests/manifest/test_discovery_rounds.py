from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.manifest import models as manifest_models
from app.manifest import service as manifest_service
from app.manifest.contracts import (
    AiCategory,
    AtsClassification,
    DiscoveryStatus,
    EntryDiscoveryResult,
    RecordDiscoveryCommand,
)
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    EntryEvidenceAuditSample,
    EntryEvidenceQuarantine,
)
from app.manifest.service import DiscoveryRecordConflict, record_discovery_result
from app.models import Base, Company

MANIFEST_VERSION = "d" * 64
COMPANY_ID = UUID(int=92_001)
OBSERVED_AT = datetime(2026, 8, 9, 1, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def seed_manifest(session: Session) -> None:
    company = Company(
        id=COMPANY_ID,
        canonical_name="Round Company",
        normalized_name="round-company",
    )
    session.add_all(
        [
            company,
            CompanyManifest(
                version=MANIFEST_VERSION,
                config_fingerprint="a" * 64,
                member_count=1,
                canonical_quota={"foundation_models": 1},
                frozen_at=datetime(2026, 8, 8, tzinfo=UTC),
            ),
        ]
    )
    session.flush()
    session.add(
        CompanyManifestMember(
            manifest_version=MANIFEST_VERSION,
            company_id=COMPANY_ID,
            position=1,
            canonical_name=company.canonical_name,
            primary_category=AiCategory.FOUNDATION_MODELS,
        )
    )
    session.commit()


def command(
    *,
    status: DiscoveryStatus,
    observed_at: datetime,
    error_code: str | None = None,
) -> RecordDiscoveryCommand:
    accepted = status is DiscoveryStatus.ACCEPTED
    return RecordDiscoveryCommand(
        manifest_version=MANIFEST_VERSION,
        company_id=COMPANY_ID,
        observed_at=observed_at,
        result=EntryDiscoveryResult(
            status=status,
            method="official_navigation",
            candidate_url="https://jobs.example.test/acme" if accepted else None,
            normalized_url="https://jobs.example.test/acme" if accepted else None,
            source_id="public_registry" if accepted else None,
            ownership_evidence="official_navigation_anchor:Careers" if accepted else None,
            classification=(
                AtsClassification(platform="custom", requires_rendering=False) if accepted else None
            ),
            error_code=error_code,
        ),
    )


def test_new_round_appends_successor_without_overwriting_prior_observation(
    session: Session,
) -> None:
    seed_manifest(session)
    prior = record_discovery_result(
        session,
        command(
            status=DiscoveryStatus.NOT_FOUND,
            observed_at=OBSERVED_AT,
            error_code="recruitment_entry_not_found",
        ),
    )
    create_round = manifest_service.create_discovery_round
    record_in_round = manifest_service.record_discovery_result_in_round
    round_summary = create_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-2026-08-09",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT + timedelta(minutes=1),
    )
    successor_command = command(
        status=DiscoveryStatus.ACCEPTED,
        observed_at=OBSERVED_AT + timedelta(minutes=2),
    )

    successor = record_in_round(
        session,
        round_id=round_summary.round_id,
        command=successor_command,
        predecessor_observation_id=prior.observation_id,
    )
    replay = record_in_round(
        session,
        round_id=round_summary.round_id,
        command=successor_command,
        predecessor_observation_id=prior.observation_id,
    )

    prior_row = session.get(EntryDiscoveryObservation, prior.observation_id)
    successor_row = session.get(EntryDiscoveryObservation, successor.observation_id)
    assert prior_row is not None
    assert prior_row.status is DiscoveryStatus.NOT_FOUND
    assert prior_row.error_code == "recruitment_entry_not_found"
    assert prior_row.observed_at == OBSERVED_AT
    assert successor_row is not None
    assert successor_row.id != prior_row.id
    assert successor_row.discovery_round_id == round_summary.round_id
    assert successor_row.predecessor_observation_id == prior_row.id
    assert successor_row.status is DiscoveryStatus.ACCEPTED
    assert replay.observation_id == successor.observation_id
    assert replay.observation_created is False
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 2


def test_round_rejects_conflicting_second_result_without_mutating_first(
    session: Session,
) -> None:
    seed_manifest(session)
    round_summary = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-2026-08-09",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT,
    )
    original = command(
        status=DiscoveryStatus.NOT_FOUND,
        observed_at=OBSERVED_AT + timedelta(minutes=1),
        error_code="recruitment_entry_not_found",
    )
    first = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=original,
    )

    with pytest.raises(DiscoveryRecordConflict, match="round already has a different result"):
        manifest_service.record_discovery_result_in_round(
            session,
            round_id=round_summary.round_id,
            command=command(
                status=DiscoveryStatus.FAILED,
                observed_at=OBSERVED_AT + timedelta(minutes=2),
                error_code="request_timeout",
            ),
        )

    stored = session.get(EntryDiscoveryObservation, first.observation_id)
    assert stored is not None
    assert stored.status is DiscoveryStatus.NOT_FOUND
    assert stored.error_code == "recruitment_entry_not_found"
    assert stored.observed_at == original.observed_at
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 1


def test_round_and_audit_models_are_registered_in_base_metadata() -> None:
    expected = {
        "entry_discovery_rounds",
        "entry_evidence_audit_samples",
        "entry_evidence_audit_findings",
    }

    assert expected <= set(Base.metadata.tables)
    assert manifest_models.EntryDiscoveryRound.__tablename__ == "entry_discovery_rounds"


def test_audit_sample_and_finding_are_append_only_and_exactly_replayable(
    session: Session,
) -> None:
    seed_manifest(session)
    round_summary = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-2026-08-09",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT,
    )
    observation = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=command(
            status=DiscoveryStatus.ACCEPTED,
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        ),
    )
    selected_at = OBSERVED_AT + timedelta(minutes=2)
    audited_at = OBSERVED_AT + timedelta(minutes=3)

    sample = manifest_service.record_evidence_audit_sample(
        session,
        round_id=round_summary.round_id,
        observation_id=observation.observation_id,
        source_id="public_registry",
        platform="custom",
        selected_at=selected_at,
    )
    sample_replay = manifest_service.record_evidence_audit_sample(
        session,
        round_id=round_summary.round_id,
        observation_id=observation.observation_id,
        source_id="public_registry",
        platform="custom",
        selected_at=selected_at,
    )
    finding = manifest_service.record_evidence_audit_finding(
        session,
        audit_sample_id=sample.audit_sample_id,
        severe_error=True,
        reason="The sampled entry belongs to another legal entity.",
        audited_at=audited_at,
    )
    finding_replay = manifest_service.record_evidence_audit_finding(
        session,
        audit_sample_id=sample.audit_sample_id,
        severe_error=True,
        reason="The sampled entry belongs to another legal entity.",
        audited_at=audited_at,
    )

    assert sample_replay.audit_sample_id == sample.audit_sample_id
    assert finding_replay.audit_finding_id == finding.audit_finding_id
    assert sample.created is True
    assert sample_replay.created is False
    assert finding.created is True
    assert finding_replay.created is False

    with pytest.raises(DiscoveryRecordConflict, match="audit finding conflicts"):
        manifest_service.record_evidence_audit_finding(
            session,
            audit_sample_id=sample.audit_sample_id,
            severe_error=False,
            reason="No severe issue.",
            audited_at=audited_at + timedelta(minutes=1),
        )

    stored = session.get(manifest_models.EntryEvidenceAuditFinding, finding.audit_finding_id)
    assert stored is not None
    assert stored.severe_error is True
    assert stored.reason == "The sampled entry belongs to another legal entity."
    assert stored.audited_at == audited_at
    quarantine = session.scalar(
        select(EntryEvidenceQuarantine).where(
            EntryEvidenceQuarantine.observation_id == observation.observation_id
        )
    )
    assert quarantine is not None
    assert quarantine.audit_finding_id == finding.audit_finding_id


def test_round_observation_persists_replay_trace_and_rejects_trace_conflict(
    session: Session,
) -> None:
    seed_manifest(session)
    round_summary = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-replay-trace",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT,
    )
    trace = {
        "public_evidence": {
            "source_url": "https://association.example.test/acme",
            "candidate_url": "https://jobs.example.test/acme",
            "page_title": "Acme careers",
            "visible_summary": "Public careers page.",
            "anchor_text": "Careers",
        },
        "model_assessment": {
            "confidence": "0.96",
            "rationale": "The public evidence matches.",
            "risk_labels": [],
        },
        "independent_validation": {"robots": {"allowed": True}},
        "prompt_fingerprint": "1" * 64,
        "schema_fingerprint": "2" * 64,
        "policy_fingerprint": "3" * 64,
        "registry_fingerprint": "4" * 64,
    }
    result_command = command(
        status=DiscoveryStatus.ACCEPTED,
        observed_at=OBSERVED_AT + timedelta(minutes=1),
    )

    created = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=result_command,
        evidence_trace=trace,
    )
    replay = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=result_command,
        evidence_trace=trace,
    )

    stored = session.get(EntryDiscoveryObservation, created.observation_id)
    assert stored is not None
    assert stored.public_evidence == trace["public_evidence"]
    assert stored.model_assessment == trace["model_assessment"]
    assert stored.independent_validation == trace["independent_validation"]
    assert stored.prompt_fingerprint == "1" * 64
    assert replay.observation_created is False

    conflicting = dict(trace)
    conflicting["policy_fingerprint"] = "5" * 64
    session.rollback()
    with pytest.raises(DiscoveryRecordConflict, match="different result"):
        manifest_service.record_discovery_result_in_round(
            session,
            round_id=round_summary.round_id,
            command=result_command,
            evidence_trace=conflicting,
        )


def test_round_can_link_latest_prior_company_observation_across_sparse_rounds(
    session: Session,
) -> None:
    seed_manifest(session)
    first = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-history-01",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT,
    )
    first_observation = manifest_service.record_discovery_result_in_round(
        session,
        round_id=first.round_id,
        command=command(
            status=DiscoveryStatus.NOT_FOUND,
            observed_at=OBSERVED_AT + timedelta(minutes=1),
            error_code="recruitment_entry_not_found",
        ),
    )
    middle = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-history-02",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        predecessor_round_id=first.round_id,
        started_at=OBSERVED_AT + timedelta(minutes=2),
    )
    latest = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-history-03",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        predecessor_round_id=middle.round_id,
        started_at=OBSERVED_AT + timedelta(minutes=3),
    )

    successor = manifest_service.record_discovery_result_in_round(
        session,
        round_id=latest.round_id,
        command=command(
            status=DiscoveryStatus.ACCEPTED,
            observed_at=OBSERVED_AT + timedelta(minutes=4),
        ),
        predecessor_observation_id=first_observation.observation_id,
    )

    stored = session.get(EntryDiscoveryObservation, successor.observation_id)
    assert stored is not None
    assert stored.predecessor_observation_id == first_observation.observation_id


def test_legacy_retry_cannot_mutate_a_round_observation(session: Session) -> None:
    seed_manifest(session)
    round_summary = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-2026-08-09",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        started_at=OBSERVED_AT,
    )
    failed_command = command(
        status=DiscoveryStatus.FAILED,
        observed_at=OBSERVED_AT + timedelta(minutes=1),
        error_code="request_timeout",
    )
    failed = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=failed_command,
    )

    with pytest.raises(DiscoveryRecordConflict, match="legacy observation"):
        manifest_service.transition_retryable_discovery_result(
            session,
            observation_id=failed.observation_id,
            command=command(
                status=DiscoveryStatus.NOT_FOUND,
                observed_at=OBSERVED_AT + timedelta(minutes=2),
                error_code="recruitment_entry_not_found",
            ),
        )

    stored = session.get(EntryDiscoveryObservation, failed.observation_id)
    assert stored is not None
    assert stored.discovery_round_id == round_summary.round_id
    assert stored.status is DiscoveryStatus.FAILED
    assert stored.error_code == "request_timeout"
    assert stored.observed_at == failed_command.observed_at


def test_round_creation_locks_sqlite_writer_before_idempotency_reads(
    session: Session,
) -> None:
    seed_manifest(session)
    statements: list[str] = []
    engine = session.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(" ".join(statement.upper().split()))

    try:
        manifest_service.create_discovery_round(
            session,
            manifest_version=MANIFEST_VERSION,
            name="entry-evidence-2026-08-09",
            config_fingerprint="b" * 64,
            model_fingerprint="c" * 64,
            started_at=OBSERVED_AT,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    lock_position = statements.index("BEGIN IMMEDIATE")
    first_read_position = next(
        index for index, statement in enumerate(statements) if "FROM COMPANY_MANIFESTS" in statement
    )
    assert lock_position < first_read_position


def test_round_membership_is_frozen_and_sampling_reconciles_only_after_completion(
    session: Session,
) -> None:
    seed_manifest(session)
    second_company_id = UUID(int=92_002)
    second_company = Company(
        id=second_company_id,
        canonical_name="Second Round Company",
        normalized_name="second-round-company",
    )
    manifest = session.get(CompanyManifest, MANIFEST_VERSION)
    assert manifest is not None
    manifest.member_count = 2
    session.add(second_company)
    session.flush()
    session.add(
        CompanyManifestMember(
            manifest_version=MANIFEST_VERSION,
            company_id=second_company_id,
            position=2,
            canonical_name=second_company.canonical_name,
            primary_category=AiCategory.FOUNDATION_MODELS,
        )
    )
    session.commit()
    membership_fingerprint = sha256(
        "\n".join(sorted((str(COMPANY_ID), str(second_company_id)))).encode()
    ).hexdigest()
    round_summary = manifest_service.create_discovery_round(
        session,
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-frozen-membership",
        config_fingerprint="b" * 64,
        model_fingerprint="c" * 64,
        membership_fingerprint=membership_fingerprint,
        intended_member_count=2,
        started_at=OBSERVED_AT,
    )

    first = manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=command(
            status=DiscoveryStatus.ACCEPTED,
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        ),
    )
    reconcile = manifest_service.reconcile_evidence_audit_samples
    partial = reconcile(session, round_id=round_summary.round_id)

    assert partial.complete is False
    assert partial.created == 0
    assert session.scalar(select(func.count()).select_from(EntryEvidenceAuditSample)) == 0
    session.rollback()

    second_command = command(
        status=DiscoveryStatus.ACCEPTED,
        observed_at=OBSERVED_AT + timedelta(minutes=2),
    )
    second_command = second_command.model_copy(
        update={
            "company_id": second_company_id,
            "result": second_command.result.model_copy(
                update={
                    "candidate_url": "https://jobs.example.test/second",
                    "normalized_url": "https://jobs.example.test/second",
                }
            ),
        }
    )
    manifest_service.record_discovery_result_in_round(
        session,
        round_id=round_summary.round_id,
        command=second_command,
        predecessor_observation_id=None,
    )
    completed = reconcile(session, round_id=round_summary.round_id)
    replay = reconcile(session, round_id=round_summary.round_id)

    assert first.observation_created is True
    assert completed.complete is True
    assert completed.created == 1
    assert completed.total == 1
    assert replay.created == 0
    assert replay.total == 1
    assert session.scalar(select(func.count()).select_from(EntryEvidenceAuditSample)) == 1
    session.rollback()

    with pytest.raises(DiscoveryRecordConflict, match="membership"):
        manifest_service.create_discovery_round(
            session,
            manifest_version=MANIFEST_VERSION,
            name="entry-evidence-frozen-membership",
            config_fingerprint="b" * 64,
            model_fingerprint="c" * 64,
            membership_fingerprint="e" * 64,
            intended_member_count=1,
            started_at=OBSERVED_AT,
        )


def test_concurrent_sqlite_round_creation_replays_after_single_writer_lock(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'round-concurrency.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed_session:
        seed_manifest(seed_session)

    delay_lock = Lock()
    first_round_read_delayed = False

    @event.listens_for(engine, "after_cursor_execute")
    def delay_first_round_read(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal first_round_read_delayed
        if "FROM entry_discovery_rounds" not in statement:
            return
        with delay_lock:
            if first_round_read_delayed:
                return
            first_round_read_delayed = True
        sleep(0.1)

    start = Barrier(2)

    def create_round() -> manifest_service.DiscoveryRoundSummary:
        with factory() as worker_session:
            start.wait()
            return manifest_service.create_discovery_round(
                worker_session,
                manifest_version=MANIFEST_VERSION,
                name="entry-evidence-2026-08-09",
                config_fingerprint="b" * 64,
                model_fingerprint="c" * 64,
                started_at=OBSERVED_AT,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: create_round(), range(2)))
    finally:
        event.remove(engine, "after_cursor_execute", delay_first_round_read)
        engine.dispose()

    assert results[0].round_id == results[1].round_id
    assert sorted(result.created for result in results) == [False, True]
