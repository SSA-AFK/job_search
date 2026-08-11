"""Aggregated statistics for the dashboard overview."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Company, CrawlRun, JobPosting

router = APIRouter(prefix="/stats", tags=["stats"])


class NameCount(BaseModel):
    name: str
    count: int


class OverviewStats(BaseModel):
    companies_total: int
    companies_with_description: int
    companies_with_website: int
    jobs_total: int
    jobs_with_city: int
    crawl_runs_total: int
    crawl_runs_by_status: list[NameCount]
    jobs_by_city: list[NameCount]
    jobs_by_type: list[NameCount]
    companies_by_funding_stage: list[NameCount]
    companies_by_scale: list[NameCount]


@router.get("/overview", response_model=OverviewStats)
def overview(session: Session = Depends(get_session)) -> OverviewStats:  # noqa: B008
    companies_total = session.scalar(select(func.count(Company.id))) or 0
    companies_with_description = session.scalar(
        select(func.count(Company.id)).where(Company.description.is_not(None))
    ) or 0
    companies_with_website = session.scalar(
        select(func.count(Company.id)).where(Company.website.is_not(None))
    ) or 0
    jobs_total = session.scalar(select(func.count(JobPosting.id))) or 0
    jobs_with_city = session.scalar(
        select(func.count(JobPosting.id)).where(
            JobPosting.city.is_not(None), JobPosting.city != ""
        )
    ) or 0

    crawl_rows = session.execute(
        select(CrawlRun.status, func.count(CrawlRun.id)).group_by(CrawlRun.status)
    ).all()
    crawl_runs_by_status = [
        NameCount(name=row[0].value if hasattr(row[0], "value") else str(row[0]), count=row[1])
        for row in crawl_rows
    ]
    crawl_runs_total = sum(item.count for item in crawl_runs_by_status)

    city_rows = session.execute(
        select(JobPosting.city, func.count(JobPosting.id))
        .where(JobPosting.city.is_not(None), JobPosting.city != "")
        .group_by(JobPosting.city)
        .order_by(func.count(JobPosting.id).desc())
        .limit(12)
    ).all()
    jobs_by_city = [NameCount(name=row[0], count=row[1]) for row in city_rows]

    type_rows = session.execute(
        select(JobPosting.job_type, func.count(JobPosting.id)).group_by(JobPosting.job_type)
    ).all()
    jobs_by_type = [NameCount(name=row[0] or "unknown", count=row[1]) for row in type_rows]

    funding_rows = session.execute(
        select(Company.funding_stage, func.count(Company.id))
        .group_by(Company.funding_stage)
        .order_by(func.count(Company.id).desc())
    ).all()
    companies_by_funding_stage = [
        NameCount(name=row[0] or "unknown", count=row[1]) for row in funding_rows
    ]

    scale_rows = session.execute(
        select(Company.scale, func.count(Company.id))
        .group_by(Company.scale)
        .order_by(func.count(Company.id).desc())
    ).all()
    companies_by_scale = [NameCount(name=row[0] or "unknown", count=row[1]) for row in scale_rows]

    return OverviewStats(
        companies_total=companies_total,
        companies_with_description=companies_with_description,
        companies_with_website=companies_with_website,
        jobs_total=jobs_total,
        jobs_with_city=jobs_with_city,
        crawl_runs_total=crawl_runs_total,
        crawl_runs_by_status=crawl_runs_by_status,
        jobs_by_city=jobs_by_city,
        jobs_by_type=jobs_by_type,
        companies_by_funding_stage=companies_by_funding_stage,
        companies_by_scale=companies_by_scale,
    )
