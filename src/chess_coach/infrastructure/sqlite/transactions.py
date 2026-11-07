"""Shared SQLite connection and transaction ownership policy."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class SQLiteUnitOfWork:
    """Open short-lived connections and guarantee commit/rollback plus close.

    The repository's existing single-writer executor remains the only asynchronous
    write lane. This unit of work does not add pooling, WAL, or another database.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self.path = str(path)
        self.timeout = float(timeout)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager

    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
