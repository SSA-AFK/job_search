"""The narrow advisory boundary for ambiguous job matches."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.ingestion.errors import ExtractionError
from app.ingestion.extraction.client import LlmClient

if TYPE_CHECKING:
    from app.ingestion.deduplication.job import JobForComparison


@dataclass(frozen=True)
class DuplicateDecision:
    is_duplicate: bool


class SemanticDuplicateJudge(Protocol):
    async def jobs_are_duplicates(
        self, left: "JobForComparison", right: "JobForComparison"
    ) -> DuplicateDecision: ...


class LlmSemanticDuplicateJudge:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def jobs_are_duplicates(
        self, left: "JobForComparison", right: "JobForComparison"
    ) -> DuplicateDecision:
        prompt = (
            "Return JSON only as {\"is_duplicate\": true|false}. Compare these "
            "two jobs as distinct operands:\n"
            f"left={left!r}\nright={right!r}"
        )
        response = await self._llm.complete(prompt)
        try:
            payload = json.loads(response)
            value = payload["is_duplicate"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ExtractionError(code="invalid_output") from error
        if type(value) is not bool:
            raise ExtractionError(code="invalid_output")
        return DuplicateDecision(value)
