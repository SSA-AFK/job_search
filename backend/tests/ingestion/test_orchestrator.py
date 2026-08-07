import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.company_identity.contracts import (
    CompanyIdentityInput,
    CompanyIdentityReviewDraft,
    IdentityReviewReason,
)
from app.company_identity.service import (
    IdentityReviewConflict,
    IdentitySearchUnavailable,
)
from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import (
    ExtractionError,
    ProviderError,
    RetryableInfrastructureError,
    RunClaimError,
)
from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.normalization.company import normalize_company
from app.ingestion.orchestrator import IngestionOrchestrator
from app.ingestion.persistence.contracts import (
    BatchBuildOutcome,
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
)
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
    claim_token: str | None = None
    started_at: datetime | None = None


class FakeRuns:
    def __init__(self, runs: dict[UUID, FakeRun]) -> None:
        self.runs = runs
        self.requests = {run_id: SimpleNamespace(query="Acme") for run_id in runs}
        self.claim_is_current = True

    def claim_queued(self, run_id: UUID):
        run = self.runs.get(run_id)
        if run is None:
            return None
        if run.status in {CollectionStatus.SUCCEEDED, CollectionStatus.PARTIAL, CollectionStatus.FAILED}:
            return SimpleNamespace(
                run=run, claimed=False, claim_token=run.claim_token
            )
        if run.status is CollectionStatus.RUNNING:
            return SimpleNamespace(
                run=run, claimed=False, claim_token=run.claim_token
            )
        run.status = CollectionStatus.RUNNING
        run.claim_token = str(uuid4())
        run.started_at = datetime(2026, 8, 4, 12, tzinfo=UTC)
        return SimpleNamespace(run=run, claimed=True, claim_token=run.claim_token)

    def get_run(self, run_id: UUID) -> FakeRun | None:
        return self.runs.get(run_id)

    def recover_claim_token(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> str | None:
        run = self.runs.get(run_id)
        if run is None or run.claim_token != expected_claim_token:
            return None
        return run.claim_token

    def owns_claim(self, run_id: UUID, *, expected_claim_token: str) -> bool:
        run = self.runs.get(run_id)
        return (
            self.claim_is_current
            and run is not None
            and run.status is CollectionStatus.RUNNING
            and run.claim_token == expected_claim_token
        )

    def get_request_for_run(self, run: FakeRun) -> SimpleNamespace:
        return self.requests[run.id]

    def finish(
        self,
        run: FakeRun,
        *,
        expected_claim_token: str | None = None,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> FakeRun:
        assert expected_claim_token == run.claim_token
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
    def __init__(
        self,
        name: str,
        result: ProviderResult | Exception,
        *,
        requires_website: bool = False,
        approved_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.result = result
        self.requires_website = requires_website
        self.approved_hosts = approved_hosts
        self.calls = 0
        self.queries: list[ProviderQuery] = []

    async def search(self, query: ProviderQuery) -> ProviderResult:
        self.calls += 1
        self.queries.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeExtractor:
    def __init__(
        self,
        *,
        profile: object | Exception,
        jobs: tuple[object, ...] = (),
        discovered: tuple[CompanyCandidate, ...] | None = None,
    ) -> None:
        self.profile = profile
        self.jobs = jobs
        self.discovered = (
            (CompanyCandidate(name="Acme", evidence_ids=("acme-home",), confidence=1),)
            if discovered is None
            else discovered
        )
        self.profile_documents: tuple[RawDocument, ...] = ()
        self.job_documents: tuple[RawDocument, ...] = ()

    async def discover(self, _documents: tuple[RawDocument, ...]) -> tuple[CompanyCandidate, ...]:
        return self.discovered

    async def extract_profile(self, _company: object, _documents: tuple[RawDocument, ...]) -> object:
        self.profile_documents = _documents
        if isinstance(self.profile, Exception):
            raise self.profile
        return self.profile

    async def extract_jobs(self, _company: object, _documents: tuple[RawDocument, ...]) -> tuple[object, ...]:
        self.job_documents = _documents
        return self.jobs


class FakeBatchBuilder:
    def __init__(self, outcome: BatchBuildOutcome | None = None) -> None:
        self.outcome = outcome

    async def build(self, **_kwargs: object) -> BatchBuildOutcome:
        if self.outcome is not None:
            return self.outcome
        source = document()
        collected_at = datetime(2026, 8, 7, tzinfo=UTC)
        return BatchBuildOutcome.ready(
            NormalizedBatch(
                documents=(
                    NormalizedDocument(
                        evidence_id="acme-home",
                        document=source,
                        fetched_at=collected_at,
                    ),
                ),
                company=NormalizedCompanyRecord(
                    candidate=normalize_company(
                        CompanyCandidate(
                            name="Acme",
                            evidence_ids=("acme-home",),
                            confidence=1,
                        )
                    ),
                    company_id=None,
                ),
                collected_at=collected_at,
            )
        )


class FakeIdentityReviewRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.records: list[tuple[UUID, CompanyIdentityReviewDraft]] = []
        self.error = error

    def record(
        self, *, crawl_run_id: UUID, draft: CompanyIdentityReviewDraft
    ) -> object:
        self.records.append((crawl_run_id, draft))
        if self.error is not None:
            raise self.error
        return object()


class FakePersistence:
    def __init__(self) -> None:
        self.calls = 0
        self.result = PersistenceResult(uuid4(), documents_written=2, jobs_written=1, warnings=())

    def persist(
        self,
        _batch: object,
        _run_id: UUID,
        *,
        expected_claim_token: str | None = None,
    ) -> PersistenceResult:
        assert expected_claim_token is not None
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


def website_document() -> RawDocument:
    return RawDocument(
        provider="company_site",
        external_id=None,
        url="https://acme.example/about",
        title="About Acme",
        text="Acme builds reliable systems.",
        published_at=None,
    )


def orchestrator_for(
    run: FakeRun,
    *,
    providers: list[FakeProvider],
    profile: object | Exception | None = None,
    jobs: tuple[object, ...] = (),
    discovered: tuple[CompanyCandidate, ...] | None = None,
    build_outcome: BatchBuildOutcome | None = None,
    identity_review_recorder: FakeIdentityReviewRecorder | None = None,
) -> tuple[IngestionOrchestrator, FakeRuns, FakePersistence]:
    runs = FakeRuns({run.id: run})
    persistence = FakePersistence()
    return (
        IngestionOrchestrator(
            providers=providers,
            extractor=FakeExtractor(
                profile=profile or SimpleNamespace(name="Acme"), jobs=jobs, discovered=discovered
            ),
            batch_builder=FakeBatchBuilder(build_outcome),
            persistence=persistence,
            runs=runs,
            identity_review_recorder=(
                identity_review_recorder or FakeIdentityReviewRecorder()
            ),
        ),
        runs,
        persistence,
    )


def identity_review_outcome() -> tuple[BatchBuildOutcome, CompanyIdentityReviewDraft]:
    draft = CompanyIdentityReviewDraft(
        identity=CompanyIdentityInput(canonical_name="Acme Artificial Intelligenc"),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    return BatchBuildOutcome.review_required(draft), draft


@pytest.mark.asyncio
async def test_review_required_records_review_and_stops_before_persistence() -> None:
    run = FakeRun(uuid4())
    outcome, draft = identity_review_outcome()
    recorder = FakeIdentityReviewRecorder()
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[FakeProvider("site", ProviderResult(documents=(document(),)))],
        build_outcome=outcome,
        identity_review_recorder=recorder,
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "company_identity_review_required"
    assert recorder.records == [(run.id, draft)]
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_lost_claim_prevents_identity_review_write() -> None:
    run = FakeRun(uuid4())
    outcome, _draft = identity_review_outcome()
    recorder = FakeIdentityReviewRecorder()
    orchestrator, runs, persistence = orchestrator_for(
        run,
        providers=[FakeProvider("site", ProviderResult(documents=(document(),)))],
        build_outcome=outcome,
        identity_review_recorder=recorder,
    )
    runs.claim_is_current = False

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.RUNNING
    assert recorder.records == []
    assert persistence.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "review_error",
    [
        OperationalError("insert review", {}, RuntimeError("private database detail")),
        IdentitySearchUnavailable(),
    ],
)
async def test_review_store_infrastructure_failure_is_retryable(
    review_error: Exception,
) -> None:
    run = FakeRun(uuid4())
    outcome, _draft = identity_review_outcome()
    recorder = FakeIdentityReviewRecorder(review_error)
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[FakeProvider("site", ProviderResult(documents=(document(),)))],
        build_outcome=outcome,
        identity_review_recorder=recorder,
    )

    with pytest.raises(RetryableInfrastructureError) as raised:
        await orchestrator.run(run.id)

    assert raised.value.claim_token == run.claim_token
    assert str(raised.value) == "retryable infrastructure failure"
    assert run.status is CollectionStatus.RUNNING
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_review_validation_conflict_is_terminal_not_retryable() -> None:
    run = FakeRun(uuid4())
    outcome, _draft = identity_review_outcome()
    recorder = FakeIdentityReviewRecorder(IdentityReviewConflict())
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[FakeProvider("site", ProviderResult(documents=(document(),)))],
        build_outcome=outcome,
        identity_review_recorder=recorder,
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "identity_review_conflict"
    assert "private" not in (run.error_detail or "")
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_unknown_run_returns_a_deterministic_failure() -> None:
    orchestrator, _runs, _persistence = orchestrator_for(
        FakeRun(uuid4()), providers=[]
    )

    result = await orchestrator.run(uuid4())

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "run_not_found"


@pytest.mark.asyncio
async def test_running_redelivery_is_an_in_progress_noop_without_external_work() -> None:
    run = FakeRun(uuid4(), status=CollectionStatus.RUNNING)
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider])

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.RUNNING
    assert result.error_code is None
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
async def test_claim_failure_recovers_only_the_attempted_token() -> None:
    attempted_token = "worker-a-token"
    current_token = "worker-b-token"
    run = FakeRun(
        uuid4(), status=CollectionStatus.RUNNING, claim_token=current_token
    )
    orchestrator, _runs, _persistence = orchestrator_for(run, providers=[])
    recovered_tokens: list[str] = []

    class ClaimFailingRuns(FakeRuns):
        def claim_queued(self, run_id: UUID):
            raise RunClaimError(claim_token=attempted_token)

        def recover_claim_token(
            self, run_id: UUID, *, expected_claim_token: str
        ) -> str | None:
            recovered_tokens.append(expected_claim_token)
            return super().recover_claim_token(
                run_id, expected_claim_token=expected_claim_token
            )

    orchestrator.runs = ClaimFailingRuns({run.id: run})

    with pytest.raises(RetryableInfrastructureError) as raised:
        await orchestrator.run(run.id)

    assert recovered_tokens == [attempted_token]
    assert raised.value.claim_token is None


@pytest.mark.asyncio
async def test_unattributed_claim_failure_does_not_recover_state() -> None:
    run = FakeRun(uuid4())
    orchestrator, _runs, _persistence = orchestrator_for(run, providers=[])
    recovery_calls: list[UUID] = []

    class UnattributedClaimFailureRuns(FakeRuns):
        def claim_queued(self, run_id: UUID):
            raise OperationalError(
                "update crawl_runs", {}, RuntimeError("database unavailable")
            )

        def recover_claim_token(self, run_id: UUID, **_kwargs: str) -> str:
            recovery_calls.append(run_id)
            return "another-workers-token"

    orchestrator.runs = UnattributedClaimFailureRuns({run.id: run})

    with pytest.raises(RetryableInfrastructureError) as raised:
        await orchestrator.run(run.id)

    assert recovery_calls == []
    assert raised.value.claim_token is None


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
    assert json.loads(runs.runs[run.id].error_detail) == {
        "issues": [{"stage": "profile", "code": "ingestion_failed"}]
    }
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
    assert json.loads(runs.runs[run.id].error_detail) == {
        "issues": [
            {"stage": "provider", "provider": "jobs", "code": "provider_timeout"}
        ]
    }
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_provider_warning_with_persisted_data_is_partial() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider(
        "site", ProviderResult(documents=(document(),), warnings=("provider_degraded",))
    )
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider])

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.PARTIAL
    assert result.error_code == "provider_degraded"
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_worker_that_lost_its_claim_cannot_write_persistence() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, runs, persistence = orchestrator_for(run, providers=[provider])
    runs.claim_is_current = False

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.RUNNING
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_website_provider_runs_after_discovery_and_enriches_extraction_documents() -> None:
    run = FakeRun(uuid4())
    discovery = FakeProvider("discovery", ProviderResult(documents=(document(),)))
    company_site = FakeProvider(
        "company_site",
        ProviderResult(documents=(website_document(),)),
        requires_website=True,
        approved_hosts=frozenset({"acme.example"}),
    )
    selected = CompanyCandidate(
        name="Acme",
        website="https://acme.example",
        evidence_ids=("acme-home",),
        confidence=1,
    )
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[discovery, company_site],
        discovered=(selected,),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert result.providers_attempted == ("discovery", "company_site")
    assert result.documents_found == 2
    assert discovery.calls == 1
    assert company_site.calls == 1
    assert str(company_site.queries[0].website) == "https://acme.example/"
    assert company_site.queries[0].allowed_hosts == frozenset({"acme.example"})
    assert [item.provider for item in orchestrator.extractor.profile_documents] == [  # type: ignore[attr-defined]
        "site",
        "company_site",
    ]
    assert [item.provider for item in orchestrator.extractor.job_documents] == [  # type: ignore[attr-defined]
        "site",
        "company_site",
    ]
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_website_provider_failure_is_partial_with_original_provider_precedence() -> None:
    run = FakeRun(uuid4())
    company_site = FakeProvider(
        "company_site",
        ProviderError(code="site_timeout", retryable=True),
        requires_website=True,
        approved_hosts=frozenset({"acme.example"}),
    )
    discovery = FakeProvider(
        "discovery",
        ProviderResult(documents=(document(),), warnings=("discovery_warning",)),
    )
    selected = CompanyCandidate(
        name="Acme",
        website="https://acme.example",
        evidence_ids=("acme-home",),
        confidence=1,
    )
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[company_site, discovery],
        discovered=(selected,),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.PARTIAL
    assert result.error_code == "site_timeout"
    assert result.providers_attempted == ("company_site", "discovery")
    assert company_site.calls == 1
    assert str(company_site.queries[0].website) == "https://acme.example/"
    assert discovery.calls == 1
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_website_provider_is_not_attempted_without_discovered_website() -> None:
    run = FakeRun(uuid4())
    discovery = FakeProvider("discovery", ProviderResult(documents=(document(),)))
    company_site = FakeProvider(
        "company_site",
        ProviderResult(documents=(website_document(),)),
        requires_website=True,
        approved_hosts=frozenset({"acme.example"}),
    )
    selected = CompanyCandidate(
        name="Acme",
        evidence_ids=("acme-home",),
        confidence=1,
    )
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[discovery, company_site],
        discovered=(selected,),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert result.providers_attempted == ("discovery",)
    assert discovery.calls == 1
    assert company_site.calls == 0
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_unapproved_discovered_website_does_not_attempt_website_provider() -> None:
    run = FakeRun(uuid4())
    discovery = FakeProvider("discovery", ProviderResult(documents=(document(),)))
    company_site = FakeProvider(
        "company_site",
        ProviderResult(documents=(website_document(),)),
        requires_website=True,
        approved_hosts=frozenset({"approved.example"}),
    )
    selected = CompanyCandidate(
        name="Acme",
        website="https://acme.example",
        evidence_ids=("acme-home",),
        confidence=1,
    )
    orchestrator, _runs, persistence = orchestrator_for(
        run,
        providers=[discovery, company_site],
        discovered=(selected,),
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert result.providers_attempted == ("discovery",)
    assert result.documents_found == 1
    assert discovery.calls == 1
    assert company_site.calls == 0
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_zero_discovered_companies_fails_without_persistence() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    orchestrator, _runs, persistence = orchestrator_for(
        run, providers=[provider], discovered=()
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "no_valid_data"
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_discovery_prefers_exact_match_among_multiple_candidates() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    exact = CompanyCandidate(name="Acme", evidence_ids=("acme-home",), confidence=1)
    other = CompanyCandidate(name="Else", evidence_ids=("acme-home",), confidence=1)
    orchestrator, _runs, persistence = orchestrator_for(
        run, providers=[provider], discovered=(other, exact)
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_discovery_fails_without_persistence() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    candidates = (
        CompanyCandidate(name="Else", evidence_ids=("acme-home",), confidence=1),
        CompanyCandidate(name="Other", evidence_ids=("acme-home",), confidence=1),
    )
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider], discovered=candidates)

    result = await orchestrator.run(run.id)

    assert result.error_code == "ambiguous_company"
    assert persistence.calls == 0


@pytest.mark.asyncio
async def test_singular_related_discovery_candidate_is_selected() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    candidate = CompanyCandidate(name="Acme Incorporated", evidence_ids=("acme-home",), confidence=1)
    orchestrator, _runs, persistence = orchestrator_for(run, providers=[provider], discovered=(candidate,))

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.SUCCEEDED
    assert persistence.calls == 1


@pytest.mark.asyncio
async def test_singular_unrelated_discovery_candidate_is_rejected() -> None:
    run = FakeRun(uuid4())
    provider = FakeProvider("site", ProviderResult(documents=(document(),)))
    candidate = CompanyCandidate(
        name="Unrelated Holdings", evidence_ids=("acme-home",), confidence=1
    )
    orchestrator, _runs, persistence = orchestrator_for(
        run, providers=[provider], discovered=(candidate,)
    )

    result = await orchestrator.run(run.id)

    assert result.status is CollectionStatus.FAILED
    assert result.error_code == "ambiguous_company"
    assert persistence.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("providers", "expected_code", "has_data"),
    [
        (
            [
                FakeProvider("first", ProviderError(code="provider_timeout", retryable=True)),
                FakeProvider("second", ProviderResult(documents=(document(),), warnings=("provider_degraded",))),
            ],
            "provider_timeout",
            True,
        ),
        (
            [
                FakeProvider("first", ProviderResult(documents=(document(),), warnings=("provider_degraded",))),
                FakeProvider("second", ProviderError(code="provider_timeout", retryable=True)),
            ],
            "provider_degraded",
            True,
        ),
        (
            [
                FakeProvider("first", ProviderResult(documents=(), warnings=("provider_degraded",))),
                FakeProvider("second", ProviderResult(documents=(), warnings=("provider_limited",))),
            ],
            "provider_degraded",
            False,
        ),
    ],
)
async def test_provider_issue_precedence_is_provider_order(
    providers: list[FakeProvider], expected_code: str, has_data: bool
) -> None:
    run = FakeRun(uuid4())
    orchestrator, _runs, persistence = orchestrator_for(run, providers=providers)

    result = await orchestrator.run(run.id)

    assert result.error_code == expected_code
    assert result.status is (CollectionStatus.PARTIAL if has_data else CollectionStatus.FAILED)
    assert result.providers_attempted == ("first", "second")
    assert persistence.calls == int(has_data)


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
    assert json.loads(runs.runs[run.id].error_detail) == {
        "issues": [{"stage": "profile", "code": "invalid_output"}]
    }
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
