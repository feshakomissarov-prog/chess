"""Profile-scoped single-instance activation over Qt's local socket."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QObject, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard(QObject):
    """Own the local server for one data profile and activate its existing window."""

    def __init__(self, profile_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        canonical = str(profile_path.expanduser().resolve()).casefold()
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        self.server_name = f"ChessCoach-{digest}"
        self.server = QLocalServer(self)
        self._activation_callback: Callable[[], None] | None = None
        self._activation_pending = False
        self.server.newConnection.connect(self._accept_connection)

        client = QLocalSocket(self)
        client.connectToServer(self.server_name)
        if client.waitForConnected(250):
            self._send_activation(client)
            client.disconnectFromServer()
            self.is_primary = False
            return

        # Qt's documented stale endpoint cleanup. If another process wins the
        # listen race, retry as a client before reporting a lock failure.
        if client.error() not in {
            QLocalSocket.LocalSocketError.ServerNotFoundError,
            QLocalSocket.LocalSocketError.ConnectionRefusedError,
        }:
            retry = QLocalSocket()
            retry.connectToServer(self.server_name)
            if retry.waitForConnected(500):
                self._send_activation(retry)
                retry.disconnectFromServer()
                self.is_primary = False
                return
            raise OSError("Не удалось проверить открытый профиль; повторный запуск заблокирован")
        QLocalServer.removeServer(self.server_name)
        self.is_primary = bool(self.server.listen(self.server_name))
        if not self.is_primary:
            retry = QLocalSocket(self)
            retry.connectToServer(self.server_name)
            if retry.waitForConnected(250):
                self._send_activation(retry)
                retry.disconnectFromServer()
                self.is_primary = False
                return
            raise OSError(f"Не удалось занять профиль приложения: {self.server.errorString()}")

    @staticmethod
    def _send_activation(socket: QLocalSocket) -> None:
        socket.write(b"activate\n")
        socket.flush()
        socket.waitForBytesWritten(250)
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()

    def set_activation_callback(self, callback: Callable[[], None]) -> None:
        self._activation_callback = callback
        if self._activation_pending:
            self._activation_pending = False
            callback()

    @Slot()
    def _accept_connection(self) -> None:
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            if connection is None:
                continue
            connection.readyRead.connect(lambda socket=connection: self._read_activation(socket))
            if connection.bytesAvailable():
                self._read_activation(connection)

    @Slot()
    def _read_activation(self, socket: QLocalSocket) -> None:
        message = bytes(socket.readAll())
        if b"activate" in message:
            callback = self._activation_callback
            if callback is None:
                self._activation_pending = True
            else:
                callback()
            socket.write(b"ok\n")
            socket.flush()
        socket.disconnectFromServer()

    def close(self) -> None:
        if self.is_primary:
            self.server.close()
            QLocalServer.removeServer(self.server_name)


def acquire_single_instance(profile_path: Path) -> SingleInstanceGuard | None:
    """Return the profile owner; return None after activating an existing process."""
    guard = SingleInstanceGuard(profile_path)
    return guard if guard.is_primary else None
