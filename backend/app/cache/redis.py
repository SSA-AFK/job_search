"""Redis implementation of the optional company-query cache."""

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from app.cache.keys import detail_key, jobs_key, jobs_key_pattern, list_key, list_version_key

logger = logging.getLogger("app.cache")

_LIST_TTL_SECONDS = 60
_DETAIL_TTL_SECONDS = 300
_JOBS_TTL_SECONDS = 300
_ValueT = TypeVar("_ValueT")


class RedisClient(Protocol):
    def get(self, key: str) -> str | None: ...

    def setex(self, key: str, seconds: int, value: str) -> object: ...

    def delete(self, *keys: str) -> int: ...

    def incr(self, key: str) -> int: ...

    def scan_iter(self, *, match: str) -> Iterable[str]: ...


class RedisCompanyCache:
    def __init__(self, client: RedisClient) -> None:
        self.client = client

    def get_list(self, params: Mapping[str, Any]) -> str | None:
        return self._call("get_list", lambda: self.client.get(self._list_key(params)), None)

    def set_list(self, params: Mapping[str, Any], value: str) -> None:
        self._call(
            "set_list",
            lambda: self.client.setex(self._list_key(params), _LIST_TTL_SECONDS, value),
            None,
        )

    def get_detail(self, company_id: UUID) -> str | None:
        return self._call("get_detail", lambda: self.client.get(detail_key(company_id)), None)

    def set_detail(self, company_id: UUID, value: str) -> None:
        self._call(
            "set_detail",
            lambda: self.client.setex(detail_key(company_id), _DETAIL_TTL_SECONDS, value),
            None,
        )

    def get_jobs(self, company_id: UUID, params: Mapping[str, Any]) -> str | None:
        return self._call("get_jobs", lambda: self.client.get(jobs_key(company_id, params)), None)

    def set_jobs(self, company_id: UUID, params: Mapping[str, Any], value: str) -> None:
        self._call(
            "set_jobs",
            lambda: self.client.setex(jobs_key(company_id, params), _JOBS_TTL_SECONDS, value),
            None,
        )

    def invalidate_company(self, company_id: UUID) -> None:
        def invalidate() -> None:
            job_keys = list(self.client.scan_iter(match=jobs_key_pattern(company_id)))
            self.client.delete(detail_key(company_id), *job_keys)
            self.client.incr(list_version_key())

        self._call("invalidate_company", invalidate, None)

    def _list_key(self, params: Mapping[str, Any]) -> str:
        value = self._call("get_list_version", lambda: self.client.get(list_version_key()), None)
        try:
            version = int(value or 0)
        except ValueError:
            logger.warning(
                "Company cache list version is invalid",
                extra={"metric": "company_cache_redis_error", "operation": "get_list_version"},
            )
            version = 0
        return list_key(params, version=version)

    @staticmethod
    def _call(operation: str, action: Callable[[], _ValueT], default: _ValueT) -> _ValueT:
        try:
            return action()
        except RedisError:
            logger.warning(
                "Company cache Redis operation failed",
                extra={"metric": "company_cache_redis_error", "operation": operation},
                exc_info=True,
            )
            return default


def configured_company_cache(redis_url: str | None) -> RedisCompanyCache | None:
    if not redis_url:
        return None
    return RedisCompanyCache(cast(RedisClient, Redis.from_url(redis_url, decode_responses=True)))
