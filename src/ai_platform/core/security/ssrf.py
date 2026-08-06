"""SSRF protection — URL validation helpers.

Prevents user-supplied URLs from resolving to internal/private networks
(RFC 1918, loopback, link-local, reserved ranges, etc.).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


ALLOWED_SCHEMES = ("http", "https")

# Block-list of hostnames that should never be reachable from agent tools.
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata.internal"}


def validate_url(url: str) -> str:
    """Validate a URL is safe to request from server-side code.

    Raises ``ValueError`` if the URL uses a disallowed scheme, resolves to a
    private/reserved IP, or targets a blocked hostname.

    Returns the original URL on success.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL is missing a hostname")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError(f"Blocked hostname: {hostname}")

    # Resolve DNS and reject private/reserved IPs
    try:
        # getaddrinfo returns a list of 5-tuples; we take the first address.
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname: {hostname}") from exc

    for addrinfo in addrinfos:
        ip_str = addrinfo[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise ValueError(
                f"URL resolves to private/reserved IP: {ip_obj}"
            )

    return url
