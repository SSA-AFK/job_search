# backend/tests/ingestion/jobs/test_contracts.py
import pytest
from pydantic import ValidationError

from app.ingestion.jobs.contracts import AtsJobCandidate, AtsListResult, AtsParseStatus


def test_ats_parse_status_values_are_stable() -> None:
    assert set(AtsParseStatus) == {"succeeded", "partial", "failed"}


def test_ats_job_candidate_rejects_empty_title_and_url() -> None:
    with pytest.raises(ValidationError):
        AtsJobCandidate(title="", url="https://jobs.feishu.cn/x", external_id="a")
    with pytest.raises(ValidationError):
        AtsJobCandidate(title="Engineer", url="not-a-url", external_id="a")


def test_ats_job_candidate_rejects_long_fields() -> None:
    base = {"title": "Engineer", "url": "https://jobs.feishu.cn/x"}
    with pytest.raises(ValidationError):
        AtsJobCandidate(**base, external_id="a" * 300)
    with pytest.raises(ValidationError):
        AtsJobCandidate(**base, city="x" * 201)
    with pytest.raises(ValidationError):
        AtsJobCandidate(**base, employment_type="y" * 51)


def test_ats_list_result_defaults_observed_count_to_candidates_length() -> None:
    result = AtsListResult(candidates=(), status=AtsParseStatus.FAILED)
    assert result.observed_count == 0
    assert result.reported_total is None
    assert result.error_code == "parse_failed"
