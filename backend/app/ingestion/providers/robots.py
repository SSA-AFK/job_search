"""Cached robots.txt policy for allowlisted company websites."""

import asyncio
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import SafeHttpClient

_Origin = tuple[str, str, int]


class RobotsPolicy:
    def __init__(self, *, http_client: SafeHttpClient, user_agent: str = "company-search") -> None:
        self._http_client = http_client
        self._user_agent = user_agent
        self._rules_by_origin: dict[_Origin, RobotFileParser | None] = {}
        self._locks_by_origin: dict[_Origin, asyncio.Lock] = {}

    async def can_fetch(self, url: str) -> bool:
        origin = self._normalize_origin(url)
        if origin is None:
            return False
        origin_key, robots_url, normalized_host = origin

        if origin_key not in self._rules_by_origin:
            lock = self._locks_by_origin.setdefault(origin_key, asyncio.Lock())
            async with lock:
                if origin_key not in self._rules_by_origin:
                    try:
                        document = await self._http_client.get_text(
                            robots_url, allowed_hosts={normalized_host}
                        )
                    except ProviderError:
                        self._rules_by_origin[origin_key] = None
                    else:
                        parser = RobotFileParser()
                        parser.set_url(document.url)
                        parser.parse(document.text.splitlines())
                        self._rules_by_origin[origin_key] = parser

        rules = self._rules_by_origin[origin_key]
        return rules is not None and rules.can_fetch(self._user_agent, url)

    @staticmethod
    def _normalize_origin(url: str) -> tuple[_Origin, str, str] | None:
        try:
            parsed = urlsplit(url)
            explicit_port = parsed.port
        except ValueError:
            return None
        host = parsed.hostname
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or host is None or parsed.username is not None:
            return None

        normalized_host = host.lower().rstrip(".")
        default_port = 443 if scheme == "https" else 80
        effective_port = default_port if explicit_port is None else explicit_port
        display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        netloc = (
            display_host
            if effective_port == default_port
            else f"{display_host}:{effective_port}"
        )
        robots_url = urlunsplit((scheme, netloc, "/robots.txt", "", ""))
        return (scheme, normalized_host, effective_port), robots_url, normalized_host
