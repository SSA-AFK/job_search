import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.collection.repository import CollectionRepository
from app.ingestion.entry_verification.contracts import (
    EntryVerificationResult,
    EntryVerificationStatus,
)
from app.models import Base, Company, JobEntry
from app.tasks.entry_verification import _run


class VerifiedService:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def find_verified_entry(self, **_kwargs: object) -> EntryVerificationResult:
        return EntryVerificationResult(
            candidate_url="https://acme.example/careers",
            final_url="https://acme.example/careers",
            status=EntryVerificationStatus.VERIFIED,
            ownership_evidence="company_domain",
        )


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


@pytest.mark.anyio
async def test_existing_company_verification_finishes_without_jobs(monkeypatch) -> None:
    engine, factory = _database()
    with Session(engine, expire_on_commit=False) as session:
        company = Company(
            canonical_name="Acme",
            normalized_name="acme",
            website="https://acme.example",
        )
        session.add(company)
        _request, run = CollectionRepository(session).create_request("Acme", "acme")
        session.commit()
        run_id = run.id

    monkeypatch.setattr("app.tasks.entry_verification.SessionLocal", factory)
    monkeypatch.setattr(
        "app.tasks.entry_verification.EntryVerificationService", VerifiedService
    )

    payload = await _run(run_id)

    assert payload["status"] == "succeeded"
    assert payload["jobs_found"] == 0
    assert payload["jobs_written"] == 0
    with factory() as session:
        entry = session.query(JobEntry).one()
        assert entry.is_primary is True
        assert entry.last_success_at is not None


@pytest.mark.anyio
async def test_unknown_company_stops_without_constructing_validator(monkeypatch) -> None:
    _engine, factory = _database()
    with factory() as session:
        _request, run = CollectionRepository(session).create_request("Missing", "missing")
        session.commit()
        run_id = run.id

    monkeypatch.setattr("app.tasks.entry_verification.SessionLocal", factory)

    class MustNotConstruct:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("external verifier must not be constructed")

    monkeypatch.setattr(
        "app.tasks.entry_verification.EntryVerificationService", MustNotConstruct
    )

    payload = await _run(run_id)

    assert payload["status"] == "partial"
    assert payload["error_code"] == "company_not_found"
