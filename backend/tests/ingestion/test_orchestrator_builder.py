import pytest

from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    JobCandidate,
)
from app.ingestion.orchestrator import NormalizedBatchBuilder


def source(external_id: str, *, provider: str = "site") -> RawDocument:
    return RawDocument(
        provider=provider,
        external_id=external_id,
        url=f"https://acme.example/{external_id}",
        title=external_id,
        text="source",
        published_at=None,
    )


@pytest.mark.asyncio
async def test_builder_derives_job_source_identity_from_its_evidence_document() -> None:
    documents = (source("company"), source("job-42", provider="careers"))
    batch = await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        profile=CompanyProfileCandidate(
            name="Acme", evidence_ids=("company",), confidence=1, description="A company"
        ),
        jobs=(JobCandidate(title="Engineer", evidence_ids=("job-42",), confidence=1),),
        documents=documents,
        discovered=CompanyCandidate(
            name="Acme", evidence_ids=("company",), confidence=1
        ),
    )

    assert batch.jobs[0].candidate.candidate.provider == "careers"
    assert batch.jobs[0].candidate.candidate.source_raw_id == "job-42"
    assert batch.jobs[0].source_evidence_id == "job-42"
    assert str(batch.jobs[0].apply_url) == "https://acme.example/job-42"
