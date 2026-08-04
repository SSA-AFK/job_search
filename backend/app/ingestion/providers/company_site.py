"""Bounded crawler for an explicitly configured company website."""

from collections import deque
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import HttpUrl

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError
from app.ingestion.providers.http import HttpDocument, SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_SEED_PATHS = ("/about", "/jobs", "/careers")
_MAX_PAGES = 10
_MAX_DEPTH = 1


class CompanySiteProvider:
    name = "company_site"
    requires_website = True

    def __init__(
        self,
        *,
        http_client: SafeHttpClient,
        robots_policy: RobotsPolicy,
        approved_hosts: frozenset[str],
    ) -> None:
        self._http_client = http_client
        self._robots_policy = robots_policy
        self.approved_hosts = frozenset(
            normalized
            for host in approved_hosts
            if (normalized := host.strip().lower().rstrip("."))
        )

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if query.website is None:
            return ProviderResult(documents=())

        website = str(query.website)
        canonical_origin = self._canonical_origin(website)
        if canonical_origin is None:
            return ProviderResult(documents=())
        origin, normalized_host = canonical_origin
        query_allowed_hosts = frozenset(
            host.lower().rstrip(".") for host in query.allowed_hosts
        )
        if (
            normalized_host not in self.approved_hosts
            or normalized_host not in query_allowed_hosts
        ):
            return ProviderResult(documents=())
        seeds = tuple(urljoin(origin, path) for path in _SEED_PATHS)

        for seed in seeds:
            if not await self._robots_policy.can_fetch(seed):
                return ProviderResult(documents=(), warnings=("robots_disallowed",))

        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
        seen = set(seeds)
        documents: list[RawDocument] = []
        warnings: list[str] = []
        fetch_count = 0
        page_limit = min(_MAX_PAGES, query.max_results)

        async def redirect_allowed(target: str) -> bool:
            candidate = self._normalize_link(origin, target, normalized_host)
            return candidate is not None and await self._robots_policy.can_fetch(candidate)

        while queue and fetch_count < page_limit:
            url, depth = queue.popleft()
            fetch_count += 1
            try:
                fetched = await self._http_client.get_text(
                    url,
                    allowed_hosts={normalized_host},
                    redirect_validator=redirect_allowed,
                )
            except ProviderError as error:
                self._warn_once(warnings, f"page_failed:{error.code}")
                continue

            if not self._same_host(fetched.url, normalized_host):
                self._warn_once(warnings, "page_failed:unsafe_redirect")
                continue

            challenge = self._access_challenge(fetched)
            if challenge is not None:
                self._warn_once(warnings, challenge)
                continue

            documents.append(
                RawDocument(
                    provider=self.name,
                    external_id=None,
                    url=HttpUrl(fetched.url),
                    title=fetched.title,
                    text=fetched.text,
                    published_at=None,
                    authority_level=1,
                )
            )

            if depth >= _MAX_DEPTH:
                continue
            for link in fetched.links:
                candidate = self._normalize_link(fetched.url, link, normalized_host)
                if candidate is None or candidate in seen:
                    continue
                seen.add(candidate)
                if await self._robots_policy.can_fetch(candidate):
                    queue.append((candidate, depth + 1))
                else:
                    self._warn_once(warnings, "robots_disallowed")

        return ProviderResult(
            documents=tuple(documents),
            truncated=bool(queue),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _same_host(url: str, host: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.hostname.lower().rstrip(".") == host
            and parsed.username is None
        )

    @staticmethod
    def _canonical_origin(url: str) -> tuple[str, str] | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        host = parsed.hostname
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or host is None or parsed.username is not None:
            return None

        normalized_host = host.lower().rstrip(".")
        default_port = 443 if scheme == "https" else 80
        display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        netloc = display_host if port is None or port == default_port else f"{display_host}:{port}"
        return urlunsplit((scheme, netloc, "/", "", "")), normalized_host

    @classmethod
    def _normalize_link(cls, base_url: str, link: str, host: str) -> str | None:
        try:
            parsed = urlsplit(urljoin(base_url, link))
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.lower().rstrip(".") != host
            or parsed.username is not None
            or not cls._eligible_path(parsed.path)
        ):
            return None

        normalized_host = parsed.hostname.lower().rstrip(".")
        scheme = parsed.scheme.lower()
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        netloc = display_host if port is None or default_port else f"{display_host}:{port}"
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((scheme, netloc, path, parsed.query, ""))

    @staticmethod
    def _eligible_path(path: str) -> bool:
        normalized = path.rstrip("/") or "/"
        return any(normalized == seed or normalized.startswith(f"{seed}/") for seed in _SEED_PATHS)

    @staticmethod
    def _access_challenge(document: HttpDocument) -> str | None:
        path = urlsplit(document.url).path.lower().rstrip("/")
        content = f"{document.title or ''}\n{document.text}".lower()
        if path.endswith(("/login", "/signin", "/sign-in")) or any(
            marker in content for marker in ("login required", "sign in to continue")
        ):
            return "login_required"
        if any(
            marker in content
            for marker in ("captcha", "verify you are human", "human verification")
        ):
            return "captcha_required"
        return None

    @staticmethod
    def _warn_once(warnings: list[str], warning: str) -> None:
        if warning not in warnings:
            warnings.append(warning)
