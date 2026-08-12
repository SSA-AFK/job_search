"""Traceable collection-run orchestration without infrastructure coupling."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pydantic import HttpUrl, ValidationError
from sqlalchemy.exc import OperationalError

from app.company_identity.contracts import (
    CompanyIdentityInput,
    CompanyIdentityResolution,
    CompanyIdentityReviewDraft,
    IdentityResolutionKind,
    IdentityReviewRecordSummary,
    PublicEvidenceReference,
)
from app.company_identity.service import IdentitySearchUnavailable
from app.core.normalization import normalize_name, normalize_url
from app.ingestion.contracts import (
    ParsedJob,
    Provider,
    ProviderFetchStats,
    ProviderQuery,
    RawDocument,
)
from app.ingestion.deduplication.job import JobDeduplicator
from app.ingestion.direct_ats import DirectAtsPersistence, DirectAtsSnapshotMetadata
from app.ingestion.entry_discovery.contracts import CompanyNamePool, EntryCandidate, EntryPlatform
from app.ingestion.entry_discovery.service import EntryDiscoveryService
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
    FilingCandidate,
    JobCandidate,
    ProfileExtraction,
)
from app.ingestion.identity_matching import (
    company_name_mentioned,
    company_name_variants,
    match_company_name,
)
from app.ingestion.normalization.company import normalize_company
from app.ingestion.normalization.job import normalize_job
from app.ingestion.persistence.contracts import (
    BatchBuildOutcome,
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
from app.models import CollectionStatus, JobSnapshotStatus
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
    parsed_jobs: tuple[ParsedJob, ...]
    issue: str | None
    stats: tuple[ProviderFetchStats, ...] = ()


@dataclass(frozen=True)
class _Diagnostic:
    stage: str
    code: str
    provider: str | None = None
    detail: dict[str, object] | None = None

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {"stage": self.stage, "code": self.code}
        if self.provider is not None:
            value["provider"] = self.provider
        if self.detail is not None:
            value.update(self.detail)
        return value


@dataclass(frozen=True)
class AtsCareerUrl:
    url: str
    source_url: str


class CrawlRunState(RunResultSource, Protocol):
    id: UUID
    claim_token: str | None
    started_at: datetime | None


class CrawlRunClaim(Protocol):
    run: CrawlRunState
    claimed: bool
    claim_token: str | None


class CollectionRequestState(Protocol):
    query: str


class CompanyIdentityResolutionSource(Protocol):
    async def resolve(
        self, identity: CompanyIdentityInput
    ) -> CompanyIdentityResolution: ...


class IdentityReviewRecorder(Protocol):
    def record(
        self, *, crawl_run_id: UUID, draft: CompanyIdentityReviewDraft
    ) -> IdentityReviewRecordSummary: ...


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
        identity_resolver: CompanyIdentityResolutionSource | None = None,
        job_deduplicator: JobDeduplicator | None = None,
    ) -> None:
        self.identity_resolver = identity_resolver
        self.job_deduplicator = job_deduplicator

    async def build(
        self,
        *,
        company: CompanyRef,
        profile: ProfileExtraction,
        jobs: Sequence[JobCandidate],
        documents: Sequence[RawDocument],
        discovered: CompanyCandidate | None = None,
    ) -> BatchBuildOutcome:
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
        company_candidate = CompanyCandidate(
            name=discovery.name,
            aliases=discovery.aliases,
            website=profile_candidate.website or discovery.website,
            description=profile_description or discovery_description,
            evidence_ids=discovery.evidence_ids,
            confidence=discovery.confidence,
            city=profile_candidate.city or discovery.city,
            industry=profile_candidate.industry or discovery.industry,
            sub_industry=profile_candidate.sub_industry or discovery.sub_industry,
            funding_stage=profile_candidate.funding_stage,
            scale=profile_candidate.scale,
            career_page_url=discovery.career_page_url,
        )
        normalized_filings: list[NormalizedFilingRecord] = []
        for filing in profile.filings:
            if not filing.filing_number:
                continue
            self._require_known_evidence(filing.evidence_ids, document_by_evidence)
            normalized_filings.append(
                NormalizedFilingRecord.from_candidate(
                    filing, source_evidence_id=filing.evidence_ids[0]
                )
            )
        identity = self._identity_input(
            company_candidate=company_candidate,
            profile_candidate=profile_candidate,
            filings=profile.filings,
            documents=document_by_evidence,
        )
        resolution = (
            await self.identity_resolver.resolve(identity)
            if self.identity_resolver is not None
            else None
        )
        if (
            resolution is not None
            and resolution.kind is IdentityResolutionKind.REVIEW_REQUIRED
        ):
            return BatchBuildOutcome.review_required(
                CompanyIdentityReviewDraft(
                    identity=identity,
                    candidate_matches=resolution.candidate_matches,
                    review_reasons=resolution.review_reasons,
                    observed_at=collected_at,
                )
            )
        company_id = None if resolution is None else resolution.company_id
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
            if company_id is not None and self.job_deduplicator is not None:
                job_id = (
                    await self.job_deduplicator.resolve(company_id, resolved_job)
                ).job_posting_id
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
        return BatchBuildOutcome.ready(
            NormalizedBatch(
                documents=tuple(
                    NormalizedDocument(
                        evidence_id=evidence_id,
                        document=document,
                        fetched_at=collected_at,
                    )
                    for evidence_id, document in zip(
                        evidence_ids, documents, strict=True
                    )
                ),
                company=NormalizedCompanyRecord(
                    candidate=normalize_company(company_candidate),
                    company_id=company_id,
                    field_evidence=field_evidence,
                ),
                jobs=tuple(normalized_jobs),
                filings=tuple(normalized_filings),
                collected_at=collected_at,
            )
        )

    @staticmethod
    def _identity_input(
        *,
        company_candidate: CompanyCandidate,
        profile_candidate: CompanyProfileCandidate,
        filings: Sequence[FilingCandidate],
        documents: dict[str, RawDocument],
    ) -> CompanyIdentityInput:
        confidence_by_evidence: dict[str, Decimal] = {}
        evidence_sources: list[tuple[Sequence[str], float]] = [
            (company_candidate.evidence_ids, company_candidate.confidence),
            (profile_candidate.evidence_ids, profile_candidate.confidence),
            *((filing.evidence_ids, filing.confidence) for filing in filings),
        ]
        for source_evidence_ids, source_confidence in evidence_sources:
            confidence = Decimal(str(source_confidence))
            for evidence_id in source_evidence_ids:
                previous = confidence_by_evidence.get(evidence_id)
                if previous is None or confidence > previous:
                    confidence_by_evidence[evidence_id] = confidence
        return CompanyIdentityInput(
            canonical_name=company_candidate.name,
            aliases=company_candidate.aliases,
            official_website=(
                None
                if company_candidate.website is None
                else str(company_candidate.website)
            ),
            legal_identifiers=tuple(
                filing.filing_number for filing in filings if filing.filing_number
            ),
            evidence=tuple(
                PublicEvidenceReference(
                    provider=documents[evidence_id].provider,
                    url=str(documents[evidence_id].url),
                    evidence_id=evidence_id,
                    confidence=confidence,
                )
                for evidence_id, confidence in confidence_by_evidence.items()
            ),
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
        extractor: Extractor | None,
        batch_builder: NormalizedBatchBuilder,
        persistence: PersistenceService,
        direct_ats_persistence: DirectAtsPersistence | None = None,
        entry_discovery_service: EntryDiscoveryService | None = None,
        runs: CrawlRunRepository,
        identity_review_recorder: IdentityReviewRecorder,
    ) -> None:
        self.providers = list(providers)
        self.extractor = extractor
        self.batch_builder = batch_builder
        self.persistence = persistence
        self.direct_ats_persistence = direct_ats_persistence
        self.entry_discovery_service = entry_discovery_service
        self.runs = runs
        self.identity_review_recorder = identity_review_recorder

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
            all_discovery_providers = tuple(
                entry for entry in provider_entries if not _requires_website(entry[1])
            )
            serper_discovery_providers = tuple(
                entry
                for entry in all_discovery_providers
                if _provider_name(entry[1]) == "serper"
            )
            discovery_providers = serper_discovery_providers or all_discovery_providers
            website_providers = tuple(
                entry for entry in provider_entries if _requires_website(entry[1])
            )
            outcomes = await self._collect_providers(
                discovery_providers,
                ProviderQuery(query=request.query),
            )
            ats_discovery_providers = (
                serper_discovery_providers
                if serper_discovery_providers
                else tuple(
                    entry
                    for entry in discovery_providers
                    if _provider_name(entry[1]) == "zhihu_global_search"
                )
            )
            entry_candidates: tuple[EntryCandidate, ...] = ()
            if self.entry_discovery_service is not None:
                discovery = await self.entry_discovery_service.discover(
                    _company_name_pool_from_request(request.query)
                )
                entry_candidates = discovery.high_confidence
            else:
                for search_name in company_name_variants(request.query):
                    for index, provider in ats_discovery_providers:
                        for query in _ats_discovery_queries(
                            search_name, site_restricted=_provider_name(provider) == "serper"
                        ):
                            outcomes = self._merge_outcome_dicts(
                                outcomes,
                                await self._collect_providers(
                                    ((index, provider),),
                                    ProviderQuery(query=query),
                                ),
                            )
            providers_attempted, documents, provider_error, diagnostics = self._merge_outcomes(
                outcomes
            )
            if entry_candidates:
                diagnostics = (*diagnostics, _Diagnostic("entry_discovery", "entries_discovered", detail={"count": len(entry_candidates)}))
            if not documents and not entry_candidates:
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
            direct_job_board_results: list[PersistenceResult] = []
            if self.direct_ats_persistence is not None:
                for outcome in outcomes.values():
                    if outcome.name != "zhipin_cdp_company" or not outcome.parsed_jobs:
                        continue
                    entry_url = str(outcome.documents[0].url) if outcome.documents else "https://www.zhipin.com"
                    persisted = self.direct_ats_persistence.persist(
                        company_name=request.query,
                        entry_url=entry_url,
                        platform="zhipin",
                        jobs=outcome.parsed_jobs,
                        crawl_run_id=run.id,
                        snapshot=_snapshot_metadata_from_outcomes({0: outcome}),
                    )
                    if persisted is not None:
                        direct_job_board_results.append(persisted)
                if direct_job_board_results:
                    company_id = direct_job_board_results[0].company_id
                    if any(result.company_id != company_id for result in direct_job_board_results):
                        raise _PipelineError("invalid_company_identity")
                    persisted = PersistenceResult(
                        company_id=company_id,
                        documents_written=0,
                        jobs_written=sum(result.jobs_written for result in direct_job_board_results),
                        warnings=(),
                    )
                    return self._finish(
                        run,
                        expected_claim_token=claim_token,
                        status=CollectionStatus.PARTIAL if provider_error else CollectionStatus.SUCCEEDED,
                        providers_attempted=providers_attempted,
                        documents_found=len(documents),
                        jobs_found=sum(
                            len(outcome.parsed_jobs)
                            for outcome in outcomes.values()
                            if outcome.name == "zhipin_cdp_company"
                        ),
                        persistence=persisted,
                        error_code=provider_error,
                        diagnostics=diagnostics,
                    )
            ats_career_urls = _scan_ats_career_urls(
                documents, company_name_variants(request.query)
            )
            ats_providers = tuple(
                entry for entry in website_providers if _provider_name(entry[1]) == "ats"
            )
            entry_ats_urls = tuple(
                candidate
                for candidate in (_entry_candidate_to_ats_url(candidate) for candidate in entry_candidates)
                if candidate is not None
            )
            ats_career_urls = _merge_ats_career_urls(entry_ats_urls, ats_career_urls)
            if ats_career_urls and self.direct_ats_persistence is not None:
                stage = "ats"
                direct_results: list[PersistenceResult] = []
                for candidate in ats_career_urls:
                    parsed_url = _parse_ats_url(candidate.url)
                    if parsed_url is None:
                        continue
                    ats_host = _normalized_website_host(parsed_url)
                    if ats_host is None:
                        continue
                    candidate_outcomes = await self._collect_providers(
                        ats_providers,
                        ProviderQuery(
                            query=request.query,
                            website=parsed_url,
                            allowed_hosts=frozenset({ats_host}),
                        ),
                    )
                    outcomes = self._merge_outcome_dicts(outcomes, candidate_outcomes)
                    successful = any(
                        outcome.name == "ats" and bool(outcome.documents) and outcome.issue is None
                        for outcome in candidate_outcomes.values()
                    )
                    if not successful:
                        continue
                    parsed_jobs = tuple(
                        job
                        for outcome in candidate_outcomes.values()
                        for job in outcome.parsed_jobs
                    )
                    snapshot = _snapshot_metadata_from_outcomes(candidate_outcomes)
                    persisted = self.direct_ats_persistence.persist(
                        company_name=request.query,
                        entry_url=candidate.url,
                        platform=_ats_platform(candidate.url),
                        jobs=parsed_jobs,
                        crawl_run_id=run.id,
                        snapshot=snapshot,
                    )
                    if persisted is not None:
                        direct_results.append(persisted)
                (
                    providers_attempted,
                    documents,
                    provider_error,
                    diagnostics,
                ) = self._merge_outcomes(outcomes)
                if direct_results:
                    company_id = direct_results[0].company_id
                    if any(result.company_id != company_id for result in direct_results):
                        raise _PipelineError("invalid_company_identity")
                    persisted = PersistenceResult(
                        company_id=company_id,
                        documents_written=0,
                        jobs_written=sum(result.jobs_written for result in direct_results),
                        warnings=(),
                    )
                    return self._finish(
                        run,
                        expected_claim_token=claim_token,
                        status=(
                            CollectionStatus.PARTIAL if provider_error else CollectionStatus.SUCCEEDED
                        ),
                        providers_attempted=providers_attempted,
                        documents_found=len(documents),
                        jobs_found=sum(
                            len(outcome.parsed_jobs)
                            for outcome in outcomes.values()
                            if outcome.name == "ats"
                        ),
                        persistence=persisted,
                        error_code=provider_error,
                        diagnostics=diagnostics,
                    )
                return self._finish(
                    run,
                    expected_claim_token=claim_token,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=len(documents),
                    jobs_found=0,
                    persistence=self._existing_company_result(request.query),
                    error_code=provider_error or "ats_collection_failed",
                    diagnostics=diagnostics,
                )
            if self.extractor is None:
                return self._finish(
                    run,
                    expected_claim_token=claim_token,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=len(documents),
                    jobs_found=0,
                    persistence=self._existing_company_result(request.query),
                    error_code="ats_entry_discovery_pending",
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
                if website_host is not None:
                    outcomes = self._merge_outcome_dicts(
                        outcomes,
                        await self._collect_providers(
                            website_providers,
                            ProviderQuery(
                                query=request.query,
                                website=selected.website,
                                allowed_hosts=frozenset({website_host}),
                            ),
                        ),
                    )
                    (
                        providers_attempted,
                        documents,
                        provider_error,
                        diagnostics,
                    ) = self._merge_outcomes(outcomes)
            if selected.career_page_url is not None and website_providers:
                career_host = _normalized_website_host(selected.career_page_url)
                if career_host is not None:
                    outcomes = self._merge_outcome_dicts(
                        outcomes,
                        await self._collect_providers(
                            website_providers,
                            ProviderQuery(
                                query=request.query,
                                website=selected.career_page_url,
                                allowed_hosts=frozenset({career_host}),
                            ),
                        ),
                    )
                    (
                        providers_attempted,
                        documents,
                        provider_error,
                        diagnostics,
                    ) = self._merge_outcomes(outcomes)
            # Also try ATS URLs found by regex scanning (bypasses LLM entirely)
            processed_ats_urls: set[str] = set()
            for ats_candidate in ats_career_urls:
                url = ats_candidate.url
                if url in processed_ats_urls:
                    continue
                processed_ats_urls.add(url)
                try:
                    parsed = HttpUrl(url)
                except ValidationError:
                    continue
                ats_host = _normalized_website_host(parsed)
                if ats_host is not None:
                    outcomes = self._merge_outcome_dicts(
                        outcomes,
                        await self._collect_providers(
                            website_providers,
                            ProviderQuery(
                                query=request.query,
                                website=parsed,
                                allowed_hosts=frozenset({ats_host}),
                            ),
                        ),
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
            batch_outcome = await self.batch_builder.build(
                company=company,
                profile=profile,
                jobs=jobs,
                documents=documents,
                discovered=selected,
            )
            stage = "claim_check"
            if not self.runs.owns_claim(
                run.id, expected_claim_token=claim_token
            ):
                current = self.runs.get_run(run.id)
                return (
                    IngestionResult.unknown_run(run.id)
                    if current is None
                    else IngestionResult.from_run(current)
                )
            if batch_outcome.review_draft is not None:
                stage = "identity_review"
                self.identity_review_recorder.record(
                    crawl_run_id=run.id,
                    draft=batch_outcome.review_draft,
                )
                code = "company_identity_review_required"
                return self._finish(
                    run,
                    expected_claim_token=claim_token,
                    status=CollectionStatus.FAILED,
                    providers_attempted=providers_attempted,
                    documents_found=len(documents),
                    jobs_found=jobs_found,
                    persistence=None,
                    error_code=code,
                    diagnostics=(*diagnostics, _Diagnostic("identity_resolution", code)),
                )
            batch = batch_outcome.batch
            if batch is None:
                raise _PipelineError("invalid_batch_outcome")
            stage = "persistence"
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
        except IdentitySearchUnavailable as error:
            raise RetryableInfrastructureError(
                claim_token=claim_token
            ) from error
        except RetryableInfrastructureError as error:
            raise RetryableInfrastructureError(
                claim_token=claim_token
            ) from error
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
            parsed_jobs: tuple[ParsedJob, ...] = ()
            stats: tuple[ProviderFetchStats, ...] = ()
            issue: str | None = None
            try:
                result = await provider.search(provider_query)
            except ProviderError as exc:
                issue = _public_code(exc, "provider_unavailable")
            except Exception:  # noqa: BLE001 - providers are an untrusted external boundary.
                issue = "provider_unavailable"
            else:
                documents = result.documents
                parsed_jobs = result.parsed_jobs
                stats = result.stats
                for warning in result.warnings:
                    issue = issue or _warning_code(warning)
            outcomes[index] = _ProviderOutcome(
                name=_provider_name(provider),
                documents=documents,
                parsed_jobs=parsed_jobs,
                issue=issue,
                stats=stats,
            )
        return outcomes

    @staticmethod
    def _merge_outcome_dicts(
        existing: dict[int, _ProviderOutcome],
        new: dict[int, _ProviderOutcome],
    ) -> dict[int, _ProviderOutcome]:
        """Merge new outcomes into existing, combining documents from the same provider."""
        result = dict(existing)
        for index, outcome in new.items():
            if index in result:
                prev = result[index]
                result[index] = _ProviderOutcome(
                    name=prev.name,
                    documents=prev.documents + outcome.documents,
                    parsed_jobs=prev.parsed_jobs + outcome.parsed_jobs,
                    issue=prev.issue or outcome.issue,
                    stats=prev.stats + outcome.stats,
                )
            else:
                result[index] = outcome
        return result

    def _existing_company_result(self, company_name: str) -> PersistenceResult | None:
        if self.direct_ats_persistence is None:
            return None
        company_id = self.direct_ats_persistence.resolve_company_id(company_name)
        if company_id is None:
            return None
        return PersistenceResult(
            company_id=company_id,
            documents_written=0,
            jobs_written=0,
            warnings=(),
        )

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
        issue_diagnostics = tuple(
            _Diagnostic("provider", outcome.issue, outcome.name)
            for outcome in ordered
            if outcome.issue is not None
        )
        stats_diagnostics = tuple(
            _Diagnostic(
                "provider",
                "fetch_stats",
                outcome.name,
                _provider_stats_payload(stat),
            )
            for outcome in ordered
            for stat in outcome.stats
            if stat.blocked_pages > 0 or stat.error_code is not None
        )
        return (
            tuple(outcome.name for outcome in ordered),
            tuple(document for outcome in ordered for document in outcome.documents),
            next((outcome.issue for outcome in ordered if outcome.issue is not None), None),
            issue_diagnostics + stats_diagnostics,
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


_ATS_URL_PATTERNS = (
    re.compile(r"https?://jobs\.feishu\.cn/[a-zA-Z0-9_@./#?&=-]+"),
    re.compile(r"https?://app\.mokahr\.com/[a-zA-Z0-9_@./#?&=-]+"),
    re.compile(r"https?://(?:www\.)?zhipin\.com/[a-zA-Z0-9_@./#?&=-]+"),
    re.compile(r"https?://(?:www\.)?liepin\.com/[a-zA-Z0-9_@./#?&=-]+"),
    re.compile(r"https?://(?:www\.)?lagou\.com/[a-zA-Z0-9_@./#?&=-]+"),
    re.compile(r"https?://jobs\.bytedance\.com/[a-zA-Z0-9_@./#?&=-]+"),
)

_ATS_DISCOVERY_TERMS = ("jobs.feishu.cn", "app.mokahr.com", "zhipin.com", "liepin.com", "lagou.com", "jobs.bytedance.com")
_ATS_HOST_PLATFORMS = {
    "jobs.feishu.cn": "feishu",
    "app.mokahr.com": "moka",
    "zhipin.com": "zhipin",
    "www.zhipin.com": "zhipin",
    "liepin.com": "liepin",
    "www.liepin.com": "liepin",
    "lagou.com": "lagou",
    "www.lagou.com": "lagou",
    "jobs.bytedance.com": "bytedance",
}


def _ats_discovery_queries(
    company_name: str, *, site_restricted: bool = False
) -> tuple[str, ...]:
    """Build the bounded P0 ATS lookup queries for the public search provider."""
    normalized_name = " ".join(company_name.split())
    if not normalized_name:
        return ()
    if site_restricted:
        return tuple(f'"{normalized_name}" site:{term}' for term in _ATS_DISCOVERY_TERMS)
    return tuple(f"{normalized_name} {term}" for term in _ATS_DISCOVERY_TERMS)


def _parse_ats_url(url: str) -> HttpUrl | None:
    try:
        parsed = HttpUrl(url)
    except ValidationError:
        return None
    host = _normalized_website_host(parsed)
    return parsed if host is not None and _platform_for_host(host) is not None else None


def _ats_platform(url: str) -> str:
    parsed = _parse_ats_url(url)
    if parsed is None:
        raise _PipelineError("invalid_ats_url")
    host = _normalized_website_host(parsed)
    platform = _platform_for_host(host) if host is not None else None
    if platform is None:
        raise _PipelineError("invalid_ats_url")
    return platform


def _platform_for_host(host: str) -> str | None:
    normalized = host.lower().rstrip(".")
    if normalized in _ATS_HOST_PLATFORMS:
        return _ATS_HOST_PLATFORMS[normalized]
    for known_host, platform in _ATS_HOST_PLATFORMS.items():
        if normalized.endswith("." + known_host):
            return platform
    return None


def _company_name_pool_from_request(query: str) -> CompanyNamePool:
    variants = company_name_variants(query)
    return CompanyNamePool(canonical_name=query, historical_aliases=tuple(v for v in variants if v != query))


def _entry_candidate_to_ats_url(candidate: EntryCandidate) -> AtsCareerUrl | None:
    if candidate.platform not in {
        EntryPlatform.ATS_FEISHU,
        EntryPlatform.ATS_MOKA,
        EntryPlatform.ATS_BYTEDANCE,
        EntryPlatform.BOSS_ZHIPIN,
        EntryPlatform.LIEPIN,
        EntryPlatform.LAGOU,
    }:
        return None
    if _parse_ats_url(candidate.url) is None:
        return None
    return AtsCareerUrl(url=candidate.url.rstrip("/"), source_url=candidate.source_url or candidate.url)


def _merge_ats_career_urls(
    discovered: tuple[AtsCareerUrl, ...], scanned: tuple[AtsCareerUrl, ...]
) -> tuple[AtsCareerUrl, ...]:
    seen: set[str] = set()
    merged: list[AtsCareerUrl] = []
    for candidate in (*discovered, *scanned):
        key = candidate.url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return tuple(merged)


def _snapshot_metadata_from_outcomes(outcomes: dict[int, _ProviderOutcome]) -> DirectAtsSnapshotMetadata:
    stats = tuple(stat for outcome in outcomes.values() for stat in outcome.stats)
    pages_fetched = sum(stat.pages_fetched for stat in stats) or 1
    parsed_jobs = sum(len(outcome.parsed_jobs) for outcome in outcomes.values())
    error_code = next((stat.error_code for stat in stats if stat.error_code), None)
    if error_code is None:
        error_code = "pagination_incomplete"
    return DirectAtsSnapshotMetadata(
        status=JobSnapshotStatus.PARTIAL,
        pagination_complete=False,
        empty_confirmed=False,
        observed_count=parsed_jobs,
        pages_fetched=pages_fetched,
        error_code=error_code,
    )


def _scan_ats_career_urls(
    documents: tuple[RawDocument, ...], requested_names: str | tuple[str, ...]
) -> tuple[AtsCareerUrl, ...]:
    """Return known ATS URLs only when their Zhihu document matches the company."""
    seen: set[str] = set()
    results: list[AtsCareerUrl] = []
    names = (requested_names,) if isinstance(requested_names, str) else requested_names
    for doc in documents:
        if not doc.text:
            continue
        observed = doc.title or ""
        exact_mention = any(company_name_mentioned(name, doc.text) for name in names)
        if not exact_mention and not any(
            match_company_name(name, observed).accepted for name in names
        ):
            continue
        for pattern in _ATS_URL_PATTERNS:
            for match in pattern.finditer(doc.text):
                url = match.group(0).rstrip("/")
                if url not in seen:
                    seen.add(url)
                    results.append(AtsCareerUrl(url=url, source_url=str(doc.url)))
    return tuple(sorted(results, key=lambda item: item.url))


def _provider_name(provider: Provider) -> str:
    value = getattr(provider, "name", type(provider).__name__)
    return str(value)[:50]


def _provider_stats_payload(stat: ProviderFetchStats) -> dict[str, object]:
    value: dict[str, object] = {
        "stat_provider": stat.provider,
        "entries_discovered": stat.entries_discovered,
        "pages_fetched": stat.pages_fetched,
        "parsed_jobs": stat.parsed_jobs,
        "blocked_pages": stat.blocked_pages,
    }
    if stat.platform is not None:
        value["platform"] = stat.platform
    if stat.error_code is not None:
        value["error_code"] = stat.error_code
    return value


def _requires_website(provider: Provider) -> bool:
    return getattr(provider, "requires_website", False) is True


def _normalized_website_host(website: HttpUrl) -> str | None:
    host = website.host
    if host is None:
        return None
    normalized = host.lower().rstrip(".")
    return normalized or None


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
