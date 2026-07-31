"""Deterministic dataset generation for performance acceptance tests."""

from datetime import UTC, datetime
from random import Random
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Engine

from app.models import Company, JobPosting, JobType

COMPANY_COUNT = 10_000
JOBS_PER_COMPANY = 10
RANDOM_SEED = 20_260_731
FIXTURE_TIMESTAMP = datetime(2026, 7, 31, tzinfo=UTC)


def seed_performance_dataset(engine: Engine) -> None:
    """Insert the representative dataset with deterministic bulk operations."""
    random = Random(RANDOM_SEED)
    cities = ("Beijing", "Hangzhou", "Shanghai", "Shenzhen")
    industries = ("Artificial Intelligence", "Cloud Services", "Software")
    companies: list[dict[str, object]] = []
    jobs: list[dict[str, object]] = []

    for company_number in range(COMPANY_COUNT):
        company_id = uuid5(NAMESPACE_URL, f"performance-company-{company_number}")
        city = random.choice(cities)
        companies.append(
            {
                "id": company_id,
                "canonical_name": f"AI Benchmark Company {company_number:05d}",
                "normalized_name": f"ai benchmark company {company_number:05d}",
                "industry": random.choice(industries),
                "sub_industry": "Benchmarking",
                "funding_stage": "unknown",
                "scale": "200_to_499",
                "city": city,
                "logo_url": None,
                "website": None,
                "description": "Deterministic performance fixture company.",
                "last_collected_at": None,
                "created_at": FIXTURE_TIMESTAMP,
                "updated_at": FIXTURE_TIMESTAMP,
            }
        )
        for job_offset in range(JOBS_PER_COMPANY):
            job_number = company_number * JOBS_PER_COMPANY + job_offset
            jobs.append(
                {
                    "id": uuid5(NAMESPACE_URL, f"performance-job-{job_number}"),
                    "company_id": company_id,
                    "title": f"AI Platform Engineer {job_number:06d}",
                    "normalized_title": f"ai platform engineer {job_number:06d}",
                    "job_type": JobType.FULL_TIME,
                    "city": city,
                    "salary_min_monthly": 30_000,
                    "salary_max_monthly": 60_000,
                    "salary_months": 14,
                    "description": "Deterministic performance fixture job.",
                    "posted_at": FIXTURE_TIMESTAMP.date(),
                    "is_active": True,
                    "created_at": FIXTURE_TIMESTAMP,
                    "updated_at": FIXTURE_TIMESTAMP,
                }
            )

    with engine.begin() as connection:
        connection.execute(Company.__table__.insert(), companies)
        connection.execute(JobPosting.__table__.insert(), jobs)
