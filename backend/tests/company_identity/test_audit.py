import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.company_identity.audit import CompanyIdentityAuditService
from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityNameOwner,
    IdentityAuditSeverity,
    IdentityReviewStatus,
)
from app.company_identity.models import CompanyIdentityReviewItem
from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
from app.manifest.contracts import AiCategory, CandidateDecisionStatus, ConfidenceTier
from app.manifest.models import CandidateFact
from app.models import (
    Base,
    CollectionStatus,
    Company,
    CompanyAlias,
    CrawlRun,
    FilingType,
    JobEntry,
    JobEntryStatus,
    RegulatoryFiling,
    RunType,
)

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
COMPANY_A = UUID("00000000-0000-0000-0000-000000000001")
COMPANY_B = UUID("00000000-0000-0000-0000-000000000002")
COMPANY_C = UUID("00000000-0000-0000-0000-000000000003")
ORPHAN_COMPANY = UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def _company(
    company_id: UUID,
    canonical_name: str,
    normalized_name: str,
    *,
    website: str | None = None,
) -> Company:
    return Company(
        id=company_id,
        canonical_name=canonical_name,
        normalized_name=normalized_name,
        website=website,
        funding_stage="unknown",
        scale="unknown",
    )


def _job_entry(company_id: UUID, entry_id: UUID, url: str) -> JobEntry:
    return JobEntry(
        id=entry_id,
        company_id=company_id,
        url=url,
        normalized_url=url,
        provider="official_site",
        platform="ats",
        status=JobEntryStatus.ACTIVE,
    )


def _accepted_fact(company_id: UUID) -> CandidateFact:
    return CandidateFact(
        stable_evidence_id=sha256(b"historical-acme-name").hexdigest(),
        canonical_name="Historical Acme Holdings",
        normalized_name="historicalacmeholdings",
        aliases=["Historical Acme"],
        primary_category=AiCategory.FOUNDATION_MODELS,
        official_website="https://public.example/company?drop=1",
        recruitment_url=None,
        source_id="public_registry",
        source_url="https://registry.example/record?drop=1",
        retrieved_at=NOW,
        evidence_summary="Public registry identity.",
        confidence_tier=ConfidenceTier.HIGH,
        confidence_reason="Public registry evidence.",
        decision_status=CandidateDecisionStatus.ACCEPTED,
        company_id=company_id,
    )


def _pending_review(
    session: Session,
    *,
    stable_hash: str = "a" * 64,
    candidate_name: str = "Beta Alias",
    normalized_name: str = "betaalias",
    candidate_matches: list[dict[str, object]] | None = None,
    review_reasons: list[str] | None = None,
) -> None:
    crawl_run = CrawlRun(
        run_type=RunType.DISCOVERY,
        status=CollectionStatus.SUCCEEDED,
        providers_attempted=[],
        created_at=NOW,
    )
    session.add(crawl_run)
    session.flush()
    session.add(
        CompanyIdentityReviewItem(
            stable_identity_hash=stable_hash,
            first_crawl_run_id=crawl_run.id,
            status=IdentityReviewStatus.PENDING,
            candidate_name=candidate_name,
            normalized_name=normalized_name,
            aliases=[],
            official_website=None,
            recruitment_identity=None,
            legal_identifiers=[],
            city=None,
            public_evidence_refs=[],
            candidate_matches=candidate_matches or [],
            review_reasons=review_reasons or ["fuzzy_name_neighbor"],
            created_at=NOW,
            resolved_at=None,
        )
    )


class DeterministicAuditRepository:
    def __init__(self, session: Session, *, similarity_available: bool = True) -> None:
        self._exact = SqlAlchemyCompanyIdentityRepository(session)
        self._similarity_available = similarity_available
        self.similarity_calls: list[tuple[frozenset[str], int]] = []

    async def find_exact_name_owners(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        return await self._exact.find_exact_name_owners(names)

    async def find_evidence_owner_ids(self, identity: CompanyIdentityInput) -> frozenset[UUID]:
        return await self._exact.find_evidence_owner_ids(identity)

    async def find_similar_names(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        self.similarity_calls.append((names, limit))
        assert 1 <= limit <= 20
        if not self._similarity_available or "alphalabs" not in names:
            return ()
        return (
            CompanyIdentityCandidateMatch(
                company_id=COMPANY_B,
                canonical_name="Alpha Lab",
                normalized_name="alphalab",
                match_kind="fuzzy_canonical",
                score=Decimal(94),
            ),
        )

    def similarity_search_available(self) -> bool:
        return self._similarity_available


def _snapshot(session: Session) -> tuple[tuple[object, ...], ...]:
    statements = (
        select(
            Company.id,
            Company.canonical_name,
            Company.normalized_name,
            Company.website,
            Company.normalized_website,
        ).order_by(Company.id),
        select(
            CompanyAlias.id,
            CompanyAlias.company_id,
            CompanyAlias.alias,
            CompanyAlias.normalized_alias,
        ).order_by(CompanyAlias.id),
        select(JobEntry.id, JobEntry.company_id, JobEntry.normalized_url).order_by(JobEntry.id),
        select(
            CandidateFact.id,
            CandidateFact.company_id,
            CandidateFact.normalized_name,
            CandidateFact.decision_status,
        ).order_by(CandidateFact.id),
        select(
            CompanyIdentityReviewItem.id,
            CompanyIdentityReviewItem.status,
            CompanyIdentityReviewItem.normalized_name,
        ).order_by(CompanyIdentityReviewItem.id),
    )
    return tuple(tuple(session.execute(statement)) for statement in statements)


def _seed_all_findings(session: Session) -> None:
    session.add_all(
        (
            _company(
                COMPANY_A,
                "Alpha Labs",
                "stale-alpha-normalized",
                website="https://shared.example/about",
            ),
            _company(
                COMPANY_B,
                "Alpha Lab",
                "alphalab",
                website="https://shared.example/about",
            ),
            _company(COMPANY_C, "Gamma", "gamma"),
            CompanyAlias(
                id=UUID("10000000-0000-0000-0000-000000000001"),
                company_id=COMPANY_B,
                alias="Alpha Labs",
                normalized_alias="stale-alpha-normalized",
            ),
            CompanyAlias(
                id=UUID("10000000-0000-0000-0000-000000000002"),
                company_id=COMPANY_B,
                alias="Beta Alias",
                normalized_alias="betaalias",
            ),
            CompanyAlias(
                id=UUID("10000000-0000-0000-0000-000000000099"),
                company_id=ORPHAN_COMPANY,
                alias="Orphan Public Alias",
                normalized_alias="orphanpublicalias",
            ),
            _job_entry(
                COMPANY_C,
                UUID("20000000-0000-0000-0000-000000000001"),
                "https://jobs.lever.co/gamma?token=drop",
            ),
            _job_entry(
                COMPANY_C,
                UUID("20000000-0000-0000-0000-000000000002"),
                "https://jobs.ashbyhq.com/gamma?secret=drop",
            ),
            _accepted_fact(COMPANY_C),
        )
    )
    filing = RegulatoryFiling(
        company_id=COMPANY_C,
        filing_type=FilingType.BUSINESS_LICENSE,
        filing_number="CN-123",
        filing_name="Gamma filing",
    )
    session.add(filing)
    _pending_review(session)
    session.commit()
    session.execute(
        text("UPDATE companies SET website = :raw_website WHERE id IN (:company_a, :company_b)"),
        {
            "raw_website": "https://user:password@shared.example/about?token=drop",
            "company_a": str(COMPANY_A),
            "company_b": str(COMPANY_B),
        },
    )
    session.execute(
        text("UPDATE regulatory_filings SET filing_number = :raw_number WHERE id = :filing_id"),
        {"raw_number": "Legacy Number", "filing_id": str(filing.id)},
    )
    session.commit()


def test_audit_reports_all_historical_categories_without_mutating_database(
    session: Session,
) -> None:
    _seed_all_findings(session)
    before = _snapshot(session)
    pending_company = _company(
        UUID("00000000-0000-0000-0000-000000000004"),
        "Must Not Autoflush",
        "mustnotautoflush",
    )
    session.add(pending_company)
    statements: list[str] = []
    flushes: list[object] = []
    event.listen(
        session.bind,
        "before_cursor_execute",
        lambda _connection, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    event.listen(session, "before_flush", lambda *_args: flushes.append(object()))
    state_before = (
        frozenset(session.new),
        frozenset(session.dirty),
        frozenset(session.deleted),
    )

    repository = DeterministicAuditRepository(session)
    with (
        patch.object(session, "flush", side_effect=AssertionError("audit flushed")),
        patch.object(session, "commit", side_effect=AssertionError("audit committed")),
        patch.object(session, "delete", side_effect=AssertionError("audit deleted")),
    ):
        report = CompanyIdentityAuditService(session, repository).build()

    with session.no_autoflush:
        after = _snapshot(session)
    state_after = (
        frozenset(session.new),
        frozenset(session.dirty),
        frozenset(session.deleted),
    )
    codes = {finding.code for finding in report.findings}
    assert codes >= {
        "accepted_candidate_name_unrepresented",
        "canonical_name_normalized_drift",
        "cross_table_name_owner",
        "filing_number_normalized_drift",
        "fuzzy_name_cluster",
        "incompatible_recruitment_identities",
        "orphan_alias",
        "pending_review_owner_changed",
        "shared_website_identity",
        "website_normalized_drift",
    }
    assert after == before
    assert state_after == state_before
    assert flushes == []
    assert statements
    assert all(
        statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements
    )
    assert report.scanned_companies == 3
    assert report.scanned_aliases == 3
    assert report.scanned_review_items == 1
    assert sum(report.finding_counts.values()) == len(report.findings)
    assert tuple(report.finding_counts) == tuple(IdentityAuditSeverity)
    assert repository.similarity_calls
    assert [next(iter(names)) for names, _limit in repository.similarity_calls] == sorted(
        next(iter(names)) for names, _limit in repository.similarity_calls
    )
    assert all(len(names) == 1 and limit == 20 for names, limit in repository.similarity_calls)
    recruitment_finding = next(
        finding
        for finding in report.findings
        if finding.code == "incompatible_recruitment_identities"
    )
    assert recruitment_finding.evidence_codes == ("job_entry",)


def test_audit_is_byte_stable_ordered_and_sanitizes_public_urls(session: Session) -> None:
    _seed_all_findings(session)
    service = CompanyIdentityAuditService(session, DeterministicAuditRepository(session))

    first = service.build()
    second = service.build()
    first_json = first.model_dump_json()
    severity_order = {severity: index for index, severity in enumerate(IdentityAuditSeverity)}
    finding_keys = [
        (
            severity_order[finding.severity],
            finding.code,
            tuple(str(company_id) for company_id in finding.company_ids),
            finding.finding_id,
        )
        for finding in first.findings
    ]

    assert second.model_dump_json() == first_json
    assert finding_keys == sorted(finding_keys)
    assert len({finding.finding_id for finding in first.findings}) == len(first.findings)
    assert "https://shared.example/about" in first_json
    assert "user:password" not in first_json
    assert "token=drop" not in first_json
    assert "secret=drop" not in first_json


def test_url_shaped_historical_display_names_cannot_leak_credentials_or_query(
    session: Session,
) -> None:
    session.add(
        _company(
            COMPANY_A,
            "https://user:password@display.example/name?token=drop",
            "stale-normalized-name",
        )
    )
    session.commit()

    report_json = (
        CompanyIdentityAuditService(
            session, DeterministicAuditRepository(session, similarity_available=False)
        )
        .build()
        .model_dump_json()
    )

    assert "user:password" not in report_json
    assert "token=drop" not in report_json


def test_missing_similarity_capability_is_reported_fail_closed(session: Session) -> None:
    session.add(_company(COMPANY_A, "Alpha Labs", "alphalabs"))
    session.commit()

    report = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    ).build()

    finding = next(item for item in report.findings if item.code == "similarity_search_unavailable")
    assert finding.severity is IdentityAuditSeverity.IMPORTANT
    assert finding.company_ids == (COMPANY_A,)


def test_alias_only_identity_sources_report_missing_similarity_fail_closed(
    session: Session,
) -> None:
    session.add(
        CompanyAlias(
            company_id=ORPHAN_COMPANY,
            alias="Orphan Similarity Source",
            normalized_alias="orphansimilaritysource",
        )
    )
    session.commit()
    service = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    )

    first = service.build()
    second = service.build()

    assert second.model_dump_json() == first.model_dump_json()
    assert (
        first.scanned_companies,
        first.scanned_aliases,
        first.scanned_review_items,
    ) == (0, 1, 0)
    assert first.finding_counts == {
        IdentityAuditSeverity.CRITICAL: 0,
        IdentityAuditSeverity.IMPORTANT: 2,
        IdentityAuditSeverity.MINOR: 0,
    }
    assert [finding.code for finding in first.findings] == [
        "orphan_alias",
        "similarity_search_unavailable",
    ]
    unavailable = first.findings[1]
    assert unavailable.severity is IdentityAuditSeverity.IMPORTANT
    assert unavailable.company_ids == (ORPHAN_COMPANY,)


# Pending review rows retain reasons, not prior exact-owner UUID sets. These tests
# cover only current ownership/cardinality changes that those reasons can prove.
def test_unchanged_ambiguous_exact_pending_review_is_not_called_owner_changed(
    session: Session,
) -> None:
    session.add_all(
        (
            _company(COMPANY_A, "Alpha Labs", "alphalabs"),
            _company(COMPANY_B, "Beta Labs", "betalabs"),
            CompanyAlias(
                company_id=COMPANY_B,
                alias="Alpha Labs",
                normalized_alias="alphalabs",
            ),
        )
    )
    _pending_review(
        session,
        candidate_name="Alpha Labs",
        normalized_name="alphalabs",
        review_reasons=["ambiguous_exact_owner"],
    )
    session.commit()

    report = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    ).build()

    assert "pending_review_owner_changed" not in {finding.code for finding in report.findings}


def test_fuzzy_pending_review_detects_same_candidate_becoming_exact_owner(
    session: Session,
) -> None:
    session.add(_company(COMPANY_B, "Alpha Lab", "alphalab"))
    session.add(
        CompanyAlias(
            company_id=COMPANY_B,
            alias="Alpha Labs",
            normalized_alias="alphalabs",
        )
    )
    _pending_review(
        session,
        candidate_name="Alpha Labs",
        normalized_name="alphalabs",
        candidate_matches=[
            {
                "company_id": str(COMPANY_B),
                "canonical_name": "Alpha Lab",
                "normalized_name": "alphalab",
                "match_kind": "fuzzy_canonical",
                "score": "94",
                "conflict_reasons": [],
            }
        ],
        review_reasons=["fuzzy_name_neighbor"],
    )
    session.commit()

    report = CompanyIdentityAuditService(session, DeterministicAuditRepository(session)).build()

    finding = next(item for item in report.findings if item.code == "pending_review_owner_changed")
    assert finding.company_ids == (COMPANY_B,)


def test_ambiguous_pending_review_detects_owner_set_collapsing_to_one(
    session: Session,
) -> None:
    session.add(_company(COMPANY_A, "Alpha Labs", "alphalabs"))
    _pending_review(
        session,
        candidate_name="Alpha Labs",
        normalized_name="alphalabs",
        review_reasons=["ambiguous_exact_owner"],
    )
    session.commit()

    report = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    ).build()

    finding = next(item for item in report.findings if item.code == "pending_review_owner_changed")
    assert finding.company_ids == (COMPANY_A,)


def test_nonambiguous_pending_review_detects_current_multiple_exact_owners(
    session: Session,
) -> None:
    session.add_all(
        (
            _company(COMPANY_A, "Alpha Labs", "alphalabs"),
            _company(COMPANY_B, "Beta Labs", "betalabs"),
            CompanyAlias(
                company_id=COMPANY_B,
                alias="Alpha Labs",
                normalized_alias="alphalabs",
            ),
        )
    )
    _pending_review(
        session,
        candidate_name="Alpha Labs",
        normalized_name="alphalabs",
        review_reasons=["website_identity_conflict"],
    )
    session.commit()

    report = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    ).build()

    finding = next(item for item in report.findings if item.code == "pending_review_owner_changed")
    assert finding.company_ids == (COMPANY_A, COMPANY_B)


def test_empty_database_has_explicit_zero_denominators_and_no_findings(
    session: Session,
) -> None:
    report = CompanyIdentityAuditService(session, DeterministicAuditRepository(session)).build()

    assert report.model_dump(mode="json") == {
        "findings": [],
        "scanned_companies": 0,
        "scanned_aliases": 0,
        "scanned_review_items": 0,
        "finding_counts": {"critical": 0, "important": 0, "minor": 0},
    }


def test_large_conflict_groups_keep_public_finding_fields_bounded(
    session: Session,
) -> None:
    for index in range(101):
        session.add(
            _company(
                UUID(int=index + 1),
                f"Bounded Company {index:03d}",
                f"boundedcompany{index:03d}",
                website="https://shared.example/careers?drop=1",
            )
        )
    session.commit()
    service = CompanyIdentityAuditService(
        session, DeterministicAuditRepository(session, similarity_available=False)
    )

    first = service.build()
    second = service.build()

    assert second.model_dump_json() == first.model_dump_json()
    assert all(len(finding.company_ids) <= 100 for finding in first.findings)
    assert all(len(finding.display_names) <= 100 for finding in first.findings)
    final_company_id = UUID(int=101)
    final_website_finding = next(
        finding
        for finding in first.findings
        if finding.code == "shared_website_identity" and final_company_id in finding.company_ids
    )
    assert final_website_finding.display_names == (
        "Bounded Company 100",
        "https://shared.example/careers",
    )


def test_real_sqlite_repository_never_claims_fuzzy_history_is_clean(session: Session) -> None:
    session.add(_company(COMPANY_A, "Alpha Labs", "alphalabs"))
    session.commit()

    report = CompanyIdentityAuditService(
        session, SqlAlchemyCompanyIdentityRepository(session)
    ).build()

    assert {finding.code for finding in report.findings} >= {"similarity_search_unavailable"}
    assert (
        asyncio.run(
            SqlAlchemyCompanyIdentityRepository(session).find_similar_names(
                frozenset({"alphalabs"}), limit=20
            )
        )
        == ()
    )
