"""Puzzle activity persistence mixin; GameRepository remains the public façade."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import hashlib
import uuid

from .constants import MAX_PAGE_SIZE


def puzzle_attempt_uid(account_id: str, puzzle_id: str, attempt_date_ms: int,
                       win: bool | int) -> str:
    """Identity of a Lichess PuzzleActivity event, excluding mutable puzzle metadata."""
    payload = json.dumps([str(account_id), str(puzzle_id), int(attempt_date_ms),
                          int(bool(win))], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(("lichess-puzzle-attempt:v1:" + payload).encode("utf-8")).hexdigest()


class PuzzleActivityStoreMixin:

    def import_puzzle_activity(self, username: str, record: dict, *,
                               backfill: bool = False,
                               _return_inserted: bool = False) -> str | tuple[str, bool]:
        result = self.import_puzzle_activities(username, [record], backfill=backfill)[0]
        return result if _return_inserted else result[0]

    def import_puzzle_activities(self, username: str, records: list[dict], *,
                                 backfill: bool = False,
                                 pilot_run_id: str | None = None) -> list[tuple[str, bool]]:
        """Persist one API page of puzzle attempts in a single SQLite transaction."""
        account_id = self.ensure_archive_account(username)
        prepared: list[tuple[str, str, int, dict, str]] = []
        for record in records:
            puzzle = record.get("puzzle")
            if not isinstance(puzzle, dict):
                raise ValueError("Puzzle activity entry has no puzzle object")
            puzzle_id = str(puzzle.get("id") or "").strip()
            try:
                attempt_date = int(record["date"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Puzzle activity entry has no valid date") from exc
            if not puzzle_id or not str(puzzle.get("fen") or ""):
                raise ValueError("Puzzle activity entry requires puzzle id and FEN")
            stable = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            attempt_uid = puzzle_attempt_uid(account_id, puzzle_id, attempt_date,
                                             bool(record.get("win")))
            prepared.append((attempt_uid, puzzle_id, attempt_date, record, stable))

        results: list[tuple[str, bool]] = []
        with self._connection() as db:
            existing: set[str] = set()
            puzzle_metadata: dict[str, tuple] = {}
            # SQLite builds on Windows often cap bound variables at 999. Chunk
            # the preflight so large puzzle pages remain portable.
            for offset in range(0, len(prepared), 500):
                chunk = prepared[offset:offset + 500]
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                existing.update(row[0] for row in db.execute(
                    f"SELECT attempt_uid FROM puzzle_attempts WHERE attempt_uid IN ({placeholders})",
                    tuple(item[0] for item in chunk)).fetchall())
            puzzle_ids = list(dict.fromkeys(item[1] for item in prepared))
            for offset in range(0, len(puzzle_ids), 500):
                chunk_ids = puzzle_ids[offset:offset + 500]
                placeholders = ",".join("?" for _ in chunk_ids)
                for row in db.execute(
                        "SELECT puzzle_id,fen,solution_json,themes_json,puzzle_rating,plays "
                        f"FROM puzzles WHERE puzzle_id IN ({placeholders})", chunk_ids).fetchall():
                    puzzle_metadata[str(row["puzzle_id"])] = tuple(row[key] for key in
                        ("fen", "solution_json", "themes_json", "puzzle_rating", "plays"))
            for attempt_uid, puzzle_id, attempt_date, record, stable in prepared:
                puzzle = record["puzzle"]
                now = datetime.now(timezone.utc).isoformat()
                solution = puzzle.get("solution") if isinstance(puzzle.get("solution"), list) else []
                themes = puzzle.get("themes") if isinstance(puzzle.get("themes"), list) else []
                incoming_puzzle = (puzzle["fen"], json.dumps(solution), json.dumps(themes),
                                   puzzle.get("rating"), puzzle.get("plays"))
                if puzzle_metadata.get(puzzle_id) != incoming_puzzle:
                    db.execute("INSERT INTO puzzles VALUES (?,?,?,?,?,?,?) ON CONFLICT(puzzle_id) "
                               "DO UPDATE SET fen=excluded.fen,solution_json=excluded.solution_json, "
                               "themes_json=excluded.themes_json,puzzle_rating=excluded.puzzle_rating, "
                               "plays=excluded.plays,updated_at=excluded.updated_at",
                               (puzzle_id, *incoming_puzzle,
                                now))
                    puzzle_metadata[puzzle_id] = incoming_puzzle
                if attempt_uid in existing:
                    inserted = 0
                else:
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO puzzle_attempts VALUES (?,?,?,?,?,?,?,?)",
                        (attempt_uid, account_id, puzzle_id, attempt_date,
                         int(bool(record.get("win"))), "lichess", stable, now)).rowcount
                    if inserted:
                        existing.add(attempt_uid)
                if pilot_run_id is not None:
                    run = db.execute("SELECT account_id,status FROM puzzle_pilot_runs WHERE run_id=?",
                                     (pilot_run_id,)).fetchone()
                    if run is None or run["account_id"] != account_id or run["status"] != "running":
                        raise ValueError("Пробная загрузка задач не активна для этого аккаунта.")
                    # Associate even an already-known attempt with this pilot run, but do
                    # not touch the full-sync cursor or imported_count.
                    db.execute("INSERT OR IGNORE INTO puzzle_pilot_run_attempts VALUES (?,?)",
                               (pilot_run_id, attempt_uid))
                    db.execute("UPDATE puzzle_pilot_runs SET received_count=received_count+1, "
                               "new_count=new_count+?,finished_at=NULL WHERE run_id=?",
                               (int(bool(inserted)), pilot_run_id))
                else:
                    db.execute("INSERT OR IGNORE INTO puzzle_sync_state(account_id,updated_at) VALUES (?,?)",
                               (account_id, now))
                    if backfill:
                        db.execute("UPDATE puzzle_sync_state SET backfill_before=?, "
                                   "imported_count=imported_count+?,updated_at=? WHERE account_id=?",
                                   (attempt_date + 1, int(bool(inserted)), now, account_id))
                    elif inserted:
                        db.execute("UPDATE puzzle_sync_state SET imported_count=imported_count+?, "
                                   "last_error=NULL,updated_at=? WHERE account_id=?",
                                   (1, now, account_id))
                results.append((attempt_uid, bool(inserted)))
        return results

    def start_puzzle_pilot(self, account_id: str, requested_limit: int) -> str:
        if requested_limit not in (5, 10):
            raise ValueError("Пробная загрузка допускает только 5 или 10 попыток.")
        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO puzzle_pilot_runs(run_id,account_id,requested_limit,status,started_at) "
                       "VALUES (?,?,?,'running',?)", (run_id, account_id, requested_limit, now))
        return run_id

    def finish_puzzle_pilot(self, run_id: str, status: str, error: str | None = None) -> dict:
        if status not in {"complete", "cancelled", "error"}:
            raise ValueError("Недопустимый итог пробной загрузки задач.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE puzzle_pilot_runs SET status=?,finished_at=?,last_error=? WHERE run_id=?",
                       (status, now, error[:500] if error else None, run_id))
            row = db.execute("SELECT * FROM puzzle_pilot_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            return dict(row)

    def puzzle_pilot_history(self, account_id: str, *, run_id: str | None = None,
                             limit: int = 100) -> list[dict]:
        limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
        with self._connection() as db:
            query = ("SELECT r.run_id,r.status,r.requested_limit,r.received_count,"
                     "r.new_count,r.started_at,r.finished_at,r.last_error,"
                     "a.attempt_uid,a.puzzle_id,a.attempt_date_ms,a.win,"
                     "p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                     "FROM puzzle_pilot_runs r LEFT JOIN puzzle_pilot_run_attempts x USING(run_id) "
                     "LEFT JOIN puzzle_attempts a USING(attempt_uid) "
                     "LEFT JOIN puzzles p USING(puzzle_id) WHERE r.account_id=? ")
            params: list[object] = [account_id]
            if run_id is not None:
                query += "AND r.run_id=? "
                params.append(run_id)
            query += "ORDER BY r.started_at DESC,a.attempt_date_ms DESC,a.attempt_uid LIMIT ?"
            params.append(limit)
            rows = db.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def has_puzzle_attempt(self, username: str, record: dict) -> bool:
        puzzle = record.get("puzzle") or {}
        account_id = self.ensure_archive_account(username)
        attempt_uid = puzzle_attempt_uid(account_id, str(puzzle.get("id") or ""),
                                         int(record.get("date", 0)), bool(record.get("win")))
        with self._connection() as db:
            return bool(db.execute("SELECT 1 FROM puzzle_attempts WHERE attempt_uid=?",
                                   (attempt_uid,)).fetchone())

    def puzzle_sync_state(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM puzzle_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return dict(row) if row else {}

    def finish_puzzle_backfill(self, account_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET backfill_complete=1,incremental_since=MAX("
                       "COALESCE(incremental_since,0),COALESCE((SELECT MAX(attempt_date_ms) FROM "
                       "puzzle_attempts WHERE account_id=?),0)),updated_at=? WHERE account_id=?",
                       (account_id, datetime.now(timezone.utc).isoformat(), account_id))

    def set_puzzle_incremental_progress(self, account_id: str, before_ms: int, target_ms: int) -> None:
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET incremental_before=?,incremental_target=?,updated_at=? "
                       "WHERE account_id=?", (int(before_ms), int(target_ms),
                       datetime.now(timezone.utc).isoformat(), account_id))

    def finish_puzzle_incremental(self, account_id: str, cursor_ms: int,
                                  last_sync_new_count: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET incremental_since=MAX(COALESCE(incremental_since,0),?),"
                       "incremental_before=NULL,incremental_target=NULL,last_sync_new_count=?,"
                       "last_success_at=?,last_error=NULL,updated_at=? WHERE account_id=?",
                       (int(cursor_ms), int(last_sync_new_count), now, now, account_id))

    def record_puzzle_error(self, account_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO puzzle_sync_state(account_id,updated_at) VALUES (?,?)",
                       (account_id, now))
            db.execute("UPDATE puzzle_sync_state SET last_error=?,updated_at=? WHERE account_id=?",
                       (error[:500], now, account_id))

    def puzzle_history(self, account_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
        offset = max(0, int(offset))
        with self._connection() as db:
            rows = db.execute("SELECT a.*,p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                               "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                               "WHERE account_id=? ORDER BY attempt_date_ms DESC,attempt_uid "
                               "LIMIT ? OFFSET ?", (account_id, limit, offset)).fetchall()
            return [dict(row) for row in rows]

    def list_puzzle_accounts(self) -> list[dict]:
        """List only accounts with locally saved puzzle attempts."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT a.account_id,a.username,COUNT(pu.attempt_uid) AS attempts "
                "FROM accounts a JOIN puzzle_attempts pu ON pu.account_id=a.account_id "
                "GROUP BY a.account_id,a.username ORDER BY lower(a.username),a.account_id").fetchall()
        return [dict(row) for row in rows]

    def puzzle_history_page(self, account_id: str, *, query: str = "",
                            outcome: str | None = None, page: int = 0,
                            page_size: int = 100) -> dict:
        """Read a bounded, filterable local puzzle history page."""
        page = max(0, int(page))
        page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        clauses = ["a.account_id=?"]
        params: list[object] = [account_id]
        if outcome in {"win", "loss"}:
            clauses.append("a.win=?")
            params.append(int(outcome == "win"))
        term = query.strip()
        if term:
            clauses.append("(a.puzzle_id LIKE ? OR p.themes_json LIKE ? OR "
                            "CAST(p.puzzle_rating AS TEXT) LIKE ? OR "
                            "CAST(a.attempt_date_ms AS TEXT) LIKE ?)")
            params.extend([f"%{term}%"] * 4)
        where = " WHERE " + " AND ".join(clauses)
        with self._connection() as db:
            total = int(db.execute("SELECT COUNT(*) FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id)"
                                   + where, params).fetchone()[0])
            rows = db.execute("SELECT a.*,p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                              "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id)" + where +
                              " ORDER BY a.attempt_date_ms DESC,a.attempt_uid LIMIT ? OFFSET ?",
                              [*params, page_size, page * page_size]).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page,
                "page_size": page_size, "pages": (total + page_size - 1) // page_size}

    def puzzle_statistics(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT COUNT(*) total,COALESCE(SUM(win),0) wins, "
                              "AVG(p.puzzle_rating) avg_puzzle_rating "
                              "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                              "WHERE account_id=?", (account_id,)).fetchone()
            total = int(row["total"])
            wins = int(row["wins"])
            themes = db.execute("SELECT p.themes_json,a.win FROM puzzle_attempts a "
                                "JOIN puzzles p USING(puzzle_id) WHERE account_id=?", (account_id,)).fetchall()
        counts: dict[str, list[int]] = {}
        for record in themes:
            try:
                values = json.loads(record["themes_json"])
            except (TypeError, json.JSONDecodeError):
                values = []
            for theme in values:
                pair = counts.setdefault(str(theme), [0, 0])
                pair[0] += 1
                pair[1] += int(bool(record["win"]))
        return {"attempts": total, "wins": wins, "failures": total - wins,
                "success_percent": round(100 * wins / total, 1) if total else None,
                "average_puzzle_rating": round(row["avg_puzzle_rating"], 1)
                    if row["avg_puzzle_rating"] is not None else None,
                "themes": {theme: {"attempts": values[0], "wins": values[1],
                                   "success_percent": round(100 * values[1] / values[0], 1)}
                           for theme, values in counts.items()}}

    def puzzle_rating_trend(self, account_id: str, *, limit: int = 12) -> list[dict]:
        """Return a bounded monthly local puzzle-rating trend, newest month last."""
        limit = max(1, min(int(limit), 60))
        with self._connection() as db:
            rows = db.execute(
                "SELECT strftime('%Y-%m',a.attempt_date_ms/1000,'unixepoch') AS month, "
                "COUNT(*) AS attempts,AVG(p.puzzle_rating) AS average_rating, "
                "SUM(a.win) AS wins FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                "WHERE a.account_id=? GROUP BY month ORDER BY month DESC LIMIT ?",
                (account_id, limit)).fetchall()
        return [{"month": row["month"], "attempts": int(row["attempts"]),
                 "average_rating": round(float(row["average_rating"]), 1)
                    if row["average_rating"] is not None else None,
                 "wins": int(row["wins"] or 0)} for row in reversed(rows)]

    def save_puzzle_dashboard(self, account_id: str, days: int, payload: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO puzzle_dashboard_snapshots VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, account_id, int(days), now,
                        json.dumps(payload, sort_keys=True)))
