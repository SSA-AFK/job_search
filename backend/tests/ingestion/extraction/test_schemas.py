import pytest
from pydantic import ValidationError

from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    ExtractionBatch,
    FilingCandidate,
    FilingType,
    JobCandidate,
)


def test_job_source_evidence_must_be_listed_and_prompt_allowed() -> None:
    with pytest.raises(ValidationError, match="one of evidence_ids"):
        JobCandidate(title="Engineer", evidence_ids=("one",), source_evidence_id="two", confidence=1)
    with pytest.raises(ValidationError, match="supplied in the prompt"):
        JobCandidate.model_validate(
            {"title": "Engineer", "evidence_ids": ["one", "two"], "source_evidence_id": "two", "confidence": 1},
            context={"allowed_evidence_ids": {"one"}},
        )


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


def test_job_candidate_preserves_apply_url_and_posted_date() -> None:
    candidate = JobCandidate.model_validate(
        {
            "title": "Software Engineer",
            "apply_url": "https://example.com/jobs/42",
            "posted_at": "2026-07-20",
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        }
    )

    assert str(candidate.apply_url) == "https://example.com/jobs/42"
    assert candidate.posted_at.isoformat() == "2026-07-20"


def test_job_candidate_rejects_oversized_apply_url() -> None:
    with pytest.raises(ValidationError):
        JobCandidate.model_validate(
            {
                "title": "Software Engineer",
                "apply_url": "https://example.com/" + "x" * 2_000,
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
            }
        )


@pytest.mark.parametrize(
    "apply_url",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.1/private",
        "http://[::1]/private",
        "https://user:password@example.com/jobs/42",
        "https://localhost/jobs/42",
        "https://localhost.localdomain/jobs/42",
        "https://service.internal/jobs/42",
        "https://service.lan/jobs/42",
        "https://service.home/jobs/42",
        "https://home.arpa/jobs/42",
        "https://service.home.arpa/jobs/42",
    ],
)
def test_job_candidate_rejects_statically_unsafe_apply_url(apply_url: str) -> None:
    with pytest.raises(ValidationError, match="public URL"):
        JobCandidate.model_validate(
            {
                "title": "Software Engineer",
                "apply_url": apply_url,
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
            }
        )


@pytest.mark.parametrize(
    "apply_url",
    [
        "https://example.com/jobs/42",
        "https://8.8.8.8/jobs/42",
    ],
)
def test_job_candidate_accepts_statically_public_apply_url(apply_url: str) -> None:
    candidate = JobCandidate.model_validate(
        {
            "title": "Software Engineer",
            "apply_url": apply_url,
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        }
    )

    assert str(candidate.apply_url) == apply_url


def test_filing_candidate_matches_persisted_filing_vocabulary_and_fields() -> None:
    candidate = FilingCandidate.model_validate(
        {
            "title": "Example ICP filing",
            "filing_type": "icp",
            "filing_number": "ICP-42",
            "filing_authority": "MIIT",
            "filing_date": "2026-07-20",
            "filing_status": "active",
            "url": "https://example.com/filings/42",
            "evidence_ids": ["doc-1"],
            "confidence": 0.9,
        }
    )

    assert candidate.filing_type is FilingType.ICP
    assert candidate.filing_number == "ICP-42"
    assert candidate.filing_authority == "MIIT"
    assert candidate.filing_date.isoformat() == "2026-07-20"
    assert candidate.filing_status == "active"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filing_number", "x" * 256),
        ("filing_authority", "x" * 256),
        ("filing_status", "x" * 51),
    ],
)
def test_filing_candidate_rejects_oversized_persistence_fields(
    field: str, value: str
) -> None:
    payload: dict[str, object] = {
        "title": "Example filing",
        "filing_type": "icp",
        "filing_number": "ICP-42",
        "evidence_ids": ["doc-1"],
        "confidence": 0.9,
        field: value,
    }

    with pytest.raises(ValidationError):
        FilingCandidate.model_validate(payload)


def test_filing_candidate_rejects_non_persisted_filing_type() -> None:
    with pytest.raises(ValidationError):
        FilingCandidate.model_validate(
            {
                "title": "Press release",
                "filing_type": "press_release",
                "filing_number": "PRESS-42",
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
            }
        )


def test_filing_candidate_rejects_name_too_long_for_persistence() -> None:
    with pytest.raises(ValidationError):
        FilingCandidate.model_validate(
            {
                "title": "x" * 256,
                "filing_type": "icp",
                "filing_number": "ICP-42",
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
            }
        )


def test_extraction_models_are_deeply_immutable_and_json_arrays_become_tuples() -> None:
    batch = ExtractionBatch.model_validate_json(
        '{"companies":[{"name":"Example","evidence_ids":["doc-1"],'
        '"confidence":0.9}],"jobs":[],"profiles":[],"filings":[]}'
    )
    candidate = batch.companies[0]

    assert isinstance(batch.companies, tuple)
    assert isinstance(candidate.evidence_ids, tuple)
    with pytest.raises(ValidationError):
        candidate.name = "Changed"
    with pytest.raises(AttributeError):
        candidate.evidence_ids.append("doc-2")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("website", "https://example.com/" + "x" * 1_000),
    ],
)
def test_company_candidate_rejects_values_too_long_for_database(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        CompanyCandidate.model_validate(
            {
                "name": "Example",
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
                field: value,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 256),
        ("location", "x" * 51),
    ],
)
def test_job_candidate_rejects_values_too_long_for_database(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        JobCandidate.model_validate(
            {
                "title": "Engineer",
                "provider": "official",
                "source_raw_id": "job-1",
                "evidence_ids": ["doc-1"],
                "confidence": 0.9,
                field: value,
            }
        )


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
