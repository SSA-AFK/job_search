"""Celery boundary for bounded recruiting-entry verification."""

import asyncio
from urllib.parse import urljoin
from uuid import UUID

from sqlalchemy import select

from app.collection.repository import CollectionRepository
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.normalization import normalize_url
from app.ingestion.contracts import ProviderQuery
from app.ingestion.entry_discovery.contracts import CompanyNamePool
from app.ingestion.entry_verification import (
    EntryCandidateInput,
    EntryUrlValidator,
    EntryVerificationService,
    EntryVerificationStatus,
)
from app.ingestion.persistence.result import PersistenceResult
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy
from app.ingestion.providers.serper import SerperProvider
from app.models import (
    CollectionStatus,
    Company,
    CompanyAlias,
    JobEntry,
    JobEntryStatus,
    VerificationStatus,
)
from app.models.base import utc_now
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.entry_verification.run_entry_verification")
def run_entry_verification(run_id: str) -> dict[str, object]:
    return asyncio.run(_run(UUID(run_id)))


async def _run(run_id: UUID) -> dict[str, object]:
    session = SessionLocal()
    try:
        repository = CollectionRepository(session)
        claim = repository.claim_queued(run_id)
        if claim is None:
            return {"run_id": str(run_id), "status": "failed", "error_code": "unknown_run"}
        run = claim.run
        if not claim.claimed or claim.claim_token is None:
            return _payload(run)
        request = repository.get_request_for_run(run)
        if request is None:
            return _payload(run)
        company = _resolve_company(session, request.normalized_query)
        if company is None:
            finished = repository.finish(
                run,
                expected_claim_token=claim.claim_token,
                status=CollectionStatus.PARTIAL,
                providers_attempted=(),
                documents_found=0,
                jobs_found=0,
                persistence=None,
                error_code="company_not_found",
                error_detail="company_not_found",
            )
            return _payload(finished)

        aliases = tuple(
            session.scalars(select(CompanyAlias.alias).where(CompanyAlias.company_id == company.id))
        )
        name_pool = CompanyNamePool(
            canonical_name=company.canonical_name,
            historical_aliases=aliases,
            domains=((company.normalized_website,) if company.normalized_website else ()),
        )
        existing_entries = tuple(
            session.scalars(
                select(JobEntry)
                .where(JobEntry.company_id == company.id)
                .order_by(JobEntry.is_primary.desc(), JobEntry.last_success_at.desc())
            )
        )
        existing = tuple(
            EntryCandidateInput(entry.url, trusted_existing_binding=True)
            for entry in existing_entries
        )
        website_links = (
            (
                EntryCandidateInput(
                    urljoin(company.website.rstrip("/") + "/", "careers"),
                    linked_from_verified_website=False,
                ),
            )
            if company.website
            else ()
        )
        http = SafeHttpClient()
        verifier = EntryVerificationService(
            validator=EntryUrlValidator(
                http_client=http,
                robots_policy=RobotsPolicy(http_client=http),
            )
        )
        providers_attempted: list[str] = ["entry_verification"]

        async def search_candidates():
            if not settings.serper_provider_enabled or not settings.serper_api_key:
                return ()
            providers_attempted.append("serper")
            result = await SerperProvider(
                enabled=True,
                api_key=settings.serper_api_key,
                gl=settings.serper_gl,
                hl=settings.serper_hl,
            ).search(
                ProviderQuery(
                    query=f"{company.canonical_name} 招聘 careers",
                    max_results=3,
                )
            )
            return tuple(EntryCandidateInput(str(document.url)) for document in result.documents)

        result = await verifier.find_verified_entry(
            company=name_pool,
            existing=existing,
            website_links=website_links,
            search=search_candidates,
        )
        if result is not None and result.status is EntryVerificationStatus.VERIFIED:
            _save_verified_entry(session, company.id, str(result.final_url))
            persistence = PersistenceResult(
                company_id=company.id,
                documents_written=0,
                jobs_written=0,
                warnings=(),
            )
            finished = repository.finish(
                run,
                expected_claim_token=claim.claim_token,
                status=CollectionStatus.SUCCEEDED,
                providers_attempted=tuple(providers_attempted),
                documents_found=0,
                jobs_found=0,
                persistence=persistence,
                error_code=None,
                error_detail=None,
            )
        else:
            reason = result.reason_code if result is not None else "not_found"
            finished = repository.finish(
                run,
                expected_claim_token=claim.claim_token,
                status=CollectionStatus.PARTIAL,
                providers_attempted=tuple(providers_attempted),
                documents_found=0,
                jobs_found=0,
                persistence=PersistenceResult(company.id, 0, 0, ()),
                error_code=reason,
                error_detail=reason,
            )
        return _payload(finished)
    finally:
        session.close()


def _resolve_company(session, normalized_query: str) -> Company | None:
    company = session.scalar(select(Company).where(Company.normalized_name == normalized_query))
    if company is not None:
        return company
    company_id = session.scalar(
        select(CompanyAlias.company_id).where(CompanyAlias.normalized_alias == normalized_query)
    )
    return session.get(Company, company_id) if company_id is not None else None


def _save_verified_entry(session, company_id: UUID, url: str) -> None:
    normalized = normalize_url(url).rstrip("/")
    entry = session.scalar(
        select(JobEntry).where(
            JobEntry.company_id == company_id,
            JobEntry.normalized_url == normalized,
        )
    )
    if entry is None:
        entry = JobEntry(
            company_id=company_id,
            url=url,
            normalized_url=normalized,
            provider="entry_verification",
            platform="public_recruiting_entry",
        )
        session.add(entry)
    session.query(JobEntry).filter(
        JobEntry.company_id == company_id,
        JobEntry.id != entry.id,
    ).update({JobEntry.is_primary: False}, synchronize_session=False)
    entry.url = url
    entry.status = JobEntryStatus.ACTIVE
    entry.is_primary = True
    entry.verification_status = VerificationStatus.VERIFIED
    entry.verified_at = utc_now()
    entry.last_checked_at = utc_now()
    entry.last_success_at = utc_now()
    entry.failure_count = 0
    session.commit()


def _payload(run) -> dict[str, object]:
    return {
        "run_id": str(run.id),
        "status": run.status.value,
        "company_id": str(run.company_id) if run.company_id else None,
        "providers_attempted": list(run.providers_attempted or []),
        "documents_found": run.documents_found,
        "jobs_found": run.jobs_found,
        "jobs_written": run.jobs_written,
        "error_code": run.error_code,
    }
