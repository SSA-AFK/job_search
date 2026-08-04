"""The sole boundary to an external language model."""

import json
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from app.ingestion.errors import ExtractionError


class LlmClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


class OpenAICompatibleLlmClient:
    """Bounded client for the standard chat-completions JSON contract."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def complete(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, trust_env=False
            ) as client, client.stream(
                "POST",
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept-Encoding": "identity",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            ) as response:
                response.raise_for_status()
                content_encoding = response.headers.get(
                    "content-encoding", "identity"
                ).strip().lower()
                if content_encoding != "identity":
                    raise ExtractionError(code="invalid_output")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as error:
                        raise ExtractionError(code="invalid_output") from error
                    if (
                        declared_size < 0
                        or declared_size > self._max_response_bytes
                    ):
                        raise ExtractionError(code="invalid_output")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self._max_response_bytes:
                        raise ExtractionError(code="invalid_output")
                    body.extend(chunk)
        except ExtractionError:
            raise
        except httpx.HTTPError as error:
            raise ExtractionError(code="model_unavailable") from error
        try:
            payload: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ExtractionError(code="invalid_output") from error
        try:
            choices = payload["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ExtractionError(code="invalid_output") from error
        if not isinstance(message, Mapping) or not isinstance(content, str):
            raise ExtractionError(code="invalid_output")
        return content
