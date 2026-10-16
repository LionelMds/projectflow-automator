from __future__ import annotations

import socket

import pytest

from projectflow.auth.network import CONNECT_TIMEOUT_SECONDS, ipv4_first, ipv4_first_connection

V6_A = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2603:1027::21", 443, 0, 0))
V6_B = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2603:1027::25", 443, 0, 0))
V4_A = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("20.190.1.1", 443))
V4_B = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("20.190.1.2", 443))


class FakeConnect:
    def __init__(self, *, unreachable: set[str]) -> None:
        self.unreachable = unreachable
        self.attempts: list[tuple[str, object]] = []

    def __call__(
        self,
        address: tuple[str, int],
        timeout: object,
        *_args: object,
        **_kwargs: object,
    ) -> str:
        self.attempts.append((address[0], timeout))
        if address[0] in self.unreachable:
            raise TimeoutError("timed out")
        return f"socket:{address[0]}"


def test_ipv4_addresses_come_first_without_duplicates() -> None:
    assert ipv4_first([V6_A, V4_A, V6_B, V4_B, V4_A]) == [V4_A, V4_B, V6_A, V6_B]


def test_unrouted_ipv6_is_never_waited_for_when_ipv4_works() -> None:
    # The Mac's network: eight IPv6 addresses listed first, none of them reachable.
    connect = FakeConnect(unreachable={"2603:1027::21", "2603:1027::25"})
    create = ipv4_first_connection(connect, lambda *_args: [V6_A, V6_B, V4_A])

    assert create(("login.microsoftonline.com", 443), 30) == "socket:20.190.1.1"
    assert connect.attempts == [("20.190.1.1", CONNECT_TIMEOUT_SECONDS)]


def test_next_address_is_tried_after_a_failure() -> None:
    connect = FakeConnect(unreachable={"20.190.1.1"})
    create = ipv4_first_connection(connect, lambda *_args: [V6_A, V4_A, V4_B])

    assert create(("login.microsoftonline.com", 443), 2) == "socket:20.190.1.2"
    assert connect.attempts == [("20.190.1.1", 2), ("20.190.1.2", 2)]


def test_last_error_is_raised_when_nothing_answers() -> None:
    connect = FakeConnect(unreachable={"20.190.1.1", "2603:1027::21"})
    create = ipv4_first_connection(connect, lambda *_args: [V6_A, V4_A])

    with pytest.raises(TimeoutError):
        create(("login.microsoftonline.com", 443), 30)


def test_unresolved_host_and_default_timeout_are_left_to_urllib3() -> None:
    sentinel = object()
    connect = FakeConnect(unreachable=set())

    def unresolved(*_args: object) -> list[tuple[object, ...]]:
        raise socket.gaierror("no such host")

    assert ipv4_first_connection(connect, unresolved)(("example.invalid", 443), sentinel) == (
        "socket:example.invalid"
    )
    assert ipv4_first_connection(connect, lambda *_args: [V4_A])(("host", 443), sentinel)
    assert connect.attempts == [("example.invalid", sentinel), ("20.190.1.1", sentinel)]
