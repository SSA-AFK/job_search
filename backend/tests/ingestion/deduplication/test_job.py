import asyncio
from collections.abc import Iterable
from uuid import UUID

import pytest

from app.ingestion.deduplication.job import JobDeduplicator, JobForComparison, SourceJobMatch
from app.ingestion.deduplication.semantic import DuplicateDecision
from app.ingestion.extraction.schemas import EmploymentType, JobCandidate
from app.models.enums import JobType

COMPANY_ID = UUID("00000000-0000-0000-0000-000000000010")
EXISTING_JOB_ID = UUID("00000000-0000-0000-0000-000000000011")
OTHER_COMPANY_ID = UUID("00000000-0000-0000-0000-000000000012")


@pytest.fixture
def semantic_judge() -> "FakeSemanticJudge":
    return FakeSemanticJudge(DuplicateDecision(is_duplicate=True))


@pytest.fixture
def repository() -> "FakeJobRepository":
    return FakeJobRepository(
        exact={("zhihu", "42"): SourceJobMatch(EXISTING_JOB_ID, COMPANY_ID)},
        jobs={
            COMPANY_ID: (
                JobForComparison(
                    job_posting_id=EXISTING_JOB_ID,
                    normalized_title="software engineer",
                    city="shanghai",
                    job_type=JobType.FULL_TIME,
                ),
            ),
            OTHER_COMPANY_ID: (
                JobForComparison(
                    job_posting_id=OTHER_COMPANY_ID,
                    normalized_title="software engineer",
                    city="shanghai",
                    job_type=JobType.FULL_TIME,
                ),
            ),
        },
    )


@pytest.fixture
def job_deduplicator(repository: "FakeJobRepository", semantic_judge: "FakeSemanticJudge") -> JobDeduplicator:
    return JobDeduplicator(repository, semantic_judge)


def test_exact_source_id_wins_without_fuzzy_or_semantic_judgment(
    job_deduplicator: JobDeduplicator, semantic_judge: "FakeSemanticJudge"
) -> None:
    match = asyncio.run(
        job_deduplicator.resolve(
            COMPANY_ID,
            job_candidate(provider="zhihu", source_raw_id="42"),
        )
    )

    assert match.kind == "existing"
    assert match.job_posting_id == EXISTING_JOB_ID
    assert semantic_judge.calls == []


def test_same_title_in_different_city_is_not_auto_merged(
    job_deduplicator: JobDeduplicator, semantic_judge: "FakeSemanticJudge"
) -> None:
    match = asyncio.run(job_deduplicator.resolve(COMPANY_ID, job_candidate(location="beijing")))

    assert match.kind == "new"
    assert semantic_judge.calls == []


def test_exact_source_from_a_different_company_is_not_merged(
    job_deduplicator: JobDeduplicator, semantic_judge: "FakeSemanticJudge"
) -> None:
    match = asyncio.run(
        job_deduplicator.resolve(
            UUID("00000000-0000-0000-0000-000000000013"),
            job_candidate(provider="zhihu", source_raw_id="42"),
        )
    )

    assert match.kind == "new"
    assert semantic_judge.calls == []


@pytest.mark.parametrize(
    ("title", "existing_title", "expected_kind", "semantic_calls"),
    [
        (
            "a" * 87 + "b" * 15,
            "a" * 87 + "c" * 16,
            "existing",
            1,
        ),  # 84.9% against the existing title.
        (
            "a" * 85 + "b" * 15,
            "a" * 85 + "c" * 15,
            "existing",
            1,
        ),  # 85.0% is still in the ambiguity band.
        (
            "a" * 86 + "b" * 15,
            "a" * 86 + "c" * 15,
            "existing",
            0,
        ),  # 85.1% is an automatic merge.
    ],
)
def test_job_similarity_boundaries_choose_semantic_or_automatic_decision(
    title: str,
    existing_title: str,
    expected_kind: str,
    semantic_calls: int,
    repository: "FakeJobRepository",
    semantic_judge: "FakeSemanticJudge",
) -> None:
    repository.jobs[COMPANY_ID] = (
        JobForComparison(
            job_posting_id=EXISTING_JOB_ID,
            normalized_title=existing_title,
            city="shanghai",
            job_type=JobType.FULL_TIME,
        ),
    )
    deduplicator = JobDeduplicator(repository, semantic_judge)

    match = asyncio.run(deduplicator.resolve(COMPANY_ID, job_candidate(title=title)))

    assert match.kind == expected_kind
    assert match.job_posting_id == EXISTING_JOB_ID
    assert len(semantic_judge.calls) == semantic_calls


def test_incompatible_job_type_is_not_merged(
    job_deduplicator: JobDeduplicator, semantic_judge: "FakeSemanticJudge"
) -> None:
    match = asyncio.run(
        job_deduplicator.resolve(
            COMPANY_ID,
            job_candidate(employment_type=EmploymentType.INTERNSHIP),
        )
    )

    assert match.kind == "new"
    assert semantic_judge.calls == []


@pytest.mark.parametrize(
    ("candidate_type", "existing_type", "expected_kind"),
    [
        (EmploymentType.PART_TIME, EmploymentType.FULL_TIME, "new"),
        (EmploymentType.TEMPORARY, EmploymentType.INTERNSHIP, "new"),
        (EmploymentType.PART_TIME, EmploymentType.PART_TIME, "existing"),
        (EmploymentType.TEMPORARY, EmploymentType.TEMPORARY, "existing"),
    ],
)
def test_explicit_employment_types_merge_only_when_they_match(
    candidate_type: EmploymentType,
    existing_type: EmploymentType,
    expected_kind: str,
    repository: "FakeJobRepository",
    semantic_judge: "FakeSemanticJudge",
) -> None:
    repository.jobs[COMPANY_ID] = (
        JobForComparison(
            job_posting_id=EXISTING_JOB_ID,
            normalized_title="softwareengineer",
            city="shanghai",
            job_type=JobType.UNKNOWN,
            employment_type=existing_type,
        ),
    )
    deduplicator = JobDeduplicator(repository, semantic_judge)

    match = asyncio.run(
        deduplicator.resolve(COMPANY_ID, job_candidate(employment_type=candidate_type))
    )

    assert match.kind == expected_kind
    assert semantic_judge.calls == []


def test_jobs_are_only_compared_within_the_requested_company(
    job_deduplicator: JobDeduplicator, semantic_judge: "FakeSemanticJudge"
) -> None:
    match = asyncio.run(
        job_deduplicator.resolve(
            UUID("00000000-0000-0000-0000-000000000013"),
            job_candidate(),
        )
    )

    assert match.kind == "new"
    assert semantic_judge.calls == []


def job_candidate(**overrides: object) -> JobCandidate:
    values: dict[str, object] = {
        "title": "software engineer",
        "employment_type": EmploymentType.FULL_TIME,
        "location": "shanghai",
        "evidence_ids": ["doc-1"],
        "confidence": 0.9,
    }
    values.update(overrides)
    return JobCandidate.model_validate(values)


class FakeJobRepository:
    def __init__(
        self,
        *,
        exact: dict[tuple[str, str], SourceJobMatch],
        jobs: dict[UUID, tuple[JobForComparison, ...]],
    ) -> None:
        self._exact = exact
        self.jobs = jobs

    async def find_by_source(
        self, provider: str, source_raw_id: str
    ) -> SourceJobMatch | None:
        return self._exact.get((provider, source_raw_id))

    async def list_for_company(self, company_id: UUID) -> Iterable[JobForComparison]:
        return self.jobs.get(company_id, ())


class FakeSemanticJudge:
    def __init__(self, decision: DuplicateDecision) -> None:
        self._decision = decision
        self.calls: list[tuple[JobForComparison, JobForComparison]] = []

    async def jobs_are_duplicates(
        self, left: JobForComparison, right: JobForComparison
    ) -> DuplicateDecision:
        self.calls.append((left, right))
        return self._decision
