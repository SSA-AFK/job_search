"""Traceable collection-run orchestration without infrastructure coupling."""

import re
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.ingestion.contracts import Provider, ProviderQuery, RawDocument
from app.ingestion.deduplication.company import CompanyDeduplicator, CompanyMatch
from app.ingestion.deduplication.job import JobDeduplicator
from app.ingestion.errors import ProviderError
from app.ingestion.extraction.crew import Extractor
from app.ingestion.extraction.prompts import assign_evidence_ids
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    JobCandidate,
)
from app.ingestion.normalization.company import normalize_company
from app.ingestion.normalization.job import normalize_job
from app.ingestion.persistence.contracts import (
    CompanyFieldEvidence,
    CompanyFieldName,
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
    NormalizedJobRecord,
)
from app.ingestion.persistence.result import PersistenceResult
from app.ingestion.persistence.service import PersistenceService
from app.ingestion.result import IngestionResult, RunResultSource
from app.models import CollectionStatus
from app.models.base import utc_now

_TERMINAL_STATUSES = {
    CollectionStatus.SUCCEEDED,
    CollectionStatus.PARTIAL,
    CollectionStatus.FAILED,
}
_PUBLIC_CODE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


class CrawlRunState(RunResultSource, Protocol):
    pass


class CollectionRequestState(Protocol):
    query: str


class CrawlRunRepository(Protocol):
    def start_or_get_terminal(self, run_id: UUID) -> CrawlRunState | None: ...

    def get_request_for_run(self, run: CrawlRunState) -> CollectionRequestState | None: ...

    def finish(
        self,
        run: CrawlRunState,
        *,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> CrawlRunState: ...


class NormalizedBatchBuilder:
    """Maps validated extraction output into the persistence boundary DTO."""

    def __init__(
        self,
        *,
        company_deduplicator: CompanyDeduplicator | None = None,
        job_deduplicator: JobDeduplicator | None = None,
    ) -> None:
        self.company_deduplicator = company_deduplicator
        self.job_deduplicator = job_deduplicator

    async def build(
        self,
        *,
        company: CompanyRef,
        profile: CompanyProfileCandidate,
        jobs: Sequence[JobCandidate],
        documents: Sequence[RawDocument],
    ) -> NormalizedBatch:
        collected_at = utc_now()
        evidence_ids = assign_evidence_ids(documents)
        company_candidate = CompanyCandidate(
            name=profile.name,
            website=profile.website,
            description=profile.description,
            evidence_ids=profile.evidence_ids,
            confidence=profile.confidence,
        )
        company_match = (
            await self.company_deduplicator.resolve(company_candidate)
            if self.company_deduplicator is not None
            else CompanyMatch("new", None)
        )
        field_evidence = self._field_evidence(company_candidate)
        normalized_jobs: list[NormalizedJobRecord] = []
        for job in jobs:
            if job.provider is None or job.source_raw_id is None or job.apply_url is None:
                continue
            job_id = None
            if company_match.company_id is not None and self.job_deduplicator is not None:
                job_id = (await self.job_deduplicator.resolve(company_match.company_id, job)).job_posting_id
            normalized_jobs.append(
                NormalizedJobRecord(
                    candidate=normalize_job(job),
                    job_posting_id=job_id,
                    source_evidence_id=job.evidence_ids[0],
                    apply_url=job.apply_url,
                    posted_at=job.posted_at,
                    seen_at=collected_at,
                )
            )
        return NormalizedBatch(
            documents=tuple(
                NormalizedDocument(
                    evidence_id=evidence_id,
                    document=document,
                    fetched_at=collected_at,
                )
                for evidence_id, document in zip(evidence_ids, documents, strict=True)
            ),
            company=NormalizedCompanyRecord(
                candidate=normalize_company(company_candidate),
                company_id=company_match.company_id,
                field_evidence=field_evidence,
            ),
            jobs=tuple(normalized_jobs),
            collected_at=collected_at,
        )

    @staticmethod
    def _field_evidence(candidate: CompanyCandidate) -> tuple[CompanyFieldEvidence, ...]:
        evidence_id = candidate.evidence_ids[0]
        fields: list[CompanyFieldName] = ["canonical_name"]
        if candidate.website is not None:
            fields.append("website")
        if candidate.description is not None:
            fields.append("description")
        return tuple(
            CompanyFieldEvidence(
                field_name=field,
                evidence_id=evidence_id,
                confidence=candidate.confidence,
            )
            for field in fields
        )


class IngestionOrchestrator:
    def __init__(
        self,
        *,
        providers: Sequence[Provider],
        extractor: Extractor,
        batch_builder: NormalizedBatchBuilder,
        persistence: PersistenceService,
        runs: CrawlRunRepository,
    ) -> None:
        self.providers = list(providers)
        self.extractor = extractor
        self.batch_builder = batch_builder
        self.persistence = persistence
        self.runs = runs

    async def run(self, run_id: UUID) -> IngestionResult:
        try:
            run = self.runs.start_or_get_terminal(run_id)
        except ValueError:
            return IngestionResult(
                run_id=run_id,
                status=CollectionStatus.FAILED,
                company_id=None,
                providers_attempted=(),
                documents_found=0,
                jobs_found=0,
                jobs_written=0,
                error_code="invalid_run_state",
            )
        except Exception as exc:  # noqa: BLE001 - invalid repository state has no runnable pipeline.
            return IngestionResult(
                run_id=run_id,
                status=CollectionStatus.FAILED,
                company_id=None,
                providers_attempted=(),
                documents_found=0,
                jobs_found=0,
                jobs_written=0,
                error_code=_public_code(exc, "ingestion_failed"),
            )
        if run is None:
            return IngestionResult.unknown_run(run_id)
        if run.status in _TERMINAL_STATUSES:
            return IngestionResult.from_run(run)

        providers_attempted: tuple[str, ...] = ()
        documents: tuple[RawDocument, ...] = ()
        jobs_found = 0
        try:
            request = self.runs.get_request_for_run(run)
            if request is None:
                raise _PipelineError("invalid_run")
            providers_attempted, documents, provider_error = await self._collect(request.query)
            if not documents:
                return self._finish(
                    run,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=0,
                    jobs_found=0,
                    persistence=None,
                    error_code=provider_error or "no_documents",
                )
            company = CompanyRef(name=request.query)
            profile = await self.extractor.extract_profile(company, documents)
            jobs = await self.extractor.extract_jobs(company, documents)
            jobs_found = len(jobs)
            batch = await self.batch_builder.build(
                company=company,
                profile=profile,
                jobs=jobs,
                documents=documents,
            )
            persisted = self.persistence.persist(batch, run.id)
            return self._finish(
                run,
                status=CollectionStatus.PARTIAL if provider_error else CollectionStatus.SUCCEEDED,
                providers_attempted=providers_attempted,
                documents_found=len(documents),
                jobs_found=jobs_found,
                persistence=persisted,
                error_code=provider_error,
            )
        except Exception as exc:  # noqa: BLE001 - terminal failure is required for unknown pipeline errors.
            return self._finish(
                run,
                status=CollectionStatus.FAILED,
                providers_attempted=providers_attempted,
                documents_found=len(documents),
                jobs_found=jobs_found,
                persistence=None,
                error_code=_public_code(exc, "ingestion_failed"),
            )

    async def _collect(
        self, query: str
    ) -> tuple[tuple[str, ...], tuple[RawDocument, ...], str | None]:
        attempted: list[str] = []
        documents: list[RawDocument] = []
        first_error: str | None = None
        provider_query = ProviderQuery(query=query)
        for provider in self.providers:
            attempted.append(_provider_name(provider))
            try:
                result = await provider.search(provider_query)
            except ProviderError as exc:
                first_error = first_error or _public_code(exc, "provider_unavailable")
            except Exception:  # noqa: BLE001 - providers are an untrusted external boundary.
                first_error = first_error or "provider_unavailable"
            else:
                documents.extend(result.documents)
        return tuple(attempted), tuple(documents), first_error

    def _finish(
        self,
        run: CrawlRunState,
        *,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
    ) -> IngestionResult:
        finished = self.runs.finish(
            run,
            status=status,
            providers_attempted=providers_attempted,
            documents_found=documents_found,
            jobs_found=jobs_found,
            persistence=persistence,
            error_code=error_code,
            error_detail=error_code,
        )
        return IngestionResult.from_run(finished)


class _PipelineError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _provider_name(provider: Provider) -> str:
    value = getattr(provider, "name", type(provider).__name__)
    return str(value)[:50]


def _public_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and _PUBLIC_CODE.fullmatch(code) else fallback
