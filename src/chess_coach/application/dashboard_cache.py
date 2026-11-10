"""Versioned, profile-scoped persistence for completed-game Home analytics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

from ..domain.accuracy import FORMULA_ID


_CACHE_FORMAT = 1
_MAX_SCOPES = 12
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _scope_key(account_id: str | None, clock: str | None) -> str:
    return json.dumps({"account": account_id, "clock": clock,
                       "formula": FORMULA_ID, "format": _CACHE_FORMAT},
                      sort_keys=True, separators=(",", ":"))


class DashboardSnapshotCache:
    """Atomic sidecar cache; SQLite stays the source of truth and revision fence."""

    def __init__(self, repository) -> None:
        db_path = Path(str(repository.path))
        self.path = db_path.with_name(db_path.name + ".home-cache.json")
        self.repository = repository
        self._lock = _lock_for(self.path)

    def load(self, account_id: str | None, clock: str | None) -> dict | None:
        try:
            revision = self.repository.dashboard_data_revision()
            with self._lock:
                if not self.path.is_file() or self.path.stat().st_size > _MAX_CACHE_BYTES:
                    return None
                document = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(document, dict)
                    or document.get("format") != _CACHE_FORMAT
                    or document.get("revision") != revision):
                return None
            value = document.get("scopes", {}).get(_scope_key(account_id, clock))
            if not isinstance(value, dict):
                return None
            snapshot = value.get("snapshot")
            if not isinstance(snapshot, dict):
                return None
            scope = snapshot.get("filter") or {}
            if (scope.get("account_id") != account_id
                    or scope.get("clock") != clock):
                return None
            return snapshot
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save(self, account_id: str | None, clock: str | None,
             revision: int, snapshot: dict) -> bool:
        try:
            if self.repository.dashboard_data_revision() != int(revision):
                return False
            with self._lock:
                try:
                    document = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    document = {}
                if (not isinstance(document, dict)
                        or document.get("format") != _CACHE_FORMAT
                        or document.get("revision") != int(revision)):
                    document = {"format": _CACHE_FORMAT,
                                "revision": int(revision), "scopes": {}}
                scopes = document.setdefault("scopes", {})
                scopes[_scope_key(account_id, clock)] = {"snapshot": snapshot}
                while len(scopes) > _MAX_SCOPES:
                    scopes.pop(next(iter(scopes)))
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > _MAX_CACHE_BYTES:
                    return False
                temporary = self.path.with_name(
                    self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("w", encoding="utf-8", newline="") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            return self.repository.dashboard_data_revision() == int(revision)
        except (OSError, TypeError, ValueError, AttributeError):
            return False
