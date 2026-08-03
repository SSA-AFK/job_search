from collections.abc import Iterable, Iterator

import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cache.redis import RedisCompanyCache
from app.companies.repository import CompanyRepository
from app.companies.schemas import CompanyQuery, JobQuery
from app.companies.service import CompanyService
from app.models import Base, Company, JobPosting


class OfflineRedis:
    def get(self, _key: str) -> str | None:
        raise RedisError("offline")

    def setex(self, _key: str, _seconds: int, _value: str) -> None:
        raise RedisError("offline")

    def delete(self, *_keys: str) -> int:
        raise RedisError("offline")

    def incr(self, _key: str) -> int:
        raise RedisError("offline")

    def scan_iter(self, *, match: str) -> Iterable[str]:
        raise RedisError(f"offline: {match}")

    def eval(self, _script: str, _numkeys: int, *_keys_and_args: str) -> object:
        raise RedisError("offline")


class VersionFailureRedis(OfflineRedis):
    def __init__(self) -> None:
        self.read_keys: list[str] = []

    def get(self, key: str) -> str | None:
        self.read_keys.append(key)
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


def test_version_read_failure_is_a_full_list_cache_miss() -> None:
    client = VersionFailureRedis()
    cache = RedisCompanyCache(client)

    entry = cache.get_list({"q": "Acme"})

    assert entry.value is None
    assert entry.version is None
    assert client.read_keys == ["company-search:companies:list:version"]


def test_detail_and_jobs_redis_failures_fall_back_to_repository(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.flush()
    session.add(
        JobPosting(
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            city="Shanghai",
            description="Build systems",
        )
    )
    session.commit()
    service = CompanyService(CompanyRepository(session), cache=RedisCompanyCache(OfflineRedis()))

    detail = service.get_detail(company.id)
    jobs = service.list_jobs(company.id, JobQuery())

    assert detail.canonical_name == "Acme"
    assert [item.title for item in jobs.items] == ["Engineer"]
    assert any(record.metric == "company_cache_redis_error" for record in caplog.records)
