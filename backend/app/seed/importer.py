from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.models import Company, CompanyAlias, JobPosting, JobSource, RegulatoryFiling
from app.seed.schema import SeedCompany, SeedFiling, SeedJob, SeedPayload


@dataclass(frozen=True)
class ImportSummary:
    companies_created: int
    companies_updated: int
    jobs_created: int
    sources_created: int


@dataclass
class _ImportCounts:
    companies_created: int = 0
    companies_updated: int = 0
    jobs_created: int = 0
    sources_created: int = 0


class SeedImporter:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.counts = _ImportCounts()

    def import_payload(self, payload: SeedPayload) -> ImportSummary:
        for company_seed in payload.companies:
            company = self._upsert_company(company_seed)
            self._upsert_aliases(company, company_seed)
            for job_seed in company_seed.jobs:
                self._upsert_job(company, job_seed)
            for filing_seed in company_seed.filings:
                self._upsert_filing(company, filing_seed)

        return ImportSummary(**vars(self.counts))

    def _upsert_company(self, seed: SeedCompany) -> Company:
        normalized_name = normalize_name(seed.canonical_name)
        company = self.session.scalar(
            select(Company).where(Company.normalized_name == normalized_name)
        )
        values = {
            "canonical_name": seed.canonical_name,
            "normalized_name": normalized_name,
            "industry": seed.industry,
            "sub_industry": seed.sub_industry,
            "funding_stage": seed.funding_stage,
            "scale": seed.scale,
            "city": seed.city,
            "headquarters": seed.headquarters,
            "founded_year": seed.founded_year,
            "logo_url": seed.logo_url,
            "website": seed.website,
            "description": seed.description,
        }
        if company is None:
            company = Company(**values)
            self.session.add(company)
            self.session.flush()
            self.counts.companies_created += 1
        else:
            for field, value in values.items():
                setattr(company, field, value)
            self.counts.companies_updated += 1
        return company

    def _upsert_aliases(self, company: Company, seed: SeedCompany) -> None:
        for alias_value in seed.aliases:
            normalized_alias = normalize_name(alias_value)
            alias = self.session.scalar(
                select(CompanyAlias).where(CompanyAlias.normalized_alias == normalized_alias)
            )
            if alias is None:
                self.session.add(
                    CompanyAlias(
                        company_id=company.id,
                        alias=alias_value,
                        normalized_alias=normalized_alias,
                    )
                )
            else:
                alias.company_id = company.id
                alias.alias = alias_value

    def _upsert_job(self, company: Company, seed: SeedJob) -> JobPosting:
        normalized_title = normalize_name(seed.title)
        job = self.session.scalar(
            select(JobPosting).where(
                JobPosting.company_id == company.id,
                JobPosting.normalized_title == normalized_title,
                JobPosting.city == seed.city,
            )
        )
        values = {
            "company_id": company.id,
            "title": seed.title,
            "normalized_title": normalized_title,
            "job_type": seed.job_type,
            "city": seed.city,
            "salary_min_monthly": seed.salary_min_monthly,
            "salary_max_monthly": seed.salary_max_monthly,
            "salary_months": seed.salary_months,
            "description": seed.description,
            "posted_at": seed.posted_at,
            "is_active": seed.is_active,
        }
        if job is None:
            job = JobPosting(**values)
            self.session.add(job)
            self.session.flush()
            self.counts.jobs_created += 1
        else:
            for field, value in values.items():
                setattr(job, field, value)

        for source_seed in seed.sources:
            source = self.session.scalar(
                select(JobSource).where(
                    JobSource.provider == source_seed.provider,
                    JobSource.source_raw_id == source_seed.source_raw_id,
                )
            )
            source_values = {
                "job_posting_id": job.id,
                "provider": source_seed.provider,
                "source_raw_id": source_seed.source_raw_id,
                "apply_url": source_seed.apply_url,
                "first_seen_at": source_seed.first_seen_at,
                "last_seen_at": source_seed.last_seen_at,
                "is_active": source_seed.is_active,
            }
            if source is None:
                self.session.add(JobSource(**source_values))
                self.counts.sources_created += 1
            else:
                for field, value in source_values.items():
                    setattr(source, field, value)
        return job

    def _upsert_filing(self, company: Company, seed: SeedFiling) -> None:
        filing = self.session.scalar(
            select(RegulatoryFiling).where(
                RegulatoryFiling.filing_type == seed.filing_type,
                RegulatoryFiling.filing_number == seed.filing_number,
            )
        )
        values = {
            "company_id": company.id,
            "filing_type": seed.filing_type,
            "filing_number": seed.filing_number,
            "filing_name": seed.filing_name,
            "filing_authority": seed.filing_authority,
            "filing_date": seed.filing_date,
            "filing_status": seed.filing_status,
            "detail_url": seed.detail_url,
        }
        if filing is None:
            self.session.add(RegulatoryFiling(**values))
        else:
            for field, value in values.items():
                setattr(filing, field, value)


def import_seed(session: Session, payload: SeedPayload) -> ImportSummary:
    validated_payload = SeedPayload.model_validate(payload.model_dump())
    with session.begin():
        return SeedImporter(session).import_payload(validated_payload)
