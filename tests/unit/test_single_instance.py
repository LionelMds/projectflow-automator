from __future__ import annotations

from uuid import uuid4

from PySide6.QtNetwork import QLocalServer

from projectflow.platform.single_instance import (
    SingleInstanceGuard,
    notify_existing_instance,
)


def test_single_instance_guard_receives_activation(qtbot) -> None:  # type: ignore[no-untyped-def]
    server_name = f"projectflow-test-{uuid4()}"
    guard = SingleInstanceGuard(server_name=server_name)
    received: list[str] = []
    guard.activation_requested.connect(received.append)

    try:
        assert guard.listen() is True
        assert notify_existing_instance(server_name, command="show") is True

        qtbot.waitUntil(lambda: received == ["show"], timeout=1000)
    finally:
        guard.release()
        QLocalServer.removeServer(server_name)


def test_second_guard_forwards_to_existing_instance(qtbot) -> None:  # type: ignore[no-untyped-def]
    server_name = f"projectflow-test-{uuid4()}"
    first_guard = SingleInstanceGuard(server_name=server_name)
    second_guard = SingleInstanceGuard(server_name=server_name)
    received: list[str] = []
    first_guard.activation_requested.connect(received.append)

    try:
        assert first_guard.listen() is True
        assert second_guard.listen() is False

        qtbot.waitUntil(lambda: received == ["show"], timeout=1000)
    finally:
        first_guard.release()
        second_guard.release()
        QLocalServer.removeServer(server_name)
