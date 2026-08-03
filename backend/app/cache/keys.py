"""Stable Redis key construction for company-query responses."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

_PREFIX = "company-search"


def list_key(params: Mapping[str, Any], *, version: int) -> str:
    """Return a canonical list-response key for the supplied query parameters."""
    digest = _params_digest(params)
    return f"{_PREFIX}:companies:list:v{version}:{digest}"


def list_version_key() -> str:
    return f"{_PREFIX}:companies:list:version"


def detail_key(company_id: UUID) -> str:
    return f"{_PREFIX}:companies:{company_id}:detail"


def jobs_key(company_id: UUID, params: Mapping[str, Any]) -> str:
    return f"{_PREFIX}:companies:{company_id}:jobs:{_params_digest(params)}"


def jobs_key_pattern(company_id: UUID) -> str:
    return f"{_PREFIX}:companies:{company_id}:jobs:*"


def _params_digest(params: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        params,
        default=str,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
