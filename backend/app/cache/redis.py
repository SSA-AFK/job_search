"""Redis implementation of the optional company-query cache."""

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from app.cache.base import ListCacheEntry
from app.cache.keys import detail_key, jobs_key, jobs_key_pattern, list_key, list_version_key

logger = logging.getLogger("app.cache")

_LIST_TTL_SECONDS = 60
_DETAIL_TTL_SECONDS = 300
_JOBS_TTL_SECONDS = 300
_SOCKET_CONNECT_TIMEOUT_SECONDS = 0.2
_SOCKET_TIMEOUT_SECONDS = 0.2
_ValueT = TypeVar("_ValueT")
_SET_LIST_IF_VERSION_UNCHANGED = """
local version = redis.call('GET', KEYS[1]) or '0'
if version ~= ARGV[1] then
    return 0
end
return redis.call('SETEX', KEYS[2], ARGV[2], ARGV[3])
"""


class RedisClient(Protocol):
    def get(self, key: str) -> str | None: ...

    def setex(self, key: str, seconds: int, value: str) -> object: ...

    def delete(self, *keys: str) -> int: ...

    def incr(self, key: str) -> int: ...

    def scan_iter(self, *, match: str) -> Iterable[str]: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...


class RedisCompanyCache:
    def __init__(self, client: RedisClient) -> None:
        self.client = client

    def get_list(self, params: Mapping[str, Any]) -> ListCacheEntry:
        def get() -> ListCacheEntry:
            version = self._list_version()
            return ListCacheEntry(self.client.get(list_key(params, version=version)), version)

        return self._call("get_list", get, ListCacheEntry(value=None, version=None))

    def set_list(self, params: Mapping[str, Any], value: str, *, version: int | None) -> None:
        if version is None:
            return
        self._call(
            "set_list",
            lambda: self.client.eval(
                _SET_LIST_IF_VERSION_UNCHANGED,
                2,
                list_version_key(),
                list_key(params, version=version),
                str(version),
                str(_LIST_TTL_SECONDS),
                value,
            ),
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
            self.client.incr(list_version_key())
            job_keys = list(self.client.scan_iter(match=jobs_key_pattern(company_id)))
            self.client.delete(detail_key(company_id), *job_keys)

        self._call("invalidate_company", invalidate, None)

    def _list_version(self) -> int:
        value = self.client.get(list_version_key())
        try:
            version = int(value or 0)
        except ValueError:
            logger.warning(
                "Company cache list version is invalid",
                extra={"metric": "company_cache_redis_error", "operation": "get_list_version"},
            )
            raise RedisError("company cache list version is invalid") from None
        return version

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
    return RedisCompanyCache(
        cast(
            RedisClient,
            Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            ),
        )
    )
