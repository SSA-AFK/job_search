from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.ingestion.coverage.contracts import (
    CoverageReport,
    RecordJobSnapshot,
    SnapshotRecordResult,
)
from app.models.enums import JobEntryStatus, JobSnapshotStatus

EARLIER = datetime(2026, 8, 5, 10, tzinfo=UTC)
LATER = EARLIER + timedelta(minutes=1)


def snapshot_command(**overrides: object) -> RecordJobSnapshot:
    values: dict[str, object] = {
        "entry_id": UUID("00000000-0000-0000-0000-000000000001"),
        "crawl_run_id": UUID("00000000-0000-0000-0000-000000000002"),
        "status": JobSnapshotStatus.SUCCEEDED,
        "pagination_complete": True,
        "empty_confirmed": False,
        "reported_total": 1,
        "pages_fetched": 1,
        "content_fingerprint": "a" * 64,
        "error_code": None,
        "started_at": EARLIER,
        "completed_at": LATER,
        "seen_source_ids": frozenset(),
    }
    values.update(overrides)
    return RecordJobSnapshot(**values)


def test_job_coverage_statuses_match_persisted_values() -> None:
    assert [status.value for status in JobEntryStatus] == [
        "unknown",
        "active",
        "stale",
        "disabled",
    ]
    assert [status.value for status in JobSnapshotStatus] == [
        "succeeded",
        "partial",
        "failed",
    ]


def test_success_requires_complete_pagination_and_no_error() -> None:
    with pytest.raises(ValidationError, match="successful snapshot must be complete"):
        snapshot_command(pagination_complete=False)
    with pytest.raises(ValidationError, match="successful snapshot cannot have an error_code"):
        snapshot_command(error_code="page_timeout")


def test_partial_requires_error_and_cannot_confirm_empty() -> None:
    with pytest.raises(ValidationError, match="partial snapshot requires error_code"):
        snapshot_command(
            status=JobSnapshotStatus.PARTIAL,
            pagination_complete=False,
            error_code=None,
        )
    with pytest.raises(
        ValidationError, match="empty confirmation requires successful complete snapshot"
    ):
        snapshot_command(
            status=JobSnapshotStatus.PARTIAL,
            pagination_complete=False,
            error_code="page_timeout",
            empty_confirmed=True,
        )


def test_confirmed_empty_has_no_seen_sources_and_zero_reported_total() -> None:
    with pytest.raises(ValidationError, match="confirmed empty snapshot cannot contain sources"):
        snapshot_command(empty_confirmed=True, reported_total=0, seen_source_ids={uuid4()})
    with pytest.raises(ValidationError, match="confirmed empty reported_total must be zero"):
        snapshot_command(empty_confirmed=True, reported_total=1)


def test_failed_requires_error_without_progress_or_sources() -> None:
    failed = {
        "status": JobSnapshotStatus.FAILED,
        "pagination_complete": False,
        "error_code": "request_failed",
        "reported_total": None,
        "pages_fetched": 0,
    }
    with pytest.raises(ValidationError, match="failed snapshot requires error_code"):
        snapshot_command(**(failed | {"error_code": None}))
    with pytest.raises(ValidationError, match="failed snapshot cannot be complete"):
        snapshot_command(**(failed | {"pagination_complete": True}))
    with pytest.raises(ValidationError, match="failed snapshot cannot contain sources"):
        snapshot_command(**(failed | {"seen_source_ids": {uuid4()}}))
    with pytest.raises(ValidationError, match="failed snapshot pages_fetched must be zero"):
        snapshot_command(**(failed | {"pages_fetched": 1}))


def test_snapshot_bounds_and_time_order() -> None:
    with pytest.raises(ValidationError, match="completed_at must not precede started_at"):
        snapshot_command(started_at=LATER, completed_at=EARLIER)
    with pytest.raises(ValidationError, match="content_fingerprint"):
        snapshot_command(content_fingerprint="not-sha256")
    with pytest.raises(ValidationError, match="greater than or equal"):
        snapshot_command(reported_total=-1)
    with pytest.raises(ValidationError, match="less than or equal"):
        snapshot_command(reported_total=2_147_483_648)
    with pytest.raises(ValidationError, match="greater than or equal"):
        snapshot_command(pages_fetched=-1)
    with pytest.raises(ValidationError, match="less than or equal"):
        snapshot_command(pages_fetched=32_768)
    with pytest.raises(ValidationError, match="at most 20000"):
        snapshot_command(seen_source_ids={uuid4() for _ in range(20_001)})


def test_command_hash_is_stable_for_set_order_and_utc_instants() -> None:
    source_ids = [uuid4(), uuid4()]
    first = snapshot_command(seen_source_ids=set(source_ids))
    second = snapshot_command(
        seen_source_ids={source_ids[1], source_ids[0]},
        started_at=EARLIER.astimezone(UTC),
        completed_at=LATER.astimezone(timezone(-timedelta(hours=8))),
    )

    assert first.command_hash() == second.command_hash()
    assert len(first.command_hash()) == 64
    assert first.command_hash() == first.command_hash().lower()


def test_contract_results_are_immutable_and_report_rates_are_quantized() -> None:
    command = snapshot_command()
    result = SnapshotRecordResult(
        snapshot_id=uuid4(),
        created=True,
        sources_reactivated=0,
        sources_missing_incremented=0,
        sources_deactivated=0,
        jobs_recomputed=0,
    )
    report = CoverageReport(
        target_companies=5,
        active_entry_companies=3,
        recently_enumerated_companies=2,
        complete_list_companies=2,
        confirmed_empty_companies=1,
        entry_coverage_rate=Decimal("0.6"),
        enumeration_rate=Decimal("0.66666"),
        completeness_rate=Decimal(1),
        refresh_slo_rate=Decimal("0.4"),
    )

    with pytest.raises(ValidationError):
        command.pages_fetched = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.created = False  # type: ignore[misc]
    assert report.entry_coverage_rate == Decimal("0.6000")
    assert report.enumeration_rate == Decimal("0.6667")
    assert report.completeness_rate == Decimal("1.0000")
    assert report.refresh_slo_rate == Decimal("0.4000")


@pytest.mark.parametrize(
    ("rate_field", "report_values"),
    [
        (
            "entry_coverage_rate",
            {
                "target_companies": 0,
                "active_entry_companies": 1,
                "recently_enumerated_companies": 1,
            },
        ),
        (
            "enumeration_rate",
            {
                "target_companies": 1,
                "active_entry_companies": 0,
                "recently_enumerated_companies": 1,
            },
        ),
        (
            "completeness_rate",
            {
                "target_companies": 1,
                "active_entry_companies": 1,
                "recently_enumerated_companies": 0,
            },
        ),
        (
            "refresh_slo_rate",
            {
                "target_companies": 0,
                "active_entry_companies": 1,
                "recently_enumerated_companies": 1,
            },
        ),
    ],
)
def test_coverage_report_rejects_defined_rate_with_zero_denominator(
    rate_field: str, report_values: dict[str, int]
) -> None:
    values = {
        "target_companies": report_values["target_companies"],
        "active_entry_companies": report_values["active_entry_companies"],
        "recently_enumerated_companies": report_values["recently_enumerated_companies"],
        "complete_list_companies": 0,
        "confirmed_empty_companies": 0,
        "entry_coverage_rate": (
            Decimal("0.1") if report_values["target_companies"] else None
        ),
        "enumeration_rate": (
            Decimal("0.2") if report_values["active_entry_companies"] else None
        ),
        "completeness_rate": (
            Decimal("0.3") if report_values["recently_enumerated_companies"] else None
        ),
        "refresh_slo_rate": (
            Decimal("0.4") if report_values["target_companies"] else None
        ),
    }
    values[rate_field] = Decimal(0)

    with pytest.raises(ValidationError, match=f"{rate_field} requires a nonzero denominator"):
        CoverageReport(**values)


@pytest.mark.parametrize(
    "rate_field",
    [
        "entry_coverage_rate",
        "enumeration_rate",
        "completeness_rate",
        "refresh_slo_rate",
    ],
)
def test_coverage_report_requires_defined_rate_with_nonzero_denominator(
    rate_field: str,
) -> None:
    values: dict[str, object] = {
        "target_companies": 1,
        "active_entry_companies": 1,
        "recently_enumerated_companies": 1,
        "complete_list_companies": 0,
        "confirmed_empty_companies": 0,
        "entry_coverage_rate": Decimal("0.1"),
        "enumeration_rate": Decimal("0.2"),
        "completeness_rate": Decimal("0.3"),
        "refresh_slo_rate": Decimal("0.4"),
    }
    values[rate_field] = None

    with pytest.raises(ValidationError, match=f"{rate_field} requires a defined rate"):
        CoverageReport(**values)
