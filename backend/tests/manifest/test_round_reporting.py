from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.manifest.contracts import AiCategory, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
    EntryDiscoveryRound,
)
from app.manifest.reporting import ManifestReportService
from app.models import Company, JobEntry
from app.models.base import Base

MANIFEST_VERSION = "c" * 64
STARTED_AT = datetime(2026, 8, 9, 8, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def test_round_aware_report_keeps_aggregate_and_round_denominators_separate(
    session: Session,
) -> None:
    companies = tuple(
        Company(
            id=UUID(int=93_000 + position),
            canonical_name=f"Round Company {position}",
            normalized_name=f"round-company-{position}",
        )
        for position in (1, 2)
    )
    session.add_all(companies)
    session.add(
        CompanyManifest(
            version=MANIFEST_VERSION,
            config_fingerprint="a" * 64,
            member_count=2,
            canonical_quota={"foundation_models": 2},
            frozen_at=STARTED_AT,
        )
    )
    session.add_all(
        CompanyManifestMember(
            manifest_version=MANIFEST_VERSION,
            company_id=company.id,
            position=position,
            canonical_name=company.canonical_name,
            primary_category=AiCategory.FOUNDATION_MODELS,
        )
        for position, company in enumerate(companies, start=1)
    )
    first_round = EntryDiscoveryRound(
        id=UUID(int=93_101),
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-first",
        config_fingerprint="a" * 64,
        model_fingerprint="b" * 64,
        started_at=STARTED_AT + timedelta(minutes=1),
    )
    second_round = EntryDiscoveryRound(
        id=UUID(int=93_102),
        manifest_version=MANIFEST_VERSION,
        name="entry-evidence-second",
        config_fingerprint="a" * 64,
        model_fingerprint="b" * 64,
        predecessor_round_id=first_round.id,
        started_at=STARTED_AT + timedelta(minutes=3),
    )
    session.add_all((first_round, second_round))
    entry = JobEntry(
        id=UUID(int=93_201),
        company_id=companies[0].id,
        url="https://jobs.example/round-company-1",
        normalized_url="https://jobs.example/round-company-1",
        provider="official_entry_discovery",
        platform="moka",
        requires_rendering=False,
    )
    session.add(entry)
    session.add_all(
        (
            EntryDiscoveryObservation(
                id=UUID(int=93_301),
                manifest_version=MANIFEST_VERSION,
                company_id=companies[0].id,
                method="legacy",
                status=DiscoveryStatus.NOT_FOUND,
                error_code="recruitment_entry_not_found",
                observed_at=STARTED_AT,
            ),
            EntryDiscoveryObservation(
                id=UUID(int=93_302),
                manifest_version=MANIFEST_VERSION,
                discovery_round_id=first_round.id,
                company_id=companies[0].id,
                method="entry_evidence_model",
                status=DiscoveryStatus.ACCEPTED,
                candidate_url=entry.url,
                normalized_url=entry.normalized_url,
                source_id="public_registry",
                ownership_evidence="official website careers anchor",
                platform="moka",
                job_entry_id=entry.id,
                observed_at=STARTED_AT + timedelta(minutes=2),
            ),
            EntryDiscoveryObservation(
                id=UUID(int=93_303),
                manifest_version=MANIFEST_VERSION,
                discovery_round_id=second_round.id,
                company_id=companies[1].id,
                method="entry_evidence_model",
                status=DiscoveryStatus.REVIEW_REQUIRED,
                source_id="public_registry",
                platform="moka",
                error_code="model_confidence_below_threshold",
                observed_at=STARTED_AT + timedelta(minutes=4),
            ),
        )
    )
    session.commit()

    report = ManifestReportService(session).build_round_aware(
        MANIFEST_VERSION,
        code_commit="abc1234",
        config_fingerprint="a" * 64,
    )
    payload = report.model_dump(mode="json")

    assert payload["aggregate"]["status_counts"] == {
        "accepted": 1,
        "review_required": 1,
        "not_found": 1,
        "blocked": 0,
        "failed": 0,
    }
    assert payload["aggregate"]["discovery_company_denominator"] == 2
    assert [round_payload["name"] for round_payload in payload["rounds"]] == [
        "entry-evidence-first",
        "entry-evidence-second",
    ]
    assert payload["rounds"][0]["status_counts"]["accepted"] == 1
    assert payload["rounds"][0]["status_counts"]["not_found"] == 0
    assert payload["rounds"][1]["status_counts"]["review_required"] == 1
    assert payload["rounds"][0]["company_denominator"] == 2
    assert payload["rounds"][1]["company_denominator"] == 2
    assert payload["rounds"][0]["predecessor_round_id"] is None
    assert payload["rounds"][1]["predecessor_round_id"] == str(first_round.id)
