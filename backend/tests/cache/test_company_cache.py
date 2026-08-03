from collections.abc import Iterator
from datetime import UTC, datetime
from fnmatch import fnmatch
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cache.keys import detail_key, jobs_key, list_key
from app.cache.redis import RedisCompanyCache, configured_company_cache
from app.companies.repository import CompanyRepository
from app.companies.schemas import CompanyQuery, JobQuery
from app.companies.service import CompanyService
from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.normalization.company import normalize_company
from app.ingestion.persistence.contracts import (
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
)
from app.ingestion.persistence.service import PersistenceError, PersistenceService
from app.models import Base, Company, JobPosting


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.increments = 0

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, seconds: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = seconds

    def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)
        return len(keys)

    def incr(self, key: str) -> int:
        self.increments += 1
        next_value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(next_value)
        return next_value

    def scan_iter(self, *, match: str) -> Iterator[str]:
        return iter([key for key in self.values if fnmatch(key, match)])

    def eval(self, _script: str, _numkeys: int, *arguments: str) -> int:
        version_key, cache_key, expected_version, ttl, value = arguments
        if self.values.get(version_key, "0") != expected_version:
            return 0
        self.setex(cache_key, int(ttl), value)
        return 1


class VersionRaceRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.bump_after_list_read = True

    def get(self, key: str) -> str | None:
        value = super().get(key)
        if self.bump_after_list_read and ":companies:list:v" in key:
            self.bump_after_list_read = False
            self.values["company-search:companies:list:version"] = "1"
        return value


class DeleteFailureRedis(FakeRedis):
    def delete(self, *keys: str) -> int:
        raise RedisError("delete failed")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def test_search_returns_cached_serialized_response_without_reading_deleted_company(
    session: Session,
) -> None:
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.commit()
    cache = RedisCompanyCache(FakeRedis())
    service = CompanyService(CompanyRepository(session), cache=cache)

    first = service.search(CompanyQuery(q="Acme"))
    session.delete(company)
    session.commit()

    second = service.search(CompanyQuery(q="Acme"))

    assert first == second
    assert second.total == 1


def test_detail_returns_cached_serialized_response_without_reading_deleted_company(
    session: Session,
) -> None:
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.commit()
    service = CompanyService(CompanyRepository(session), cache=RedisCompanyCache(FakeRedis()))

    first = service.get_detail(company.id)
    session.delete(company)
    session.commit()

    second = service.get_detail(company.id)

    assert second == first
    assert second.canonical_name == "Acme"


def test_jobs_returns_cached_serialized_response_without_reading_deleted_job(session: Session) -> None:
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.flush()
    job = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build systems",
    )
    session.add(job)
    session.commit()
    service = CompanyService(CompanyRepository(session), cache=RedisCompanyCache(FakeRedis()))

    first = service.list_jobs(company.id, JobQuery())
    session.delete(job)
    session.commit()

    second = service.list_jobs(company.id, JobQuery())

    assert second == first
    assert second.items[0].title == "Engineer"


def test_cache_uses_required_response_ttls() -> None:
    client = FakeRedis()
    cache = RedisCompanyCache(client)
    company_id = uuid4()

    cache.set_list({"q": "Acme"}, "list", version=0)
    cache.set_detail(company_id, "detail")
    cache.set_jobs(company_id, {"city": "Shanghai"}, "jobs")

    assert client.ttls[list_key({"q": "Acme"}, version=0)] == 60
    assert client.ttls[detail_key(company_id)] == 300
    assert client.ttls[jobs_key(company_id, {"city": "Shanghai"})] == 300


def test_configured_cache_uses_bounded_redis_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    construction: dict[str, object] = {}

    def from_url(url: str, **kwargs: object) -> FakeRedis:
        construction["url"] = url
        construction.update(kwargs)
        return FakeRedis()

    monkeypatch.setattr("app.cache.redis.Redis.from_url", from_url)

    assert configured_company_cache("redis://cache.example/0") is not None
    assert construction["socket_connect_timeout"] == 0.2
    assert construction["socket_timeout"] == 0.2


def test_interleaved_invalidation_does_not_cache_pre_commit_result_at_new_version(
    session: Session,
) -> None:
    session.add(Company(canonical_name="Acme", normalized_name="acme"))
    session.commit()
    client = VersionRaceRedis()
    service = CompanyService(CompanyRepository(session), cache=RedisCompanyCache(client))
    params = CompanyQuery(q="Acme").model_dump(mode="json")

    result = service.search(CompanyQuery(q="Acme"))

    assert result.total == 1
    assert list_key(params, version=1) not in client.values


def test_invalidation_advances_list_version_before_detail_or_jobs_delete_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    company_id = uuid4()
    params = {"q": "Acme"}
    client = DeleteFailureRedis()
    client.values[list_key(params, version=0)] = "stale"
    cache = RedisCompanyCache(client)

    cache.invalidate_company(company_id)

    entry = cache.get_list(params)

    assert entry.value is None
    assert entry.version == 1
    assert any(record.metric == "company_cache_redis_error" for record in caplog.records)


def test_persistence_invalidates_cache_only_after_successful_commit(session: Session) -> None:
    client = FakeRedis()
    cache = RedisCompanyCache(client)
    persistence = PersistenceService(session, cache=cache)
    batch = _batch()

    result = persistence.persist(batch, run_id=uuid4())
    cache.set_detail(result.company_id, "detail")
    cache.set_jobs(result.company_id, JobQuery().model_dump(mode="json"), "jobs")
    increments_after_first_commit = client.increments

    persistence.persist(_batch(company_id=result.company_id), run_id=uuid4())

    assert detail_key(result.company_id) not in client.values
    assert jobs_key(result.company_id, JobQuery().model_dump(mode="json")) not in client.values
    assert client.increments == increments_after_first_commit + 1

    with pytest.raises(PersistenceError, match="unknown company_id"):
        persistence.persist(_batch(company_id=uuid4()), run_id=uuid4())

    assert client.increments == increments_after_first_commit + 1


def _batch(*, company_id: UUID | None = None) -> NormalizedBatch:
    document = RawDocument(
        provider="official",
        external_id="acme-1",
        url="https://acme.example",
        title="Acme",
        text="Acme source",
        published_at=None,
    )
    return NormalizedBatch(
        documents=(
            NormalizedDocument(
                evidence_id="acme-1",
                document=document,
                fetched_at=datetime(2026, 8, 3, tzinfo=UTC),
            ),
        ),
        company=NormalizedCompanyRecord(
            candidate=normalize_company(
                CompanyCandidate(name="Acme", evidence_ids=("acme-1",), confidence=1)
            ),
            company_id=company_id,
        ),
        collected_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
