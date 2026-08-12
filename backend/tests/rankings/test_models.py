from sqlalchemy import create_engine, inspect

from app.models import Base


def test_ranking_signal_tables_do_not_have_sensitive_or_raw_payload_columns() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("ranking_collection_runs", "company_ranking_signals")
    }

    forbidden = {
        "phone",
        "email",
        "address",
        "legal_person",
        "executive",
        "raw_payload",
        "vendor_score",
    }
    assert not forbidden & columns["ranking_collection_runs"]
    assert not forbidden & columns["company_ranking_signals"]
    assert "response_sha256" in columns["ranking_collection_runs"]
    assert "response_sha256" in columns["company_ranking_signals"]
