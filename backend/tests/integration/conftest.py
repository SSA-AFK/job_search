from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.company_identity.contracts import CompanyIdentityCandidateMatch
from app.core.config import settings
from app.core.database import get_session
from app.ingestion.contracts import Provider
from app.ingestion.extraction.crew import CrewExtractor
from app.main import create_app
from app.models import Base
from app.tasks.celery_app import celery_app
from app.tasks.collection import RuntimeComponents


class FakeLlm:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.prompts: list[str] = []

    async def complete(
        self, prompt: str, *, response_schema: object = None
    ) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected LLM request")
        return self.responses.pop(0)


@dataclass
class IntegrationHarness:
    engine: Any
    client: TestClient
    fake_llm: FakeLlm
    providers: Sequence[Provider] = field(default_factory=tuple)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def configure(self, providers: Sequence[Provider], responses: Sequence[str]) -> None:
        self.providers = providers
        self.fake_llm.responses = list(responses)

    def runtime_components(self) -> RuntimeComponents:
        return RuntimeComponents(
            providers=self.providers,
            extractor=CrewExtractor(self.fake_llm),
        )


def successful_llm_responses(*, evidence_id: str = "answer-123") -> tuple[str, str, str]:
    return (
        json.dumps(
            {
                "companies": [
                    {
                        "name": "Example Technologies",
                        "website": "https://www.example.com",
                        "description": "Enterprise data systems company.",
                        "evidence_ids": [evidence_id],
                        "confidence": 0.98,
                    }
                ],
                "profiles": [],
                "jobs": [],
                "filings": [],
            }
        ),
        json.dumps(
            {
                "companies": [],
                "profiles": [
                    {
                        "name": "Example Technologies",
                        "website": "https://www.example.com",
                        "description": "Enterprise data systems company.",
                        "headquarters": "Shanghai",
                        "founded_year": 2018,
                        "evidence_ids": [evidence_id],
                        "confidence": 0.97,
                    }
                ],
                "jobs": [],
                "filings": [],
            }
        ),
        json.dumps(
            {
                "companies": [],
                "profiles": [],
                "jobs": [
                    {
                        "company_name": "Example Technologies",
                        "title": "Senior Data Engineer",
                        "employment_type": "full_time",
                        "location": "Shanghai",
                        "provider": "zhihu_global_search",
                        "source_raw_id": "answer-123",
                        "source_evidence_id": evidence_id,
                        "apply_url": "https://www.example.com/answer/123",
                        "posted_at": "2026-07-31",
                        "salary": "30K-45Kx13 months",
                        "description": "Build reliable data platforms.",
                        "evidence_ids": [evidence_id],
                        "confidence": 0.95,
                    }
                ],
                "filings": [],
            }
        ),
    )


@pytest.fixture
def zhihu_payload() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "zhihu_success.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def integration_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationHarness]:
    monkeypatch.setattr(settings, "collection_enabled", True)

    async def no_similar_identity_neighbors(
        _repository: object,
        _names: frozenset[str],
        *,
        limit: int,
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        assert limit == 20
        return ()

    monkeypatch.setattr(
        "app.ingestion.runtime.SqlAlchemyCompanyDeduplicationRepository."
        "similarity_search_available",
        lambda _repository: True,
    )
    monkeypatch.setattr(
        "app.ingestion.runtime.SqlAlchemyCompanyDeduplicationRepository.find_similar_names",
        no_similar_identity_neighbors,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'acceptance.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)  # type: ignore[attr-defined]

    def session_dependency() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_dependency
    fake_llm = FakeLlm()
    previous_celery = dict(celery_app.conf)
    celery_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=False,
    )
    monkeypatch.setattr(
        "app.tasks.collection.SessionLocal",
        lambda: Session(engine, expire_on_commit=False),
    )
    with TestClient(app) as client:
        harness = IntegrationHarness(engine=engine, client=client, fake_llm=fake_llm)
        monkeypatch.setattr(
            "app.tasks.collection.load_runtime_components",
            harness.runtime_components,
        )
        yield harness
    celery_app.conf.update(previous_celery)
    engine.dispose()
