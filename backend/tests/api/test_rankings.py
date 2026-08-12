from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models import (
    Base,
    Company,
    CompanyRankingSnapshot,
    RankingPilot,
    RankingPilotMember,
)
from app.rankings.service import RULE_VERSION


@pytest.fixture
def ranking_client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as session:
        pilot = RankingPilot(
            industry="ai",
            input_sha256="a" * 64,
            selection_seed="test",
            sample_size=3,
            created_at=now,
        )
        session.add(pilot)
        session.flush()
        rows = (("甲公司", 80, True), ("乙公司", 80, True), ("观察公司", 0, False))
        for index, (name, score, eligible) in enumerate(rows):
            company = Company(canonical_name=name, normalized_name=name)
            session.add(company)
            session.flush()
            session.add(
                RankingPilotMember(
                    pilot_id=pilot.id,
                    company_id=company.id,
                    source_row=index + 3,
                    source_identity_hash=str(index).zfill(64),
                    stratum="test",
                    selection_reason="test",
                )
            )
            session.add(
                CompanyRankingSnapshot(
                    pilot_id=pilot.id,
                    company_id=company.id,
                    industry="ai",
                    rule_version=RULE_VERSION,
                    total_score=Decimal(score),
                    component_scores={
                        "ai_core": 25 if name == "乙公司" else 20,
                        "market_validation": 20,
                        "growth_momentum": 15,
                        "industry_influence": 10,
                        "reliability": 10,
                    },
                    raw_component_scores={},
                    stage_percentiles={},
                    evidence_coverage={},
                    company_stage="growth",
                    missing_fields=[] if eligible else ["ai_core"],
                    eligibility_reasons=[] if eligible else ["missing_ai"],
                    is_eligible=eligible,
                    calculated_at=now,
                )
            )
        session.commit()
        app = create_app()

        def override() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_session] = override
        with TestClient(app) as client:
            yield client


def test_ranked_list_uses_component_tiebreak_and_continuous_ranks(
    ranking_client: TestClient,
) -> None:
    response = ranking_client.get("/api/v1/rankings/ai", params={"page_size": 100})

    assert response.status_code == 200
    body = response.json()
    assert body["ranked_total"] == 2
    assert body["observation_total"] == 1
    assert [(item["company_name"], item["rank"]) for item in body["items"]] == [
        ("乙公司", 1),
        ("甲公司", 2),
    ]


def test_observation_list_has_no_rank_and_no_internal_fields(ranking_client: TestClient) -> None:
    response = ranking_client.get("/api/v1/rankings/ai", params={"status": "observation"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["rank"] is None
    serialized = response.text.lower()
    for forbidden in ("source_row", "identity_hash", "response_sha256", "tianyan"):
        assert forbidden not in serialized


def test_ranking_not_published_is_stable() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        response = client.get("/api/v1/rankings/ai")
    session.close()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ranking_not_published"
