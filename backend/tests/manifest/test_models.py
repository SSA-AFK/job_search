import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    ReviewAction,
)
from app.models import (
    Base,
    CandidateFact,
    CandidateReview,
    Company,
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    JobEntry,
)


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


def persisted_company(session: Session, *, suffix: str = "") -> Company:
    company = Company(
        canonical_name=f"Example{suffix}",
        normalized_name=f"example{suffix}",
    )
    session.add(company)
    session.flush()
    return company


def persisted_manifest(session: Session, *, suffix: str = "a") -> CompanyManifest:
    manifest = CompanyManifest(
        version=f"{suffix}" * 64,
        config_fingerprint="b" * 64,
        member_count=1,
        canonical_quota={"foundation_models": 1},
        frozen_at=datetime.now(UTC),
    )
    session.add(manifest)
    session.flush()
    return manifest


def manifest_member(
    manifest: CompanyManifest, company: Company, *, position: int
) -> CompanyManifestMember:
    return CompanyManifestMember(
        manifest_version=manifest.version,
        company_id=company.id,
        position=position,
        canonical_name=company.canonical_name,
        primary_category=AiCategory.FOUNDATION_MODELS,
    )


def persisted_entry(session: Session, company: Company) -> JobEntry:
    entry = JobEntry(
        company_id=company.id,
        url=f"https://careers.{company.normalized_name}.example/jobs",
        normalized_url=f"https://careers.{company.normalized_name}.example/jobs",
        provider="official",
        platform="custom",
    )
    session.add(entry)
    session.flush()
    return entry


def discovery_observation(
    manifest: CompanyManifest, company: Company, *, job_entry_id: object | None = None
) -> EntryDiscoveryObservation:
    return EntryDiscoveryObservation(
        manifest_version=manifest.version,
        company_id=company.id,
        method="official_site",
        status=DiscoveryStatus.ACCEPTED,
        candidate_url="https://careers.example/jobs",
        normalized_url="https://careers.example/jobs",
        platform="custom",
        job_entry_id=job_entry_id,
        observed_at=datetime.now(UTC),
    )


def test_manifest_models_have_stable_tables_and_persisted_enum_values() -> None:
    assert CandidateFact.__tablename__ == "candidate_facts"
    assert CandidateReview.__tablename__ == "candidate_reviews"
    assert CompanyManifest.__tablename__ == "company_manifests"
    assert CompanyManifestMember.__tablename__ == "company_manifest_members"
    assert EntryDiscoveryObservation.__tablename__ == "entry_discovery_observations"
    assert CandidateFact.__table__.c.decision_status.type.enums == [
        status.value for status in CandidateDecisionStatus
    ]
    assert CandidateFact.__table__.c.confidence_tier.type.enums == [
        tier.value for tier in ConfidenceTier
    ]
    assert CandidateReview.__table__.c.action.type.enums == [action.value for action in ReviewAction]
    assert EntryDiscoveryObservation.__table__.c.status.type.enums == [
        status.value for status in DiscoveryStatus
    ]


def test_manifest_models_are_directly_importable() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import app.manifest.models"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_base_metadata_registers_manifest_models_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.models import Base; "
                "assert {'candidate_facts', 'candidate_reviews', 'company_manifests', "
                "'company_manifest_members', 'entry_discovery_observations'} "
                "<= set(Base.metadata.tables)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_candidate_fact_defaults_and_utc_fields_round_trip(session: Session) -> None:
    plus_eight = timezone(timedelta(hours=8))
    fact = CandidateFact(
        stable_evidence_id="a" * 64,
        canonical_name="Example AI",
        normalized_name="example ai",
        aliases=["Example"],
        primary_category=AiCategory.FOUNDATION_MODELS,
        source_id="public_list",
        source_url="https://example.com/list",
        retrieved_at=datetime(2026, 8, 6, 12, tzinfo=plus_eight),
        evidence_summary="Published member directory entry.",
        confidence_tier=ConfidenceTier.HIGH,
        confidence_reason="Government directory with an official website.",
    )
    session.add(fact)
    session.commit()
    session.expire(fact)

    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert fact.retrieved_at == datetime(2026, 8, 6, 4, tzinfo=UTC)
    assert CandidateFact.__table__.c.decision_status.server_default is not None


def test_manifest_membership_is_unique_by_position_and_company(session: Session) -> None:
    manifest = persisted_manifest(session)
    company = persisted_company(session)
    session.add_all(
        [
            manifest_member(manifest, company, position=1),
            manifest_member(manifest, company, position=2),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_discovery_observation_cannot_own_another_company_entry(session: Session) -> None:
    manifest = persisted_manifest(session)
    company_a = persisted_company(session, suffix="a")
    company_b = persisted_company(session, suffix="b")
    entry_for_company_b = persisted_entry(session, company_b)
    observation = discovery_observation(
        manifest,
        company_a,
        job_entry_id=entry_for_company_b.id,
    )
    session.add(observation)

    with pytest.raises(IntegrityError):
        session.flush()


def test_discovery_observation_defaults_rendering_to_false(session: Session) -> None:
    manifest = persisted_manifest(session)
    company = persisted_company(session)
    observation = discovery_observation(manifest, company)
    session.add(observation)
    session.flush()

    assert observation.requires_rendering is False
    assert EntryDiscoveryObservation.__table__.c.requires_rendering.server_default is not None
