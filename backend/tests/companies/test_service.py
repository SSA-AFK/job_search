from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.companies.repository import CompanyRepository
from app.companies.schemas import CompanyQuery, CompanySort
from app.companies.service import CompanyService
from app.core.normalization import normalize_name
from app.models import Base, Company, CompanyAlias


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def test_relevance_orders_exact_name_alias_prefix_then_contains(session: Session) -> None:
    companies = [
        Company(canonical_name="DeepSeek", normalized_name=normalize_name("DeepSeek")),
        Company(canonical_name="Alias Holder", normalized_name=normalize_name("Alias Holder")),
        Company(
            canonical_name="DeepSeek Systems",
            normalized_name=normalize_name("DeepSeek Systems"),
        ),
        Company(
            canonical_name="The DeepSeek Lab",
            normalized_name=normalize_name("The DeepSeek Lab"),
        ),
        Company(
            canonical_name="Alias Prefix Holder",
            normalized_name=normalize_name("Alias Prefix Holder"),
        ),
        Company(
            canonical_name="Alias Contains Holder",
            normalized_name=normalize_name("Alias Contains Holder"),
        ),
    ]
    session.add_all(companies)
    session.flush()
    session.add_all(
        [
            CompanyAlias(
                company_id=companies[1].id,
                alias="DeepSeek",
                normalized_alias=normalize_name("DeepSeek"),
            ),
            CompanyAlias(
                company_id=companies[4].id,
                alias="DeepSeek Research",
                normalized_alias=normalize_name("DeepSeek Research"),
            ),
            CompanyAlias(
                company_id=companies[5].id,
                alias="The DeepSeek Group",
                normalized_alias=normalize_name("The DeepSeek Group"),
            ),
        ]
    )
    session.commit()

    page = CompanyService(CompanyRepository(session)).search(
        CompanyQuery(q="  DEEPSEEK  ", sort=CompanySort.RELEVANCE, page_size=10)
    )

    assert [item.canonical_name for item in page.items] == [
        "DeepSeek",
        "Alias Holder",
        "DeepSeek Systems",
        "Alias Prefix Holder",
        "The DeepSeek Lab",
        "Alias Contains Holder",
    ]
    assert page.total == 6


def test_search_uses_updated_at_by_default_without_a_query(session: Session) -> None:
    session.add_all(
        [
            Company(
                canonical_name="Older",
                normalized_name="older",
                updated_at=datetime(2026, 7, 1, tzinfo=UTC),
            ),
            Company(
                canonical_name="Newer",
                normalized_name="newer",
                updated_at=datetime(2026, 7, 2, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    page = CompanyService(CompanyRepository(session)).search(CompanyQuery())

    assert [item.canonical_name for item in page.items] == ["Newer", "Older"]
    assert page.page == 1
    assert page.page_size == 20
    assert page.total == 2
