import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.imports.boss_json import load_boss_json
from app.job_enumeration.manual_batch import ManualBossImportService
from app.models import Base, Company, JobCollectionSnapshot, JobPosting, JobSource


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _batch(tmp_path, company_name: str = "Acme"):
    path = tmp_path / "boss.json"
    path.write_text(
        json.dumps(
            [
                {
                    "job_id": "boss-job-1",
                    "job_name": "AI Engineer",
                    "job_url": "https://www.zhipin.com/job_detail/1.html",
                    "company_name": company_name,
                    "city": "上海",
                }
            ]
        ),
        encoding="utf-8",
    )
    return load_boss_json(path, observed_at=datetime(2026, 8, 12, tzinfo=UTC))


def test_imports_only_into_existing_company_without_complete_snapshot(session, tmp_path) -> None:
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.commit()

    summary = ManualBossImportService(session).import_file(_batch(tmp_path))

    assert summary.jobs_created == 1
    assert session.query(JobPosting).one().company_id == company.id
    assert session.query(JobSource).one().lifecycle_managed is False
    assert session.query(JobCollectionSnapshot).count() == 0


def test_unmatched_company_is_not_created(session, tmp_path) -> None:
    summary = ManualBossImportService(session).import_file(_batch(tmp_path, "Unknown"))

    assert summary.records_unmatched == 1
    assert session.query(Company).count() == 0
    assert session.query(JobPosting).count() == 0


def test_reimport_is_idempotent(session, tmp_path) -> None:
    session.add(Company(canonical_name="Acme", normalized_name="acme"))
    session.commit()
    service = ManualBossImportService(session)
    batch = _batch(tmp_path)

    first = service.import_file(batch)
    second = service.import_file(batch)

    assert first.sources_created == 1
    assert second.sources_created == 0
    assert session.query(JobPosting).count() == 1
    assert session.query(JobSource).count() == 1
