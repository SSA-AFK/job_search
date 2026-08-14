"""Explicit caller-owned session composition for ingestion workers."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.cache.redis import configured_company_cache
from app.collection.repository import CollectionRepository
from app.company_identity.contracts import (
    CompanyIdentityReviewDraft,
    IdentityReviewRecordSummary,
)
from app.company_identity.resolver import CompanyIdentityResolver
from app.company_identity.service import IdentitySearchUnavailable, record_identity_review
from app.core.config import settings
from app.ingestion.contracts import Provider
from app.ingestion.deduplication.job import JobDeduplicator
from app.ingestion.direct_ats import DirectAtsPersistence
from app.ingestion.entry_discovery.service import EntryDiscoveryService
from app.ingestion.errors import RetryableInfrastructureError
from app.ingestion.orchestrator import (
    CrawlRunRepository,
    IngestionOrchestrator,
    NormalizedBatchBuilder,
)
from app.ingestion.persistence.service import PersistenceService
from app.ingestion.repositories import (
    SqlAlchemyCompanyDeduplicationRepository,
    SqlAlchemyJobDeduplicationRepository,
)


@dataclass(frozen=True)
class RuntimeComponents:
    providers: Sequence[Provider]


@dataclass(frozen=True)
class SqlAlchemyIdentityReviewRecorder:
    session: Session

    def record(
        self, *, crawl_run_id: UUID, draft: CompanyIdentityReviewDraft
    ) -> IdentityReviewRecordSummary:
        try:
            return record_identity_review(
                self.session,
                crawl_run_id=crawl_run_id,
                draft=draft,
            )
        except IdentitySearchUnavailable as error:
            raise RetryableInfrastructureError() from error


def build_ingestion_orchestrator(
    *,
    run_state_session: Session,
    dedup_read_session: Session,
    identity_review_write_session: Session,
    persistence_write_session: Session,
    providers: Sequence[Provider],
) -> IngestionOrchestrator:
    """Build without closing sessions; the caller owns all session lifecycles."""
    runtime_sessions = (
        run_state_session,
        dedup_read_session,
        identity_review_write_session,
        persistence_write_session,
    )
    if len({id(session) for session in runtime_sessions}) != 4:
        raise ValueError("ingestion runtime requires distinct sessions")
    builder = NormalizedBatchBuilder(
        identity_resolver=CompanyIdentityResolver(
            SqlAlchemyCompanyDeduplicationRepository(dedup_read_session)
        ),
        job_deduplicator=JobDeduplicator(
            SqlAlchemyJobDeduplicationRepository(dedup_read_session)
        ),
    )
    return IngestionOrchestrator(
        providers=providers, batch_builder=builder,
        persistence=PersistenceService(
            persistence_write_session,
            cache=configured_company_cache(settings.cache_redis_url),
        ),
        direct_ats_persistence=DirectAtsPersistence(
            persistence_write_session,
            cache=configured_company_cache(settings.cache_redis_url),
        ),
        entry_discovery_service=(
            EntryDiscoveryService(serper_provider=serper_provider)
            if (serper_provider := next((provider for provider in providers if getattr(provider, "name", None) == "serper"), None)) is not None
            else None
        ),
        runs=cast(CrawlRunRepository, CollectionRepository(run_state_session)),
        identity_review_recorder=SqlAlchemyIdentityReviewRecorder(
            identity_review_write_session
        ),
    )
