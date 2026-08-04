import pytest

from app.ingestion.extraction.schemas import JobCandidate
from app.ingestion.normalization.job import normalize_job


@pytest.mark.parametrize("employment_type", ["part_time", "temporary"])
def test_known_employment_types_remain_first_class_after_normalization(
    employment_type: str,
) -> None:
    candidate = JobCandidate(
        company_name="Example",
        title="Software Engineer",
        employment_type=employment_type,
        location="Shanghai",
        provider="official",
        source_raw_id=f"{employment_type}-1",
        evidence_ids=("doc-1",),
        confidence=0.9,
    )

    normalized = normalize_job(candidate)

    assert normalized.job_type.value == employment_type
