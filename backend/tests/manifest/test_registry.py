import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.manifest.contracts import SourceRole
from app.manifest.registry import SourceRegistryError, load_source_registry

GATE1_REGISTRY_PATH = Path(__file__).parents[2] / "data" / "gate1" / "source_registry.json"


def registry_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "official_list",
        "name": "Official public list",
        "base_url": "https://example.com/public-list",
        "source_class": "government",
        "authorization_basis": "Public list approved for Gate 1 rehearsal.",
        "robots_policy": "required",
        "roles": ["candidate_pool"],
        "requests_per_second": "1.0",
        "rehearsal_request_budget": 100,
        "enabled": True,
    }
    entry.update(overrides)
    return entry


def write_registry(tmp_path: Path, *, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "source_registry.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def test_registry_rejects_unsafe_rate_and_duplicate_source_id(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path,
        entries=[registry_entry(id="official_list", requests_per_second=1.1), registry_entry(id="official_list")],
    )
    with pytest.raises(ValueError, match="source registry is invalid"):
        load_source_registry(path)


@pytest.mark.parametrize(
    "entry",
    [
        registry_entry(base_url="https://secret@example.com/public-list"),
        registry_entry(enabled=False),
        registry_entry(rehearsal_request_budget=0),
    ],
)
def test_registry_rejects_unreviewed_or_invalid_entries(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    with pytest.raises(SourceRegistryError, match="source registry is invalid"):
        load_source_registry(write_registry(tmp_path, entries=[entry]))


def test_registry_rejects_credential_query_parameters_without_echoing_values(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path,
        entries=[registry_entry(base_url="https://example.com/public?Api_Key=not-a-secret")],
    )

    with pytest.raises(SourceRegistryError) as error:
        load_source_registry(path)

    assert str(error.value) == "source registry is invalid"
    assert "not-a-secret" not in str(error.value)


def test_registry_allows_ordinary_public_query_parameters(tmp_path: Path) -> None:
    registry = load_source_registry(
        write_registry(
            tmp_path,
            entries=[registry_entry(base_url="https://example.com/public?locale=en&page=2")],
        )
    )

    assert str(registry.require("official_list").base_url).endswith("locale=en&page=2")


def test_registry_allows_independently_budgeted_entries_on_one_host(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path,
        entries=[
            registry_entry(id="official_list", rehearsal_request_budget=10),
            registry_entry(id="official_detail", rehearsal_request_budget=20),
        ],
    )

    registry = load_source_registry(path)

    assert registry.require("official_list").rehearsal_request_budget == 10
    assert registry.require("official_detail").rehearsal_request_budget == 20


def test_registry_contains_only_discovery_fallback_zhihu_initially() -> None:
    registry = load_source_registry(GATE1_REGISTRY_PATH)
    zhihu = registry.require("zhihu_global_search")
    assert str(zhihu.base_url) == "https://developer.zhihu.com/api/v1/content/global_search"
    assert zhihu.roles == frozenset({SourceRole.ENTRY_DISCOVERY_FALLBACK})
    assert zhihu.requests_per_second == Decimal("1.0")
    assert zhihu.rehearsal_request_budget == 200
