from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.models import Base, CollectionStatus, Company, CrawlRun, RecruitingStatus, RunType
from app.recruiting_coverage.service import RecruitingCoverageService


def test_pending_entry_reports_the_latest_discovery_attempt() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    checked_at = datetime(2026, 8, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        company = Company(canonical_name="Acme", normalized_name=normalize_name("Acme"))
        session.add(company)
        session.flush()
        session.add(
            CrawlRun(
                company_id=company.id,
                run_type=RunType.ON_DEMAND,
                status=CollectionStatus.FAILED,
                completed_at=checked_at,
            )
        )
        session.commit()

        coverage = RecruitingCoverageService(session).build(company.id, now=checked_at)

    assert coverage.status is RecruitingStatus.ENTRY_DISCOVERY_PENDING
    assert coverage.last_checked_at == checked_at
    assert coverage.reason_code == "ats_entry_discovery_pending"
