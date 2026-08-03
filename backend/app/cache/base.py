"""Cache boundary for serialized company query responses."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ListCacheEntry:
    value: str | None
    version: int | None


class CompanyCache(Protocol):
    def get_list(self, params: Mapping[str, Any]) -> ListCacheEntry: ...

    def set_list(self, params: Mapping[str, Any], value: str, *, version: int | None) -> None: ...

    def get_detail(self, company_id: UUID) -> str | None: ...

    def set_detail(self, company_id: UUID, value: str) -> None: ...

    def get_jobs(self, company_id: UUID, params: Mapping[str, Any]) -> str | None: ...

    def set_jobs(self, company_id: UUID, params: Mapping[str, Any], value: str) -> None: ...

    def invalidate_company(self, company_id: UUID) -> None: ...
