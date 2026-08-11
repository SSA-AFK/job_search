from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.company_identity.contracts import (
    CompanyIdentityInput,
    CompanyIdentityResolution,
    CompanyIdentityReviewDraft,
    IdentityResolutionKind,
    IdentityReviewReason,
)
from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    FilingCandidate,
    JobCandidate,
    ProfileExtraction,
)
from app.ingestion.orchestrator import NormalizedBatchBuilder
from app.ingestion.persistence.contracts import BatchBuildOutcome, NormalizedBatch


def ready_batch(outcome: BatchBuildOutcome) -> NormalizedBatch:
    assert outcome.review_draft is None
    assert outcome.batch is not None
    return outcome.batch


def review_draft() -> CompanyIdentityReviewDraft:
    return CompanyIdentityReviewDraft(
        identity=CompanyIdentityInput(canonical_name="Acme"),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at="2026-08-07T00:00:00Z",
    )


def test_batch_build_outcome_requires_exactly_one_payload() -> None:
    draft = review_draft()

    with pytest.raises(ValidationError, match="exactly one"):
        BatchBuildOutcome()
    with pytest.raises(ValidationError, match="exactly one"):
        BatchBuildOutcome(batch=None, review_draft=None)

    outcome = BatchBuildOutcome.review_required(draft)
    assert outcome.batch is None
    assert outcome.review_draft == draft


def source(external_id: str, *, provider: str = "site") -> RawDocument:
    return RawDocument(
        provider=provider,
        external_id=external_id,
        url=f"https://acme.example/{external_id}",
        title=external_id,
        text="source",
        published_at=None,
    )


def profile(candidate: CompanyProfileCandidate) -> ProfileExtraction:
    return ProfileExtraction(profile=candidate)


class IdentityResolver:
    def __init__(self, kind: IdentityResolutionKind, company_id: UUID | None = None) -> None:
        self.kind = kind
        self.company_id = company_id
        self.identity: CompanyIdentityInput | None = None

    async def resolve(self, identity: CompanyIdentityInput) -> CompanyIdentityResolution:
        self.identity = identity
        reasons = (
            (IdentityReviewReason.FUZZY_NAME_NEIGHBOR,)
            if self.kind is IdentityResolutionKind.REVIEW_REQUIRED
            else ()
        )
        stable_hash = CompanyIdentityReviewDraft(
            identity=identity,
            review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
            observed_at=datetime(2026, 8, 7, tzinfo=UTC),
        ).stable_identity_hash
        return CompanyIdentityResolution(
            kind=self.kind,
            company_id=self.company_id,
            stable_identity_hash=stable_hash,
            review_reasons=reasons,
        )


@pytest.mark.asyncio
async def test_builder_returns_review_draft_with_identity_evidence() -> None:
    resolver = IdentityResolver(IdentityResolutionKind.REVIEW_REQUIRED)
    outcome = await NormalizedBatchBuilder(identity_resolver=resolver).build(
        company=CompanyRef(name="Acme", website="https://acme.example"),
        discovered=CompanyCandidate(
            name="Acme",
            aliases=("Acme AI",),
            website="https://acme.example",
            evidence_ids=("company",),
            confidence=0.9,
        ),
        profile=ProfileExtraction(
            profile=CompanyProfileCandidate(
                name="Acme",
                website="https://acme.example",
                evidence_ids=("company",),
                confidence=0.8,
            ),
            filings=(
                FilingCandidate(
                    title="Acme ICP",
                    filing_type="icp",
                    filing_number="ICP-42",
                    evidence_ids=("company",),
                    confidence=0.7,
                ),
            ),
        ),
        jobs=(),
        documents=(source("company", provider="official"),),
    )

    assert outcome.batch is None
    assert outcome.review_draft is not None
    assert outcome.review_draft.review_reasons == (
        IdentityReviewReason.FUZZY_NAME_NEIGHBOR,
    )
    assert resolver.identity is not None
    assert resolver.identity.aliases == ("Acme AI",)
    assert resolver.identity.official_website == "https://acme.example/"
    assert resolver.identity.legal_identifiers == ("icp-42",)
    assert [reference.model_dump() for reference in resolver.identity.evidence] == [
        {
            "provider": "official",
            "url": "https://acme.example/company",
            "evidence_id": "company",
            "confidence": Decimal("0.9"),
        }
    ]
    assert resolver.identity.evidence[0].confidence == Decimal("0.9")


@pytest.mark.asyncio
async def test_builder_ready_outcome_preserves_candidate_aliases() -> None:
    company_id = uuid4()
    resolver = IdentityResolver(IdentityResolutionKind.EXISTING, company_id)
    outcome = await NormalizedBatchBuilder(identity_resolver=resolver).build(
        company=CompanyRef(name="Acme"),
        discovered=CompanyCandidate(
            name="Acme",
            aliases=("Acme AI",),
            evidence_ids=("company",),
            confidence=1,
        ),
        profile=profile(
            CompanyProfileCandidate(
                name="Acme", evidence_ids=("company",), confidence=1
            )
        ),
        jobs=(),
        documents=(source("company"),),
    )

    batch = ready_batch(outcome)
    assert batch.company.company_id == company_id
    assert batch.company.candidate.candidate.aliases == ("Acme AI",)
    with pytest.raises(ValidationError, match="exactly one"):
        BatchBuildOutcome(batch=batch, review_draft=review_draft())
    with pytest.raises(ValidationError, match="frozen"):
        outcome.batch = None  # type: ignore[misc]


@pytest.mark.asyncio
async def test_builder_derives_job_source_identity_from_its_evidence_document() -> None:
    documents = (source("company"), source("job-42", provider="careers"))
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        profile=profile(CompanyProfileCandidate(
            name="Acme", evidence_ids=("company",), confidence=1, description="A company"
        )),
        jobs=(JobCandidate(company_name="Acme", title="Engineer", evidence_ids=("job-42",), confidence=1),),
        documents=documents,
        discovered=CompanyCandidate(
            name="Acme", evidence_ids=("company",), confidence=1
        ),
    ))

    assert batch.jobs[0].candidate.candidate.provider == "careers"
    assert batch.jobs[0].candidate.candidate.source_raw_id == "job-42"
    assert batch.jobs[0].source_evidence_id == "job-42"
    assert str(batch.jobs[0].apply_url) == "https://acme.example/job-42"


@pytest.mark.asyncio
async def test_builder_uses_discovery_evidence_for_discovery_fallback_fields() -> None:
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        discovered=CompanyCandidate(name="Acme", website="https://acme.example", description="Acme", evidence_ids=("company",), confidence=1),
        profile=profile(CompanyProfileCandidate(name="Acme", evidence_ids=("profile",), confidence=1)),
        jobs=(), documents=(source("company"), source("profile")),
    ))

    assert {(item.field_name, item.evidence_id) for item in batch.company.field_evidence} == {
        ("canonical_name", "company"), ("website", "company"), ("description", "company")
    }


@pytest.mark.asyncio
async def test_builder_treats_whitespace_profile_description_as_discovery_fallback() -> None:
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        discovered=CompanyCandidate(name="Acme Holdings", description="Discovery text", evidence_ids=("company",), confidence=1),
        profile=profile(CompanyProfileCandidate(name="  Acme   Holdings ", description="   ", evidence_ids=("profile",), confidence=1)),
        jobs=(), documents=(source("company"), source("profile")),
    ))

    assert batch.company.candidate.candidate.name == "Acme Holdings"
    assert batch.company.candidate.candidate.description == "Discovery text"
    assert {(item.field_name, item.evidence_id) for item in batch.company.field_evidence} == {
        ("canonical_name", "company"), ("description", "company")
    }


@pytest.mark.asyncio
async def test_builder_cites_profile_evidence_for_profile_description() -> None:
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"), discovered=CompanyCandidate(name="Acme", evidence_ids=("company",), confidence=1),
        profile=profile(CompanyProfileCandidate(name="Acme", description=" Profile text ", evidence_ids=("profile",), confidence=1)),
        jobs=(), documents=(source("company"), source("profile")),
    ))

    assert batch.company.candidate.candidate.description == "Profile text"
    assert ("description", "profile") in {(item.field_name, item.evidence_id) for item in batch.company.field_evidence}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        CompanyProfileCandidate(name="Else", evidence_ids=("profile",), confidence=1),
        CompanyProfileCandidate(name="Acme", website="https://else.example", evidence_ids=("profile",), confidence=1),
    ],
)
async def test_builder_rejects_conflicting_profile_data(profile: CompanyProfileCandidate) -> None:
    with pytest.raises(Exception, match="invalid_evidence"):
        await NormalizedBatchBuilder().build(
            company=CompanyRef(name="Acme"),
            discovered=CompanyCandidate(name="Acme", website="https://acme.example", description="Acme", evidence_ids=("company",), confidence=1),
            profile=ProfileExtraction(profile=profile), jobs=(), documents=(source("company"), source("profile")),
        )


@pytest.mark.asyncio
async def test_builder_accepts_divergent_profile_description() -> None:
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        discovered=CompanyCandidate(
            name="Acme",
            description="A  B",
            evidence_ids=("company",),
            confidence=1,
        ),
        profile=profile(CompanyProfileCandidate(
            name="Acme",
            description="A B",
            evidence_ids=("profile",),
            confidence=1,
        )),
        jobs=(),
        documents=(source("company"), source("profile")),
    ))

    assert batch.company.candidate.candidate.description == "A B"


@pytest.mark.asyncio
async def test_builder_requires_explicit_source_for_multiple_job_evidence() -> None:
    with pytest.raises(Exception, match="invalid_evidence"):
        await NormalizedBatchBuilder().build(
            company=CompanyRef(name="Acme"), discovered=CompanyCandidate(name="Acme", evidence_ids=("company",), confidence=1),
            profile=profile(CompanyProfileCandidate(name="Acme", evidence_ids=("company",), confidence=1)),
            jobs=(JobCandidate(company_name="Acme", title="Engineer", evidence_ids=("job-1", "job-2"), confidence=1),),
            documents=(source("company"), source("job-1"), source("job-2")),
        )


@pytest.mark.asyncio
async def test_builder_uses_explicit_source_for_multiple_job_evidence() -> None:
    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"), discovered=CompanyCandidate(name="Acme", evidence_ids=("company",), confidence=1),
        profile=profile(CompanyProfileCandidate(name="Acme", evidence_ids=("company",), confidence=1)),
        jobs=(JobCandidate(company_name="Acme", title="Engineer", evidence_ids=("job-1", "job-2"), source_evidence_id="job-2", confidence=1),),
        documents=(source("company"), source("job-1", provider="first"), source("job-2", provider="second")),
    ))

    job = batch.jobs[0]
    assert job.source_evidence_id == "job-2"
    assert job.candidate.candidate.provider == "second"
    assert job.candidate.candidate.source_raw_id == "job-2"


@pytest.mark.asyncio
async def test_builder_normalizes_profile_filings_into_the_persistence_batch() -> None:
    extraction = ProfileExtraction(
        profile=CompanyProfileCandidate(
            name="Acme", evidence_ids=("company",), confidence=1
        ),
        filings=(
            FilingCandidate(
                title="Acme ICP",
                filing_type="icp",
                filing_number="ICP-42",
                evidence_ids=("company",),
                confidence=1,
            ),
        ),
    )

    batch = ready_batch(await NormalizedBatchBuilder().build(
        company=CompanyRef(name="Acme"),
        profile=extraction,
        jobs=(),
        documents=(source("company"),),
        discovered=CompanyCandidate(
            name="Acme", evidence_ids=("company",), confidence=1
        ),
    ))

    assert [filing.filing_number for filing in batch.filings] == ["icp-42"]
    assert batch.filings[0].source_evidence_id == "company"
