from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

ALLOWED_SOURCE_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "huggingface.co",
        "zenodo.org",
        "www.zenodo.org",
    }
)


class UnsafeSourceUrl(ValueError):
    pass


def validate_source_url(raw_url: str, allowed_hosts: Iterable[str] = ALLOWED_SOURCE_HOSTS) -> str:
    """Validate a source URL before it is routed to a source-specific adapter."""
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https":
        raise UnsafeSourceUrl("source URLs must use HTTPS")
    if parsed.username or parsed.password:
        raise UnsafeSourceUrl("source URLs must not contain credentials")
    if parsed.port not in (None, 443):
        raise UnsafeSourceUrl("source URLs must use the default HTTPS port")
    if host not in set(allowed_hosts):
        raise UnsafeSourceUrl("source host is not allowlisted")
    return raw_url


def ensure_public_addresses(addresses: Iterable[str]) -> None:
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeSourceUrl("source host resolved to a non-public address")


def resolve_public_host(host: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = tuple(sorted({record[4][0] for record in records}))
    if not addresses:
        raise UnsafeSourceUrl("source host did not resolve")
    ensure_public_addresses(addresses)
    return addresses
