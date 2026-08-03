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


@pytest.mark.asyncio
async def test_builder_uses_discovery_evidence_for_discovery_fallback_fields() -> None:
    batch = await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        discovered=CompanyCandidate(name="Acme", website="https://acme.example", description="Acme", evidence_ids=("company",), confidence=1),
        profile=CompanyProfileCandidate(name="Acme", evidence_ids=("profile",), confidence=1),
        jobs=(), documents=(source("company"), source("profile")),
    )

    assert {(item.field_name, item.evidence_id) for item in batch.company.field_evidence} == {
        ("canonical_name", "company"), ("website", "company"), ("description", "company")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        CompanyProfileCandidate(name="Else", evidence_ids=("profile",), confidence=1),
        CompanyProfileCandidate(name="Acme", website="https://else.example", evidence_ids=("profile",), confidence=1),
        CompanyProfileCandidate(name="Acme", description="Else", evidence_ids=("profile",), confidence=1),
    ],
)
async def test_builder_rejects_conflicting_profile_data(profile: CompanyProfileCandidate) -> None:
    with pytest.raises(Exception, match="invalid_evidence"):
        await NormalizedBatchBuilder().build(
            company=CompanyRef(name="Acme"),
            discovered=CompanyCandidate(name="Acme", website="https://acme.example", description="Acme", evidence_ids=("company",), confidence=1),
            profile=profile, jobs=(), documents=(source("company"), source("profile")),
        )


@pytest.mark.asyncio
async def test_builder_requires_explicit_source_for_multiple_job_evidence() -> None:
    with pytest.raises(Exception, match="invalid_evidence"):
        await NormalizedBatchBuilder().build(
            company=CompanyRef(name="Acme"), discovered=CompanyCandidate(name="Acme", evidence_ids=("company",), confidence=1),
            profile=CompanyProfileCandidate(name="Acme", evidence_ids=("company",), confidence=1),
            jobs=(JobCandidate(title="Engineer", evidence_ids=("job-1", "job-2"), confidence=1),),
            documents=(source("company"), source("job-1"), source("job-2")),
        )


@pytest.mark.asyncio
async def test_builder_uses_explicit_source_for_multiple_job_evidence() -> None:
    batch = await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"), discovered=CompanyCandidate(name="Acme", evidence_ids=("company",), confidence=1),
        profile=CompanyProfileCandidate(name="Acme", evidence_ids=("company",), confidence=1),
        jobs=(JobCandidate(title="Engineer", evidence_ids=("job-1", "job-2"), source_evidence_id="job-2", confidence=1),),
        documents=(source("company"), source("job-1", provider="first"), source("job-2", provider="second")),
    )

    job = batch.jobs[0]
    assert job.source_evidence_id == "job-2"
    assert job.candidate.candidate.provider == "second"
    assert job.candidate.candidate.source_raw_id == "job-2"
