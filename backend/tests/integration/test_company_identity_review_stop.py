from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityNameOwner,
)
from app.company_identity.resolver import CompanyIdentityResolver
from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.deduplication.semantic import DuplicateDecision
from app.ingestion.errors import RetryableInfrastructureError
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    FilingCandidate,
    JobCandidate,
    ProfileExtraction,
)
from app.ingestion.runtime import build_ingestion_orchestrator
from app.models import (
    Base,
    CollectionStatus,
    Company,
    CompanyAlias,
    CompanyIdentityReviewItem,
    CompanySource,
    CrawlRun,
    JobPosting,
    RegulatoryFiling,
    SourceDocument,
)

OBSERVED_AT = datetime(2026, 8, 7, 12, tzinfo=UTC)


class Provider:
    name = "official"

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        return ProviderResult(
            documents=(
                RawDocument(
                    provider="official",
                    external_id="company-profile",
                    url="https://example.ai/about",
                    title="Example AI",
                    text="Example Artificial Intelligence profile and ICP-42 filing.",
                    published_at=None,
                ),
            )
        )


class Extractor:
    async def discover(
        self, _documents: tuple[RawDocument, ...]
    ) -> tuple[CompanyCandidate, ...]:
        return (
            CompanyCandidate(
                name="Example Artificial Intelligenc",
                aliases=("Example AI",),
                website="https://example.ai",
                evidence_ids=("company-profile",),
                confidence=1,
            ),
        )

    async def extract_profile(
        self, company: CompanyRef, _documents: tuple[RawDocument, ...]
    ) -> ProfileExtraction:
        return ProfileExtraction(
            profile=CompanyProfileCandidate(
                name=company.name,
                website=company.website,
                description="Example AI profile",
                evidence_ids=("company-profile",),
                confidence=1,
            ),
            filings=(
                FilingCandidate(
                    title="Example AI ICP",
                    filing_type="icp",
                    filing_number="ICP-42",
                    evidence_ids=("company-profile",),
                    confidence=1,
                ),
            ),
        )

    async def extract_jobs(
        self, company: CompanyRef, _documents: tuple[RawDocument, ...]
    ) -> tuple[JobCandidate, ...]:
        return (
            JobCandidate(
                company_name=company.name,
                title="Engineer",
                evidence_ids=("company-profile",),
                confidence=1,
            ),
        )


class SemanticJudge:
    async def jobs_are_duplicates(self, _left: object, _right: object) -> DuplicateDecision:
        return DuplicateDecision(False)


class FuzzyIdentityRepository:
    async def find_exact_name_owners(
        self, _names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        return ()

    async def find_evidence_owner_ids(
        self, _identity: CompanyIdentityInput
    ) -> frozenset[UUID]:
        return frozenset()

    async def find_similar_names(
        self, _names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        assert limit == 20
        return (
            CompanyIdentityCandidateMatch(
                company_id=UUID("00000000-0000-0000-0000-000000000001"),
                canonical_name="Example Artificial Intelligence",
                normalized_name="exampleartificialintelligence",
                match_kind="fuzzy_name",
                score=Decimal(96),
            ),
        )

    def similarity_search_available(self) -> bool:
        return True


class RecordingCache:
    def __init__(self) -> None:
        self.invalidated: list[UUID] = []

    def invalidate_company(self, company_id: UUID) -> None:
        self.invalidated.append(company_id)


def business_table_counts(session: Session) -> tuple[int, ...]:
    tables = (
        Company,
        CompanyAlias,
        JobPosting,
        RegulatoryFiling,
        SourceDocument,
        CompanySource,
    )
    return tuple(
        session.scalar(select(func.count()).select_from(table)) or 0
        for table in tables
    )


@pytest.mark.asyncio
async def test_review_required_records_once_and_writes_no_business_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'identity-review.sqlite3'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    cache = RecordingCache()
    monkeypatch.setattr("app.ingestion.orchestrator.utc_now", lambda: OBSERVED_AT)
    monkeypatch.setattr(
        "app.ingestion.runtime.configured_company_cache", lambda _url: cache
    )

    with Session(engine, expire_on_commit=False) as state, Session(
        engine, expire_on_commit=False
    ) as dedup, Session(engine, expire_on_commit=False) as review, Session(
        engine, expire_on_commit=False
    ) as write:
        repository = CollectionRepository(state)
        _first_request, first_run = repository.create_request(
            "Example Artificial Intelligenc", "exampleartificialintelligenc"
        )
        state.commit()
        first_run_id = first_run.id
        orchestrator = build_ingestion_orchestrator(
            run_state_session=state,
            dedup_read_session=dedup,
            identity_review_write_session=review,
            persistence_write_session=write,
            providers=(Provider(),),
            extractor=Extractor(),
            semantic_judge=SemanticJudge(),
        )
        orchestrator.batch_builder.identity_resolver = CompanyIdentityResolver(
            FuzzyIdentityRepository()
        )
        with Session(engine) as verification:
            before = business_table_counts(verification)

        first_result = await orchestrator.run(first_run_id)
        _second_request, second_run = repository.create_request(
            "Example Artificial Intelligenc", "exampleartificialintelligenc"
        )
        state.commit()
        second_result = await orchestrator.run(second_run.id)

    with Session(engine) as verification:
        after = business_table_counts(verification)
        items = tuple(verification.scalars(select(CompanyIdentityReviewItem)))

    assert first_result.status is CollectionStatus.FAILED
    assert second_result.status is CollectionStatus.FAILED
    assert first_result.error_code == "company_identity_review_required"
    assert second_result.error_code == "company_identity_review_required"
    assert after == before == (0, 0, 0, 0, 0, 0)
    assert len(items) == 1
    assert items[0].first_crawl_run_id == first_run_id
    assert items[0].candidate_name == "Example Artificial Intelligenc"
    assert items[0].created_at == OBSERVED_AT
    assert cache.invalidated == []


@pytest.mark.asyncio
async def test_review_store_unavailable_is_retryable_and_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'identity-review-failure.sqlite3'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    cache = RecordingCache()
    monkeypatch.setattr("app.ingestion.orchestrator.utc_now", lambda: OBSERVED_AT)
    monkeypatch.setattr(
        "app.ingestion.runtime.configured_company_cache", lambda _url: cache
    )

    with Session(engine, expire_on_commit=False) as state, Session(
        engine, expire_on_commit=False
    ) as dedup, Session(engine, expire_on_commit=False) as review, Session(
        engine, expire_on_commit=False
    ) as write:
        repository = CollectionRepository(state)
        _request, run = repository.create_request(
            "Example Artificial Intelligenc", "exampleartificialintelligenc"
        )
        state.commit()
        run_id = run.id
        orchestrator = build_ingestion_orchestrator(
            run_state_session=state,
            dedup_read_session=dedup,
            identity_review_write_session=review,
            persistence_write_session=write,
            providers=(Provider(),),
            extractor=Extractor(),
            semantic_judge=SemanticJudge(),
        )
        orchestrator.batch_builder.identity_resolver = CompanyIdentityResolver(
            FuzzyIdentityRepository()
        )

        @event.listens_for(review, "do_orm_execute")
        def fail_review_store(*_args: object) -> None:
            raise OperationalError(
                "insert identity review",
                {},
                RuntimeError("private database detail"),
            )

        with pytest.raises(RetryableInfrastructureError) as raised:
            await orchestrator.run(run_id)

    with Session(engine) as verification:
        persisted_run = verification.get(CrawlRun, run_id)
        assert persisted_run is not None
        assert persisted_run.status is CollectionStatus.RUNNING
        assert business_table_counts(verification) == (0, 0, 0, 0, 0, 0)
        assert verification.scalar(
            select(func.count()).select_from(CompanyIdentityReviewItem)
        ) == 0
    assert raised.value.claim_token is not None
    assert str(raised.value) == "retryable infrastructure failure"
    assert cache.invalidated == []
