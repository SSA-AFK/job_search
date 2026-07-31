from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    CollectionStatus,
    Company,
    CompanyAlias,
    CompanySource,
    CrawlRun,
    FilingType,
    JobPosting,
    JobSource,
    RegulatoryFiling,
    SourceDocument,
)


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
def company(session: Session) -> Company:
    value = Company(canonical_name="Example", normalized_name="example")
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def other_company(session: Session) -> Company:
    value = Company(canonical_name="Other", normalized_name="other")
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def job(session: Session, company: Company) -> JobPosting:
    value = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build systems",
    )
    session.add(value)
    session.commit()
    return value


def test_job_source_identity_is_provider_scoped(session: Session, job: JobPosting) -> None:
    session.add(
        JobSource(
            job_posting_id=job.id,
            provider="zhihu",
            source_raw_id="42",
            apply_url="https://example.com/a",
        )
    )
    session.commit()
    session.add(
        JobSource(
            job_posting_id=job.id,
            provider="zhihu",
            source_raw_id="42",
            apply_url="https://example.com/b",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_same_raw_job_id_is_allowed_for_different_providers(
    session: Session, job: JobPosting
) -> None:
    session.add_all(
        [
            JobSource(
                job_posting_id=job.id,
                provider="zhihu",
                source_raw_id="42",
                apply_url="https://example.com/a",
            ),
            JobSource(
                job_posting_id=job.id,
                provider="linkedin",
                source_raw_id="42",
                apply_url="https://example.com/b",
            ),
        ]
    )

    session.commit()


def test_alias_can_belong_to_only_one_company(
    session: Session, company: Company, other_company: Company
) -> None:
    session.add(CompanyAlias(company_id=company.id, alias="示例", normalized_alias="示例"))
    session.commit()
    session.add(
        CompanyAlias(company_id=other_company.id, alias="示例", normalized_alias="示例")
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_filing_identity_is_type_and_number_scoped(
    session: Session, company: Company, other_company: Company
) -> None:
    session.add(
        RegulatoryFiling(
            company_id=company.id,
            filing_type=FilingType.ICP,
            filing_number="沪ICP备123号",
            filing_name="Example",
        )
    )
    session.commit()
    session.add(
        RegulatoryFiling(
            company_id=other_company.id,
            filing_type=FilingType.ICP,
            filing_number="沪ICP备123号",
            filing_name="Other",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_company_cascades_owned_records(
    session: Session, company: Company, job: JobPosting
) -> None:
    now = datetime.now(UTC)
    source = SourceDocument(
        provider="official",
        url="https://example.com",
        text_excerpt="Evidence",
        content_hash="a" * 64,
        fetched_at=now,
    )
    session.add(source)
    session.flush()
    session.add_all(
        [
            CompanyAlias(company_id=company.id, alias="Example Ltd", normalized_alias="example ltd"),
            CompanySource(
                company_id=company.id,
                source_document_id=source.id,
                covered_fields=["canonical_name"],
                confidence=1,
            ),
            JobSource(
                job_posting_id=job.id,
                source_document_id=source.id,
                provider="official",
                source_raw_id="job-1",
                apply_url="https://example.com/jobs/1",
            ),
            RegulatoryFiling(
                company_id=company.id,
                filing_type=FilingType.BUSINESS_LICENSE,
                filing_number="91310000EXAMPLE",
                filing_name="Example",
            ),
        ]
    )
    session.commit()

    session.delete(company)
    session.commit()

    assert session.scalar(select(func.count()).select_from(CompanyAlias)) == 0
    assert session.scalar(select(func.count()).select_from(CompanySource)) == 0
    assert session.scalar(select(func.count()).select_from(JobPosting)) == 0
    assert session.scalar(select(func.count()).select_from(JobSource)) == 0
    assert session.scalar(select(func.count()).select_from(RegulatoryFiling)) == 0
    assert session.scalar(select(func.count()).select_from(SourceDocument)) == 1


def test_collection_status_values_match_persisted_contract() -> None:
    assert [status.value for status in CollectionStatus] == [
        "queued",
        "running",
        "succeeded",
        "partial",
        "failed",
    ]


def test_enum_columns_keep_documented_storage_widths() -> None:
    assert JobPosting.__table__.c.job_type.type.length == 50
    assert RegulatoryFiling.__table__.c.filing_type.type.length == 50
    assert CrawlRun.__table__.c.run_type.type.length == 30
    assert CrawlRun.__table__.c.status.type.length == 20


def test_crawl_run_counters_have_database_defaults() -> None:
    for column_name in ("documents_found", "jobs_found", "jobs_written"):
        assert CrawlRun.__table__.c[column_name].server_default is not None


def test_sqlite_datetime_round_trip_normalizes_aware_values_to_utc(session: Session) -> None:
    utc_plus_eight = timezone(timedelta(hours=8))
    source = SourceDocument(
        provider="official",
        url="https://example.com/time",
        text_excerpt="Timestamp evidence",
        content_hash="b" * 64,
        published_at=datetime(2026, 7, 31, 12, 30, tzinfo=utc_plus_eight),
        fetched_at=datetime(2026, 7, 31, 13, 45, tzinfo=utc_plus_eight),
    )
    session.add(source)
    session.commit()
    session.expire(source)

    assert source.published_at == datetime(2026, 7, 31, 4, 30, tzinfo=UTC)
    assert source.fetched_at == datetime(2026, 7, 31, 5, 45, tzinfo=UTC)
