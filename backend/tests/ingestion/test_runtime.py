import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.deduplication.semantic import DuplicateDecision
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    JobCandidate,
)
from app.ingestion.runtime import build_ingestion_orchestrator
from app.models import (
    Base,
    CollectionRequest,
    CollectionStatus,
    Company,
    CompanySource,
    CrawlRun,
    JobPosting,
    JobSource,
    SourceDocument,
)


class Provider:
    name = "careers"
    calls = 0

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        self.calls += 1
        return ProviderResult(documents=(RawDocument(provider="careers", external_id="job-1", url="https://acme.example/jobs/1", title="Engineer", text="Acme engineer", published_at=None),))


class Extractor:
    calls = 0

    async def discover(self, _documents):
        self.calls += 1
        return (CompanyCandidate(name="Acme", evidence_ids=("job-1",), confidence=1),)

    async def extract_profile(self, _company: CompanyRef, _documents):
        return CompanyProfileCandidate(name="Acme", evidence_ids=("job-1",), confidence=1, description="Acme")

    async def extract_jobs(self, _company: CompanyRef, _documents):
        return (JobCandidate(title="Engineer", evidence_ids=("job-1",), confidence=1),)


class SemanticJudge:
    async def jobs_are_duplicates(self, _left, _right) -> DuplicateDecision:
        return DuplicateDecision(False)


@pytest.mark.parametrize("reused", [("run", "dedup"), ("run", "write"), ("dedup", "write")])
def test_runtime_rejects_each_reused_session_pair(reused: tuple[str, str]) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as first, Session(engine) as second, Session(engine) as third, pytest.raises(ValueError, match="distinct sessions"):
        sessions = {"run": first, "dedup": second, "write": third}
        sessions[reused[1]] = sessions[reused[0]]
        build_ingestion_orchestrator(
            run_state_session=sessions["run"],
            dedup_read_session=sessions["dedup"],
            persistence_write_session=sessions["write"],
            providers=(),
            extractor=None,  # type: ignore[arg-type]
            semantic_judge=None,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_runtime_runs_real_three_session_pipeline_and_terminal_retry(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as state, Session(engine, expire_on_commit=False) as dedup, Session(engine, expire_on_commit=False) as write:
        request, run = CollectionRepository(state).create_request("Acme", "acme")
        state.commit()
        provider = Provider()
        extractor = Extractor()
        orchestrator = build_ingestion_orchestrator(
            run_state_session=state, dedup_read_session=dedup, persistence_write_session=write,
            providers=(provider,), extractor=extractor, semantic_judge=SemanticJudge(),
        )

        result = await orchestrator.run(run.id)

        persisted_run = state.get(CrawlRun, run.id)
        persisted_request = state.get(CollectionRequest, request.id)
        persisted_document = state.query(SourceDocument).one()
        persisted_company = state.query(Company).one()
        persisted_company_source = state.query(CompanySource).one()
        assert result.status is CollectionStatus.SUCCEEDED
        assert result.company_id == persisted_company.id
        assert result.documents_found == 1
        assert result.jobs_found == 1
        assert result.jobs_written == 1
        assert persisted_run is not None
        assert persisted_run.status is CollectionStatus.SUCCEEDED
        assert persisted_run.company_id == persisted_company.id
        assert persisted_run.documents_found == 1
        assert persisted_run.jobs_found == 1
        assert persisted_run.jobs_written == 1
        assert persisted_request is not None
        assert persisted_request.status is CollectionStatus.SUCCEEDED
        assert persisted_request.company_id == persisted_company.id
        assert persisted_document.external_id == "job-1"
        assert persisted_company.canonical_name == "Acme"
        assert persisted_company_source.company_id == persisted_company.id
        assert persisted_company_source.source_document_id == persisted_document.id
        assert persisted_company_source.covered_fields == ["canonical_name", "description"]
        assert state.query(JobPosting).count() == 1 and state.query(JobSource).count() == 1
        assert persisted_run.providers_attempted == ["careers"]
        assert state.query(JobSource).one().source_raw_id == "job-1"

    class FailProvider:
        name = "fail"

        async def search(self, _query):
            raise AssertionError("terminal retry must not call providers")

    class FailExtractor:
        async def discover(self, _documents):
            raise AssertionError("terminal retry must not call extraction")

        async def extract_profile(self, _company, _documents):
            raise AssertionError("terminal retry must not call extraction")

        async def extract_jobs(self, _company, _documents):
            raise AssertionError("terminal retry must not call extraction")

    with Session(engine, expire_on_commit=False) as state, Session(engine, expire_on_commit=False) as dedup, Session(engine, expire_on_commit=False) as write:
        retry = build_ingestion_orchestrator(
            run_state_session=state, dedup_read_session=dedup, persistence_write_session=write,
            providers=(FailProvider(),), extractor=FailExtractor(), semantic_judge=SemanticJudge(),
        )
        counts_before_retry = {
            "documents_written": state.query(SourceDocument).count(),
            "companies": state.query(Company).count(),
            "company_sources": state.query(CompanySource).count(),
            "jobs": state.query(JobPosting).count(),
            "job_sources": state.query(JobSource).count(),
        }
        repeated = await retry.run(run.id)
        counts_after_retry = {
            "documents_written": state.query(SourceDocument).count(),
            "companies": state.query(Company).count(),
            "company_sources": state.query(CompanySource).count(),
            "jobs": state.query(JobPosting).count(),
            "job_sources": state.query(JobSource).count(),
        }
        assert repeated.status is CollectionStatus.SUCCEEDED
        assert repeated.company_id == persisted_company.id
        assert repeated.documents_found == 1
        assert repeated.jobs_found == 1
        assert repeated.jobs_written == 1
        assert counts_before_retry == {
            "documents_written": 1,
            "companies": 1,
            "company_sources": 1,
            "jobs": 1,
            "job_sources": 1,
        }
        assert counts_after_retry == counts_before_retry
