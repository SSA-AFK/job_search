"""A small adapter exposing fixed extraction roles without CrewAI runtime coupling."""

import json
from collections.abc import Sequence
from typing import Protocol

from pydantic import ValidationError

from app.core.normalization import normalize_name
from app.ingestion.contracts import RawDocument
from app.ingestion.errors import ExtractionError
from app.ingestion.extraction.client import LlmClient
from app.ingestion.extraction.prompts import build_prompt
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyRef,
    ExtractionBatch,
    JobCandidate,
    ProfileExtraction,
)


class Extractor(Protocol):
    async def discover(
        self, documents: Sequence[RawDocument]
    ) -> tuple[CompanyCandidate, ...]: ...

    async def extract_profile(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> ProfileExtraction: ...

    async def extract_jobs(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> tuple[JobCandidate, ...]: ...


class CrewExtractor:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def discover(
        self, documents: Sequence[RawDocument]
    ) -> tuple[CompanyCandidate, ...]:
        batch = await self._extract_batch("discover", documents)
        return batch.companies

    async def extract_profile(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> ProfileExtraction:
        batch = await self._extract_batch("profile", documents, company)
        for profile in batch.profiles:
            if normalize_name(profile.name) == normalize_name(company.name):
                return ProfileExtraction(profile=profile, filings=batch.filings)
        raise ExtractionError(code="invalid_output", detail="profile missing from model output")

    async def extract_jobs(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> tuple[JobCandidate, ...]:
        batch = await self._extract_batch("jobs", documents, company)
        if any(
            normalize_name(job.company_name) != normalize_name(company.name)
            for job in batch.jobs
        ):
            raise ExtractionError(code="invalid_output", detail="job company mismatch")
        return batch.jobs

    async def _extract_batch(
        self,
        role: str,
        documents: Sequence[RawDocument],
        company: CompanyRef | None = None,
    ) -> ExtractionBatch:
        evidence_ids, prompt = build_prompt(role, documents, company)
        response = await self._llm.complete(prompt)
        try:
            payload = json.loads(response)
            return ExtractionBatch.model_validate(
                payload, context={"allowed_evidence_ids": evidence_ids}
            )
        except (json.JSONDecodeError, ValidationError) as error:
            raise ExtractionError(code="invalid_output", detail=str(error)) from error
