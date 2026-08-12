import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.job_enumeration.contracts import JobEnumerationStatus
from app.job_enumeration.jobhunt import JobHuntCli, JobHuntError


class FakeJobHunt(JobHuntCli):
    def __init__(self, outputs: dict[tuple[str, ...], str]) -> None:
        super().__init__(
            executable=Path("C:/tools/job.exe"),
            expected_version="0.2.4",
            now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        )
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    async def _run(self, arguments: tuple[str, ...]) -> str:
        self.calls.append(arguments)
        return self.outputs[arguments]


@pytest.mark.anyio
async def test_enumerates_declared_nature_to_standard_candidates() -> None:
    cli = FakeJobHunt(
        {
            ("--version",): "0.2.4\n",
            ("acme", "all", "--nature", "social", "--max", "2000", "--format", "json"): json.dumps(
                {
                    "jobs": [
                        {
                            "id": "job-1",
                            "name": "AI Engineer",
                            "url": "https://jobs.acme.example/1",
                            "nature_code": "social",
                            "location_names": ["上海"],
                        }
                    ],
                    "pagination_complete": True,
                }
            ),
        }
    )

    result = await cli.enumerate(site="acme", natures=("social",))

    assert result.status is JobEnumerationStatus.SOURCE_SUCCEEDED
    assert result.pagination_complete is True
    assert result.jobs[0].source_provider == "jobhunt:acme"
    assert result.jobs[0].source_raw_id == "job-1"


@pytest.mark.anyio
async def test_version_mismatch_stops_before_query() -> None:
    cli = FakeJobHunt({("--version",): "0.3.0\n"})

    with pytest.raises(JobHuntError, match="jobhunt_version_mismatch"):
        await cli.enumerate(site="acme", natures=("social",))

    assert cli.calls == [("--version",)]


@pytest.mark.anyio
async def test_plain_list_below_requested_max_is_complete() -> None:
    command = ("acme", "all", "--nature", "social", "--max", "2000", "--format", "json")
    cli = FakeJobHunt(
        {
            ("--version",): "0.2.4",
            command: json.dumps(
                [{"id": "1", "name": "Engineer", "url": "https://acme.example/jobs/1"}]
            ),
        }
    )

    result = await cli.enumerate(site="acme", natures=("social",))

    assert result.status is JobEnumerationStatus.SOURCE_SUCCEEDED
    assert result.pagination_complete is True


@pytest.mark.anyio
async def test_sites_accepts_current_cli_id_field() -> None:
    cli = FakeJobHunt(
        {
            ("sites", "--format", "json"): json.dumps(
                [{"id": "meituan", "supported_natures": ["social", "campus"]}]
            )
        }
    )

    assert await cli.sites() == {"meituan": frozenset({"social", "campus"})}


@pytest.mark.anyio
async def test_later_nature_failure_preserves_completed_jobs() -> None:
    social = ("acme", "all", "--nature", "social", "--max", "2000", "--format", "json")

    class PartialCli(FakeJobHunt):
        async def _run(self, arguments: tuple[str, ...]) -> str:
            if arguments[2:4] == ("--nature", "campus"):
                raise JobHuntError("jobhunt_source_unavailable")
            return await super()._run(arguments)

    cli = PartialCli(
        {
            ("--version",): "0.2.4",
            social: json.dumps(
                [{"id": "1", "name": "Engineer", "url": "https://acme.example/jobs/1"}]
            ),
        }
    )

    result = await cli.enumerate(site="acme", natures=("campus", "social"))

    assert result.status is JobEnumerationStatus.SOURCE_PARTIAL
    assert len(result.jobs) == 1
    assert result.error_code == "jobhunt_source_unavailable"
