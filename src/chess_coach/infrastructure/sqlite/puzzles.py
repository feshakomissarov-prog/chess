"""Puzzle activity persistence mixin; GameRepository remains the public façade."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
import hashlib
import uuid
import time

from .constants import MAX_PAGE_SIZE
from ...lichess.puzzle_training import practice_rating_delta


def puzzle_attempt_uid(account_id: str, puzzle_id: str, attempt_date_ms: int,
                       win: bool | int) -> str:
    """Identity of a Lichess PuzzleActivity event, excluding mutable puzzle metadata."""
    payload = json.dumps([str(account_id), str(puzzle_id), int(attempt_date_ms),
                          int(bool(win))], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(("lichess-puzzle-attempt:v1:" + payload).encode("utf-8")).hexdigest()


class PuzzleActivityStoreMixin:

    def prepare_rated_puzzle_attempt(self, attempt: dict) -> tuple[dict, bool]:
        """Persist a frozen rated outcome before any request can reach Lichess."""
        payload = dict(attempt or {})
        attempt_id = str(payload.get("attempt_id") or "").strip()
        session_id = str(payload.get("session_id") or attempt_id).strip()
        account_id = str(payload.get("account_id") or "").strip()
        puzzle_id = str(payload.get("puzzle_id") or "").strip()
        if not all((attempt_id, session_id, account_id, puzzle_id)):
            raise ValueError("Для рейтингового результата нужны профиль, задача и попытка.")
        if payload.get("mode") not in (None, "LICHESS_RATED") or payload.get("rated", True) is not True:
            raise ValueError("Рейтинговая запись должна иметь frozen mode LICHESS_RATED.")
        now = int(time.time() * 1000)
        with self._connection() as db:
            existing = db.execute(
                "SELECT * FROM rated_puzzle_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                row = dict(existing)
                identity = (row["account_id"], row["session_id"], row["puzzle_id"])
                if identity != (account_id, session_id, puzzle_id):
                    raise ValueError("ID рейтинговой попытки уже относится к другой задаче.")
                return row, False
            db.execute(
                "INSERT INTO rated_puzzle_attempts "
                "(attempt_id,account_id,session_id,puzzle_id,mode,theme,difficulty,win,rated,status,"
                "created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,'prepared',?,?)",
                (attempt_id, account_id, session_id, puzzle_id, "LICHESS_RATED",
                 str(payload.get("angle") or payload.get("theme") or "mix"),
                 str(payload.get("difficulty") or "normal"), int(bool(payload.get("win"))),
                 1, now, now))
            row = db.execute(
                "SELECT * FROM rated_puzzle_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return dict(row), True

    def update_rated_puzzle_attempt(self, attempt_id: str, status: str, *,
                                    rating_diff: int | None = None,
                                    rating: int | None = None,
                                    error: str | None = None) -> dict:
        allowed = {"prepared", "sending", "confirmed", "rejected", "unknown", "not_sent"}
        if status not in allowed:
            raise ValueError("Недопустимый статус рейтинговой попытки.")
        now = int(time.time() * 1000)
        with self._connection() as db:
            db.execute(
                "UPDATE rated_puzzle_attempts SET status=?,updated_at_ms=?,"
                "server_confirmed_at_ms=CASE WHEN ?='confirmed' THEN ? ELSE server_confirmed_at_ms END,"
                "server_rating_diff=COALESCE(?,server_rating_diff),"
                "server_rating=COALESCE(?,server_rating),last_error=? WHERE attempt_id=?",
                (status, now, status, now, rating_diff, rating,
                 str(error)[:500] if error else None, str(attempt_id)))
            row = db.execute("SELECT * FROM rated_puzzle_attempts WHERE attempt_id=?",
                             (str(attempt_id),)).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return dict(row)

    def rated_puzzle_attempt(self, attempt_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM rated_puzzle_attempts WHERE attempt_id=?",
                             (str(attempt_id),)).fetchone()
        return dict(row) if row else None

    def unresolved_rated_puzzle_attempts(self, account_id: str) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM rated_puzzle_attempts WHERE account_id=? "
                "AND status IN ('prepared','sending','unknown','not_sent') "
                "ORDER BY created_at_ms", (str(account_id),)).fetchall()
        return [dict(row) for row in rows]

    def mark_interrupted_rated_attempts_unknown(self, account_id: str) -> int:
        """A process restart cannot distinguish a sent POST from a lost response."""
        now = int(time.time() * 1000)
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE rated_puzzle_attempts SET status='unknown',updated_at_ms=?,"
                "last_error='process ended while request was in flight' "
                "WHERE account_id=? AND status='sending'",
                (now, str(account_id)))
        return int(cursor.rowcount)

    def enqueue_puzzle_attempt(self, attempt: dict, *, error: str | None = None) -> None:
        """Durably retain an unacknowledged local result, scoped to its account."""
        payload = dict(attempt or {})
        attempt_id = str(payload.get("attempt_id") or "").strip()
        account_id = str(payload.get("account_id") or "local").strip() or "local"
        puzzle_id = str(payload.get("puzzle_id") or "").strip()
        if not attempt_id or not puzzle_id:
            raise ValueError("Для очереди сохранения нужны attempt_id и puzzle_id.")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        now = int(time.time() * 1000)
        with self._connection() as db:
            existing = db.execute(
                "SELECT account_id,puzzle_id FROM local_puzzle_attempt_outbox WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            if existing and (str(existing["account_id"]) != account_id
                             or str(existing["puzzle_id"]) != puzzle_id):
                raise ValueError("Идентификатор сохранения уже принадлежит другому профилю.")
            db.execute(
                "INSERT INTO local_puzzle_attempt_outbox "
                "(attempt_id,account_id,puzzle_id,payload_json,status,last_error,created_at_ms,updated_at_ms) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET "
                "status=excluded.status,last_error=excluded.last_error,updated_at_ms=excluded.updated_at_ms",
                (attempt_id, account_id, puzzle_id, serialized,
                 "failed" if error else "pending", str(error) if error else None, now, now))

    def pending_puzzle_attempts(self, account_id: str) -> list[dict]:
        """Load only pending attempts belonging to the requested local profile."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT payload_json,status,last_error,created_at_ms FROM local_puzzle_attempt_outbox "
                "WHERE account_id=? ORDER BY created_at_ms,attempt_id", (str(account_id or "local"),)
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                payload["save_status"] = str(row["status"])
                payload["last_error"] = row["last_error"]
                payload["outbox_created_at_ms"] = int(row["created_at_ms"])
                result.append(payload)
        return result

    def mark_puzzle_attempt_failed(self, attempt_id: str, error: str) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE local_puzzle_attempt_outbox SET status='failed',last_error=?,updated_at_ms=? "
                "WHERE attempt_id=?", (str(error), int(time.time() * 1000), str(attempt_id)))

    def acknowledge_puzzle_attempt(self, attempt_id: str) -> None:
        """Idempotently remove a durable outbox row after the attempt commit."""
        with self._connection() as db:
            db.execute("DELETE FROM local_puzzle_attempt_outbox WHERE attempt_id=?",
                       (str(attempt_id),))

    def cache_training_puzzle(self, account_id: str, puzzle_id: str,
                              puzzle_json: str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO local_puzzle_cache VALUES (?,?,?,?) "
                "ON CONFLICT(account_id,puzzle_id) DO UPDATE SET "
                "puzzle_json=excluded.puzzle_json,cached_at_ms=excluded.cached_at_ms",
                (str(account_id or "local"), str(puzzle_id), str(puzzle_json),
                 int(time.time() * 1000)))

    def cached_training_puzzles(self, account_id: str, *, limit: int = 100,
                                theme: str | None = None) -> list[dict]:
        limit = min(100, max(1, int(limit)))
        clauses = ["account_id=?"]
        params: list[object] = [str(account_id or "local")]
        if theme:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(puzzle_json) "
                "THEN puzzle_json ELSE '{}' END, '$.puzzle.themes') AS themes "
                "WHERE themes.value=?)")
            params.append(str(theme))
        params.append(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT account_id,puzzle_id,puzzle_json,cached_at_ms FROM local_puzzle_cache "
                "WHERE " + " AND ".join(clauses) +
                " ORDER BY cached_at_ms DESC,puzzle_id LIMIT ?", params).fetchall()
        return [dict(row) for row in rows]

    def get_cached_training_puzzle(self, account_id: str, puzzle_id: str) -> dict | None:
        """Exact account-scoped lookup; never substitute a bounded recent page."""
        with self._connection() as db:
            row = db.execute(
                "SELECT account_id,puzzle_id,puzzle_json,cached_at_ms FROM local_puzzle_cache "
                "WHERE account_id=? AND puzzle_id=? LIMIT 1",
                (str(account_id or "local"), str(puzzle_id))).fetchone()
        return dict(row) if row else None

    def get_local_training_attempt_by_id(self, account_id: str, puzzle_id: str) -> dict | None:
        """Recover an exact locally practiced puzzle, even if its cache row is gone."""
        with self._connection() as db:
            row = db.execute(
                "SELECT local_attempt_id,account_id,puzzle_id,result,solved,mistakes,hints,"
                "duration_ms,puzzle_json,created_at_ms FROM local_puzzle_attempts "
                "WHERE account_id=? AND puzzle_id=? "
                "ORDER BY created_at_ms DESC,local_attempt_id DESC LIMIT 1",
                (str(account_id or "local"), str(puzzle_id))).fetchone()
        return dict(row) if row else None

    def get_puzzle_attempt_by_id(self, account_id: str, puzzle_id: str) -> dict | None:
        """Exact lookup of imported Lichess activity, scoped before returning puzzle data."""
        with self._connection() as db:
            row = db.execute(
                "SELECT a.*,p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                "WHERE a.account_id=? AND a.puzzle_id=? "
                "ORDER BY a.attempt_date_ms DESC,a.attempt_uid DESC LIMIT 1",
                (str(account_id), str(puzzle_id))).fetchone()
        return dict(row) if row else None

    def local_puzzle_error_ids(self, account_id: str, *, theme: str | None = None,
                               limit: int = 100) -> list[str]:
        limit = min(100, max(1, int(limit)))
        params: list[object] = [str(account_id or "local")]
        theme_clause = ""
        if theme:
            theme_clause = (
                " AND EXISTS (SELECT 1 FROM local_puzzle_attempt_themes t "
                "WHERE t.local_attempt_id=a.local_attempt_id AND t.theme_id=?)")
            params.append(str(theme))
        params.append(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT a.puzzle_id,MAX(a.created_at_ms) AS recent_at "
                "FROM local_puzzle_attempts a WHERE a.account_id=? AND a.result='mistake'" +
                theme_clause + " GROUP BY a.puzzle_id ORDER BY recent_at DESC,a.puzzle_id LIMIT ?",
                params).fetchall()
        return [str(row["puzzle_id"]) for row in rows]

    def save_local_puzzle_attempt(self, account_id: str, puzzle_id: str, *,
                                  result: str, solved: bool, mistakes: int, hints: int,
                                  duration_ms: int, puzzle_json: str,
                                  attempt_id: str | None = None,
                                  rating_eligible: bool = False,
                                  mode: str = "LEGACY_LOCAL",
                                  selected_theme: str = "mix",
                                  selected_difficulty: str = "normal") -> tuple[str, bool]:
        """Append a local training result without changing mirrored Lichess activity."""
        if result not in {"success", "mistake", "hinted", "revealed", "skipped"}:
            raise ValueError("Некорректный результат локальной задачи.")
        attempt_id = str(attempt_id or uuid.uuid4().hex).strip()
        if not attempt_id:
            raise ValueError("Для локальной попытки требуется стабильный идентификатор.")
        account_id = str(account_id or "local")
        puzzle_id = str(puzzle_id)
        puzzle: dict = {}
        themes: list = []
        try:
            puzzle_payload = json.loads(str(puzzle_json))
            if isinstance(puzzle_payload, dict):
                parsed_puzzle = puzzle_payload.get("puzzle", {})
                if isinstance(parsed_puzzle, dict):
                    puzzle = parsed_puzzle
                    parsed_themes = puzzle.get("themes", [])
                    if isinstance(parsed_themes, (list, tuple)):
                        themes = parsed_themes
        except (ValueError, TypeError, AttributeError):
            pass
        with self._connection() as db:
            existing = db.execute(
                "SELECT account_id,puzzle_id FROM local_puzzle_attempts WHERE local_attempt_id=?",
                (attempt_id,)).fetchone()
            if existing is not None and (str(existing["account_id"]) != account_id
                                         or str(existing["puzzle_id"]) != puzzle_id):
                raise ValueError("Идентификатор попытки уже принадлежит другой задаче или профилю.")
            inserted = existing is None
            if inserted:
                db.execute(
                    "INSERT INTO local_puzzle_attempts "
                    "(local_attempt_id,account_id,puzzle_id,result,solved,mistakes,hints,"
                    "duration_ms,puzzle_json,created_at_ms,mode,selected_theme,selected_difficulty) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (attempt_id, account_id, puzzle_id, result, int(bool(solved)),
                     max(0, int(mistakes)), max(0, int(hints)), max(0, int(duration_ms)),
                     str(puzzle_json), int(time.time() * 1000), str(mode),
                     str(selected_theme or "mix"), str(selected_difficulty or "normal")))
                db.executemany(
                    "INSERT OR IGNORE INTO local_puzzle_attempt_themes VALUES (?,?)",
                    [(attempt_id, str(theme)) for theme in themes if theme])
                rating_value = puzzle.get("rating") if isinstance(puzzle, dict) else None
                if rating_eligible and str(mode) == "LEGACY_LOCAL" and rating_value is not None:
                    try:
                        puzzle_rating = max(0, int(rating_value))
                    except (ValueError, TypeError):
                        puzzle_rating = None
                    if puzzle_rating is not None:
                        scored = bool(solved and result == "success"
                                      and int(mistakes) == 0 and int(hints) == 0)
                        score = 1.0 if scored else 0.0
                        already_rated = db.execute(
                            "SELECT 1 FROM local_puzzle_rating_events "
                            "WHERE account_id=? AND puzzle_id=?",
                            (account_id, puzzle_id)).fetchone()
                        if not already_rated:
                            current_row = db.execute(
                                "SELECT rating,rated_attempts FROM local_puzzle_practice_ratings "
                                "WHERE account_id=?", (account_id,)).fetchone()
                            previous = int(current_row["rating"]) if current_row else 1500
                            delta = practice_rating_delta(previous, puzzle_rating, score)
                            after = previous + delta
                            created_at = datetime.now(timezone.utc).isoformat()
                            db.execute(
                                "INSERT INTO local_puzzle_rating_events "
                                "(event_id,account_id,puzzle_id,attempt_id,puzzle_rating,score,"
                                "rating_before,delta,rating_after,created_at) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex, account_id, puzzle_id, attempt_id,
                                 puzzle_rating, score, previous, delta, after, created_at))
                            db.execute(
                                "INSERT INTO local_puzzle_practice_ratings "
                                "(account_id,rating,last_delta,rated_attempts,updated_at) "
                                "VALUES (?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
                                "rating=excluded.rating,last_delta=excluded.last_delta,"
                                "rated_attempts=excluded.rated_attempts,updated_at=excluded.updated_at",
                                (account_id, after, delta,
                                 (int(current_row["rated_attempts"]) + 1) if current_row else 1,
                                 created_at))
        return attempt_id, inserted

    def local_puzzle_rating(self, account_id: str) -> dict:
        """Return the independent Chess Coach practice rating (never Lichess rating)."""
        with self._connection() as db:
            row = db.execute(
                "SELECT rating,last_delta,rated_attempts,updated_at "
                "FROM local_puzzle_practice_ratings WHERE account_id=?",
                (str(account_id or "local"),)).fetchone()
        if row is None:
            return {"rating": 1500, "delta": 0, "rated_attempts": 0, "date": None}
        return {"rating": int(row["rating"]), "delta": int(row["last_delta"]),
                "rated_attempts": int(row["rated_attempts"]), "date": row["updated_at"]}

    def local_puzzle_rating_event(self, account_id: str, puzzle_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT event_id,account_id,puzzle_id,attempt_id,puzzle_rating,score,"
                "rating_before,delta,rating_after,created_at FROM local_puzzle_rating_events "
                "WHERE account_id=? AND puzzle_id=?",
                (str(account_id or "local"), str(puzzle_id))).fetchone()
        return dict(row) if row else None

    def has_local_puzzle_rating_event(self, account_id: str, puzzle_id: str) -> bool:
        return self.local_puzzle_rating_event(account_id, puzzle_id) is not None

    def local_puzzle_attempts(self, account_id: str, *, limit: int = 8) -> list[dict]:
        limit = min(100, max(1, int(limit)))
        with self._connection() as db:
            rows = db.execute(
                "SELECT local_attempt_id,account_id,puzzle_id,result,solved,mistakes,hints,"
                "duration_ms,puzzle_json,created_at_ms,mode,selected_theme,selected_difficulty "
                "FROM local_puzzle_attempts "
                "WHERE account_id=? ORDER BY created_at_ms DESC,local_attempt_id DESC LIMIT ?",
                (str(account_id or "local"), limit)).fetchall()
        return [dict(row) for row in rows]

    def local_puzzle_attempt_ids(self, account_id: str) -> set[str]:
        """Return account-scoped locally practiced IDs for online de-duplication."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT puzzle_id FROM local_puzzle_attempts WHERE account_id=? "
                "GROUP BY puzzle_id ORDER BY MAX(created_at_ms) DESC",
                (str(account_id or "local"),)).fetchall()
        return {str(row["puzzle_id"]) for row in rows if row["puzzle_id"]}

    def practiced_puzzle_ids(self, account_id: str) -> set[str]:
        """IDs already seen in local attempts or imported Lichess puzzle history."""
        account_id = str(account_id or "local")
        with self._connection() as db:
            rows = db.execute(
                "SELECT puzzle_id FROM local_puzzle_attempts WHERE account_id=? "
                "UNION SELECT puzzle_id FROM puzzle_attempts WHERE account_id=?",
                (account_id, account_id)).fetchall()
        return {str(row["puzzle_id"]) for row in rows if row["puzzle_id"]}

    def local_puzzle_attempt_count(self, account_id: str) -> int:
        with self._connection() as db:
            row = db.execute("SELECT COUNT(*) FROM local_puzzle_attempts WHERE account_id=?",
                             (str(account_id or "local"),)).fetchone()
        return int(row[0]) if row else 0

    def local_puzzle_statistics(self, account_id: str, theme: str | None = None) -> dict:
        with self._connection() as db:
            if theme:
                row = db.execute(
                    "SELECT COUNT(*) attempts,COALESCE(SUM(a.solved),0) solved "
                    "FROM local_puzzle_attempts a JOIN local_puzzle_attempt_themes t "
                    "USING(local_attempt_id) WHERE a.account_id=? AND t.theme_id=?",
                    (str(account_id or "local"), str(theme))).fetchone()
            else:
                row = db.execute(
                    "SELECT COUNT(*) attempts,COALESCE(SUM(solved),0) solved "
                    "FROM local_puzzle_attempts WHERE account_id=?",
                    (str(account_id or "local"),)).fetchone()
        return {"attempts": int(row["attempts"]), "solved": int(row["solved"]),
                "success_percent": (round(int(row["solved"]) / int(row["attempts"]) * 100, 1)
                                    if int(row["attempts"]) else None)}

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

    def puzzle_history(self, account_id: str, *, limit: int = 100, offset: int = 0,
                       theme: str | None = None, outcome: str | None = None) -> list[dict]:
        limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
        offset = max(0, int(offset))
        clauses = ["a.account_id=?"]
        params: list[object] = [str(account_id)]
        if theme:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(p.themes_json) "
                "THEN p.themes_json ELSE '[]' END) AS themes WHERE themes.value=?)")
            params.append(str(theme))
        if outcome in {"win", "loss"}:
            clauses.append("a.win=?")
            params.append(int(outcome == "win"))
        params.extend((limit, offset))
        with self._connection() as db:
            rows = db.execute(
                "SELECT a.*,p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) WHERE " +
                " AND ".join(clauses) +
                " ORDER BY a.attempt_date_ms DESC,a.attempt_uid LIMIT ? OFFSET ?", params).fetchall()
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

    def puzzle_player_rating_refresh_due(self, account_id: str) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self._connection() as db:
            return db.execute(
                "SELECT 1 FROM puzzle_player_rating_history WHERE account_id=? AND fetched_at>?",
                (account_id, cutoff)).fetchone() is None

    def save_puzzle_player_rating(self, account_id: str, points: list) -> None:
        ratings = {}
        now = datetime.now(timezone.utc)
        for point in points:
            if not isinstance(point, list) or len(point) != 4 or any(type(n) is not int for n in point):
                continue
            year, month, day, rating = point
            try:
                played = date(year, month + 1, day)
            except ValueError:
                continue
            if 0 < rating < 10_000 and played <= now.date():
                ratings[played.isoformat()] = rating
        with self._connection() as db:
            db.execute("""INSERT INTO puzzle_player_rating_history VALUES (?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET fetched_at=excluded.fetched_at,
                points_json=CASE WHEN excluded.points_json='[]' THEN points_json
                                 ELSE excluded.points_json END""",
                       (account_id, now.isoformat(), json.dumps(sorted(ratings.items())[-91:])))

    def puzzle_player_rating(self, account_id: str) -> dict:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=89)
        with self._connection() as db:
            row = db.execute("SELECT points_json FROM puzzle_player_rating_history WHERE account_id=?",
                             (account_id,)).fetchone()
            if not row:
                return {}
            points = json.loads(row["points_json"])
            if not points:
                return {}
            start_ms = int(datetime.combine(cutoff, datetime.min.time(), timezone.utc).timestamp() * 1000)
            attempts = {item["day"]: (int(item["attempts"]), int(item["wins"]))
                        for item in db.execute(
                            "SELECT date(attempt_date_ms/1000,'unixepoch') day,COUNT(*) attempts,"
                            "SUM(win) wins FROM puzzle_attempts WHERE account_id=? AND attempt_date_ms>=? "
                            "GROUP BY day", (account_id, start_ms))}
        return {"latest": points[-1][1], "date": points[-1][0],
                "points": [{"day": day, "rating": rating, "attempts": attempts.get(day)}
                           for day, rating in points if day >= cutoff.isoformat()]}
