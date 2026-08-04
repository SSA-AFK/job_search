"""Traceable collection-run orchestration without infrastructure coupling."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import HttpUrl
from sqlalchemy.exc import OperationalError

from app.core.normalization import normalize_name, normalize_url
from app.ingestion.contracts import (
    Provider,
    ProviderQuery,
    RawDocument,
    WebsiteDependentProvider,
)
from app.ingestion.deduplication.company import CompanyDeduplicator, CompanyMatch
from app.ingestion.deduplication.job import JobDeduplicator
from app.ingestion.errors import (
    ProviderError,
    RetryableInfrastructureError,
    RunClaimError,
)
from app.ingestion.extraction.crew import Extractor
from app.ingestion.extraction.prompts import assign_evidence_ids
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    JobCandidate,
    ProfileExtraction,
)
from app.ingestion.normalization.company import normalize_company
from app.ingestion.normalization.job import normalize_job
from app.ingestion.persistence.contracts import (
    CompanyFieldEvidence,
    CompanyFieldName,
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
    NormalizedFilingRecord,
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


@dataclass(frozen=True)
class _ProviderOutcome:
    name: str
    documents: tuple[RawDocument, ...]
    issue: str | None


@dataclass(frozen=True)
class _Diagnostic:
    stage: str
    code: str
    provider: str | None = None

    def payload(self) -> dict[str, str]:
        value = {"stage": self.stage, "code": self.code}
        if self.provider is not None:
            value["provider"] = self.provider
        return value


class CrawlRunState(RunResultSource, Protocol):
    claim_token: str | None
    started_at: datetime | None


class CrawlRunClaim(Protocol):
    run: CrawlRunState
    claimed: bool
    claim_token: str | None


class CollectionRequestState(Protocol):
    query: str


class CrawlRunRepository(Protocol):
    def claim_queued(self, run_id: UUID) -> CrawlRunClaim | None: ...

    def get_run(self, run_id: UUID) -> CrawlRunState | None: ...

    def recover_claim_token(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> str | None: ...

    def get_request_for_run(self, run: CrawlRunState) -> CollectionRequestState | None: ...

    def owns_claim(self, run_id: UUID, *, expected_claim_token: str) -> bool: ...

    def finish(
        self,
        run: CrawlRunState,
        *,
        expected_claim_token: str,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> CrawlRunState: ...

    def requeue_for_retry(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> CrawlRunState | None: ...

    def fail_retry_exhausted(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> CrawlRunState | None: ...


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
        profile: ProfileExtraction,
        jobs: Sequence[JobCandidate],
        documents: Sequence[RawDocument],
        discovered: CompanyCandidate | None = None,
    ) -> NormalizedBatch:
        collected_at = utc_now()
        evidence_ids = assign_evidence_ids(documents)
        document_by_evidence = dict(zip(evidence_ids, documents, strict=True))
        profile_candidate = profile.profile
        self._require_known_evidence(profile_candidate.evidence_ids, document_by_evidence)
        discovery = discovered or CompanyCandidate(
            name=profile_candidate.name,
            website=profile_candidate.website,
            description=profile_candidate.description,
            evidence_ids=profile_candidate.evidence_ids,
            confidence=profile_candidate.confidence,
        )
        self._require_known_evidence(discovery.evidence_ids, document_by_evidence)
        if normalize_name(profile_candidate.name) != normalize_name(discovery.name):
            raise _PipelineError("invalid_evidence")
        if (
            profile_candidate.website is not None
            and discovery.website is not None
            and normalize_url(str(profile_candidate.website))
            != normalize_url(str(discovery.website))
        ):
            raise _PipelineError("invalid_evidence")
        profile_description = _plain_text(profile_candidate.description)
        discovery_description = _plain_text(discovery.description)
        if profile_description is not None and discovery_description is not None and profile_description != discovery_description:
            raise _PipelineError("invalid_evidence")
        company_candidate = CompanyCandidate(
            name=discovery.name,
            website=profile_candidate.website or discovery.website,
            description=profile_description or discovery_description,
            evidence_ids=discovery.evidence_ids,
            confidence=discovery.confidence,
        )
        company_match = (
            await self.company_deduplicator.resolve(company_candidate)
            if self.company_deduplicator is not None
            else CompanyMatch("new", None)
        )
        field_evidence = self._field_evidence(
            discovery, profile_candidate, profile_description
        )
        normalized_jobs: list[NormalizedJobRecord] = []
        for job in jobs:
            self._require_known_evidence(job.evidence_ids, document_by_evidence)
            if normalize_name(job.company_name) != normalize_name(company.name):
                raise _PipelineError("invalid_evidence")
            if len(job.evidence_ids) > 1 and job.source_evidence_id is None:
                raise _PipelineError("invalid_evidence")
            source_evidence_id = job.source_evidence_id or job.evidence_ids[0]
            source_document = document_by_evidence[source_evidence_id]
            if source_document.external_id is None:
                raise _PipelineError("invalid_evidence")
            if job.provider is not None and job.provider != source_document.provider:
                raise _PipelineError("invalid_evidence")
            if job.source_raw_id is not None and job.source_raw_id != source_document.external_id:
                raise _PipelineError("invalid_evidence")
            resolved_job = JobCandidate.model_validate(
                {
                    **job.model_dump(),
                    "provider": source_document.provider,
                    "source_raw_id": source_document.external_id,
                    "apply_url": job.apply_url or source_document.url,
                }
            )
            if resolved_job.apply_url is None:
                raise _PipelineError("invalid_evidence")
            job_id = None
            if company_match.company_id is not None and self.job_deduplicator is not None:
                job_id = (await self.job_deduplicator.resolve(company_match.company_id, resolved_job)).job_posting_id
            normalized_jobs.append(
                NormalizedJobRecord(
                    candidate=normalize_job(resolved_job),
                    job_posting_id=job_id,
                    source_evidence_id=source_evidence_id,
                    apply_url=resolved_job.apply_url,
                    posted_at=resolved_job.posted_at,
                    seen_at=collected_at,
                )
            )
        normalized_filings: list[NormalizedFilingRecord] = []
        for filing in profile.filings:
            self._require_known_evidence(filing.evidence_ids, document_by_evidence)
            normalized_filings.append(
                NormalizedFilingRecord.from_candidate(
                    filing, source_evidence_id=filing.evidence_ids[0]
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
            filings=tuple(normalized_filings),
            collected_at=collected_at,
        )

    @staticmethod
    def _field_evidence(
        discovered: CompanyCandidate, profile: CompanyProfileCandidate, profile_description: str | None
    ) -> tuple[CompanyFieldEvidence, ...]:
        fields: list[tuple[CompanyFieldName, tuple[str, ...], float]] = [
            ("canonical_name", discovered.evidence_ids, discovered.confidence)
        ]
        if profile.website is not None:
            fields.append(("website", profile.evidence_ids, profile.confidence))
        elif discovered.website is not None:
            fields.append(("website", discovered.evidence_ids, discovered.confidence))
        if profile_description is not None:
            fields.append(("description", profile.evidence_ids, profile.confidence))
        elif _plain_text(discovered.description) is not None:
            fields.append(("description", discovered.evidence_ids, discovered.confidence))
        return tuple(
            CompanyFieldEvidence(
                field_name=field,
                evidence_id=evidence_id,
                confidence=confidence,
            )
            for field, evidence_ids, confidence in fields
            for evidence_id in evidence_ids
        )

    @staticmethod
    def _require_known_evidence(
        evidence_ids: Sequence[str], documents: dict[str, RawDocument]
    ) -> None:
        if not evidence_ids or any(evidence_id not in documents for evidence_id in evidence_ids):
            raise _PipelineError("invalid_evidence")


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
            claim = self.runs.claim_queued(run_id)
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
        except RunClaimError as error:
            try:
                claim_token = self.runs.recover_claim_token(
                    run_id, expected_claim_token=error.claim_token
                )
            except Exception:  # noqa: BLE001 - retry can proceed without recovery metadata.
                claim_token = None
            raise RetryableInfrastructureError(claim_token=claim_token) from error
        except (ConnectionError, OperationalError) as error:
            raise RetryableInfrastructureError() from error
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
        if claim is None:
            return IngestionResult.unknown_run(run_id)
        run = claim.run
        if not claim.claimed:
            return IngestionResult.from_run(run)
        claim_token = claim.claim_token
        if claim_token is None:
            raise RetryableInfrastructureError()

        providers_attempted: tuple[str, ...] = ()
        documents: tuple[RawDocument, ...] = ()
        jobs_found = 0
        diagnostics: tuple[_Diagnostic, ...] = ()
        stage = "provider"
        try:
            request = self.runs.get_request_for_run(run)
            if request is None:
                raise _PipelineError("invalid_run")
            provider_entries = tuple(enumerate(self.providers))
            discovery_providers = tuple(
                entry for entry in provider_entries if not _requires_website(entry[1])
            )
            website_providers = tuple(
                entry for entry in provider_entries if _requires_website(entry[1])
            )
            outcomes = await self._collect_providers(
                discovery_providers,
                ProviderQuery(query=request.query),
            )
            providers_attempted, documents, provider_error, diagnostics = self._merge_outcomes(
                outcomes
            )
            if not documents:
                if not diagnostics:
                    diagnostics = (_Diagnostic("provider", "no_documents"),)
                return self._finish(
                    run,
                    expected_claim_token=claim_token,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=0,
                    jobs_found=0,
                    persistence=None,
                    error_code=provider_error or "no_documents",
                    diagnostics=diagnostics,
                )
            stage = "discovery"
            discovered = await self.extractor.discover(documents)
            selected, discovery_error = _select_company(request.query, discovered)
            if selected is None:
                discovery_error = discovery_error or "ambiguous_company"
                return self._finish(
                    run,
                    expected_claim_token=claim_token,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=len(documents),
                    jobs_found=0,
                    persistence=None,
                    error_code=discovery_error,
                    diagnostics=(_Diagnostic("discovery", discovery_error),),
                )
            company = CompanyRef(name=selected.name, website=selected.website)
            if selected.website is not None and website_providers:
                website_host = _normalized_website_host(selected.website)
                approved_website_providers = tuple(
                    entry
                    for entry in website_providers
                    if website_host is not None
                    and _provider_approves_host(entry[1], website_host)
                )
                if website_host is not None and approved_website_providers:
                    outcomes.update(
                        await self._collect_providers(
                            approved_website_providers,
                            ProviderQuery(
                                query=request.query,
                                website=selected.website,
                                allowed_hosts=frozenset({website_host}),
                            ),
                        )
                    )
                    (
                        providers_attempted,
                        documents,
                        provider_error,
                        diagnostics,
                    ) = self._merge_outcomes(outcomes)
            stage = "profile"
            profile = await self.extractor.extract_profile(company, documents)
            stage = "jobs"
            jobs = await self.extractor.extract_jobs(company, documents)
            jobs_found = len(jobs)
            stage = "normalization"
            batch = await self.batch_builder.build(
                company=company,
                profile=profile,
                jobs=jobs,
                documents=documents,
                discovered=selected,
            )
            stage = "persistence"
            if not self.runs.owns_claim(
                run.id, expected_claim_token=claim_token
            ):
                current = self.runs.get_run(run.id)
                return (
                    IngestionResult.unknown_run(run.id)
                    if current is None
                    else IngestionResult.from_run(current)
                )
            persisted = self.persistence.persist(
                batch, run.id, expected_claim_token=claim_token
            )
            return self._finish(
                run,
                expected_claim_token=claim_token,
                status=CollectionStatus.PARTIAL if provider_error else CollectionStatus.SUCCEEDED,
                providers_attempted=providers_attempted,
                documents_found=len(documents),
                jobs_found=jobs_found,
                persistence=persisted,
                error_code=provider_error,
                diagnostics=diagnostics,
            )
        except (ConnectionError, OperationalError) as error:
            raise RetryableInfrastructureError(
                claim_token=claim_token
            ) from error
        except Exception as exc:  # noqa: BLE001 - terminal failure is required for unknown pipeline errors.
            code = _public_code(exc, "ingestion_failed")
            return self._finish(
                run,
                expected_claim_token=claim_token,
                status=CollectionStatus.FAILED,
                providers_attempted=providers_attempted,
                documents_found=len(documents),
                jobs_found=jobs_found,
                persistence=None,
                error_code=code,
                diagnostics=(*diagnostics, _Diagnostic(stage, code)),
            )

    async def _collect_providers(
        self,
        providers: Sequence[tuple[int, Provider]],
        provider_query: ProviderQuery,
    ) -> dict[int, _ProviderOutcome]:
        outcomes: dict[int, _ProviderOutcome] = {}
        for index, provider in providers:
            documents: tuple[RawDocument, ...] = ()
            issue: str | None = None
            try:
                result = await provider.search(provider_query)
            except ProviderError as exc:
                issue = _public_code(exc, "provider_unavailable")
            except Exception:  # noqa: BLE001 - providers are an untrusted external boundary.
                issue = "provider_unavailable"
            else:
                documents = result.documents
                for warning in result.warnings:
                    issue = issue or _warning_code(warning)
            outcomes[index] = _ProviderOutcome(
                name=_provider_name(provider),
                documents=documents,
                issue=issue,
            )
        return outcomes

    @staticmethod
    def _merge_outcomes(
        outcomes: dict[int, _ProviderOutcome],
    ) -> tuple[
        tuple[str, ...],
        tuple[RawDocument, ...],
        str | None,
        tuple[_Diagnostic, ...],
    ]:
        ordered = tuple(outcomes[index] for index in sorted(outcomes))
        return (
            tuple(outcome.name for outcome in ordered),
            tuple(document for outcome in ordered for document in outcome.documents),
            next((outcome.issue for outcome in ordered if outcome.issue is not None), None),
            tuple(
                _Diagnostic("provider", outcome.issue, outcome.name)
                for outcome in ordered
                if outcome.issue is not None
            ),
        )

    def _finish(
        self,
        run: CrawlRunState,
        *,
        expected_claim_token: str,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        diagnostics: tuple[_Diagnostic, ...] = (),
    ) -> IngestionResult:
        try:
            finished = self.runs.finish(
                run,
                expected_claim_token=expected_claim_token,
                status=status,
                providers_attempted=providers_attempted,
                documents_found=documents_found,
                jobs_found=jobs_found,
                persistence=persistence,
                error_code=error_code,
                error_detail=(
                    json.dumps(
                        {"issues": [item.payload() for item in diagnostics]},
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                    if diagnostics
                    else None
                ),
            )
        except (ConnectionError, OperationalError) as error:
            raise RetryableInfrastructureError(
                claim_token=expected_claim_token
            ) from error
        return IngestionResult.from_run(finished)

    def requeue_for_retry(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> None:
        self.runs.requeue_for_retry(
            run_id, expected_claim_token=expected_claim_token
        )

    def fail_retry_exhausted(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> IngestionResult:
        run = self.runs.fail_retry_exhausted(
            run_id, expected_claim_token=expected_claim_token
        )
        return IngestionResult.unknown_run(run_id) if run is None else IngestionResult.from_run(run)


class _PipelineError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _provider_name(provider: Provider) -> str:
    value = getattr(provider, "name", type(provider).__name__)
    return str(value)[:50]


def _requires_website(provider: Provider) -> bool:
    return getattr(provider, "requires_website", False) is True


def _normalized_website_host(website: HttpUrl) -> str | None:
    host = website.host
    if host is None:
        return None
    normalized = host.lower().rstrip(".")
    return normalized or None


def _provider_approves_host(provider: Provider, host: str) -> bool:
    return isinstance(provider, WebsiteDependentProvider) and host in provider.approved_hosts


def _public_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and _PUBLIC_CODE.fullmatch(code) else fallback


def _warning_code(warning: str) -> str:
    return warning if _PUBLIC_CODE.fullmatch(warning) else "provider_warning"


def _plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _select_company(
    query: str, candidates: Sequence[CompanyCandidate]
) -> tuple[CompanyCandidate | None, str | None]:
    exact = [candidate for candidate in candidates if _candidate_matches_query(query, candidate)]
    if len(exact) == 1:
        candidate = exact[0]
    elif len(candidates) == 1:
        return None, "ambiguous_company"
    elif not candidates:
        return None, "no_valid_data"
    else:
        return None, "ambiguous_company"
    return candidate, None


def _candidate_matches_query(query: str, candidate: CompanyCandidate) -> bool:
    normalized_query = normalize_name(query)
    normalized_name = normalize_name(candidate.name)
    if not normalized_query:
        return False
    if normalized_query == normalized_name:
        return True
    if normalized_query in {normalize_name(alias) for alias in candidate.aliases}:
        return True
    return len(normalized_query) >= 2 and normalized_query in normalized_name
