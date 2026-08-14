"""OpenAI-compatible transport.

NOTE: The main ingestion / ranking pipeline no longer performs LLM extraction.
This module is retained as a type-contract stub only because the independent
public-entry evidence subsystem (manifest) declares these symbols in its type
imports. Calling ``__init__`` or ``complete`` at runtime will raise so that
any accidental remaining extraction path surfaces as an explicit failure.
"""

from __future__ import annotations

from typing import Any, Protocol


class LlmClient(Protocol):
    async def complete(
        self, prompt: str, *, response_schema: dict[str, Any] | None = None
    ) -> str:
        ...


class OpenAICompatibleLlmClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float | int = 30.0,
    ) -> None:
        raise NotImplementedError(
            "LLM extraction transport has been removed from the company "
            "collection pipeline; approved paths use structured parsing "
            "(天眼查 CLI, ATS direct, and BOSS CDP) only."
        )

    async def complete(
        self, prompt: str, *, response_schema: dict[str, Any] | None = None
    ) -> str:
        raise NotImplementedError(
            "LLM extraction transport has been removed from the company "
            "collection pipeline; approved paths use structured parsing "
            "(天眼查 CLI, ATS direct, and BOSS CDP) only."
        )
