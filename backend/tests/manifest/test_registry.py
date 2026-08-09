import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

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


@pytest.mark.parametrize(
    ("base_url", "disallowed_value"),
    [
        ("https://example.com/public?locale=en&page=2", "locale=en"),
        ("https://example.com/public?credential=not-a-secret", "not-a-secret"),
        ("https://example.com/public#private-section", "private-section"),
    ],
)
def test_registry_rejects_query_strings_and_fragments_without_echoing_values(
    tmp_path: Path, base_url: str, disallowed_value: str
) -> None:
    path = write_registry(
        tmp_path,
        entries=[registry_entry(base_url=base_url)],
    )

    with pytest.raises(SourceRegistryError) as error:
        load_source_registry(path)

    assert str(error.value) == "source registry is invalid"
    assert disallowed_value not in str(error.value)


def test_registry_allows_path_only_https_urls(tmp_path: Path) -> None:
    registry = load_source_registry(
        write_registry(
            tmp_path,
            entries=[registry_entry(base_url="https://example.com/public-list")],
        )
    )

    assert str(registry.require("official_list").base_url) == "https://example.com/public-list"


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


def test_gate1_registry_has_reviewed_source_census_and_host_budgets() -> None:
    registry = load_source_registry(GATE1_REGISTRY_PATH)
    zhihu = registry.require("zhihu_global_search")

    role_counts = Counter(role.value for entry in registry.entries for role in entry.roles)
    class_counts = Counter(entry.source_class.value for entry in registry.entries)
    candidate_host_budgets: Counter[str] = Counter()
    for entry in registry.entries:
        if SourceRole.CANDIDATE_POOL not in entry.roles:
            continue
        assert entry.rehearsal_request_budget is not None
        host = urlsplit(str(entry.base_url)).hostname
        assert host is not None
        candidate_host_budgets[host] += entry.rehearsal_request_budget

    assert len(registry.entries) == 57
    assert role_counts == {
        SourceRole.CANDIDATE_POOL.value: 56,
        SourceRole.ENTRY_DISCOVERY_FALLBACK.value: 1,
    }
    assert class_counts == {
        "association": 37,
        "authorized_api": 1,
        "government": 19,
    }
    assert candidate_host_budgets == {
        "www.cagd.gov.cn": 32,
        "www.hunan.gov.cn": 2,
        "www.jssia.cn": 33,
        "www.miit.gov.cn": 4,
        "www.sae-china.org": 3,
        "www.zjsia.org.cn": 3,
    }
    assert str(zhihu.base_url) == "https://developer.zhihu.com/api/v1/content/global_search"
    assert zhihu.roles == frozenset({SourceRole.ENTRY_DISCOVERY_FALLBACK})
    assert zhihu.requests_per_second == Decimal("1.0")
    assert zhihu.rehearsal_request_budget == 200
