"""Network safety checks for provider HTTP requests."""

import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import ip_address
from typing import cast

from app.ingestion.errors import ProviderError

DnsResolver = Callable[[str], Awaitable[Sequence[str]]]


def is_public_ip(address: str) -> bool:
    """Return whether an address is globally routable, for both IP families."""
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return parsed.is_global and not (
        parsed.is_loopback
        or parsed.is_private
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_reserved
    )


async def resolve_host(host: str) -> Sequence[str]:
    """Resolve a hostname without allowing the caller to bypass validation."""
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise ProviderError(code="dns_failure", retryable=True, detail=str(error)) from error

    return tuple(cast(str, record[4][0]) for record in records)
