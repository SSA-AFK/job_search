from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.ingestion.coverage.repository import CoverageRepository
from app.models import Base, Company


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def repository(session: Session) -> CoverageRepository:
    return CoverageRepository(session)


@pytest.fixture
def company(session: Session) -> Company:
    result = Company(canonical_name="Example", normalized_name="example")
    session.add(result)
    session.flush()
    return result


@pytest.fixture
def companies(session: Session, company: Company) -> tuple[Company, Company]:
    other = Company(canonical_name="Other", normalized_name="other")
    session.add(other)
    session.flush()
    return company, other
