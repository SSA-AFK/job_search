from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ExtractionError, ProviderError
from app.ingestion.orchestrator import IngestionOrchestrator
from app.ingestion.persistence.result import PersistenceResult
from app.models import CollectionStatus


@dataclass
class FakeRun:
    id: UUID
    status: CollectionStatus = CollectionStatus.QUEUED
    company_id: UUID | None = None
    providers_attempted: list[str] = field(default_factory=list)
    documents_found: int = 0
    jobs_found: int = 0
    jobs_written: int = 0
    error_code: str | None = None
    error_detail: str | None = None


class FakeRuns:
    def __init__(self, runs: dict[UUID, FakeRun]) -> None:
        self.runs = runs
        self.requests = {run_id: SimpleNamespace(query="Acme") for run_id in runs}

    def start_or_get_terminal(self, run_id: UUID) -> FakeRun | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        if run.status in {CollectionStatus.SUCCEEDED, CollectionStatus.PARTIAL, CollectionStatus.FAILED}:
            return run
        if run.status is not CollectionStatus.QUEUED:
            raise ValueError("invalid_run_state")
        run.status = CollectionStatus.RUNNING
        return run

    def get_request_for_run(self, run: FakeRun) -> SimpleNamespace:
        return self.requests[run.id]

    def finish(
        self,
        run: FakeRun,
        *,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> FakeRun:
        run.status = status
        run.providers_attempted = list(providers_attempted)
        run.documents_found = documents_found
        run.jobs_found = jobs_found
        run.jobs_written = persistence.jobs_written if persistence else 0
        run.company_id = persistence.company_id if persistence else None
        run.error_code = error_code
        run.error_detail = error_detail
        return run


class FakeProvider:
    def __init__(self, name: str, result: ProviderResult | Exception) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    async def search(self, _query: ProviderQuery) -> ProviderResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeExtractor:
    def __init__(self, *, profile: object | Exception, jobs: tuple[object, ...] = ()) -> None:
        self.profile = profile
        self.jobs = jobs

    async def discover(self, _documents: tuple[RawDocument, ...]) -> tuple[object, ...]:
        return ()

    async def extract_profile(self, _company: object, _documents: tuple[RawDocument, ...]) -> object:
        if isinstance(self.profile, Exception):
            raise self.profile
        return self.profile

    async def extract_jobs(self, _company: object, _documents: tuple[RawDocument, ...]) -> tuple[object, ...]:
        return self.jobs


class FakeBatchBuilder:
    async def build(self, **_kwargs: object) -> object:
        return object()


class FakePersistence:
    def __init__(self) -> None:
        self.calls = 0
        self.result = PersistenceResult(uuid4(), documents_written=2, jobs_written=1, warnings=())

    def persist(self, _batch: object, _run_id: UUID) -> PersistenceResult:
        self.calls += 1
        return self.result


def document() -> RawDocument:
    return RawDocument(
        provider="site",
        external_id="acme-home",
        url="https://acme.example/careers",
        title="Careers",
        text="Acme is hiring.",
        published_at=None,
    )


def orchestrator_for(
    run: FakeRun,
    *,
    providers: list[FakeProvider],
    profile: object | Exception | None = None,
    jobs: tuple[object, ...] = (),
) -> tuple[IngestionOrchestrator, FakeRuns, FakePersistence]:
    runs = FakeRuns({run.id: run})
    persistence = FakePersistence()
    return (
        IngestionOrchestrator(
            providers=providers,
            extractor=FakeExtractor(profile=profile or SimpleNamespace(name="Acme"), jobs=jobs),
            batch_builder=FakeBatchBuilder(),
            persistence=persistence,
            runs=runs,
        ),
        runs,
        persistence,
    )


@pytest.mark.asyncio
async def test_unknown_run_returns_a_deterministic_failure() -> None:
    orchestrator, _runs, _persistence = orchestrator_for(
        FakeRun(uuid4()), providers=[]
    )

    result = await orchestrator.run(uuid4())

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "run_not_found"


@pytest.mark.asyncio
async def test_invalid_starting_state_returns_a_failure_without_external_work() -> None:
    run = FakeRun(uuid4(), status=CollectionStatus.RUNNING)
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider])

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "invalid_run_state"
    assert provider.calls == 0
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_terminal_run_is_returned_without_reprocessing() -> None:
    run = FakeRun(uuid4(), status=CollectionStatus.SUCCEEDED, documents_found=3)
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider])

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert result.documents_found == 3
    assert provider.calls == 0
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_unexpected_extraction_exception_is_sanitized_before_terminal_failure() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, runs, persistence = orchestrator_for(
        run,
        providers=[provider],
        profile=RuntimeError("postgres://private-password"),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "ingestion_failed"
    assert runs.runs[run.id].error_detail == "ingestion_failed"
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_provider_failure_with_persisted_data_is_partial() -> None:
    run = FakeRun(uuid4())
    successful = FakeProvider("site", ProviderResult(documents=(document(),)))
    failing = FakeProvider(
        "jobs", ProviderError(code="provider_timeout", retryable=True, detail="private upstream address")
    )
    orchestrator, runs, persistence = orchestrator_for(run, providers=[successful, failing])

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.PARTIAL
    assert result.error_code == "provider_timeout"
    assert result.providers_attempted == ("site", "jobs")
    assert runs.runs[run.id].status is CollectionStatus.PARTIAL
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_extraction_failure_without_data_is_failed_without_persistence() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, runs, persistence = orchestrator_for(
        run,
        providers=[provider],
        profile=ExtractionError(code="invalid_output", detail="model response"),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "invalid_output"
    assert runs.runs[run.id].error_detail == "invalid_output"
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_success_records_document_and_job_counters() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(), document())))
    orchestrator, runs, _persistence = orchestrator_for(
        run,
        providers=[provider],
        jobs=(SimpleNamespace(), SimpleNamespace()),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert result.documents_found == 2
    assert result.jobs_found == 2
    assert result.jobs_written == 1
    assert runs.runs[run.id].status is CollectionStatus.SUCCEEDED
