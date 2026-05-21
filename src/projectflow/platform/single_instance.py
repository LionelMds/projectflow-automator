from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import cast

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

INSTANCE_SERVER_NAME = "ch.balzmetal.projectflow.automator"
_CONNECT_TIMEOUT_MS = 250


class SingleInstanceGuard(QObject):
    activation_requested = Signal(str)

    def __init__(
        self,
        *,
        server_name: str = INSTANCE_SERVER_NAME,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._server_name = server_name
        self._server = QLocalServer(self)
        self._lock = QLockFile(str(_lock_path(server_name)))
        self._connections: set[QLocalSocket] = set()
        self._has_lock = False
        self._server.newConnection.connect(self._handle_connection)

    @property
    def server_name(self) -> str:
        return self._server_name

    def listen(self) -> bool:
        if not self._lock.tryLock(0):
            notify_existing_instance(self._server_name)
            return False
        self._has_lock = True
        if self._server.listen(self._server_name):
            return True
        QLocalServer.removeServer(self._server_name)
        self._server.listen(self._server_name)
        return True

    def release(self) -> None:
        self._server.close()
        if self._has_lock:
            self._lock.unlock()
            self._has_lock = False
        QLocalServer.removeServer(self._server_name)

    def _handle_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.setParent(self)
            self._connections.add(socket)
            socket.disconnected.connect(lambda sock=socket: self._connections.discard(sock))
            socket.readyRead.connect(lambda sock=socket: self._read_command(sock))
            if socket.bytesAvailable() > 0:
                self._read_command(socket)

    def _read_command(self, socket: QLocalSocket) -> None:
        raw_command = cast("bytes", socket.readAll().data()).decode(
            "utf-8",
            errors="ignore",
        ).strip()
        self.activation_requested.emit(raw_command or "show")
        socket.disconnectFromServer()


def notify_existing_instance(
    server_name: str = INSTANCE_SERVER_NAME,
    *,
    command: str = "show",
) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    socket.write(command.encode("utf-8"))
    socket.flush()
    QCoreApplication.processEvents()
    QThread.msleep(100)
    QCoreApplication.processEvents()
    socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


def _lock_path(server_name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", server_name).strip("-")
    return Path(tempfile.gettempdir()) / f"{safe_name}.lock"
