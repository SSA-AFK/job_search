from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.manifest.contracts import (
    AiCategory,
    AtsClassification,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    EntryDiscoveryResult,
    RecordDiscoveryCommand,
)
from app.manifest.models import CandidateFact
from app.manifest.reporting import ManifestReportService
from app.manifest.service import FrozenManifest, freeze_manifest, record_discovery_result
from app.models import Base, Company


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def import_fixture_candidates(session: Session, *, accepted: int) -> None:
    companies: list[Company] = []
    facts: list[CandidateFact] = []
    for identity in range(1, accepted + 1):
        category = tuple(AiCategory)[(identity - 1) % len(AiCategory)]
        company = Company(
            id=UUID(int=identity),
            canonical_name=f"Integration Company {identity:04d}",
            normalized_name=f"integration company {identity:04d}",
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
                source_id="public_registry",
                source_url=f"https://registry.example/{identity}",
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


def persist_fixture_discoveries(session: Session, frozen: FrozenManifest) -> None:
    for member in frozen.members:
        url = f"https://jobs.example.com/company-{member.position}"
        record_discovery_result(
            session,
            RecordDiscoveryCommand(
                manifest_version=frozen.manifest_version,
                company_id=member.company.company_id,
                observed_at=datetime(2026, 8, 7, tzinfo=UTC),
                result=EntryDiscoveryResult(
                    status=DiscoveryStatus.ACCEPTED,
                    method="evidenced_recruitment_url",
                    candidate_url=url,
                    normalized_url=url,
                    ownership_evidence="reviewed_fixture_recruitment_url",
                    classification=AtsClassification(platform="self_hosted"),
                ),
            ),
        )


def test_offline_integration_freezes_and_reports_full_fixture(session: Session) -> None:
    import_fixture_candidates(session, accepted=1500)
    frozen = freeze_manifest(session, config_fingerprint="a" * 64)
    persist_fixture_discoveries(session, frozen)

    report = ManifestReportService(session).build(
        frozen.manifest_version,
        code_commit="abc1234",
        config_fingerprint="a" * 64,
    )

    assert len(frozen.members) == 1000
    assert frozen.allocation.total == 1000
    assert report.manifest_companies == 1000
    assert report.accepted_entries == 1000
    assert report.entry_companies == 1000
    assert report.status_counts[DiscoveryStatus.ACCEPTED] == 1000
