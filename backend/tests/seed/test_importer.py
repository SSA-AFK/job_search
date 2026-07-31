from collections.abc import Iterator
from datetime import UTC, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    CompanyAlias,
    JobPosting,
    JobSource,
    RegulatoryFiling,
)
from app.seed.importer import import_seed
from app.seed.schema import SeedPayload


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def seed_data() -> dict[str, object]:
    return {
        "version": 1,
        "companies": [
            {
                "canonical_name": "DeepSeek（深度求索）",
                "aliases": ["Deep Seek", "深度求索"],
                "industry": "Artificial Intelligence",
                "sub_industry": "Foundation Models",
                "funding_stage": "private",
                "scale": "100-499",
                "city": "Hangzhou",
                "website": "https://deepseek.com/",
                "description": "Builds foundation models.",
                "jobs": [
                    {
                        "title": "Machine Learning Engineer",
                        "job_type": "full_time",
                        "city": "Hangzhou",
                        "salary_min_monthly": 30000,
                        "salary_max_monthly": 60000,
                        "salary_months": 14,
                        "description": "Train language models.",
                        "posted_at": "2026-07-01",
                        "is_active": True,
                        "sources": [
                            {
                                "provider": "official",
                                "source_raw_id": "ds-ml-1",
                                "apply_url": "https://deepseek.com/jobs/1",
                                "first_seen_at": "2026-07-01T00:00:00Z",
                                "last_seen_at": "2026-07-31T00:00:00Z",
                                "is_active": True,
                            }
                        ],
                    }
                ],
                "filings": [
                    {
                        "filing_type": "icp",
                        "filing_number": "浙ICP备2023025841号",
                        "filing_name": "DeepSeek website",
                        "filing_authority": "MIIT",
                        "filing_date": "2023-08-01",
                        "filing_status": "active",
                        "detail_url": "https://beian.miit.gov.cn/",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def seed_payload(seed_data: dict[str, object]) -> SeedPayload:
    return SeedPayload.model_validate(seed_data)


def test_importing_seed_twice_is_idempotent(
    session: Session, seed_payload: SeedPayload
) -> None:
    first = import_seed(session, seed_payload)
    second = import_seed(session, seed_payload)

    assert first.companies_created == 1
    assert first.jobs_created == 1
    assert first.sources_created == 1
    assert second.companies_created == 0
    assert second.companies_updated == 1
    assert second.jobs_created == 0
    assert second.sources_created == 0
    assert session.scalar(select(func.count(JobSource.id))) == 1
    assert session.scalar(select(func.count(CompanyAlias.id))) == 2
    assert session.scalar(select(func.count(RegulatoryFiling.id))) == 1


def test_import_updates_company_and_source_without_changing_identity(
    session: Session, seed_data: dict[str, object]
) -> None:
    original = SeedPayload.model_validate(seed_data)
    import_seed(session, original)
    changed_data = original.model_dump(mode="json")
    changed_data["companies"][0]["description"] = "Updated description."
    changed_data["companies"][0]["jobs"][0]["sources"][0]["apply_url"] = (
        "https://deepseek.com/careers/1"
    )

    summary = import_seed(session, SeedPayload.model_validate(changed_data))

    assert summary.companies_created == 0
    assert summary.companies_updated == 1
    assert session.scalar(select(Company.description)) == "Updated description."
    assert session.scalar(select(JobSource.apply_url)) == "https://deepseek.com/careers/1"


def test_two_sources_are_merged_into_one_canonical_job(
    session: Session, seed_data: dict[str, object]
) -> None:
    sources = seed_data["companies"][0]["jobs"][0]["sources"]  # type: ignore[index]
    sources.append(  # type: ignore[union-attr]
        {
            "provider": "linkedin",
            "source_raw_id": "linkedin-ds-1",
            "apply_url": "https://linkedin.com/jobs/view/1",
            "first_seen_at": "2026-07-02T00:00:00+00:00",
            "last_seen_at": "2026-07-30T00:00:00+00:00",
            "is_active": True,
        }
    )

    import_seed(session, SeedPayload.model_validate(seed_data))

    assert session.scalar(select(func.count(JobPosting.id))) == 1
    assert session.scalar(select(func.count(JobSource.id))) == 2
    assert len(set(session.scalars(select(JobSource.job_posting_id)))) == 1


def test_seed_schema_rejects_non_http_urls(seed_data: dict[str, object]) -> None:
    seed_data["companies"][0]["website"] = "file:///etc/passwd"  # type: ignore[index]

    with pytest.raises(ValidationError, match="HTTP"):
        SeedPayload.model_validate(seed_data)


def test_seed_schema_normalizes_source_timestamps_to_utc(
    seed_data: dict[str, object]
) -> None:
    source = seed_data["companies"][0]["jobs"][0]["sources"][0]  # type: ignore[index]
    source["first_seen_at"] = "2026-07-31T08:00:00+08:00"

    parsed = SeedPayload.model_validate(seed_data)
    first_seen_at = parsed.companies[0].jobs[0].sources[0].first_seen_at

    assert first_seen_at.tzinfo is UTC
    assert first_seen_at.utcoffset() == timedelta(0)


def test_invalid_entity_is_rejected_before_any_rows_are_written(
    session: Session, seed_payload: SeedPayload
) -> None:
    invalid_company = seed_payload.companies[0].model_copy(
        update={"website": "file:///etc/passwd"}
    )
    bypassed_validation = seed_payload.model_copy(
        update={"companies": [seed_payload.companies[0], invalid_company]}
    )

    with pytest.raises(ValidationError, match="HTTP"):
        import_seed(session, bypassed_validation)

    assert session.scalar(select(func.count(Company.id))) == 0
