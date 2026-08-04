from app.core.config import Settings


def test_collection_is_disabled_without_environment_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("COLLECTION_ENABLED", raising=False)

    configured = Settings(_env_file=None)

    assert configured.collection_enabled is False
