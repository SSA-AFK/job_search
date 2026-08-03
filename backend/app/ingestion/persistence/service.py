"""Transactional, idempotent persistence for normalized ingestion batches."""

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.normalization import normalize_url
from app.ingestion.persistence.contracts import (
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
    NormalizedFilingRecord,
    NormalizedJobRecord,
)
from app.ingestion.persistence.result import PersistenceResult
from app.models import (
    Company,
    CompanySource,
    JobPosting,
    JobSource,
    RegulatoryFiling,
    SourceDocument,
)

_TEXT_EXCERPT_LIMIT = 4_000
_KNOWN_CONSTRAINT_MARKERS = {
    "companies.normalized_name": "uq_company_normalized_name",
    "source_documents.provider, source_documents.external_id": (
        "uq_source_document_provider_external_id"
    ),
    "job_sources.provider, job_sources.source_raw_id": "uq_job_source_provider_raw_id",
    "regulatory_filings.filing_type, regulatory_filings.filing_number": (
        "uq_filing_type_number"
    ),
}


class PersistenceError(Exception):
    code = "persistence_conflict"

    def __init__(
        self,
        *,
        run_id: UUID,
        constraint: str | None,
        detail: str,
    ) -> None:
        self.run_id = run_id
        self.constraint = constraint
        self.detail = detail
        super().__init__(f"{self.code}: run_id={run_id} constraint={constraint}: {detail}")


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _plain_text_excerpt(value: str) -> str:
    parser = _PlainTextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())[:_TEXT_EXCERPT_LIMIT]


class PersistenceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist(self, batch: NormalizedBatch, run_id: UUID) -> PersistenceResult:
        try:
            with self.session.begin():
                documents = self._upsert_documents(batch.documents)
                company = self._upsert_company(batch.company, run_id)
                self._upsert_company_evidence(company, batch.company, documents)
                job_ids, warnings = self._upsert_jobs(company.id, batch.jobs, documents, run_id)
                self._upsert_filings(company.id, batch.filings, documents, run_id)
                company.last_collected_at = batch.collected_at
                return PersistenceResult(
                    company_id=company.id,
                    documents_written=len({document.id for document in documents.values()}),
                    jobs_written=len(job_ids),
                    warnings=warnings,
                )
        except PersistenceError:
            raise
        except IntegrityError as exc:
            constraint = self._constraint_name(exc)
            raise PersistenceError(
                run_id=run_id,
                constraint=constraint,
                detail="database uniqueness conflict",
            ) from exc

    def _upsert_documents(
        self, records: tuple[NormalizedDocument, ...]
    ) -> dict[str, SourceDocument]:
        by_evidence_id: dict[str, SourceDocument] = {}
        for record in records:
            source = record.document
            normalized_url = normalize_url(str(source.url))
            content_hash = sha256(source.text.encode("utf-8")).hexdigest()
            if source.external_id is not None:
                document = self.session.scalar(
                    select(SourceDocument).where(
                        SourceDocument.provider == source.provider,
                        SourceDocument.external_id == source.external_id,
                    )
                )
            else:
                document = self.session.scalar(
                    select(SourceDocument).where(
                        SourceDocument.provider == source.provider,
                        SourceDocument.external_id.is_(None),
                        SourceDocument.url == normalized_url,
                        SourceDocument.content_hash == content_hash,
                    )
                )
            values = {
                "provider": source.provider,
                "external_id": source.external_id,
                "url": normalized_url,
                "title": source.title,
                "text_excerpt": _plain_text_excerpt(source.text),
                "content_hash": content_hash,
                "authority_level": source.authority_level,
                "published_at": source.published_at,
                "fetched_at": record.fetched_at,
            }
            if document is None:
                document = SourceDocument(**values)
                self.session.add(document)
                self.session.flush()
            else:
                document.provider = source.provider
                document.external_id = source.external_id
                document.url = normalized_url
                document.title = source.title
                document.text_excerpt = _plain_text_excerpt(source.text)
                document.content_hash = content_hash
                document.authority_level = source.authority_level
                document.published_at = source.published_at
                document.fetched_at = max(document.fetched_at, record.fetched_at)
            by_evidence_id[record.evidence_id] = document
        return by_evidence_id

    def _upsert_company(self, record: NormalizedCompanyRecord, run_id: UUID) -> Company:
        normalized = record.candidate
        candidate = normalized.candidate
        if record.company_id is not None:
            company = self.session.get(Company, record.company_id)
            if company is None:
                raise PersistenceError(
                    run_id=run_id,
                    constraint="company_id",
                    detail=f"unknown company_id: {record.company_id}",
                )
        else:
            company = self.session.scalar(
                select(Company).where(Company.normalized_name == normalized.normalized_name)
            )

        if company is None:
            company = Company(
                canonical_name=candidate.name,
                normalized_name=normalized.normalized_name,
                website=str(candidate.website) if candidate.website is not None else None,
                description=candidate.description,
            )
            self.session.add(company)
            self.session.flush()
        else:
            company.canonical_name = candidate.name
            company.normalized_name = normalized.normalized_name
            if candidate.website is not None:
                company.website = str(candidate.website)
            if candidate.description is not None:
                company.description = candidate.description
        return company

    def _upsert_company_evidence(
        self,
        company: Company,
        record: NormalizedCompanyRecord,
        documents: dict[str, SourceDocument],
    ) -> None:
        by_document: dict[UUID, list[tuple[str, float]]] = defaultdict(list)
        for item in record.field_evidence:
            by_document[documents[item.evidence_id].id].append(
                (item.field_name, item.confidence)
            )

        for document_id, evidence in by_document.items():
            company_source = self.session.get(CompanySource, (company.id, document_id))
            fields = sorted({field for field, _confidence in evidence})
            confidence = Decimal(str(max(value for _field, value in evidence)))
            if company_source is None:
                self.session.add(
                    CompanySource(
                        company_id=company.id,
                        source_document_id=document_id,
                        covered_fields=fields,
                        confidence=confidence,
                    )
                )
            else:
                company_source.covered_fields = sorted(
                    set(company_source.covered_fields) | set(fields)
                )
                company_source.confidence = max(company_source.confidence, confidence)

    def _upsert_jobs(
        self,
        company_id: UUID,
        records: tuple[NormalizedJobRecord, ...],
        documents: dict[str, SourceDocument],
        run_id: UUID,
    ) -> tuple[set[UUID], tuple[str, ...]]:
        job_ids: set[UUID] = set()
        warnings: list[str] = []
        for record in records:
            source_candidate = record.candidate.candidate
            provider = source_candidate.provider
            source_raw_id = source_candidate.source_raw_id
            assert provider is not None and source_raw_id is not None
            source = self.session.scalar(
                select(JobSource).where(
                    JobSource.provider == provider,
                    JobSource.source_raw_id == source_raw_id,
                )
            )
            job = self._resolve_job(company_id, record, source, run_id)
            if job is None:
                job = self._find_canonical_job(company_id, record)
            if job is None:
                description = source_candidate.description or ""
                job = JobPosting(
                    company_id=company_id,
                    title=source_candidate.title,
                    normalized_title=record.candidate.normalized_title,
                    job_type=record.candidate.job_type,
                    city=record.candidate.normalized_city,
                    salary_min_monthly=record.candidate.salary_minimum_monthly,
                    salary_max_monthly=record.candidate.salary_maximum_monthly,
                    salary_months=record.candidate.salary_months,
                    description=description,
                    posted_at=record.posted_at,
                    is_active=record.is_active,
                )
                self.session.add(job)
                self.session.flush()
            else:
                self._merge_job(job, record)

            source_document_id = (
                documents[record.source_evidence_id].id
                if record.source_evidence_id is not None
                else None
            )
            source_values = {
                "job_posting_id": job.id,
                "source_document_id": source_document_id,
                "provider": provider,
                "source_raw_id": source_raw_id,
                "apply_url": str(record.apply_url),
                "first_seen_at": record.seen_at,
                "last_seen_at": record.seen_at,
                "is_active": record.is_active,
            }
            if source is None:
                source = JobSource(**source_values)
                self.session.add(source)
            else:
                source.job_posting_id = job.id
                source.source_document_id = source_document_id
                source.apply_url = str(record.apply_url)
                source.first_seen_at = min(source.first_seen_at, record.seen_at)
                source.last_seen_at = max(source.last_seen_at, record.seen_at)
                source.is_active = record.is_active
            job_ids.add(job.id)
            warnings.extend(record.candidate.warnings)

        self.session.flush()
        for job_id in job_ids:
            job = self.session.get(JobPosting, job_id)
            assert job is not None
            statuses = self.session.scalars(
                select(JobSource.is_active).where(JobSource.job_posting_id == job_id)
            )
            job.is_active = any(statuses)
        return job_ids, tuple(dict.fromkeys(warnings))

    def _resolve_job(
        self,
        company_id: UUID,
        record: NormalizedJobRecord,
        source: JobSource | None,
        run_id: UUID,
    ) -> JobPosting | None:
        if source is not None:
            job = self.session.get(JobPosting, source.job_posting_id)
            if job is None or job.company_id != company_id:
                raise PersistenceError(
                    run_id=run_id,
                    constraint="uq_job_source_provider_raw_id",
                    detail="job source belongs to another company",
                )
            if record.job_posting_id is not None and record.job_posting_id != job.id:
                raise PersistenceError(
                    run_id=run_id,
                    constraint="uq_job_source_provider_raw_id",
                    detail="job source conflicts with the resolved canonical job",
                )
            return job
        if record.job_posting_id is None:
            return None
        job = self.session.get(JobPosting, record.job_posting_id)
        if job is None or job.company_id != company_id:
            raise PersistenceError(
                run_id=run_id,
                constraint="job_posting_company_id",
                detail="canonical job does not belong to the persisted company",
            )
        return job

    def _find_canonical_job(
        self, company_id: UUID, record: NormalizedJobRecord
    ) -> JobPosting | None:
        return self.session.scalar(
            select(JobPosting).where(
                JobPosting.company_id == company_id,
                JobPosting.normalized_title == record.candidate.normalized_title,
                JobPosting.city == record.candidate.normalized_city,
            )
        )

    @staticmethod
    def _merge_job(job: JobPosting, record: NormalizedJobRecord) -> None:
        candidate = record.candidate.candidate
        job.title = candidate.title
        job.job_type = record.candidate.job_type
        incoming_description = candidate.description or ""
        if len(incoming_description) > len(job.description):
            job.description = incoming_description
        if record.posted_at is not None and (
            job.posted_at is None or record.posted_at < job.posted_at
        ):
            job.posted_at = record.posted_at
        if record.candidate.salary_minimum_monthly is not None:
            job.salary_min_monthly = record.candidate.salary_minimum_monthly
        if record.candidate.salary_maximum_monthly is not None:
            job.salary_max_monthly = record.candidate.salary_maximum_monthly
        if record.candidate.salary_months is not None:
            job.salary_months = record.candidate.salary_months

    def _upsert_filings(
        self,
        company_id: UUID,
        records: tuple[NormalizedFilingRecord, ...],
        documents: dict[str, SourceDocument],
        run_id: UUID,
    ) -> None:
        keys = [(record.filing_type, record.filing_number) for record in records]
        if len(keys) != len(set(keys)):
            raise PersistenceError(
                run_id=run_id,
                constraint="uq_filing_type_number",
                detail="duplicate filing identity in batch",
            )

        for record in records:
            filing = self.session.scalar(
                select(RegulatoryFiling).where(
                    RegulatoryFiling.filing_type == record.filing_type,
                    RegulatoryFiling.filing_number == record.filing_number,
                )
            )
            if filing is not None and filing.company_id != company_id:
                raise PersistenceError(
                    run_id=run_id,
                    constraint="uq_filing_type_number",
                    detail="filing identity belongs to another company",
                )
            values = {
                "company_id": company_id,
                "source_document_id": (
                    documents[record.source_evidence_id].id
                    if record.source_evidence_id is not None
                    else None
                ),
                "filing_type": record.filing_type,
                "filing_number": record.filing_number,
                "filing_name": record.filing_name,
                "filing_authority": record.filing_authority,
                "filing_date": record.filing_date,
                "filing_status": record.filing_status,
                "detail_url": str(record.detail_url) if record.detail_url is not None else None,
            }
            if filing is None:
                self.session.add(RegulatoryFiling(**values))
            else:
                for field, value in values.items():
                    setattr(filing, field, value)

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        diagnostic = getattr(exc.orig, "diag", None)
        name = getattr(diagnostic, "constraint_name", None)
        if isinstance(name, str):
            return name
        message = str(exc.orig)
        for marker, constraint in _KNOWN_CONSTRAINT_MARKERS.items():
            if marker in message:
                return constraint
        return None
