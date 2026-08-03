"""Cached robots.txt policy for allowlisted company websites."""

import asyncio
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import SafeHttpClient


class RobotsPolicy:
    def __init__(self, *, http_client: SafeHttpClient, user_agent: str = "company-search") -> None:
        self._http_client = http_client
        self._user_agent = user_agent
        self._rules_by_host: dict[str, RobotFileParser | None] = {}
        self._locks_by_host: dict[str, asyncio.Lock] = {}

    async def can_fetch(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or host is None or parsed.username is not None:
            return False

        normalized_host = host.lower().rstrip(".")
        if normalized_host not in self._rules_by_host:
            lock = self._locks_by_host.setdefault(normalized_host, asyncio.Lock())
            async with lock:
                if normalized_host not in self._rules_by_host:
                    robots_url = urlunsplit(
                        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
                    )
                    try:
                        document = await self._http_client.get_text(
                            robots_url, allowed_hosts={normalized_host}
                        )
                    except ProviderError:
                        self._rules_by_host[normalized_host] = None
                    else:
                        parser = RobotFileParser()
                        parser.set_url(document.url)
                        parser.parse(document.text.splitlines())
                        self._rules_by_host[normalized_host] = parser

        rules = self._rules_by_host[normalized_host]
        return rules is not None and rules.can_fetch(self._user_agent, url)
