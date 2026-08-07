import json
from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.manifest.contracts import (
    AiCategory,
    AtsClassification,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    EntryDiscoveryResult,
    RecordDiscoveryCommand,
)
from app.manifest.models import (
    CandidateFact,
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)
from app.manifest.service import (
    DiscoveryRecordConflict,
    ManifestFreezeConflict,
    ManifestFreezeError,
    freeze_manifest,
    record_discovery_result,
)
from app.models import Base, Company, JobCollectionSnapshot, JobEntry, JobEntryStatus

DISCOVERY_MANIFEST_VERSION = "d" * 64
DISCOVERY_COMPANY_ID = UUID(int=91_001)
DISCOVERY_OBSERVED_AT = datetime(2026, 8, 7, 4, tzinfo=UTC)


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


def seed_resolved_candidates(
    session: Session,
    *,
    accepted: int,
    category_counts: dict[AiCategory, int] | None = None,
) -> tuple[Company, ...]:
    if category_counts is None:
        base, extra = divmod(accepted, len(AiCategory))
        category_counts = {
            category: base + (1 if index < extra else 0)
            for index, category in enumerate(AiCategory)
        }
    assert sum(category_counts.values()) == accepted
    companies: list[Company] = []
    facts: list[CandidateFact] = []
    identity = 1
    for category in AiCategory:
        for category_index in range(category_counts.get(category, 0)):
            company = Company(
                id=UUID(int=identity),
                canonical_name=f"Company {identity:04d}",
                normalized_name=f"company {identity:04d}",
                scale=("one_to_49", "50_to_199", "unknown")[identity % 3],
                city=("Beijing", "Shanghai", None)[identity % 3],
            )
            fact = CandidateFact(
                id=UUID(int=10_000 + identity),
                stable_evidence_id=f"{identity:064x}",
                canonical_name=company.canonical_name,
                normalized_name=company.normalized_name,
                aliases=[],
                primary_category=category,
                official_website=f"https://company-{identity}.example/about",
                recruitment_url=f"https://company-{identity}.example/jobs",
                source_id="public_registry",
                source_url=f"https://registry.example/{identity}",
                retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
                evidence_summary=f"Public evidence {category_index}.",
                confidence_tier=(
                    ConfidenceTier.HIGH if category_index % 2 == 0 else ConfidenceTier.MEDIUM
                ),
                confidence_reason="Reviewed public registry evidence.",
                decision_status=CandidateDecisionStatus.ACCEPTED,
                company_id=company.id,
            )
            companies.append(company)
            facts.append(fact)
            identity += 1
    session.add_all(companies)
    session.flush()
    session.add_all(facts)
    session.commit()
    return tuple(companies)


def seed_discovery_manifest(
    session: Session,
    *,
    company_id: UUID = DISCOVERY_COMPANY_ID,
    position: int = 1,
) -> Company:
    company = Company(
        id=company_id,
        canonical_name=f"Discovery Company {position}",
        normalized_name=f"discovery-company-{position}",
    )
    session.add(company)
    if session.get(CompanyManifest, DISCOVERY_MANIFEST_VERSION) is None:
        session.add(
            CompanyManifest(
                version=DISCOVERY_MANIFEST_VERSION,
                config_fingerprint="a" * 64,
                member_count=1,
                canonical_quota={"foundation_models": 1},
                frozen_at=datetime(2026, 8, 7, tzinfo=UTC),
            )
        )
    session.flush()
    session.add(
        CompanyManifestMember(
            manifest_version=DISCOVERY_MANIFEST_VERSION,
            company_id=company.id,
            position=position,
            canonical_name=company.canonical_name,
            primary_category=AiCategory.FOUNDATION_MODELS,
        )
    )
    session.commit()
    return company


def accepted_discovery_command(
    *,
    company_id: UUID = DISCOVERY_COMPANY_ID,
    candidate_url: str | None = None,
    normalized_url: str = "https://jobs.feishu.cn/acme/careers",
    platform: str = "feishu",
    observed_at: datetime = DISCOVERY_OBSERVED_AT,
) -> RecordDiscoveryCommand:
    return RecordDiscoveryCommand(
        manifest_version=DISCOVERY_MANIFEST_VERSION,
        company_id=company_id,
        observed_at=observed_at,
        result=EntryDiscoveryResult(
            status=DiscoveryStatus.ACCEPTED,
            method="official_navigation",
            candidate_url=candidate_url or normalized_url,
            normalized_url=normalized_url,
            ownership_evidence="official_navigation_anchor:Careers",
            classification=AtsClassification(
                platform=platform,
                requires_rendering=True,
            ),
        ),
    )


def test_freeze_counts_unique_companies_for_1500_identity_prerequisite(
    session: Session,
) -> None:
    companies = seed_resolved_candidates(session, accepted=1499)
    duplicate_evidence = CandidateFact(
        id=UUID(int=50_000),
        stable_evidence_id="f" * 64,
        canonical_name=companies[0].canonical_name,
        normalized_name=companies[0].normalized_name,
        aliases=[],
        primary_category=AiCategory.FOUNDATION_MODELS,
        official_website="https://duplicate.example/about",
        source_id="second_registry",
        source_url="https://second.example/evidence",
        retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
        evidence_summary="A second fact for the same recruiting identity.",
        confidence_tier=ConfidenceTier.LOW,
        confidence_reason="Authorized API fallback.",
        decision_status=CandidateDecisionStatus.ACCEPTED,
        company_id=companies[0].id,
    )
    session.add(duplicate_evidence)
    session.commit()

    with pytest.raises(ManifestFreezeError, match="at least 1500 accepted identities"):
        freeze_manifest(session, config_fingerprint="a" * 64)

    assert session.scalar(select(func.count()).select_from(CompanyManifest)) == 0


def test_freeze_rejects_accepted_fact_without_resolved_company(session: Session) -> None:
    seed_resolved_candidates(session, accepted=1500)
    session.add(
        CandidateFact(
            id=UUID(int=60_000),
            stable_evidence_id="e" * 64,
            canonical_name="Unresolved Accepted",
            normalized_name="unresolved accepted",
            aliases=[],
            primary_category=AiCategory.FOUNDATION_MODELS,
            source_id="public_registry",
            source_url="https://registry.example/unresolved",
            retrieved_at=datetime(2026, 8, 3, tzinfo=UTC),
            evidence_summary="Incorrectly accepted before identity resolution.",
            confidence_tier=ConfidenceTier.HIGH,
            confidence_reason="Reviewed public registry evidence.",
            decision_status=CandidateDecisionStatus.ACCEPTED,
            company_id=None,
        )
    )
    session.commit()

    with pytest.raises(ManifestFreezeError, match="accepted candidate is unresolved"):
        freeze_manifest(session, config_fingerprint="a" * 64)


def test_freeze_rejects_category_floor_shortage_without_writes(session: Session) -> None:
    categories = list(AiCategory)
    counts = {category: 182 for category in categories}
    counts[categories[0]] = 43
    counts[categories[1]] += 1500 - sum(counts.values())
    seed_resolved_candidates(session, accepted=1500, category_counts=counts)

    with pytest.raises(ManifestFreezeError, match="category floor shortage"):
        freeze_manifest(session, config_fingerprint="a" * 64)

    assert session.scalar(select(func.count()).select_from(CompanyManifest)) == 0
    assert session.scalar(select(func.count()).select_from(CompanyManifestMember)) == 0


def test_freeze_persists_exactly_1000_unique_members_in_one_transaction(
    session: Session,
) -> None:
    seed_resolved_candidates(session, accepted=1500)
    commit_count = 0

    def count_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1

    event.listen(session, "after_commit", count_commit)
    try:
        frozen = freeze_manifest(session, config_fingerprint="a" * 64)
    finally:
        event.remove(session, "after_commit", count_commit)

    persisted_company_ids = session.scalars(
        select(CompanyManifestMember.company_id).order_by(CompanyManifestMember.position)
    ).all()
    assert commit_count == 1
    assert len(frozen.members) == 1000
    assert frozen.allocation.total == 1000
    assert len(persisted_company_ids) == len(set(persisted_company_ids)) == 1000
    assert [member.position for member in frozen.members] == list(range(1, 1001))
    assert sha256(frozen.canonical_bytes).hexdigest() == frozen.manifest_version


def test_freeze_locks_candidate_pool_before_reading_manifest_in_postgresql(
    session: Session,
) -> None:
    seed_resolved_candidates(session, accepted=1500)
    captured_statements: list[object] = []
    bind = session.get_bind()

    def capture_statement(
        _connection: object,
        clauseelement: object,
        _multiparams: object,
        _params: object,
        _execution_options: object,
    ) -> None:
        captured_statements.append(clauseelement)

    event.listen(bind, "before_execute", capture_statement)
    try:
        freeze_manifest(session, config_fingerprint="a" * 64)
    finally:
        event.remove(bind, "before_execute", capture_statement)

    compiled = tuple(
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in captured_statements
        if isinstance(statement, Select)
    )
    candidate_lock_index = next(
        index
        for index, statement in enumerate(compiled)
        if "FROM candidate_facts" in statement and "FOR UPDATE" in statement
    )
    manifest_read_index = next(
        index
        for index, statement in enumerate(compiled)
        if "FROM company_manifests" in statement
    )
    assert candidate_lock_index < manifest_read_index


def test_frozen_manifest_replay_is_byte_exact_and_changed_pool_conflicts(
    session: Session,
) -> None:
    companies = seed_resolved_candidates(session, accepted=1500)

    first = freeze_manifest(session, config_fingerprint="a" * 64)
    replay = freeze_manifest(session, config_fingerprint="a" * 64)

    assert replay == first
    assert replay.manifest_bytes == first.manifest_bytes
    assert replay.quota_bytes == first.quota_bytes

    companies[0].canonical_name = "Changed Selected Company"
    companies[0].normalized_name = "changed selected company"
    session.commit()
    with pytest.raises(ManifestFreezeConflict):
        freeze_manifest(session, config_fingerprint="a" * 64)

    assert session.scalar(select(func.count()).select_from(CompanyManifest)) == 1
    assert session.scalar(select(func.count()).select_from(CompanyManifestMember)) == 1000


def test_freeze_rejects_changed_config_for_existing_membership(session: Session) -> None:
    seed_resolved_candidates(session, accepted=1500)
    freeze_manifest(session, config_fingerprint="a" * 64)

    with pytest.raises(ManifestFreezeConflict, match="conflicts with existing manifest"):
        freeze_manifest(session, config_fingerprint="b" * 64)


def test_hash_excludes_freeze_timestamp_but_artifacts_include_utc_z(
    session: Session,
) -> None:
    seed_resolved_candidates(session, accepted=1500)

    frozen = freeze_manifest(session, config_fingerprint="a" * 64)
    manifest_artifact = json.loads(frozen.manifest_bytes)
    quota_artifact = json.loads(frozen.quota_bytes)

    assert b"frozen_at" not in frozen.canonical_bytes
    assert manifest_artifact["frozen_at"].endswith("Z")
    assert quota_artifact["frozen_at"] == manifest_artifact["frozen_at"]
    assert manifest_artifact["manifest_version"] == frozen.manifest_version
    assert quota_artifact["manifest_version"] == frozen.manifest_version
    assert sha256(frozen.canonical_bytes).hexdigest() == frozen.manifest_version


def test_freeze_returns_data_without_writing_manifest_files(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_resolved_candidates(session, accepted=1500)
    monkeypatch.chdir(tmp_path)

    frozen = freeze_manifest(session, config_fingerprint="a" * 64)

    assert frozen.manifest_bytes
    assert frozen.quota_bytes
    assert tuple(tmp_path.rglob("*")) == ()


@pytest.mark.parametrize("fingerprint", ["short", "g" * 64, "A" * 64])
def test_freeze_rejects_noncanonical_config_fingerprint(
    session: Session, fingerprint: str
) -> None:
    with pytest.raises(ManifestFreezeError, match="config fingerprint"):
        freeze_manifest(session, config_fingerprint=fingerprint)


def test_freeze_requires_clean_session_for_owned_transaction(session: Session) -> None:
    session.execute(select(Company.id)).all()

    with pytest.raises(ManifestFreezeError, match="clean session"):
        freeze_manifest(session, config_fingerprint="a" * 64)


def test_accepted_discovery_upserts_owned_entry_without_snapshot(
    session: Session,
) -> None:
    seed_discovery_manifest(session)

    result = record_discovery_result(session, accepted_discovery_command())

    entry = session.get(JobEntry, result.job_entry_id)
    assert result.entry_created is True
    assert result.observation_created is True
    assert entry is not None
    assert entry.company_id == DISCOVERY_COMPANY_ID
    assert entry.provider == "official_entry_discovery"
    assert entry.platform == "feishu"
    assert entry.requires_rendering is True
    assert entry.status is JobEntryStatus.UNKNOWN
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0


def test_exact_discovery_replay_does_not_duplicate_observation_or_entry(
    session: Session,
) -> None:
    seed_discovery_manifest(session)
    command = accepted_discovery_command()

    first = record_discovery_result(session, command)
    second = record_discovery_result(session, command)

    assert second.observation_id == first.observation_id
    assert second.job_entry_id == first.job_entry_id
    assert second.observation_created is False
    assert second.entry_created is False
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 1
    assert session.scalar(select(func.count()).select_from(JobEntry)) == 1


def test_accepted_discovery_upserts_existing_owned_entry_without_resetting_status(
    session: Session,
) -> None:
    company = seed_discovery_manifest(session)
    existing = JobEntry(
        company_id=company.id,
        url="https://jobs.feishu.cn/acme/careers",
        normalized_url="https://jobs.feishu.cn/acme/careers",
        provider="legacy_import",
        platform="unknown",
        requires_rendering=False,
        status=JobEntryStatus.ACTIVE,
    )
    session.add(existing)
    session.commit()

    result = record_discovery_result(session, accepted_discovery_command())

    assert result.job_entry_id == existing.id
    assert result.entry_created is False
    assert existing.provider == "official_entry_discovery"
    assert existing.platform == "feishu"
    assert existing.requires_rendering is True
    assert existing.status is JobEntryStatus.ACTIVE


def test_discovery_allows_multiple_distinct_owned_entries_for_one_company(
    session: Session,
) -> None:
    seed_discovery_manifest(session)

    first = record_discovery_result(session, accepted_discovery_command())
    second = record_discovery_result(
        session,
        accepted_discovery_command(
            normalized_url="https://jobs.feishu.cn/acme/campus",
        ),
    )

    assert first.job_entry_id != second.job_entry_id
    assert first.entry_created is True
    assert second.entry_created is True
    assert session.scalar(select(func.count()).select_from(JobEntry)) == 2
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 2


def test_same_normalized_url_with_changed_observation_conflicts_atomically(
    session: Session,
) -> None:
    seed_discovery_manifest(session)
    first = record_discovery_result(session, accepted_discovery_command())

    with pytest.raises(DiscoveryRecordConflict, match="conflicts"):
        record_discovery_result(
            session,
            accepted_discovery_command(platform="moka"),
        )

    observation = session.get(EntryDiscoveryObservation, first.observation_id)
    entry = session.get(JobEntry, first.job_entry_id)
    assert observation is not None
    assert observation.platform == "feishu"
    assert entry is not None
    assert entry.platform == "feishu"
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 1


def test_accepted_discovery_rejects_mismatched_normalized_url_identity(
    session: Session,
) -> None:
    seed_discovery_manifest(session)

    with pytest.raises(DiscoveryRecordConflict, match="normalized URL"):
        record_discovery_result(
            session,
            accepted_discovery_command(
                candidate_url="https://jobs.feishu.cn/different/careers",
            ),
        )

    assert session.scalar(select(func.count()).select_from(JobEntry)) == 0
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 0


def test_accepted_discovery_rejects_unknown_platform_classification(
    session: Session,
) -> None:
    seed_discovery_manifest(session)

    with pytest.raises(DiscoveryRecordConflict, match="known platform"):
        record_discovery_result(
            session,
            accepted_discovery_command(platform="unknown"),
        )

    assert session.scalar(select(func.count()).select_from(JobEntry)) == 0
    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 0


def test_discovery_rejects_normalized_url_owned_by_another_company(
    session: Session,
) -> None:
    seed_discovery_manifest(session)
    other = Company(
        id=UUID(int=91_002),
        canonical_name="Other Owner",
        normalized_name="other-owner",
    )
    session.add(other)
    session.flush()
    existing = JobEntry(
        company_id=other.id,
        url="https://jobs.feishu.cn/acme/careers",
        normalized_url="https://jobs.feishu.cn/acme/careers",
        provider="official_entry_discovery",
        platform="feishu",
    )
    session.add(existing)
    session.commit()

    with pytest.raises(DiscoveryRecordConflict, match="another company"):
        record_discovery_result(session, accepted_discovery_command())

    assert session.scalar(select(func.count()).select_from(EntryDiscoveryObservation)) == 0
    assert session.scalar(select(func.count()).select_from(JobEntry)) == 1


def test_discovery_locks_manifest_before_entry_upsert_in_postgresql(
    session: Session,
) -> None:
    seed_discovery_manifest(session)
    captured_statements: list[object] = []
    bind = session.get_bind()

    def capture_statement(
        _connection: object,
        clauseelement: object,
        _multiparams: object,
        _params: object,
        _execution_options: object,
    ) -> None:
        captured_statements.append(clauseelement)

    event.listen(bind, "before_execute", capture_statement)
    try:
        record_discovery_result(session, accepted_discovery_command())
    finally:
        event.remove(bind, "before_execute", capture_statement)

    compiled = tuple(
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in captured_statements
        if isinstance(statement, Select)
    )
    manifest_locks = [
        index
        for index, statement in enumerate(compiled)
        if "FROM company_manifests" in statement and "FOR UPDATE" in statement
    ]
    entry_read_index = next(
        index for index, statement in enumerate(compiled) if "FROM job_entries" in statement
    )
    assert manifest_locks
    assert manifest_locks[0] < entry_read_index


def test_review_required_discovery_records_observation_without_entry(
    session: Session,
) -> None:
    seed_discovery_manifest(session)
    command = RecordDiscoveryCommand(
        manifest_version=DISCOVERY_MANIFEST_VERSION,
        company_id=DISCOVERY_COMPANY_ID,
        observed_at=DISCOVERY_OBSERVED_AT,
        result=EntryDiscoveryResult(
            status=DiscoveryStatus.REVIEW_REQUIRED,
            method="authorized_fallback",
            candidate_url="https://jobs.example.com/acme",
            normalized_url="https://jobs.example.com/acme",
            source_id="zhihu_global_search",
            classification=AtsClassification(platform="unknown"),
            error_code="ownership_unverified",
        ),
    )

    result = record_discovery_result(session, command)

    assert result.job_entry_id is None
    assert result.entry_created is False
    assert result.observation_created is True
    assert session.get(EntryDiscoveryObservation, result.observation_id) is not None
    assert session.scalar(select(func.count()).select_from(JobEntry)) == 0
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0


def test_discovery_requires_clean_session_for_owned_transaction(session: Session) -> None:
    seed_discovery_manifest(session)
    session.execute(select(Company.id)).all()

    with pytest.raises(DiscoveryRecordConflict, match="clean session"):
        record_discovery_result(session, accepted_discovery_command())
