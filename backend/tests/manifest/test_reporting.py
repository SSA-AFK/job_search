import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.manifest.contracts import AiCategory, DiscoveryStatus
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)
from app.manifest.reporting import ManifestReportError, ManifestReportService
from app.models import Base, Company, JobEntry

MANIFEST_VERSION = "c" * 64


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


def seed_manifest(session: Session, *, member_count: int) -> tuple[Company, ...]:
    manifest = CompanyManifest(
        version=MANIFEST_VERSION,
        config_fingerprint="a" * 64,
        member_count=member_count,
        canonical_quota={"foundation_models": member_count},
        frozen_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    session.add(manifest)
    companies = tuple(
        Company(
            id=UUID(int=100_000 + position),
            canonical_name=f"Report Company {position:04d}",
            normalized_name=f"report-company-{position:04d}",
        )
        for position in range(1, member_count + 1)
    )
    session.add_all(companies)
    session.flush()
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
    session.commit()
    return companies


def add_accepted_observation(
    session: Session,
    company: Company,
    *,
    suffix: str,
    platform: str,
) -> None:
    url = f"https://jobs.example.com/{suffix}"
    entry = JobEntry(
        company_id=company.id,
        url=url,
        normalized_url=url,
        provider="official_entry_discovery",
        platform=platform,
        requires_rendering=platform != "self_hosted",
    )
    session.add(entry)
    session.flush()
    session.add(
        EntryDiscoveryObservation(
            manifest_version=MANIFEST_VERSION,
            company_id=company.id,
            method="official_navigation",
            status=DiscoveryStatus.ACCEPTED,
            candidate_url=url,
            normalized_url=url,
            ownership_evidence="official_navigation_anchor:Careers",
            platform=platform,
            requires_rendering=entry.requires_rendering,
            job_entry_id=entry.id,
            observed_at=datetime(2026, 8, 7, 4, tzinfo=UTC),
        )
    )


def test_report_has_explicit_denominators_and_platform_counts(session: Session) -> None:
    companies = seed_manifest(session, member_count=1000)
    add_accepted_observation(session, companies[0], suffix="primary", platform="moka")
    add_accepted_observation(session, companies[0], suffix="campus", platform="moka")
    add_accepted_observation(
        session,
        companies[1],
        suffix="self-hosted",
        platform="self_hosted",
    )
    session.add(
        EntryDiscoveryObservation(
            manifest_version=MANIFEST_VERSION,
            company_id=companies[2].id,
            method="authorized_fallback",
            status=DiscoveryStatus.REVIEW_REQUIRED,
            candidate_url="https://unknown.example/jobs",
            normalized_url="https://unknown.example/jobs",
            source_id="zhihu_global_search",
            platform="unknown",
            error_code="ownership_unverified",
            observed_at=datetime(2026, 8, 7, 5, tzinfo=UTC),
        )
    )
    session.commit()

    report = ManifestReportService(session).build(
        MANIFEST_VERSION,
        code_commit="abc1234",
        config_fingerprint="a" * 64,
    )

    assert report.manifest_version == MANIFEST_VERSION
    assert report.code_commit == "abc1234"
    assert report.config_fingerprint == "a" * 64
    assert report.manifest_companies == 1000
    assert report.discovery_company_denominator == 1000
    assert report.entry_company_denominator == 1000
    assert report.platform_entry_denominator == 3
    assert report.accepted_entries == 3
    assert report.entry_companies == 2
    assert report.entry_coverage_rate == Decimal("0.0020")
    assert report.entries_per_company == Decimal("0.0030")
    assert report.platform_entry_counts == {"moka": 2, "self_hosted": 1}
    assert sum(report.platform_entry_counts.values()) == report.accepted_entries
    assert report.self_hosted_entries == 1
    assert report.self_hosted_rate == Decimal("0.3333")
    assert report.status_counts == {
        DiscoveryStatus.ACCEPTED: 3,
        DiscoveryStatus.REVIEW_REQUIRED: 1,
        DiscoveryStatus.NOT_FOUND: 0,
        DiscoveryStatus.BLOCKED: 0,
        DiscoveryStatus.FAILED: 0,
    }


def test_report_serializes_undefined_rates_as_null_and_exposes_no_raw_fields(
    session: Session,
) -> None:
    seed_manifest(session, member_count=0)

    report = ManifestReportService(session).build(
        MANIFEST_VERSION,
        code_commit="abc1234",
        config_fingerprint="b" * 64,
    )
    payload = report.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["entry_coverage_rate"] is None
    assert payload["entries_per_company"] is None
    assert payload["self_hosted_rate"] is None
    assert "raw_response" not in serialized
    assert "authorization" not in serialized.lower()
    assert "database_url" not in serialized
    assert set(payload) == {
        "manifest_version",
        "code_commit",
        "config_fingerprint",
        "manifest_companies",
        "discovered_companies",
        "discovery_company_denominator",
        "discovery_coverage_rate",
        "status_counts",
        "accepted_entries",
        "entry_companies",
        "entry_company_denominator",
        "entry_coverage_rate",
        "entries_per_company",
        "platform_entry_counts",
        "platform_entry_denominator",
        "self_hosted_entries",
        "self_hosted_rate",
    }


def test_report_reads_observations_once_for_one_consistent_census(
    session: Session,
) -> None:
    companies = seed_manifest(session, member_count=2)
    add_accepted_observation(session, companies[0], suffix="primary", platform="moka")
    session.commit()
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
        report = ManifestReportService(session).build(
            MANIFEST_VERSION,
            code_commit="abc1234",
            config_fingerprint="a" * 64,
        )
    finally:
        event.remove(bind, "before_execute", capture_statement)

    observation_reads = [
        statement
        for statement in captured_statements
        if isinstance(statement, Select)
        and "FROM entry_discovery_observations" in str(statement)
    ]
    assert report.accepted_entries == 1
    assert len(observation_reads) == 1


@pytest.mark.parametrize("fingerprint", ["", "short", "G" * 64])
def test_report_rejects_noncanonical_config_fingerprint(
    session: Session,
    fingerprint: str,
) -> None:
    seed_manifest(session, member_count=0)

    with pytest.raises(ValidationError, match="String should match pattern"):
        ManifestReportService(session).build(
            MANIFEST_VERSION,
            code_commit="abc1234",
            config_fingerprint=fingerprint,
        )


@pytest.mark.parametrize(
    "code_commit",
    [
        "abc123",
        "a" * 41,
        "ABC1234",
        "postgresql://user:secret@database/jobs",
        "Bearer secret-token",
    ],
)
def test_report_rejects_noncanonical_code_commit_without_echoing_hostile_input(
    session: Session,
    code_commit: str,
) -> None:
    seed_manifest(session, member_count=0)

    with pytest.raises(ManifestReportError, match="code commit is invalid") as captured:
        ManifestReportService(session).build(
            MANIFEST_VERSION,
            code_commit=code_commit,
            config_fingerprint="a" * 64,
        )

    diagnostic = str(captured.value)
    assert code_commit not in diagnostic
    assert "secret" not in diagnostic.lower()
