"""Connections to Microsoft's sign-in servers that never wait on a broken IPv6 route.

MSAL connects through urllib3, which tries each address of a server in turn and gives
each one the whole timeout. login.microsoftonline.com has eight IPv6 addresses: on a
network that announces IPv6 without routing it, every sign-in step waited 8 x 30 s
before reaching IPv4. Browsers race both families and never showed the problem.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from typing import Any

# A reachable Microsoft server answers in well under a second.
CONNECT_TIMEOUT_SECONDS = 5.0

AddressInfo = tuple[Any, ...]
_installed = False


def ipv4_first(infos: Sequence[AddressInfo]) -> list[AddressInfo]:
    """Order getaddrinfo results with IPv4 first, keeping each family's order."""
    unique = list(dict.fromkeys(infos))
    return [info for info in unique if info[0] == socket.AF_INET] + [
        info for info in unique if info[0] != socket.AF_INET
    ]


def ipv4_first_connection(
    original: Callable[..., socket.socket],
    getaddrinfo: Callable[..., list[AddressInfo]] = socket.getaddrinfo,
) -> Callable[..., socket.socket]:
    """Wrap urllib3's create_connection: IPv4 first, a short wait per address."""

    def create_connection(
        address: tuple[str, int],
        timeout: object = None,
        *args: object,
        **kwargs: object,
    ) -> socket.socket:
        host, port = address
        try:
            infos = ipv4_first(getaddrinfo(host.strip("[]"), port, 0, socket.SOCK_STREAM))
        except OSError:
            return original(address, timeout, *args, **kwargs)
        if not infos:
            return original(address, timeout, *args, **kwargs)
        # urllib3 passes a sentinel object when no timeout is set.
        attempt_timeout = (
            min(timeout, CONNECT_TIMEOUT_SECONDS) if isinstance(timeout, int | float) else timeout
        )
        error: OSError | None = None
        for info in infos:
            try:
                return original((info[4][0], port), attempt_timeout, *args, **kwargs)
            except OSError as exc:
                error = exc
        raise error if error is not None else OSError("getaddrinfo returned no address")

    return create_connection


def install_ipv4_first_connections() -> None:
    """Apply the IPv4-first connection to every urllib3 connection (only MSAL uses it)."""
    global _installed  # noqa: PLW0603
    if _installed:
        return
    from urllib3.util import connection  # noqa: PLC0415 - only needed with MSAL

    connection.create_connection = ipv4_first_connection(connection.create_connection)
    _installed = True
