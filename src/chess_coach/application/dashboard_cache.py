"""Compact, versioned two-tier persistence for completed-game Home read models."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import uuid
from contextlib import contextmanager

from ..domain.accuracy import FORMULA_ID
from ..infrastructure.sqlite.constants import SCHEMA_VERSION


_CACHE_FORMAT = 2
_MAX_SCOPES = 12
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_GRAPH_POINTS = 64
_METRIC_KEYS = {"games", "accuracy", "analysis", "errors"}
_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _scope_key(account_id: str | None, clock: str | None) -> str:
    return json.dumps({"account": account_id, "clock": clock,
                       "formula": FORMULA_ID, "schema": SCHEMA_VERSION,
                       "format": _CACHE_FORMAT},
                      sort_keys=True, separators=(",", ":"))


def _scope_matches(value: object, account_id: str | None, clock: str | None) -> bool:
    return (isinstance(value, dict) and
            value.get("account_id") == account_id and value.get("clock") == clock)


def _valid_metrics(metrics: object, allowed: set[str]) -> bool:
    if not isinstance(metrics, list) or len(metrics) > len(_METRIC_KEYS):
        return False
    seen: set[str] = set()
    for item in metrics:
        if not isinstance(item, dict):
            return False
        key = item.get("key")
        if (key not in allowed or key in seen or
                any(not isinstance(item.get(field), str)
                    for field in ("label", "value", "detail", "source"))):
            return False
        if any(len(item[field]) > 2000 for field in ("label", "value", "detail", "source")):
            return False
        seen.add(key)
    return True


def _valid_game_shell(game: object) -> bool:
    if not isinstance(game, dict):
        return False
    required_text = ("game_id", "status")
    if any(not isinstance(game.get(key), str) or len(game[key]) > 200
           for key in required_text):
        return False
    if not (game.get("winner") is None or game.get("winner") in {"white", "black"}):
        return False
    for key in ("color", "account_color"):
        if game.get(key) is not None and game.get(key) not in {"white", "black"}:
            return False
    for key in ("account_id", "account_opponent", "opponent", "account_player_name",
                "player_name", "started_at", "clock", "opening_eco", "opening_name",
                "source", "source_game_id"):
        value = game.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 500):
            return False
    if game.get("line_revision") is not None and (
            type(game.get("line_revision")) is not int or game["line_revision"] < 0):
        return False
    metrics = game.get("review_metrics")
    if metrics is not None:
        if not isinstance(metrics, dict):
            return False
        allowed = {"accuracy", "accuracy_source", "errors", "inaccuracies", "mistakes",
                   "blunders", "strong", "status", "coverage", "source", "strong_detail",
                   "owner_accuracy", "opponent_accuracy", "owner_inaccuracies",
                   "owner_mistakes", "owner_blunders", "opponent_inaccuracies",
                   "opponent_mistakes", "opponent_blunders", "opponent_coverage",
                   "graph_points", "total_positions"}
        if set(metrics) - allowed:
            return False
        for key, value in metrics.items():
            if key == "graph_points":
                if not isinstance(value, list) or len(value) > _MAX_GRAPH_POINTS:
                    return False
                for point in value:
                    if (not isinstance(point, dict) or type(point.get("ply")) is not int
                            or (point.get("white_cp") is not None
                                and type(point.get("white_cp")) is not int)
                            or (point.get("mate_white") is not None
                                and type(point.get("mate_white")) is not int)):
                        return False
            elif key == "total_positions":
                if type(value) is not int or value < 0:
                    return False
            elif value is not None and (not isinstance(value, str) or len(value) > 500):
                return False
    return True


def _valid_tier_a(tier: object, account_id: str | None, clock: str | None) -> bool:
    if not isinstance(tier, dict) or not _scope_matches(tier.get("filter"), account_id, clock):
        return False
    if not isinstance(tier.get("scope"), str) or len(tier["scope"]) > 300:
        return False
    if not _valid_metrics(tier.get("metrics"), {"games"}):
        return False
    games = tier.get("recent_games")
    if not isinstance(games, list) or len(games) > 3 or not all(
            _valid_game_shell(game) for game in games):
        return False
    return isinstance(tier.get("is_preview", False), bool)


def _valid_tier_b(tier: object) -> bool:
    if not isinstance(tier, dict):
        return False
    if not _valid_metrics(tier.get("metrics"), _METRIC_KEYS - {"games"}):
        return False
    rating = tier.get("puzzle_player_rating", {})
    if not isinstance(rating, dict) or set(rating) - {"latest", "date", "points"}:
        return False
    if rating.get("latest") is not None and type(rating.get("latest")) not in {int, float}:
        return False
    if rating.get("date") is not None and (
            not isinstance(rating.get("date"), str) or len(rating["date"]) > 40):
        return False
    points = rating.get("points", [])
    if not isinstance(points, list) or len(points) > 90:
        return False
    for point in points:
        if (not isinstance(point, dict) or
                not isinstance(point.get("day"), str) or type(point.get("rating")) not in {int, float}
                or (point.get("attempts") is not None and type(point.get("attempts")) is not int)):
            return False
    trend = tier.get("activity_trend", [])
    if not isinstance(trend, list) or len(trend) > 24:
        return False
    for item in trend:
        if (not isinstance(item, dict) or not isinstance(item.get("month"), str)
                or type(item.get("attempts")) is not int or type(item.get("wins")) is not int):
            return False
    recent = tier.get("recent_metrics", [])
    if not isinstance(recent, list) or len(recent) > 3:
        return False
    for item in recent:
        if (not isinstance(item, dict) or not isinstance(item.get("game_id"), str)
                or type(item.get("line_revision")) is not int
                or not _valid_game_shell({
                    "game_id": item["game_id"], "status": "complete",
                    "review_metrics": item.get("review_metrics")})):
            return False
    return isinstance(tier.get("activity_trend_source"), (str, type(None)))


def _split_snapshot(snapshot: dict) -> tuple[dict, dict]:
    metrics = snapshot.get("metrics") if isinstance(snapshot, dict) else None
    tier_a_metrics = [item for item in metrics or [] if item.get("key") == "games"]
    tier_b_metrics = [item for item in metrics or [] if item.get("key") != "games"]
    recent_games = []
    recent_metrics = []
    for original in (snapshot.get("recent_games") or [])[:3]:
        game = dict(original)
        review_metrics = game.pop("review_metrics", None)
        # Tier A only carries the fields needed to show and open the first screen.
        shell = {key: game.get(key) for key in (
            "game_id", "status", "winner", "color", "account_color", "account_id",
            "account_opponent", "opponent", "account_player_name", "player_name",
            "started_at", "clock", "opening_eco", "opening_name", "source",
            "source_game_id", "line_revision")}
        if isinstance(review_metrics, dict):
            recent_metrics.append({
                "game_id": str(shell.get("game_id") or ""),
                "line_revision": int(shell.get("line_revision") or 0),
                "review_metrics": _compact_review_metrics(review_metrics),
            })
        recent_games.append(shell)
    tier_a = {
        "scope": str(snapshot.get("scope") or ""),
        "filter": dict(snapshot.get("filter") or {}),
        "metrics": tier_a_metrics,
        "recent_games": recent_games,
        "is_preview": bool(snapshot.get("is_preview", False)),
    }
    tier_b = {
        "metrics": tier_b_metrics,
        "puzzle_player_rating": _compact_rating(snapshot.get("puzzle_player_rating") or {}),
        "activity_trend": list(snapshot.get("activity_trend") or [])[-24:],
        "activity_trend_source": snapshot.get("activity_trend_source"),
        "recent_metrics": recent_metrics,
    }
    return tier_a, tier_b


def _compact_review_metrics(metrics: dict) -> dict:
    result = {key: metrics.get(key) for key in (
        "accuracy", "accuracy_source", "errors", "inaccuracies", "mistakes", "blunders",
        "strong", "status", "coverage", "source", "strong_detail", "owner_accuracy",
        "opponent_accuracy", "owner_inaccuracies", "owner_mistakes", "owner_blunders",
        "opponent_inaccuracies", "opponent_mistakes", "opponent_blunders",
        "opponent_coverage", "total_positions")}
    graph = metrics.get("graph_points") or []
    result["graph_points"] = [
        {"ply": int(row["ply"]),
         "white_cp": int(row["white_cp"]) if row.get("white_cp") is not None else None,
         "mate_white": int(row["mate_white"]) if row.get("mate_white") is not None else None}
        for row in graph[-_MAX_GRAPH_POINTS:]
        if isinstance(row, dict) and type(row.get("ply")) is int
    ]
    return {key: value for key, value in result.items() if value is not None}


def _compact_rating(rating: dict) -> dict:
    points = rating.get("points")
    if not isinstance(points, list):
        points = []
    compact = {"latest": rating.get("latest"), "date": rating.get("date"),
               "points": [dict(point) for point in points[-90:] if isinstance(point, dict)]}
    return compact if compact["latest"] is not None or compact["date"] is not None or compact["points"] else {}


def _merge_tiers(tier_a: dict, tier_b: dict | None) -> dict:
    snapshot = {
        "scope": tier_a["scope"], "filter": dict(tier_a["filter"]),
        "metrics": list(tier_a["metrics"]), "recent_games": [
            dict(game, review_metrics={"status": "loading", "coverage": "—", "source": "—"})
            for game in tier_a["recent_games"]],
        "puzzle_player_rating": {}, "activity_trend": [],
    }
    if tier_a.get("is_preview"):
        snapshot["is_preview"] = True
    if not isinstance(tier_b, dict):
        return snapshot
    # Tier B is committed only after the complete aggregate finished. Once it
    # exists alongside this base tier, the merged view is no longer a preview.
    snapshot.pop("is_preview", None)
    snapshot["metrics"].extend(tier_b["metrics"])
    snapshot["puzzle_player_rating"] = tier_b["puzzle_player_rating"]
    snapshot["activity_trend"] = tier_b["activity_trend"]
    snapshot["activity_trend_source"] = tier_b.get("activity_trend_source")
    by_id = {item["game_id"]: item for item in tier_b["recent_metrics"]}
    for game in snapshot["recent_games"]:
        saved = by_id.get(str(game.get("game_id")))
        if saved and saved.get("line_revision") == (game.get("line_revision") or 0):
            game["review_metrics"] = saved["review_metrics"]
    return snapshot


class DashboardSnapshotCache:
    """Atomic profile-scoped Tier A/Tier B cache; SQLite remains authoritative."""

    def __init__(self, repository) -> None:
        db_path = Path(str(repository.path))
        self.path = db_path.with_name(db_path.name + ".home-cache.json")
        self.previous_path = self.path.with_name(self.path.name + ".previous")
        self.repository = repository
        self._lock = _lock_for(self.path)
        self.last_save_status = "not_saved"
        self.last_save_bytes = 0

    @contextmanager
    def _process_lock(self):
        """Serialize cache reads/writes across windows/processes as well as threads."""
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read_document(self, expected_revision: int) -> dict | None:
        for candidate in (self.path, self.previous_path):
            try:
                if not candidate.is_file() or candidate.stat().st_size > _MAX_CACHE_BYTES:
                    continue
                document = json.loads(candidate.read_text(encoding="utf-8"))
                if (not isinstance(document, dict)
                        or document.get("format") != _CACHE_FORMAT
                        or document.get("schema_version") != SCHEMA_VERSION
                        or document.get("formula_id") != FORMULA_ID
                        or document.get("revision") != int(expected_revision)
                        or not isinstance(document.get("scopes"), dict)):
                    continue
                return document
            except (OSError, ValueError, TypeError):
                continue
        return None

    def load(self, account_id: str | None, clock: str | None, *,
             expected_revision: int | None = None) -> dict | None:
        try:
            revision = (self.repository.dashboard_data_revision()
                        if expected_revision is None else int(expected_revision))
            with self._lock, self._process_lock():
                document = self._read_document(revision)
            if (document is None
                    or self.repository.dashboard_data_revision() != revision):
                return None
            entry = document["scopes"].get(_scope_key(account_id, clock))
            if not isinstance(entry, dict):
                return None
            tier_a = entry.get("tier_a")
            tier_b = entry.get("tier_b")
            if not _valid_tier_a(tier_a, account_id, clock):
                return None
            if tier_b is not None and not _valid_tier_b(tier_b):
                return None
            return _merge_tiers(tier_a, tier_b)
        except Exception:
            return None

    def save(self, account_id: str | None, clock: str | None,
             revision: int, snapshot: dict) -> bool:
        try:
            tier_a, tier_b = _split_snapshot(snapshot)
            if not _valid_tier_a(tier_a, account_id, clock) or not _valid_tier_b(tier_b):
                self.last_save_status = "invalid_schema"
                return False
            with self._lock, self._process_lock():
                document = self._read_document(int(revision)) or {
                    "format": _CACHE_FORMAT, "schema_version": SCHEMA_VERSION,
                    "formula_id": FORMULA_ID, "revision": int(revision), "scopes": {}}
                entry = document["scopes"].setdefault(
                    _scope_key(account_id, clock), {"tier_a": None, "tier_b": None})
                entry["tier_a"] = tier_a
                entry["tier_b"] = tier_b
                while len(document["scopes"]) > _MAX_SCOPES:
                    document["scopes"].pop(next(iter(document["scopes"])))
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                size = len(encoded.encode("utf-8"))
                self.last_save_bytes = size
                if size > _MAX_CACHE_BYTES:
                    self.last_save_status = "oversized"
                    return False
                if self.repository.dashboard_data_revision() != int(revision):
                    self.last_save_status = "revision_changed"
                    return False
                self._atomic_write(encoded)
            self.last_save_status = "saved"
            return self.repository.dashboard_data_revision() == int(revision)
        except (OSError, TypeError, ValueError, AttributeError):
            self.last_save_status = "io_error"
            return False

    def save_tier_a(self, account_id: str | None, clock: str | None,
                    revision: int, snapshot: dict) -> bool:
        tier_a, _ = _split_snapshot(snapshot)
        if not _valid_tier_a(tier_a, account_id, clock):
            self.last_save_status = "invalid_tier_a"
            return False
        return self._save_tier(account_id, clock, revision, "tier_a", tier_a)

    def save_tier_b(self, account_id: str | None, clock: str | None,
                    revision: int, snapshot: dict) -> bool:
        _, tier_b = _split_snapshot(snapshot)
        if not _valid_tier_b(tier_b):
            self.last_save_status = "invalid_tier_b"
            return False
        return self._save_tier(account_id, clock, revision, "tier_b", tier_b)

    def _save_tier(self, account_id, clock, revision, tier_name, tier) -> bool:
        try:
            if self.repository.dashboard_data_revision() != int(revision):
                self.last_save_status = "revision_changed"
                return False
            with self._lock, self._process_lock():
                document = self._read_document(int(revision)) or {
                    "format": _CACHE_FORMAT, "schema_version": SCHEMA_VERSION,
                    "formula_id": FORMULA_ID, "revision": int(revision), "scopes": {}}
                entry = document["scopes"].setdefault(
                    _scope_key(account_id, clock), {"tier_a": None, "tier_b": None})
                entry[tier_name] = tier
                while len(document["scopes"]) > _MAX_SCOPES:
                    document["scopes"].pop(next(iter(document["scopes"])))
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                size = len(encoded.encode("utf-8"))
                self.last_save_bytes = size
                if size > _MAX_CACHE_BYTES:
                    self.last_save_status = "oversized"
                    return False
                if self.repository.dashboard_data_revision() != int(revision):
                    self.last_save_status = "revision_changed"
                    return False
                self._atomic_write(encoded)
            self.last_save_status = "saved"
            current = self.repository.dashboard_data_revision() == int(revision)
            if not current:
                self.last_save_status = "revision_changed"
            return current
        except (OSError, TypeError, ValueError, AttributeError):
            self.last_save_status = "io_error"
            return False

    def _atomic_write(self, encoded: str) -> None:
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        backup_temporary = self.previous_path.with_name(
            self.previous_path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            if self.path.exists():
                backup_temporary.write_bytes(self.path.read_bytes())
                os.replace(backup_temporary, self.previous_path)
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            for path in (temporary, backup_temporary):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
