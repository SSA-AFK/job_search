import pytest
from pydantic import ValidationError

from app.ingestion.extraction.schemas import CompanyCandidate, JobCandidate


def test_rejects_unknown_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        CompanyCandidate.model_validate(
            {"name": "示例", "evidence_ids": ["not-provided"], "confidence": 0.9},
            context={"allowed_evidence_ids": {"doc-1"}},
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x" * 201, "evidence_ids": ["doc-1"], "confidence": 0.9},
        {"name": "示例", "evidence_ids": ["doc-1"], "confidence": 1.1},
        {
            "name": "示例",
            "website": "not-a-url",
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        },
        {
            "name": "示例",
            "description": "<p>HTML is not evidence</p>",
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        },
    ],
)
def test_company_candidate_rejects_invalid_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CompanyCandidate.model_validate(payload, context={"allowed_evidence_ids": {"doc-1"}})


def test_job_candidate_rejects_unknown_employment_type() -> None:
    with pytest.raises(ValidationError):
        JobCandidate.model_validate(
            {
                "title": "工程师",
                "employment_type": "contractor",
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
            },
            context={"allowed_evidence_ids": {"doc-1"}},
        )


def test_job_candidate_preserves_optional_source_and_salary_fields() -> None:
    candidate = JobCandidate.model_validate(
        {
            "title": "Software Engineer",
            "provider": "zhihu",
            "source_raw_id": "42",
            "salary": "30k-50k\u00b714\u85aa",
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        }
    )

    assert candidate.provider == "zhihu"
    assert candidate.source_raw_id == "42"
    assert candidate.salary == "30k-50k\u00b714\u85aa"


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "x" * 51),
        ("source_raw_id", "x" * 256),
        ("salary", "x" * 101),
    ],
)
def test_job_candidate_rejects_oversized_optional_source_and_salary_fields(
    field: str, value: str
) -> None:
    payload: dict[str, object] = {
        "title": "Software Engineer",
        "evidence_ids": ["doc-1"],
        "confidence": 0.9,
        field: value,
    }

    with pytest.raises(ValidationError):
        JobCandidate.model_validate(payload)
