"""Compact, versioned two-tier persistence for completed-game Home read models."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from ..domain.accuracy import FORMULA_ID
from ..infrastructure.sqlite.constants import SCHEMA_VERSION


_CACHE_FORMAT = 3
_COMPATIBLE_CACHE_FORMATS = {2, 3}
# Cache payloads are independent of SQLite layout; preserve the last-good
# Home facts across one additive database migration while the worker refreshes.
_COMPATIBLE_SCHEMA_VERSIONS = {SCHEMA_VERSION, SCHEMA_VERSION - 1}
_MAX_SCOPES = 12
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_GRAPH_POINTS = 1024
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


def _document_scope_key(key: object, account_id: str | None,
                        clock: str | None) -> bool:
    try:
        value = json.loads(key) if isinstance(key, str) else None
    except (ValueError, TypeError):
        return False
    return (isinstance(value, dict) and value.get("account") == account_id
            and value.get("clock") == clock and value.get("formula") == FORMULA_ID
            and value.get("schema") in _COMPATIBLE_SCHEMA_VERSIONS
            and value.get("format") in _COMPATIBLE_CACHE_FORMATS)


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
                "source", "source_game_id", "line_hash"):
        value = game.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 500):
            return False
    if game.get("last_move_at") is not None and (
            type(game["last_move_at"]) is not int or game["last_move_at"] < 0):
        return False
    if game.get("game_time_precision") not in {None, "timestamp", "date_only", "unknown"}:
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
                   "white_inaccuracies", "white_mistakes", "white_blunders",
                   "black_inaccuracies", "black_mistakes", "black_blunders",
                   "white_coverage", "black_coverage", "phase_boundaries",
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
                                and type(point.get("mate_white")) is not int)
                            or (point.get("connect_from_previous") is not None
                                and type(point.get("connect_from_previous")) is not bool)
                            or (point.get("mate_start_ply") is not None
                                and type(point.get("mate_start_ply")) is not int)
                            or (point.get("mate_sample_count") is not None
                                and (type(point.get("mate_sample_count")) is not int
                                     or point["mate_sample_count"] < 1))):
                        return False
            elif key == "total_positions":
                if type(value) is not int or value < 0:
                    return False
            elif key == "phase_boundaries":
                if not isinstance(value, dict) or set(value) - {
                        "middle_start_ply", "end_start_ply", "source", "method_version"}:
                    return False
                for boundary in ("middle_start_ply", "end_start_ply"):
                    if value.get(boundary) is not None and (
                            type(value.get(boundary)) is not int or value[boundary] < 0):
                        return False
                for label in ("source", "method_version"):
                    if value.get(label) is not None and (
                            not isinstance(value[label], str) or len(value[label]) > 100):
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
    return _tier_b_validation_issue(tier) is None


def _tier_b_validation_issue(tier: object) -> str | None:
    """Return a stable, non-sensitive reason for rejecting a last-good tier."""
    if not isinstance(tier, dict):
        return "tier_b_not_object"
    if not _valid_metrics(tier.get("metrics"), _METRIC_KEYS - {"games"}):
        return "tier_b_metrics"
    rating = tier.get("puzzle_player_rating", {})
    if not isinstance(rating, dict) or set(rating) - {"latest", "date", "points"}:
        return "puzzle_rating_shape"
    if rating.get("latest") is not None and type(rating.get("latest")) not in {int, float}:
        return "puzzle_rating_latest"
    if rating.get("date") is not None and (
            not isinstance(rating.get("date"), str) or len(rating["date"]) > 40):
        return "puzzle_rating_date"
    points = rating.get("points", [])
    if not isinstance(points, list) or len(points) > 90:
        return "puzzle_rating_points"
    for point in points:
        activity = point.get("attempts") if isinstance(point, dict) else None
        if (not isinstance(point, dict) or
                not isinstance(point.get("day"), str) or type(point.get("rating")) not in {int, float}
                or (activity is not None and not (
                    type(activity) is int or
                    (isinstance(activity, (list, tuple)) and len(activity) == 2
                     and all(type(value) is int for value in activity))))):
            return "puzzle_rating_point"
    trend = tier.get("activity_trend", [])
    if not isinstance(trend, list) or len(trend) > 24:
        return "activity_trend_shape"
    for item in trend:
        if (not isinstance(item, dict) or not isinstance(item.get("month"), str)
                or type(item.get("attempts")) is not int or type(item.get("wins")) is not int):
            return "activity_trend_item"
    recent = tier.get("recent_metrics", [])
    if not isinstance(recent, list) or len(recent) > 3:
        return "recent_metrics_shape"
    for item in recent:
        if (not isinstance(item, dict) or not isinstance(item.get("game_id"), str)
                or type(item.get("line_revision")) is not int
                or (item.get("line_hash") is not None and
                    (not isinstance(item["line_hash"], str) or len(item["line_hash"]) > 200))
                or item.get("account_color") not in {None, "white", "black"}
                or (item.get("source_id") is not None and
                    (not isinstance(item["source_id"], str) or len(item["source_id"]) > 500))
                or not _valid_game_shell({
                    "game_id": item["game_id"], "status": "complete",
                    "review_metrics": item.get("review_metrics")})):
            return "recent_metrics_item"
    if not isinstance(tier.get("activity_trend_source"), (str, type(None))):
        return "activity_trend_source"
    return None


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
            "source_game_id", "line_revision", "line_hash", "last_move_at",
            "game_time_precision")}
        if isinstance(review_metrics, dict):
            recent_metrics.append({
                "game_id": str(shell.get("game_id") or ""),
                "line_revision": int(shell.get("line_revision") or 0),
                "line_hash": shell.get("line_hash"),
                "account_color": shell.get("account_color") or shell.get("color"),
                "source_id": shell.get("source"),
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
        "opponent_coverage", "white_inaccuracies", "white_mistakes", "white_blunders",
        "black_inaccuracies", "black_mistakes", "black_blunders", "white_coverage",
        "black_coverage", "phase_boundaries", "total_positions")}
    graph = metrics.get("graph_points") or []
    result["graph_points"] = [
        {"ply": int(row["ply"]),
         "white_cp": int(row["white_cp"]) if row.get("white_cp") is not None else None,
         "mate_white": int(row["mate_white"]) if row.get("mate_white") is not None else None,
         **({"connect_from_previous": row["connect_from_previous"]}
            if type(row.get("connect_from_previous")) is bool else {}),
         **({"mate_start_ply": int(row["mate_start_ply"])}
            if type(row.get("mate_start_ply")) is int else {}),
         **({"mate_sample_count": int(row["mate_sample_count"])}
            if type(row.get("mate_sample_count")) is int else {})}
        for row in graph[-_MAX_GRAPH_POINTS:]
        if isinstance(row, dict) and type(row.get("ply")) is int
    ]
    return {key: value for key, value in result.items() if value is not None}


def _compact_rating(rating: dict) -> dict:
    points = rating.get("points")
    if not isinstance(points, list):
        points = []
    compact_points = []
    for point in points[-90:]:
        if not isinstance(point, dict):
            continue
        compact_point = dict(point)
        activity = compact_point.get("attempts")
        # Repository results use a (attempts, wins) tuple; JSON cache restores
        # it as a list. Normalize before validation and serialization.
        if isinstance(activity, tuple) and len(activity) == 2:
            compact_point["attempts"] = list(activity)
        compact_points.append(compact_point)
    compact = {"latest": rating.get("latest"), "date": rating.get("date"),
               "points": compact_points}
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
        same_identity = bool(saved and
            saved.get("line_revision") == (game.get("line_revision") or 0) and
            saved.get("line_hash") == game.get("line_hash") and
            saved.get("account_color") == (game.get("account_color") or game.get("color")) and
            saved.get("source_id") == game.get("source"))
        if same_identity:
            game["review_metrics"] = saved["review_metrics"]
    return snapshot


def merge_last_good(current: dict, previous: dict | None, *, stale: bool) -> dict:
    """Overlay prior verified Tier B facts onto the current bounded Tier A rows."""
    result = dict(current)
    if previous:
        old_metrics = {item.get("key"): item for item in previous.get("metrics", [])}
        result["metrics"] = [old_metrics.get(item.get("key"), item)
                             if item.get("key") != "games" else item
                             for item in result.get("metrics", [])]
        if previous.get("puzzle_player_rating"):
            result["puzzle_player_rating"] = previous["puzzle_player_rating"]
        if previous.get("activity_trend"):
            result["activity_trend"] = previous["activity_trend"]
            result["activity_trend_source"] = previous.get("activity_trend_source")
        old_games = {game.get("game_id"): game for game in previous.get("recent_games", [])}
        for game in result.get("recent_games", []):
            old = old_games.get(game.get("game_id"))
            if not old:
                continue
            same_identity = (
                game.get("line_revision") == old.get("line_revision")
                and game.get("line_hash") == old.get("line_hash")
                and (game.get("account_color") or game.get("color"))
                == (old.get("account_color") or old.get("color"))
                and game.get("source") == old.get("source"))
            if same_identity and old.get("review_metrics"):
                game["review_metrics"] = old["review_metrics"]
            elif stale:
                game["review_metrics"] = {
                    "status": "updating", "coverage": "—", "source": "—"}
    result.pop("is_preview", None)
    result["_dashboard_stale"] = bool(stale)
    result["_dashboard_tier_b_saved_at"] = (previous or {}).get(
        "_dashboard_tier_b_saved_at")
    return result


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
        self.last_validation_issue: str | None = None

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

    def _read_document(self, expected_revision: int | None = None) -> dict | None:
        for document in self._read_documents():
            valid = True
            for key, entry in document["scopes"].items():
                scope = None
                try:
                    scope = json.loads(key)
                except (TypeError, ValueError):
                    valid = False
                    break
                if not isinstance(scope, dict):
                    valid = False
                    break
                account_id, clock = scope.get("account"), scope.get("clock")
                if (not _document_scope_key(key, account_id, clock)
                        or not isinstance(entry, dict)
                        or not _valid_tier_a(entry.get("tier_a"), account_id, clock)
                        or (entry.get("tier_b") is not None
                            and not _valid_tier_b(entry.get("tier_b")))):
                    valid = False
                    break
            if valid:
                return document
        return None

    def _read_documents(self) -> list[dict]:
        documents = []
        for candidate in (self.path, self.previous_path):
            try:
                if not candidate.is_file() or candidate.stat().st_size > _MAX_CACHE_BYTES:
                    continue
                document = json.loads(candidate.read_text(encoding="utf-8"))
                if (not isinstance(document, dict)
                        or document.get("format") not in _COMPATIBLE_CACHE_FORMATS
                        or document.get("schema_version") not in _COMPATIBLE_SCHEMA_VERSIONS
                        or document.get("formula_id") != FORMULA_ID
                        or not isinstance(document.get("scopes"), dict)):
                    continue
                saved_revision = int(document.get("revision", -1))
                if document["format"] == 2:
                    scopes = {}
                    for old_key, old_entry in document["scopes"].items():
                        try:
                            old_scope = json.loads(old_key)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(old_scope, dict):
                            continue
                        account_id, clock = old_scope.get("account"), old_scope.get("clock")
                        if not _document_scope_key(old_key, account_id, clock):
                            continue
                        scopes[_scope_key(account_id, clock)] = {
                            **old_entry, "tier_a_revision": saved_revision,
                            "tier_b_revision": saved_revision, "tier_b_saved_at": None}
                    document = {**document, "format": _CACHE_FORMAT, "scopes": scopes}
                else:
                    migrated_scopes = {}
                    for old_key, entry in document["scopes"].items():
                        try:
                            old_scope = json.loads(old_key)
                        except (TypeError, ValueError):
                            continue
                        if (not isinstance(old_scope, dict)
                                or not _document_scope_key(
                                    old_key, old_scope.get("account"), old_scope.get("clock"))):
                            continue
                        new_key = _scope_key(old_scope.get("account"), old_scope.get("clock"))
                        migrated_scopes[new_key] = entry
                    for entry in document["scopes"].values():
                        if isinstance(entry, dict):
                            entry.setdefault("tier_a_revision", saved_revision)
                            entry.setdefault("tier_b_revision", saved_revision)
                            entry.setdefault("tier_b_saved_at", None)
                    document = {**document, "schema_version": SCHEMA_VERSION,
                                "scopes": migrated_scopes}
                documents.append(document)
            except (OSError, ValueError, TypeError):
                continue
        return documents

    @staticmethod
    def _load_from_documents(documents: list[dict], account_id: str | None,
                             clock: str | None) -> dict | None:
        scope_key = _scope_key(account_id, clock)
        for document in documents:
            entry = document["scopes"].get(scope_key)
            if entry is None:
                entry = next((value for key, value in document["scopes"].items()
                              if _document_scope_key(key, account_id, clock)), None)
            if not isinstance(entry, dict):
                continue
            tier_a, tier_b = entry.get("tier_a"), entry.get("tier_b")
            if (not _valid_tier_a(tier_a, account_id, clock)
                    or (tier_b is not None and not _valid_tier_b(tier_b))):
                continue
            saved_revision = int(document.get("revision", -1))
            result = _merge_tiers(tier_a, tier_b)
            result["_dashboard_revision"] = saved_revision
            result["_dashboard_tier_a_revision"] = int(
                entry.get("tier_a_revision", saved_revision))
            result["_dashboard_tier_b_revision"] = int(
                entry.get("tier_b_revision", saved_revision))
            result["_dashboard_stale"] = False
            result["_dashboard_tier_b_saved_at"] = entry.get("tier_b_saved_at")
            return result
        return None

    def load_last_good(self, account_id: str | None, clock: str | None) -> dict | None:
        """Return the validated saved scope without waiting for SQLite or a file lock."""
        try:
            result = self._load_from_documents(self._read_documents(), account_id, clock)
            if result is not None and result.get("_dashboard_tier_b_revision") is not None:
                result["_dashboard_stale"] = True
            return result
        except Exception:
            return None

    def load(self, account_id: str | None, clock: str | None, *,
             expected_revision: int | None = None) -> dict | None:
        try:
            revision = (self.repository.dashboard_data_revision(account_id)
                        if expected_revision is None else int(expected_revision))
            with self._lock, self._process_lock():
                documents = self._read_documents()
            if (not documents
                    or self.repository.dashboard_data_revision(account_id) != revision):
                return None
            result = self._load_from_documents(documents, account_id, clock)
            if result is None or self.repository.dashboard_data_revision(account_id) != revision:
                return None
            result["_dashboard_revision"] = revision
            result["_dashboard_stale"] = (
                result.get("_dashboard_tier_b_revision") is not None and
                result.get("_dashboard_tier_b_revision") != revision)
            return result
        except Exception:
            return None

    def save(self, account_id: str | None, clock: str | None,
             revision: int, snapshot: dict) -> bool:
        try:
            tier_a, tier_b = _split_snapshot(snapshot)
            tier_a_valid = _valid_tier_a(tier_a, account_id, clock)
            self.last_validation_issue = (
                None if tier_a_valid else "tier_a_shape")
            if not tier_a_valid:
                self.last_save_status = "invalid_schema"
                return False
            self.last_validation_issue = _tier_b_validation_issue(tier_b)
            if self.last_validation_issue is not None:
                self.last_save_status = "invalid_schema"
                return False
            with self._lock, self._process_lock():
                document = self._read_document() or {
                    "format": _CACHE_FORMAT, "schema_version": SCHEMA_VERSION,
                    "formula_id": FORMULA_ID, "revision": int(revision), "scopes": {}}
                entry = document["scopes"].setdefault(
                    _scope_key(account_id, clock), {"tier_a": None, "tier_b": None})
                entry["tier_a"] = tier_a
                entry["tier_b"] = tier_b
                entry["tier_a_revision"] = int(revision)
                entry["tier_b_revision"] = int(revision)
                entry["tier_b_saved_at"] = datetime.now(timezone.utc).isoformat()
                document["revision"] = int(revision)
                while len(document["scopes"]) > _MAX_SCOPES:
                    document["scopes"].pop(next(iter(document["scopes"])))
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                size = len(encoded.encode("utf-8"))
                self.last_save_bytes = size
                if size > _MAX_CACHE_BYTES:
                    self.last_save_status = "oversized"
                    return False
                if self.repository.dashboard_data_revision(account_id) != int(revision):
                    self.last_save_status = "revision_changed"
                    return False
                self._atomic_write(encoded)
            self.last_save_status = "saved"
            return self.repository.dashboard_data_revision(account_id) == int(revision)
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
            if self.repository.dashboard_data_revision(account_id) != int(revision):
                self.last_save_status = "revision_changed"
                return False
            with self._lock, self._process_lock():
                document = self._read_document() or {
                    "format": _CACHE_FORMAT, "schema_version": SCHEMA_VERSION,
                    "formula_id": FORMULA_ID, "revision": int(revision), "scopes": {}}
                entry = document["scopes"].setdefault(
                    _scope_key(account_id, clock), {"tier_a": None, "tier_b": None,
                                                     "tier_a_revision": None,
                                                     "tier_b_revision": None,
                                                     "tier_b_saved_at": None})
                entry[tier_name] = tier
                entry[f"{tier_name}_revision"] = int(revision)
                if tier_name == "tier_b":
                    entry["tier_b_saved_at"] = datetime.now(timezone.utc).isoformat()
                document["revision"] = int(revision)
                while len(document["scopes"]) > _MAX_SCOPES:
                    document["scopes"].pop(next(iter(document["scopes"])))
                encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                size = len(encoded.encode("utf-8"))
                self.last_save_bytes = size
                if size > _MAX_CACHE_BYTES:
                    self.last_save_status = "oversized"
                    return False
                if self.repository.dashboard_data_revision(account_id) != int(revision):
                    self.last_save_status = "revision_changed"
                    return False
                self._atomic_write(encoded)
            self.last_save_status = "saved"
            current = self.repository.dashboard_data_revision(account_id) == int(revision)
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
