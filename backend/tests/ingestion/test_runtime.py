import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ingestion.runtime import build_ingestion_orchestrator
from app.models import Base


def test_runtime_rejects_reused_session() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(ValueError, match="distinct sessions"):
        build_ingestion_orchestrator(
            run_state_session=session,
            dedup_read_session=session,
            persistence_write_session=session,
            providers=(),
            extractor=None,  # type: ignore[arg-type]
            semantic_judge=None,  # type: ignore[arg-type]
        )
