from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.collection.router import get_collection_service
from app.collection.service import CollectionService
from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider
from app.models import (
    CollectionRequest,
    Company,
    CompanySource,
    CrawlRun,
    JobPosting,
    JobSource,
    SourceDocument,
)
from app.tasks.collection import run_ingestion

from .conftest import IntegrationHarness, successful_llm_responses

ENDPOINT = ZhihuGlobalSearchProvider.endpoint


async def no_sleep(_delay: float) -> None:
    return None


def zhihu_provider() -> ZhihuGlobalSearchProvider:
    return ZhihuGlobalSearchProvider(
        enabled=True,
        access_secret="integration-secret",
        sleep=no_sleep,
        jitter=lambda: 0.0,
    )


def row_count(harness: IntegrationHarness, model: type[object]) -> int:
    with harness.session() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_collection_request_runs_worker_and_persists_searchable_evidence(
    integration_harness: IntegrationHarness,
    zhihu_payload: dict[str, object],
    respx_mock,
) -> None:
    route = respx_mock.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=zhihu_payload)
    )
    integration_harness.configure((zhihu_provider(),), successful_llm_responses())

    submitted = integration_harness.client.post(
        "/api/v1/collection-requests", json={"query": "Example Technologies"}
    )
    terminal = integration_harness.client.get(
        f"/api/v1/collection-requests/{submitted.json()['id']}"
    )
    search = integration_harness.client.get(
        "/api/v1/companies", params={"q": "Example Technologies"}
    )
    company_id = search.json()["items"][0]["id"]
    detail = integration_harness.client.get(f"/api/v1/companies/{company_id}")
    jobs = integration_harness.client.get(f"/api/v1/companies/{company_id}/jobs")

    assert submitted.status_code == 202
    assert terminal.json()["status"] == "succeeded"
    assert terminal.json()["company_id"] == company_id
    assert terminal.json()["error_code"] is None
    assert search.json()["total"] == 1
    assert detail.json()["sources"] == [
        {
            "provider": "zhihu_global_search",
            "url": "https://www.example.com/answer/123",
            "title": "Example Technologies hiring",
            "covered_fields": ["canonical_name", "description", "website"],
            "confidence": "0.980",
            "published_at": "2026-07-31T00:00:00Z",
            "fetched_at": detail.json()["sources"][0]["fetched_at"],
        }
    ]
    assert jobs.json()["total"] == 1
    assert jobs.json()["items"][0]["sources"] == [
        {
            "provider": "zhihu_global_search",
            "apply_url": "https://www.example.com/answer/123",
        }
    ]
    assert route.call_count == 1
    assert route.calls[0].request.url.params["Count"] == "10"
    assert row_count(integration_harness, CollectionRequest) == 1
    assert row_count(integration_harness, CrawlRun) == 1
    assert row_count(integration_harness, Company) == 1
    assert row_count(integration_harness, SourceDocument) == 1
    assert row_count(integration_harness, CompanySource) == 1
    assert row_count(integration_harness, JobPosting) == 1
    assert row_count(integration_harness, JobSource) == 1


def test_concurrent_duplicate_submission_returns_one_active_request(
    integration_harness: IntegrationHarness,
    monkeypatch,
) -> None:
    first_reads = Barrier(2)
    winner_committed = Event()
    duplicate_returned = Event()
    conflicts_recovered = 0
    dispatched: list[str] = []
    role_lock = Lock()
    roles = iter(("winner", "loser"))
    original_classifier = CollectionService._is_active_request_conflict
    original_get_active = CollectionRepository.get_active_request

    def record_conflict(error) -> bool:
        nonlocal conflicts_recovered
        result = original_classifier(error)
        conflicts_recovered += int(result)
        return result

    monkeypatch.setattr(
        CollectionService,
        "_is_active_request_conflict",
        staticmethod(record_conflict),
    )

    def synchronize_empty_read(
        repository: CollectionRepository, normalized_query: str
    ):
        result = original_get_active(repository, normalized_query)
        if result is None and not repository.session.info.get("first_read_synchronized"):
            repository.session.info["first_read_synchronized"] = True
            # End both read transactions before writes so SQLite produces the
            # uniqueness conflict instead of a snapshot-upgrade lock error.
            repository.session.rollback()
            first_reads.wait(timeout=5)
        return result

    monkeypatch.setattr(
        CollectionRepository,
        "get_active_request",
        synchronize_empty_read,
    )

    class CoordinatedSession(Session):
        def __init__(self, role: str) -> None:
            super().__init__(bind=integration_harness.engine, expire_on_commit=False)
            self.info["submission_role"] = role

        def commit(self) -> None:
            creates_request = any(
                isinstance(record, CollectionRequest) for record in self.new
            )
            if creates_request and self.info["submission_role"] == "loser":
                assert winner_committed.wait(timeout=5)
            super().commit()
            if creates_request and self.info["submission_role"] == "winner":
                winner_committed.set()

    class ObservedCollectionService(CollectionService):
        def submit(self, query: str):
            result = super().submit(query)
            if self.session.info["submission_role"] == "loser":
                duplicate_returned.set()
            return result

    def dispatch(run_id) -> str:
        dispatched.append(str(run_id))
        return "task-duplicate"

    def service_dependency():
        with role_lock:
            role = next(roles)
        session = CoordinatedSession(role)
        try:
            yield ObservedCollectionService(session, dispatch)
        finally:
            session.close()

    integration_harness.client.app.dependency_overrides[get_collection_service] = service_dependency

    def submit() -> dict[str, object]:
        response = integration_harness.client.post(
            "/api/v1/collection-requests", json={"query": "Example Technologies"}
        )
        assert response.status_code == 202
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit)
        second = executor.submit(submit)
        results = (first.result(timeout=10), second.result(timeout=10))

    assert duplicate_returned.is_set()
    assert conflicts_recovered == 1
    assert results[0]["id"] == results[1]["id"]
    assert len(dispatched) == 1
    assert row_count(integration_harness, CollectionRequest) == 1
    assert row_count(integration_harness, CrawlRun) == 1


def test_duplicate_task_delivery_is_terminally_idempotent(
    integration_harness: IntegrationHarness,
    zhihu_payload: dict[str, object],
    respx_mock,
) -> None:
    route = respx_mock.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=zhihu_payload)
    )
    integration_harness.configure((zhihu_provider(),), successful_llm_responses())
    submitted = integration_harness.client.post(
        "/api/v1/collection-requests", json={"query": "Example Technologies"}
    ).json()
    with integration_harness.session() as session:
        run_id = session.scalar(
            select(CrawlRun.id).where(CrawlRun.collection_request_id == submitted["id"])
        )

    repeated = run_ingestion.apply(args=[str(run_id)]).get()

    assert repeated["status"] == "succeeded"
    assert repeated["jobs_written"] == 1
    assert route.call_count == 1
    assert len(integration_harness.fake_llm.prompts) == 3
    assert row_count(integration_harness, SourceDocument) == 1
    assert row_count(integration_harness, CompanySource) == 1
    assert row_count(integration_harness, JobPosting) == 1
    assert row_count(integration_harness, JobSource) == 1
