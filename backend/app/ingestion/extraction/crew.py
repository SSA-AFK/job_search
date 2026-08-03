"""A small adapter exposing fixed extraction roles without CrewAI runtime coupling."""

import json
from collections.abc import Sequence
from typing import Protocol

from pydantic import ValidationError

from app.ingestion.contracts import RawDocument
from app.ingestion.errors import ExtractionError
from app.ingestion.extraction.client import LlmClient
from app.ingestion.extraction.prompts import build_prompt
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    CompanyProfileCandidate,
    CompanyRef,
    ExtractionBatch,
    JobCandidate,
)


class Extractor(Protocol):
    async def discover(self, documents: Sequence[RawDocument]) -> list[CompanyCandidate]: ...

    async def extract_profile(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> CompanyProfileCandidate: ...

    async def extract_jobs(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> list[JobCandidate]: ...


class CrewExtractor:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def discover(self, documents: Sequence[RawDocument]) -> list[CompanyCandidate]:
        batch = await self._extract_batch("discover", documents)
        return batch.companies

    async def extract_profile(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> CompanyProfileCandidate:
        batch = await self._extract_batch("profile", documents)
        for profile in batch.profiles:
            if profile.name == company.name:
                return profile
        raise ExtractionError(code="invalid_output", detail="profile missing from model output")

    async def extract_jobs(
        self, company: CompanyRef, documents: Sequence[RawDocument]
    ) -> list[JobCandidate]:
        batch = await self._extract_batch("jobs", documents)
        return batch.jobs

    async def _extract_batch(
        self, role: str, documents: Sequence[RawDocument]
    ) -> ExtractionBatch:
        evidence_ids, prompt = build_prompt(role, documents)
        response = await self._llm.complete(prompt)
        try:
            payload = json.loads(response)
            return ExtractionBatch.model_validate(
                payload, context={"allowed_evidence_ids": evidence_ids}
            )
        except (json.JSONDecodeError, ValidationError) as error:
            raise ExtractionError(code="invalid_output", detail=str(error)) from error
