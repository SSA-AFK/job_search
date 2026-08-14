import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models import (
    Base,
    Company,
    CompanyAlias,
    CompanyRankingSnapshot,
    CompanyRankingSignal,
    CompanySource,
    JobPosting,
    JobSource,
    RankingPilot,
    RankingPilotMember,
    SourceDocument,
)
from app.rankings.service import RULE_VERSION
from app.seed.importer import import_seed
from app.seed.schema import SeedPayload


@pytest.fixture
def seeded_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        seed_path = Path(__file__).parents[2] / "data" / "companies.seed.json"
        seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
        deepseek_seed = seed_data["companies"][0]
        deepseek_seed["canonical_name"] = "DeepSeek"
        deepseek_seed["aliases"] = ["深度求索"]
        sources = deepseek_seed["jobs"][0]["sources"]
        sources[0].update(
            {
                "provider": "jobhunt:company_site",
                "source_raw_id": "company-1",
                "apply_url": "https://example.com/jobs/1",
            }
        )
        sources[1].update(
            {
                "provider": "jobhunt:zhihu",
                "source_raw_id": "zhihu-1",
                "apply_url": "https://jobs.example.com/1",
            }
        )
        import_seed(session, SeedPayload.model_validate(seed_data))

        deepseek = session.scalar(select(Company).where(Company.canonical_name == "DeepSeek"))
        assert deepseek is not None
        rank_companies = [
            Company(canonical_name="Alias Holder", normalized_name="aliasholder"),
            Company(canonical_name="DeepSeek Systems", normalized_name="deepseeksystems"),
            Company(canonical_name="The DeepSeek Lab", normalized_name="thedeepseeklab"),
        ]
        session.add_all(rank_companies)
        session.flush()
        session.add(
            CompanyAlias(
                company_id=rank_companies[0].id,
                alias="DeepSeek",
                normalized_alias="deepseek",
            )
        )

        ordered_names = [
            "DeepSeek",
            "月之暗面",
            "智谱AI",
            "MiniMax",
            "字节跳动",
            "Alias Holder",
            "DeepSeek Systems",
            "The DeepSeek Lab",
        ]
        for day, name in enumerate(ordered_names, start=1):
            company = session.scalar(select(Company).where(Company.canonical_name == name))
            assert company is not None
            company.updated_at = datetime(2026, 7, day, tzinfo=UTC)

        source = SourceDocument(
            provider="official_registry",
            external_id="deepseek-registry",
            url="https://registry.example.com/deepseek",
            title="DeepSeek registry record",
            text_excerpt="Verified company record",
            content_hash="a" * 64,
            authority_level=5,
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 31, tzinfo=UTC),
        )
        session.add(source)
        session.flush()
        session.add(
            CompanySource(
                company_id=deepseek.id,
                source_document_id=source.id,
                covered_fields=["canonical_name", "website"],
                confidence=Decimal("0.975"),
            )
        )
        pilot = RankingPilot(
            industry="ai",
            input_sha256="b" * 64,
            selection_seed="api-test",
            sample_size=len(ordered_names),
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(pilot)
        session.flush()
        for rank, name in enumerate(ordered_names, start=1):
            company = session.scalar(select(Company).where(Company.canonical_name == name))
            assert company is not None
            session.add(
                RankingPilotMember(
                    pilot_id=pilot.id,
                    company_id=company.id,
                    source_row=rank + 2,
                    source_identity_hash=str(rank).zfill(64),
                    stratum="api-test",
                    selection_reason="api-test",
                )
            )
            session.add(
                CompanyRankingSnapshot(
                    pilot_id=pilot.id,
                    company_id=company.id,
                    industry="ai",
                    rule_version=RULE_VERSION,
                    total_score=Decimal(101 - rank),
                    component_scores={
                        "ai_core": 25,
                        "market_validation": 20,
                        "growth_momentum": 15,
                        "industry_influence": 12,
                        "reliability": 8,
                    },
                    raw_component_scores={},
                    stage_percentiles={},
                    evidence_coverage={},
                    company_stage="growth",
                    missing_fields=[],
                    eligibility_reasons=[],
                    is_eligible=True,
                    calculated_at=datetime(2026, 8, 1, tzinfo=UTC),
                )
            )
        session.commit()
        yield session


@pytest.fixture
def client(seeded_session: Session) -> Iterator[TestClient]:
    app = create_app()

    def override_session() -> Iterator[Session]:
        yield seeded_session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def deepseek_id(seeded_session: Session) -> UUID:
    company_id = seeded_session.scalar(
        select(Company.id).where(Company.canonical_name == "DeepSeek")
    )
    assert company_id is not None
    return company_id


def test_alias_exact_match_precedes_contains_match(client: TestClient) -> None:
    response = client.get("/api/v1/companies", params={"q": "深度求索"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["canonical_name"] == "DeepSeek"


@pytest.mark.parametrize(
    ("parameter", "value", "expected_names"),
    [
        (
            "industry",
            "Artificial Intelligence",
            ["DeepSeek", "MiniMax", "智谱AI", "月之暗面"],
        ),
        ("sub_industry", "Multimodal Models", ["MiniMax"]),
        ("funding_stage", "series_c_plus", ["智谱AI"]),
        ("scale", "200_to_499", ["DeepSeek"]),
        ("city", "Shanghai", ["MiniMax"]),
    ],
)
def test_company_search_applies_each_exact_filter(
    client: TestClient,
    parameter: str,
    value: str,
    expected_names: list[str],
) -> None:
    response = client.get(
        "/api/v1/companies",
        params={parameter: value, "sort": "name", "page_size": 100},
    )

    assert response.status_code == 200
    assert [item["canonical_name"] for item in response.json()["items"]] == expected_names


@pytest.mark.parametrize(
    ("parameter", "unsupported_value"),
    [
        ("funding_stage", "private"),
        ("funding_stage", "series_c"),
        ("funding_stage", "ipo"),
        ("scale", "100-499"),
    ],
)
def test_company_search_rejects_unsupported_company_vocabulary(
    client: TestClient,
    parameter: str,
    unsupported_value: str,
) -> None:
    response = client.get("/api/v1/companies", params={parameter: unsupported_value})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_company_search_combines_filters(client: TestClient) -> None:
    response = client.get(
        "/api/v1/companies",
        params={
            "industry": "Artificial Intelligence",
            "sub_industry": "Foundation Models",
            "city": "Beijing",
        },
    )

    assert response.status_code == 200
    assert {item["canonical_name"] for item in response.json()["items"]} == {
        "智谱AI",
        "月之暗面",
    }


def test_relevance_sort_uses_all_match_tiers(client: TestClient) -> None:
    response = client.get(
        "/api/v1/companies",
        params={"q": "DeepSeek", "sort": "relevance", "page_size": 10},
    )

    assert response.status_code == 200
    assert [item["canonical_name"] for item in response.json()["items"]] == [
        "DeepSeek",
        "Alias Holder",
        "DeepSeek Systems",
        "The DeepSeek Lab",
    ]


def test_relevance_places_alias_prefix_and_contains_in_their_match_tiers(
    client: TestClient, seeded_session: Session
) -> None:
    alias_prefix = Company(canonical_name="Alias Prefix Holder", normalized_name="aliasprefix")
    alias_contains = Company(
        canonical_name="Alias Contains Holder", normalized_name="aliascontains"
    )
    seeded_session.add_all([alias_prefix, alias_contains])
    seeded_session.flush()
    seeded_session.add_all(
        [
            CompanyAlias(
                company_id=alias_prefix.id,
                alias="DeepSeek Research",
                normalized_alias="deepseekresearch",
            ),
            CompanyAlias(
                company_id=alias_contains.id,
                alias="The DeepSeek Group",
                normalized_alias="thedeepseekgroup",
            ),
        ]
    )
    seeded_session.commit()

    response = client.get(
        "/api/v1/companies",
        params={"q": "DeepSeek", "sort": "relevance", "page_size": 10},
    )

    assert response.status_code == 200
    assert [item["canonical_name"] for item in response.json()["items"]] == [
        "DeepSeek",
        "Alias Holder",
        "DeepSeek Systems",
        "The DeepSeek Lab",
    ]


def test_name_sort_ignores_relevance_rank(client: TestClient) -> None:
    response = client.get(
        "/api/v1/companies",
        params={"q": "DeepSeek", "sort": "name", "page_size": 10},
    )

    assert response.status_code == 200
    assert [item["canonical_name"] for item in response.json()["items"]] == [
        "Alias Holder",
        "DeepSeek",
        "DeepSeek Systems",
        "The DeepSeek Lab",
    ]


def test_updated_at_sort_is_descending(client: TestClient) -> None:
    response = client.get(
        "/api/v1/companies",
        params={"q": "DeepSeek", "sort": "updated_at", "page_size": 10},
    )

    assert response.status_code == 200
    assert [item["canonical_name"] for item in response.json()["items"]] == [
        "The DeepSeek Lab",
        "DeepSeek Systems",
        "Alias Holder",
        "DeepSeek",
    ]


def test_search_defaults_to_updated_at_without_query_and_paginates(client: TestClient) -> None:
    response = client.get("/api/v1/companies", params={"page": 2, "page_size": 3})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total"}
    assert body["page"] == 2
    assert body["page_size"] == 3
    assert body["total"] == 8
    assert [item["canonical_name"] for item in body["items"]] == [
        "MiniMax",
        "字节跳动",
        "Alias Holder",
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"sort": "unknown"},
    ],
)
def test_search_rejects_invalid_page_bounds_and_sort(
    client: TestClient, params: dict[str, object]
) -> None:
    response = client.get("/api/v1/companies", params=params)

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_company_detail_includes_aliases_filings_sources_and_job_count(
    client: TestClient, deepseek_id: UUID
) -> None:
    response = client.get(f"/api/v1/companies/{deepseek_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["updated_at"] == "2026-07-01T00:00:00Z"
    assert body["aliases"] == ["深度求索"]
    assert body["job_count"] == 2
    assert body["filings"] == [
        {
            "filing_type": "icp",
            "filing_number": "浙ICP备2023025841号",
            "filing_name": "DeepSeek official website",
            "filing_authority": "Ministry of Industry and Information Technology",
            "filing_date": "2023-08-01",
            "filing_status": "active",
            "verification_status": "pending_verification",
            "detail_url": "https://beian.miit.gov.cn/",
        }
    ]
    assert body["sources"] == [
        {
            "provider": "official_registry",
            "url": "https://registry.example.com/deepseek",
            "title": "DeepSeek registry record",
            "covered_fields": ["canonical_name", "website"],
            "field_verification": {},
            "confidence": "0.975",
            "published_at": "2026-06-01T00:00:00Z",
            "fetched_at": "2026-07-31T00:00:00Z",
        }
    ]


def test_company_detail_projects_verified_financing_signal(
    client: TestClient, seeded_session: Session, deepseek_id: UUID
) -> None:
    seeded_session.add_all(
        [
            CompanyRankingSignal(
                company_id=deepseek_id,
                category="growth",
                signal_key="financing",
                value={"round": "A+轮", "investors": ["示例资本"]},
                event_date=datetime(2026, 5, 1, tzinfo=UTC),
                source_fingerprint="c" * 64,
                confidence=Decimal("0.900"),
                verification_status="internal_verified",
                fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
            CompanyRankingSignal(
                company_id=deepseek_id,
                category="growth",
                signal_key="financing",
                value={"round": "出资设立", "investors": ["母公司"]},
                event_date=datetime(2026, 6, 1, tzinfo=UTC),
                source_fingerprint="d" * 64,
                confidence=Decimal("0.900"),
                verification_status="internal_verified",
                fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
        ]
    )
    seeded_session.commit()

    body = client.get(f"/api/v1/companies/{deepseek_id}").json()

    assert body["latest_funding_round"] == "A+轮"
    assert body["funding_events"][0] == {
        "round_label": "A+轮",
        "announced_at": "2026-05-01",
        "amount": None,
        "currency": None,
        "investors": ["示例资本"],
        "verification_status": "verified",
    }


def test_job_sources_keep_provider_url_pairing(
    client: TestClient, deepseek_id: UUID
) -> None:
    body = client.get(f"/api/v1/companies/{deepseek_id}/jobs").json()
    sources = body["items"][0]["sources"]
    assert sources == [
        {
            "provider": "jobhunt:company_site",
            "apply_url": "https://example.com/jobs/1",
            "verification_status": "pending_verification",
        },
        {
            "provider": "jobhunt:zhihu",
            "apply_url": "https://jobs.example.com/1",
            "verification_status": "pending_verification",
        },
    ]


def test_jobs_default_to_active_and_can_include_inactive(
    client: TestClient, deepseek_id: UUID
) -> None:
    active = client.get(f"/api/v1/companies/{deepseek_id}/jobs")
    all_jobs = client.get(
        f"/api/v1/companies/{deepseek_id}/jobs", params={"active_only": False}
    )

    assert active.status_code == 200
    assert active.json()["total"] == 1
    assert [item["title"] for item in active.json()["items"]] == [
        "Large Model Algorithm Engineer"
    ]
    assert all_jobs.status_code == 200
    assert all_jobs.json()["total"] == 1


@pytest.mark.parametrize(
    ("params", "expected_title"),
    [
        ({"job_type": "full_time"}, "Large Model Algorithm Engineer"),
        ({"city": "Hangzhou"}, "Large Model Algorithm Engineer"),
    ],
)
def test_jobs_apply_type_city_and_active_filters(
    client: TestClient,
    deepseek_id: UUID,
    params: dict[str, object],
    expected_title: str,
) -> None:
    response = client.get(f"/api/v1/companies/{deepseek_id}/jobs", params=params)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == expected_title


@pytest.mark.parametrize("job_type", ["part_time", "temporary"])
def test_jobs_serialize_and_filter_first_class_employment_types(
    job_type: str,
    client: TestClient,
    seeded_session: Session,
    deepseek_id: UUID,
) -> None:
    posting = JobPosting(
            company_id=deepseek_id,
            title=f"{job_type} engineer",
            normalized_title=f"{job_type}engineer",
            job_type=job_type,
            city="Shanghai",
            description="Scoped role",
            is_active=True,
        )
    seeded_session.add(posting)
    seeded_session.flush()
    seeded_session.add(
        JobSource(
            job_posting_id=posting.id,
            provider="jobhunt:test",
            source_raw_id=f"{job_type}-test",
            apply_url=f"https://jobs.example.com/{job_type}",
            is_active=True,
        )
    )
    seeded_session.commit()

    response = client.get(
        f"/api/v1/companies/{deepseek_id}/jobs",
        params={"job_type": job_type},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["job_type"] == job_type


def test_jobs_paginate(client: TestClient, deepseek_id: UUID) -> None:
    response = client.get(
        f"/api/v1/companies/{deepseek_id}/jobs",
        params={"active_only": False, "page": 2, "page_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 1
    assert response.json()["total"] == 1
    assert response.json()["items"] == []


def test_jobs_explicitly_order_unknown_posting_dates_last(
    client: TestClient,
    seeded_session: Session,
    deepseek_id: UUID,
) -> None:
    posting = JobPosting(
            company_id=deepseek_id,
            title="Undated Role",
            normalized_title="undatedrole",
            city="Hangzhou",
            description="Posting date is unavailable.",
            posted_at=None,
            is_active=True,
        )
    seeded_session.add(posting)
    seeded_session.flush()
    seeded_session.add(
        JobSource(
            job_posting_id=posting.id,
            provider="jobhunt:test",
            source_raw_id="undated-test",
            apply_url="https://jobs.example.com/undated",
            is_active=True,
        )
    )
    seeded_session.commit()
    statements: list[str] = []
    engine = seeded_session.get_bind()

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = client.get(
            f"/api/v1/companies/{deepseek_id}/jobs", params={"page_size": 10}
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == [
        "Large Model Algorithm Engineer",
        "Undated Role",
    ]
    assert any("posted_at DESC NULLS LAST" in statement for statement in statements)


def test_malformed_company_uuid_returns_stable_422(client: TestClient) -> None:
    response = client.get("/api/v1/companies/not-a-uuid")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
        }
    }


def test_unknown_api_route_returns_stable_404(client: TestClient) -> None:
    response = client.get("/api/v1/no-such-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Resource not found",
        }
    }


@pytest.mark.parametrize("suffix", ["", "/jobs"])
def test_absent_company_returns_stable_404(client: TestClient, suffix: str) -> None:
    response = client.get(f"/api/v1/companies/00000000-0000-0000-0000-000000000000{suffix}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "company_not_found",
            "message": "Company not found",
        }
    }
