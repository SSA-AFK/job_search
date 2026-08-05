import importlib
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_coverage_test_packages_have_distinct_module_identities() -> None:
    report_module = importlib.import_module("tests.coverage.test_service")
    lifecycle_module = importlib.import_module("tests.ingestion.coverage.test_service")

    assert report_module.__name__ == "tests.coverage.test_service"
    assert lifecycle_module.__name__ == "tests.ingestion.coverage.test_service"
    assert report_module is not lifecycle_module


@pytest.mark.parametrize(
    "paths",
    (
        ("tests/coverage", "tests/ingestion/coverage"),
        ("tests/ingestion/coverage", "tests/coverage"),
    ),
)
def test_mixed_coverage_collection_keeps_tests_under_their_own_paths(
    paths: tuple[str, str],
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "tests/coverage/test_service.py::"
        "test_build_reports_exact_company_level_coverage_rates"
    ) in result.stdout
    assert (
        "tests/ingestion/coverage/test_service.py::"
        "test_two_complete_absences_deactivate_source_and_posting"
    ) in result.stdout
