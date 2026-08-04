"""Explicit caller-owned session composition for ingestion workers."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy.orm import Session

from app.cache.redis import configured_company_cache
from app.collection.repository import CollectionRepository
from app.core.config import settings
from app.ingestion.contracts import Provider
from app.ingestion.deduplication.company import CompanyDeduplicator
from app.ingestion.deduplication.job import JobDeduplicator
from app.ingestion.deduplication.semantic import SemanticDuplicateJudge
from app.ingestion.extraction.crew import Extractor
from app.ingestion.orchestrator import (
    CrawlRunRepository,
    IngestionOrchestrator,
    NormalizedBatchBuilder,
)


@dataclass(frozen=True)
class RuntimeComponents:
    providers: Sequence[Provider]
    extractor: Extractor
    semantic_judge: SemanticDuplicateJudge
from app.ingestion.persistence.service import PersistenceService
from app.ingestion.repositories import (
    SqlAlchemyCompanyDeduplicationRepository,
    SqlAlchemyJobDeduplicationRepository,
)


def build_ingestion_orchestrator(
    *, run_state_session: Session, dedup_read_session: Session, persistence_write_session: Session,
    providers: Sequence[Provider], extractor: Extractor, semantic_judge: SemanticDuplicateJudge,
) -> IngestionOrchestrator:
    """Build without closing sessions; the caller owns all session lifecycles."""
    if len({id(run_state_session), id(dedup_read_session), id(persistence_write_session)}) != 3:
        raise ValueError("ingestion runtime requires distinct sessions")
    builder = NormalizedBatchBuilder(
        company_deduplicator=CompanyDeduplicator(SqlAlchemyCompanyDeduplicationRepository(dedup_read_session)),
        job_deduplicator=JobDeduplicator(SqlAlchemyJobDeduplicationRepository(dedup_read_session), semantic_judge),
    )
    return IngestionOrchestrator(
        providers=providers, extractor=extractor, batch_builder=builder,
        persistence=PersistenceService(
            persistence_write_session,
            cache=configured_company_cache(settings.cache_redis_url),
        ),
        runs=cast(CrawlRunRepository, CollectionRepository(run_state_session)),
    )
