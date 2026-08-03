from collections.abc import Iterator

import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cache.redis import RedisCompanyCache
from app.companies.repository import CompanyRepository
from app.companies.schemas import CompanyQuery
from app.companies.service import CompanyService
from app.models import Base, Company


class OfflineRedis:
    def get(self, _key: str) -> str | None:
        raise RedisError("offline")

    def setex(self, _key: str, _seconds: int, _value: str) -> None:
        raise RedisError("offline")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def test_redis_failure_falls_back_to_repository_and_logs_warning_metric(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    session.add(Company(canonical_name="Acme", normalized_name="acme"))
    session.commit()
    service = CompanyService(CompanyRepository(session), cache=RedisCompanyCache(OfflineRedis()))

    result = service.search(CompanyQuery(q="Acme"))

    assert result.total == 1
    assert any(record.metric == "company_cache_redis_error" for record in caplog.records)
