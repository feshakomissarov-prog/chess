"""Durable Lichess backfill and local analysis-job storage mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from .constants import MAX_PAGE_SIZE

class AnalysisJobsStoreMixin:

    def enqueue_lichess_analysis_backfill(self, account_id: str, *, limit: int = 10_000) -> int:
        """Durably queue all saved, finished games for one account without touching lines."""
        now = datetime.now(timezone.utc).isoformat()
        limit = max(1, min(50_000, int(limit)))
        with self._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill SET accuracy_status='queued',"
                       "evals_status='queued',updated_at=? WHERE account_id=? "
                       "AND (accuracy_status='running' OR evals_status='running')",
                       (now, account_id))
            db.execute("""
                INSERT INTO lichess_analysis_backfill(
                  account_id,game_id,line_revision,line_hash,accuracy_status,evals_status,updated_at)
                SELECT ag.account_id,g.game_id,gr.line_revision,gr.line_hash,
                  CASE WHEN g.lichess_accuracy_white IS NOT NULL AND g.lichess_accuracy_black IS NOT NULL
                       THEN 'has_data' WHEN g.lichess_accuracy_white IS NOT NULL
                         OR g.lichess_accuracy_black IS NOT NULL THEN 'partial' ELSE 'queued' END,
                  CASE WHEN (SELECT COUNT(DISTINCT pa.ply) FROM analysis_runs ar
                       JOIN position_assessments pa ON pa.run_id=ar.run_id
                       WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision
                         AND ar.source='lichess-pgn' AND ar.status IN ('complete','partial')
                         AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) >=
                       MAX(1,(SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=g.game_id
                           AND gm.line_revision=gr.line_revision)) THEN 'has_data'
                       WHEN EXISTS(SELECT 1 FROM analysis_runs ar JOIN position_assessments pa
                       ON pa.run_id=ar.run_id WHERE ar.game_id=g.game_id
                         AND ar.line_revision=gr.line_revision AND ar.source='lichess-pgn'
                         AND ar.status IN ('complete','partial')
                         AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL))
                       THEN 'partial' ELSE 'queued' END,?
                FROM account_games ag JOIN games g ON g.game_id=ag.game_id
                JOIN game_revisions gr ON gr.game_id=g.game_id AND gr.is_current=1
                WHERE ag.account_id=? AND g.status NOT IN
                  ('created','started','started_soon','playing')
                  AND g.source_game_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM lichess_analysis_backfill queued
                    WHERE queued.account_id=ag.account_id AND queued.game_id=g.game_id
                      AND queued.line_revision=gr.line_revision AND queued.line_hash=gr.line_hash)
                ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,
                         g.started_at DESC,g.game_id DESC LIMIT ?
                ON CONFLICT(account_id,game_id) DO UPDATE SET
                  line_revision=excluded.line_revision,line_hash=excluded.line_hash,
                  accuracy_status='queued',evals_status='queued',
                  accuracy_checked_at=NULL,evals_checked_at=NULL,
                  accuracy_next_retry_at=NULL,evals_next_retry_at=NULL,
                  last_error=NULL,updated_at=excluded.updated_at
                WHERE lichess_analysis_backfill.line_revision<>excluded.line_revision
                   OR lichess_analysis_backfill.line_hash<>excluded.line_hash
            """, (now, account_id, limit))
            return int(db.execute("SELECT changes()").fetchone()[0])

    def list_lichess_analysis_backfill(self, account_id: str, *, limit: int = 10,
                                       force_retry: bool = False) -> list[dict]:
        """Select a small due page; no full archive materialization in Python."""
        now = datetime.now(timezone.utc).isoformat()
        limit = max(1, min(25, int(limit)))
        due = "('queued','error','rate_limited','no_server_data','partial','stale_line')"
        with self._connection() as db:
            budget = db.execute(
                "SELECT retry_after_at FROM lichess_analysis_backfill_budget WHERE account_id=?",
                (account_id,)).fetchone()
            if budget and budget[0] and str(budget[0]) > now:
                return []
            if force_retry:
                db.execute("UPDATE lichess_analysis_backfill SET "
                           "accuracy_next_retry_at=CASE WHEN accuracy_status IN "
                           "('no_server_data','partial','error') THEN NULL ELSE accuracy_next_retry_at END,"
                           "evals_next_retry_at=CASE WHEN evals_status IN "
                           "('no_server_data','partial','error') THEN NULL ELSE evals_next_retry_at END "
                           "WHERE account_id=?", (account_id,))
            rows = db.execute(f"""
                SELECT q.account_id,q.game_id,q.line_revision,q.line_hash,
                       q.accuracy_status,q.evals_status,q.attempts,q.last_error,
                       (q.accuracy_status IN {due} AND
                        (q.accuracy_next_retry_at IS NULL OR q.accuracy_next_retry_at<=?)) AS check_accuracy,
                       (q.evals_status IN {due} AND
                        (q.evals_next_retry_at IS NULL OR q.evals_next_retry_at<=?)) AS check_evals,
                       g.source_game_id,g.status,g.pgn,g.color,
                       ag.player_name,ag.color AS account_color,gr.moves
                FROM lichess_analysis_backfill q
                JOIN games g ON g.game_id=q.game_id
                JOIN account_games ag ON ag.account_id=q.account_id AND ag.game_id=q.game_id
                JOIN game_revisions gr ON gr.game_id=q.game_id
                  AND gr.line_revision=q.line_revision AND gr.is_current=1
                WHERE q.account_id=? AND (
                  (q.accuracy_status IN {due} AND
                   (q.accuracy_next_retry_at IS NULL OR q.accuracy_next_retry_at<=?)) OR
                  (q.evals_status IN {due} AND
                   (q.evals_next_retry_at IS NULL OR q.evals_next_retry_at<=?)))
                ORDER BY CASE WHEN q.accuracy_status='queued' OR q.evals_status='queued'
                              THEN 0 ELSE 1 END,
                         g.started_at DESC,q.game_id LIMIT ?
            """, (now, now, account_id, now, now, limit)).fetchall()
        return [dict(row) for row in rows]

    def lichess_analysis_backfill_has_due_work(self, account_id: str, *,
                                                daily_limit: int = 25,
                                                force_retry: bool = False) -> bool:
        """Check local queue/budget before spending a separate API preflight request."""
        self.enqueue_lichess_analysis_backfill(account_id)
        if self.lichess_analysis_backfill_paused(account_id):
            return False
        retry_at = self.lichess_analysis_retry_after(account_id)
        now = datetime.now(timezone.utc)
        if retry_at and retry_at > now.isoformat():
            return False
        with self._connection() as db:
            row = db.execute("SELECT utc_day,requests FROM lichess_analysis_backfill_budget "
                             "WHERE account_id=?", (account_id,)).fetchone()
        if row and row["utc_day"] == now.date().isoformat() and int(row["requests"]) >= max(
                1, min(100, int(daily_limit))):
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(),
                                        tzinfo=timezone.utc).isoformat()
            self.set_lichess_analysis_retry_after(account_id, int(
                (datetime.fromisoformat(tomorrow) - now).total_seconds()))
            return False
        return bool(self.list_lichess_analysis_backfill(
            account_id, limit=1, force_retry=force_retry))

    def lichess_analysis_backfill_next_eligible_at(self, account_id: str, *,
                                                    daily_limit: int = 25,
                                                    force_retry: bool = False) -> str | None:
        """Return the durable earliest eligibility time for queued work, if any."""
        self.enqueue_lichess_analysis_backfill(account_id)
        if self.lichess_analysis_backfill_paused(account_id):
            return None
        now = datetime.now(timezone.utc)
        due = "('queued','error','rate_limited','no_server_data','partial','stale_line')"
        if force_retry:
            # Match list_lichess_analysis_backfill(force_retry=True): only
            # transient/no-data results are made immediately eligible.
            with self._connection() as db:
                db.execute("UPDATE lichess_analysis_backfill SET "
                           "accuracy_next_retry_at=CASE WHEN accuracy_status IN "
                           "('no_server_data','partial','error') THEN NULL ELSE accuracy_next_retry_at END,"
                           "evals_next_retry_at=CASE WHEN evals_status IN "
                           "('no_server_data','partial','error') THEN NULL ELSE evals_next_retry_at END "
                           "WHERE account_id=?", (account_id,))
        with self._connection() as db:
            row = db.execute(
                f"SELECT MIN(CASE WHEN accuracy_status IN {due} THEN "
                "COALESCE(accuracy_next_retry_at,?) END) accuracy_at,"
                f"MIN(CASE WHEN evals_status IN {due} THEN "
                "COALESCE(evals_next_retry_at,?) END) evals_at "
                "FROM lichess_analysis_backfill WHERE account_id=?",
                (now.isoformat(), now.isoformat(), account_id)).fetchone()
            budget = db.execute(
                "SELECT utc_day,requests,retry_after_at FROM lichess_analysis_backfill_budget "
                "WHERE account_id=?", (account_id,)).fetchone()
        facet_times = [str(row[key]) for key in ("accuracy_at", "evals_at")
                       if row and row[key] is not None]
        if not facet_times:
            return None
        eligible = max([min(facet_times), now.isoformat()])
        if budget and budget["retry_after_at"]:
            eligible = max(eligible, str(budget["retry_after_at"]))
        limit = max(1, min(100, int(daily_limit)))
        if (budget and budget["utc_day"] == now.date().isoformat()
                and int(budget["requests"]) >= limit):
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(),
                                        tzinfo=timezone.utc).isoformat()
            # Persist the reset even if the last request in the batch exactly
            # reached the budget and no 26th request was attempted.
            with self._connection() as db:
                db.execute("UPDATE lichess_analysis_backfill_budget SET retry_after_at=?,updated_at=? "
                           "WHERE account_id=?", (tomorrow, now.isoformat(), account_id))
            eligible = max(eligible, tomorrow)
        return eligible

    def mark_lichess_analysis_backfill_running(self, account_id: str, game_id: str, *,
                                               line_revision: int | None = None,
                                               line_hash: str | None = None,
                                               check_accuracy: bool = True,
                                               check_evals: bool = True) -> None:
        revision_guard = " AND line_revision=?" if line_revision is not None else ""
        hash_guard = " AND line_hash=?" if line_hash is not None else ""
        params = [int(check_accuracy), int(check_evals),
                  datetime.now(timezone.utc).isoformat(), account_id, game_id]
        if line_revision is not None:
            params.append(int(line_revision))
        if line_hash is not None:
            params.append(str(line_hash))
        with self._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill SET accuracy_status=CASE "
                       "WHEN ?=1 AND accuracy_status IN ('queued','error','rate_limited','no_server_data','partial','stale_line') "
                       "THEN 'running' ELSE accuracy_status END,evals_status=CASE "
                       "WHEN ?=1 AND evals_status IN ('queued','error','rate_limited','no_server_data','partial','stale_line') "
                       "THEN 'running' ELSE evals_status END,attempts=attempts+1,updated_at=? "
                       "WHERE account_id=? AND game_id=?" + revision_guard + hash_guard, params)

    def record_lichess_analysis_backfill(self, account_id: str, game_id: str, *,
                                         accuracy_status: str, evals_status: str,
                                         retry_after_seconds: int = 0,
                                         error: str | None = None,
                                         line_revision: int | None = None,
                                         line_hash: str | None = None,
                                         check_accuracy: bool = True,
                                         check_evals: bool = True) -> None:
        allowed = {"queued", "running", "has_data", "no_server_data", "partial",
                   "rate_limited", "error", "stale_line"}
        if accuracy_status not in allowed or evals_status not in allowed:
            raise ValueError("Invalid Lichess analytics status")
        now = datetime.now(timezone.utc)
        checked = now.isoformat()
        retry = ((now + timedelta(seconds=max(1, int(retry_after_seconds)))).isoformat()
                 if retry_after_seconds else None)
        revision_guard = " AND line_revision=?" if line_revision is not None else ""
        hash_guard = " AND line_hash=?" if line_hash is not None else ""
        assignments: list[str] = []
        params: list[object] = []
        retry_states = "'no_server_data','partial','rate_limited','error','stale_line'"
        checked_states = "'has_data','no_server_data','partial'"
        if check_accuracy:
            assignments.extend([
                "accuracy_status=?",
                f"accuracy_checked_at=CASE WHEN ? IN ({checked_states}) THEN ? ELSE accuracy_checked_at END",
                f"accuracy_next_retry_at=CASE WHEN ? IN ({retry_states}) THEN ? ELSE NULL END",
            ])
            params.extend([accuracy_status, accuracy_status, checked,
                           accuracy_status, retry])
        if check_evals:
            assignments.extend([
                "evals_status=?",
                f"evals_checked_at=CASE WHEN ? IN ({checked_states}) THEN ? ELSE evals_checked_at END",
                f"evals_next_retry_at=CASE WHEN ? IN ({retry_states}) THEN ? ELSE NULL END",
            ])
            params.extend([evals_status, evals_status, checked, evals_status, retry])
        assignments.extend(["last_error=?", "updated_at=?"])
        params.extend([(str(error)[:500] if error else None), checked, account_id, game_id])
        if line_revision is not None:
            params.append(int(line_revision))
        if line_hash is not None:
            params.append(str(line_hash))
        with self._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill SET " + ",".join(assignments) +
                       " WHERE account_id=? AND game_id=?" + revision_guard + hash_guard, params)

    def lichess_analysis_backfill_summary(self, account_id: str) -> dict[str, int | str | None]:
        with self._connection() as db:
            row = db.execute("""
              SELECT COUNT(*) total,
                SUM(accuracy_status IN ('has_data','partial')) accuracy,
                SUM(evals_status IN ('has_data','partial')) evals,
                SUM(accuracy_status='no_server_data' AND evals_status='no_server_data') no_data,
                SUM(accuracy_status IN ('error','rate_limited','stale_line') OR evals_status IN ('error','rate_limited','stale_line')) errors,
                SUM(accuracy_status='queued' OR evals_status='queued' OR accuracy_status='running' OR evals_status='running') pending,
                SUM(accuracy_checked_at IS NOT NULL OR evals_checked_at IS NOT NULL) checked,
                MAX(updated_at) updated_at,
                MIN(accuracy_next_retry_at) accuracy_retry_at,
                MIN(evals_next_retry_at) evals_retry_at
              FROM lichess_analysis_backfill WHERE account_id=?
            """, (account_id,)).fetchone()
        summary = {key: row[key] for key in row.keys()} if row else {
            "total": 0, "accuracy": 0, "evals": 0, "no_data": 0,
            "errors": 0, "pending": 0, "checked": 0,
            "updated_at": None, "accuracy_retry_at": None, "evals_retry_at": None}
        retry_values = [str(summary[key]) for key in ("accuracy_retry_at", "evals_retry_at")
                        if summary.get(key)]
        account_retry = self.lichess_analysis_retry_after(account_id)
        if account_retry:
            retry_values.append(str(account_retry))
        summary["retry_at"] = min(retry_values) if retry_values else None
        return summary

    def lichess_analysis_backfill_state(self, account_id: str, game_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM lichess_analysis_backfill "
                             "WHERE account_id=? AND game_id=?", (account_id, game_id)).fetchone()
        return dict(row) if row else None

    def reserve_lichess_analysis_request(self, account_id: str, *, daily_limit: int = 25) -> bool:
        """Atomically enforce a per-account UTC daily request budget across restarts."""
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        daily_limit = max(1, min(100, int(daily_limit)))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT utc_day,requests,paused,retry_after_at FROM lichess_analysis_backfill_budget "
                             "WHERE account_id=?", (account_id,)).fetchone()
            if row and row["paused"]:
                return False
            if row and row["retry_after_at"] and row["retry_after_at"] > now.isoformat():
                return False
            used = int(row["requests"]) if row and row["utc_day"] == day else 0
            if used >= daily_limit:
                tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(),
                                            tzinfo=timezone.utc).isoformat()
                db.execute("UPDATE lichess_analysis_backfill_budget SET retry_after_at=?,updated_at=? "
                           "WHERE account_id=?", (tomorrow, now.isoformat(), account_id))
                return False
            db.execute("INSERT INTO lichess_analysis_backfill_budget(account_id,utc_day,requests,updated_at) "
                       "VALUES(?,?,1,?) ON CONFLICT(account_id) DO UPDATE SET utc_day=excluded.utc_day,"
                       "requests=CASE WHEN lichess_analysis_backfill_budget.utc_day=excluded.utc_day "
                       "THEN lichess_analysis_backfill_budget.requests+1 ELSE 1 END,updated_at=excluded.updated_at",
                       (account_id, day, now.isoformat()))
            if used + 1 >= daily_limit:
                tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(),
                                            tzinfo=timezone.utc).isoformat()
                db.execute("UPDATE lichess_analysis_backfill_budget SET retry_after_at=?,updated_at=? "
                           "WHERE account_id=?", (tomorrow, now.isoformat(), account_id))
            return True

    def set_lichess_analysis_backfill_paused(self, account_id: str, paused: bool) -> None:
        now = datetime.now(timezone.utc)
        with self._connection() as db:
            db.execute("INSERT INTO lichess_analysis_backfill_budget(account_id,utc_day,requests,paused,updated_at) "
                       "VALUES(?,?,0,?,?) ON CONFLICT(account_id) DO UPDATE SET paused=excluded.paused,"
                       "updated_at=excluded.updated_at",
                       (account_id, now.date().isoformat(), int(bool(paused)), now.isoformat()))

    def set_lichess_analysis_retry_after(self, account_id: str, seconds: int) -> None:
        now = datetime.now(timezone.utc)
        retry_at = (now + timedelta(seconds=max(1, int(seconds)))).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO lichess_analysis_backfill_budget "
                       "(account_id,utc_day,requests,retry_after_at,updated_at) VALUES(?,?,0,?,?) "
                       "ON CONFLICT(account_id) DO UPDATE SET retry_after_at=excluded.retry_after_at,"
                       "updated_at=excluded.updated_at",
                       (account_id, now.date().isoformat(), retry_at, now.isoformat()))

    def lichess_analysis_retry_after(self, account_id: str) -> str | None:
        with self._connection() as db:
            row = db.execute("SELECT retry_after_at FROM lichess_analysis_backfill_budget "
                             "WHERE account_id=?", (account_id,)).fetchone()
        return str(row[0]) if row and row[0] else None

    def lichess_analysis_backfill_paused(self, account_id: str) -> bool:
        with self._connection() as db:
            row = db.execute("SELECT paused FROM lichess_analysis_backfill_budget WHERE account_id=?",
                             (account_id,)).fetchone()
        return bool(row and row[0])

    def list_games_missing_lichess_evaluations(self, account_id: str, *,
                                                limit: int = 25,
                                                recheck_checked: bool = False) -> list[dict]:
        """Return unchecked incomplete Lichess lines, newest first and in bounded pages."""
        limit = max(1, min(100, int(limit)))
        checked_filter = "" if recheck_checked else (
            "AND (ers.status IS NULL OR ers.status NOT IN "
            "('checked_no_evals','partial','changed','api_failure') OR "
            "(ers.status='api_failure' AND ers.next_retry_at IS NOT NULL "
            "AND ers.next_retry_at<=?)) "
        )
        params: list[object] = [account_id]
        if not recheck_checked:
            params.append(datetime.now(timezone.utc).isoformat())
        params.append(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT g.game_id,gr.line_revision,gr.line_hash,g.started_at,g.variant,"
                "COALESCE(ers.status,'not_checked') AS refresh_status,ers.last_checked_at "
                "FROM games g JOIN account_games ag ON ag.game_id=g.game_id "
                "JOIN game_revisions gr ON gr.game_id=g.game_id AND gr.is_current=1 "
                "LEFT JOIN lichess_eval_refresh_state ers ON ers.account_id=ag.account_id "
                "AND ers.game_id=g.game_id AND ers.line_revision=gr.line_revision "
                "AND ers.line_hash=gr.line_hash "
                "WHERE ag.account_id=? AND g.status NOT IN "
                "('created','started','started_soon','playing') AND g.variant='standard' "
                "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
                "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
                "AND ar.line_hash=gr.line_hash AND ar.source IN ('lichess-pgn','lichess-json') "
                "AND ar.status='complete' AND "
                "(SELECT COUNT(*) FROM position_assessments pa WHERE pa.run_id=ar.run_id "
                "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) >= "
                "(SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=g.game_id "
                "AND gm.line_revision=gr.line_revision)) " + checked_filter +
                "ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,"
                "g.started_at DESC,g.game_id DESC LIMIT ?", params).fetchall()
        return [dict(row) for row in rows]

    def lichess_eval_score_count(self, game_id: str, line_revision: int) -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT COUNT(DISTINCT pa.ply) FROM position_assessments pa "
                "JOIN analysis_runs ar ON ar.run_id=pa.run_id "
                "WHERE ar.game_id=? AND ar.line_revision=? AND ar.source='lichess-pgn' "
                "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)",
                (game_id, int(line_revision))).fetchone()
        return int(row[0])

    def game_move_count(self, game_id: str, line_revision: int) -> int:
        with self._connection() as db:
            row = db.execute("SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
                             (game_id, int(line_revision))).fetchone()
        return int(row[0])

    def lichess_eval_pair_count(self, game_id: str, line_revision: int) -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=? AND gm.line_revision=? "
                "AND EXISTS (SELECT 1 FROM position_assessments pre JOIN analysis_runs ar "
                "ON ar.run_id=pre.run_id WHERE ar.game_id=gm.game_id "
                "AND ar.line_revision=gm.line_revision AND ar.source='lichess-pgn' "
                "AND pre.ply=gm.ply-1 AND (pre.white_cp IS NOT NULL OR pre.mate_white IS NOT NULL)) "
                "AND EXISTS (SELECT 1 FROM position_assessments post JOIN analysis_runs ar "
                "ON ar.run_id=post.run_id WHERE ar.game_id=gm.game_id "
                "AND ar.line_revision=gm.line_revision AND ar.source='lichess-pgn' "
                "AND post.ply=gm.ply AND (post.white_cp IS NOT NULL OR post.mate_white IS NOT NULL))",
                (game_id, int(line_revision))).fetchone()
        return int(row[0])

    def record_lichess_eval_refresh(self, account_id: str, game_id: str, *,
                                    line_revision: int, line_hash: str, status: str,
                                    error: str | None = None, retry_after_seconds: int = 0) -> None:
        allowed = {"checked_no_evals", "partial", "changed", "api_failure"}
        if status not in allowed:
            raise ValueError("Invalid targeted evaluation refresh status")
        now = datetime.now(timezone.utc)
        last_checked = None if status == "api_failure" else now.isoformat()
        retry_at = ((now + timedelta(seconds=max(1, int(retry_after_seconds)))).isoformat()
                    if retry_after_seconds else None)
        with self._connection() as db:
            db.execute(
                "INSERT INTO lichess_eval_refresh_state(account_id,game_id,line_revision,line_hash,"
                "status,last_checked_at,next_retry_at,last_error) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id,game_id,line_revision,line_hash) DO UPDATE SET "
                "status=excluded.status,last_checked_at=excluded.last_checked_at,"
                "next_retry_at=excluded.next_retry_at,last_error=excluded.last_error",
                (account_id, game_id, int(line_revision), line_hash, status,
                 last_checked, retry_at, error))

    def list_games_needing_fast_analysis(self, *, account_id: str | None = None,
                                         limit: int = 1000,
                                         engine_version: str | None = None,
                                         parameters: dict | None = None,
                                         retry_failed: bool = False) -> list[dict]:
        """Page recent eligible lines, skipping work already represented for this config."""
        limit = max(1, min(10_000, int(limit)))
        join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
        account_filter = "AND ag.account_id=? " if account_id else ""
        params: list[object] = [account_id] if account_id else []
        represented = ""
        if engine_version is not None and parameters is not None:
            params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
            represented_job_statuses = ("'queued','running','paused','complete'" if retry_failed
                                        else "'queued','running','paused','complete','failed','cancelled','stale'")
            represented = (
                "AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj "
                "WHERE aj.game_id=g.game_id AND aj.line_revision=gr.line_revision "
                "AND aj.line_hash=gr.line_hash AND aj.analysis_pass='fast' "
                "AND aj.engine_version=? AND aj.parameters_json=? "
                f"AND aj.status IN ({represented_job_statuses})) "
                "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
                "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
                "AND ar.line_hash=gr.line_hash AND ar.source='stockfish' "
                "AND ar.engine_version=? AND ar.parameters_json=? AND ar.status='complete') "
            )
            params.extend([engine_version, params_json, engine_version, params_json])
        params.append(limit)
        sql = (
            "SELECT DISTINCT g.game_id,gr.line_revision,gr.line_hash,g.started_at,g.variant "
            "FROM games g " + join +
            "JOIN game_revisions gr ON gr.game_id=g.game_id AND gr.is_current=1 "
            "WHERE g.status IN ('mate','resign','stalemate','timeout','draw','outoftime',"
            "'cheat','noStart','variantEnd','finished') AND COALESCE(g.final_sync_pending,0)=0 "
            "AND g.import_error IS NULL AND g.pgn IS NOT NULL "
            "AND (g.pgn LIKE '%[Result \"1-0\"]%' OR g.pgn LIKE '%[Result \"0-1\"]%' "
            "OR g.pgn LIKE '%[Result \"1/2-1/2\"]%') AND g.variant='standard' "
            + account_filter +
            "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
            "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
            "AND ar.line_hash=gr.line_hash AND ar.source IN ('lichess-pgn','lichess-json') "
            "AND ar.status='complete' AND "
            "(SELECT COUNT(*) FROM position_assessments pa WHERE pa.run_id=ar.run_id "
            "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) >= "
            "(SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=g.game_id "
            "AND gm.line_revision=gr.line_revision)) "
            + represented +
            "ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,"
            "g.started_at DESC,g.game_id DESC LIMIT ?")
        with self._connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def enqueue_analysis_jobs(self, candidates: list[dict], *, analysis_pass: str,
                              engine_version: str, parameters: dict,
                              retry_failed: bool = False) -> dict[str, int]:
        if analysis_pass not in {"fast", "review"}:
            raise ValueError("Unknown analysis pass")
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        queued = skipped = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                game_id = str(candidate["game_id"])
                revision = int(candidate["line_revision"])
                line_hash = str(candidate["line_hash"])
                job_id = uuid.uuid4().hex
                if retry_failed:
                    requeued = db.execute(
                        "UPDATE analysis_jobs SET status='queued',attempts=0,lease_owner=NULL,"
                        "lease_until=NULL,result_run_id=NULL,error=NULL,started_at=NULL,finished_at=NULL,"
                        "updated_at=? WHERE game_id=? AND line_revision=? AND line_hash=? "
                        "AND analysis_pass=? AND engine_version=? AND parameters_json=? "
                        "AND status IN ('failed','cancelled','stale')",
                        (now, game_id, revision, line_hash, analysis_pass, engine_version, params_json))
                    if requeued.rowcount:
                        queued += 1
                        continue
                cursor = db.execute(
                    "INSERT OR IGNORE INTO analysis_jobs(job_id,game_id,line_revision,line_hash,"
                    "analysis_pass,engine_version,parameters_json,status,attempts,priority_at,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',0,?,?,?)",
                    (job_id, game_id, revision, line_hash, analysis_pass, engine_version,
                     params_json, candidate.get("started_at"), now, now))
                if cursor.rowcount:
                    queued += 1
                else:
                    skipped += 1
        return {"queued": queued, "already_present": skipped}

    def pending_fast_analysis_parameters(self, engine_version: str) -> dict | None:
        """Return the persisted configuration of the newest resumable fast queue."""
        with self._connection() as db:
            row = db.execute(
                "SELECT parameters_json FROM analysis_jobs WHERE analysis_pass='fast' "
                "AND engine_version=? AND status IN ('queued','paused','running') "
                "ORDER BY updated_at DESC,created_at DESC LIMIT 1", (engine_version,)).fetchone()
        if not row:
            return None
        try:
            parameters = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return parameters if isinstance(parameters, dict) else None

    def claim_next_analysis_job(self, worker_id: str, *, engine_version: str,
                                analysis_pass: str, parameters: dict | None = None,
                                lease_seconds: int = 300) -> dict | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        lease_seconds = max(30, min(3600, int(lease_seconds)))
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":")) if parameters is not None else None
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            while True:
                params_clause = " AND parameters_json=?" if params_json is not None else ""
                values = [analysis_pass, engine_version]
                if params_json is not None:
                    values.append(params_json)
                values.append(now)
                row = db.execute(
                    "SELECT * FROM analysis_jobs WHERE analysis_pass=? AND engine_version=? "
                    + params_clause + " AND (status='queued' OR "
                    "(status='running' AND (lease_until IS NULL OR lease_until<?))) "
                    "ORDER BY CASE WHEN priority_at IS NULL THEN 1 ELSE 0 END,"
                    "priority_at DESC,created_at,job_id LIMIT 1", values).fetchone()
                if not row:
                    return None
                current = db.execute(
                    "SELECT line_revision,line_hash FROM game_revisions "
                    "WHERE game_id=? AND is_current=1", (row["game_id"],)).fetchone()
                if (not current or int(current["line_revision"]) != int(row["line_revision"])
                        or current["line_hash"] != row["line_hash"]):
                    db.execute("UPDATE analysis_jobs SET status='stale',updated_at=?,"
                                "lease_owner=NULL,lease_until=NULL WHERE job_id=?",
                                (now, row["job_id"]))
                    continue
                db.execute("UPDATE analysis_jobs SET status='running',attempts=attempts+1,"
                            "lease_owner=?,lease_until=?,started_at=?,updated_at=?,error=NULL "
                            "WHERE job_id=?",
                            (worker_id, until, now, now, row["job_id"]))
                result = dict(row)
                result.update(status="running", attempts=int(row["attempts"]) + 1,
                              lease_owner=worker_id, lease_until=until, started_at=now,
                              updated_at=now, error=None)
                return result

    def list_completed_fast_analysis_jobs(self, *, engine_version: str,
                                          parameters: dict,
                                          account_id: str | None = None) -> list[dict]:
        """List exact-config completed fast jobs with their persisted result runs."""
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        join = " JOIN account_games ag ON ag.game_id=aj.game_id " if account_id else ""
        account_clause = " AND ag.account_id=?" if account_id else ""
        values: list[object] = [engine_version, params_json]
        if account_id:
            values.append(account_id)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT DISTINCT aj.game_id,aj.line_revision,aj.line_hash,aj.result_run_id "
                "FROM analysis_jobs aj " + join + "WHERE aj.analysis_pass='fast' "
                "AND aj.engine_version=? AND aj.parameters_json=? AND aj.status='complete' "
                "AND aj.result_run_id IS NOT NULL" + account_clause +
                " ORDER BY aj.game_id", values)]

    def list_reviewable_analysis_runs(self, *, engine_version: str,
                                      parameters: dict,
                                      account_id: str | None = None,
                                      limit: int = 100, offset: int = 0) -> list[dict]:
        """Select a bounded, deterministic newest-first page for selective review."""
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        account_join = " JOIN account_games ag ON ag.game_id=ar.game_id " if account_id else ""
        account_clause = " AND ag.account_id=?" if account_id else ""
        values: list[object] = [engine_version, params_json]
        if account_id:
            values.append(account_id)
        sql = (
            "WITH eligible AS (SELECT ar.run_id,ar.game_id,ar.line_revision,ar.line_hash,ar.source,"
            "g.started_at,"
            "ROW_NUMBER() OVER(PARTITION BY ar.game_id ORDER BY "
            "CASE WHEN ar.source='stockfish' THEN 0 ELSE 1 END,ar.finished_at DESC,ar.run_id DESC) rank "
            "FROM analysis_runs ar JOIN games g ON g.game_id=ar.game_id "
            "JOIN game_revisions gr ON gr.game_id=ar.game_id "
            "AND gr.line_revision=ar.line_revision AND gr.is_current=1 "
            "LEFT JOIN analysis_jobs aj ON aj.result_run_id=ar.run_id " + account_join +
            "WHERE ((ar.source='stockfish' AND ar.status='complete' AND aj.analysis_pass='fast' "
            "AND aj.status='complete' AND aj.engine_version=? AND aj.parameters_json=?) "
            "OR (ar.source='lichess-pgn' AND ar.status IN ('complete','partial')))" +
            account_clause + ") SELECT run_id,game_id,line_revision,line_hash,source,started_at "
            "FROM eligible WHERE rank=1 ORDER BY CASE WHEN started_at IS NULL THEN 1 ELSE 0 END,"
            "started_at DESC,game_id DESC LIMIT ? OFFSET ?")
        values.extend([limit, offset])
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, values)]

    def renew_analysis_job_lease(self, job_id: str, worker_id: str, *,
                                 lease_seconds: int = 300) -> bool:
        lease_seconds = max(30, min(3600, int(lease_seconds)))
        now_dt = datetime.now(timezone.utc)
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE analysis_jobs SET lease_until=?,updated_at=? "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                ((now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                 now_dt.isoformat(), job_id, worker_id))
        return cursor.rowcount == 1

    def finish_analysis_job(self, job_id: str, worker_id: str, *, status: str,
                            result_run_id: str | None = None,
                            error: str | None = None) -> None:
        if status not in {"complete", "paused", "cancelled", "failed", "stale"}:
            raise ValueError("Invalid terminal analysis job status")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE analysis_jobs SET status=?,result_run_id=?,error=?,updated_at=?,"
                "finished_at=?,lease_owner=NULL,lease_until=NULL "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                (status, result_run_id, error[:500] if error else None, now,
                 now if status in {"complete", "cancelled", "failed", "stale"} else None,
                 job_id, worker_id))
            if cursor.rowcount != 1:
                raise RuntimeError("Analysis job lease was lost before it could be finalized.")

    def resume_paused_analysis_jobs(self, *, engine_version: str | None = None,
                                    parameters: dict | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        params_json = (json.dumps(parameters, sort_keys=True, separators=(",", ":"))
                       if parameters is not None else None)
        with self._connection() as db:
            if engine_version is None and params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused'", (now,))
            elif params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND engine_version=?",
                                    (now, engine_version))
            elif engine_version is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND parameters_json=?",
                                    (now, params_json))
            else:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND engine_version=? "
                                    "AND parameters_json=?",
                                    (now, engine_version, params_json))
        return cursor.rowcount

    def cancel_queued_analysis_jobs(self, *, engine_version: str | None = None,
                                    parameters: dict | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        params_json = (json.dumps(parameters, sort_keys=True, separators=(",", ":"))
                       if parameters is not None else None)
        with self._connection() as db:
            if engine_version is None and params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued'", (now, now))
            elif params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND engine_version=?",
                                    (now, now, engine_version))
            elif engine_version is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND parameters_json=?",
                                    (now, now, params_json))
            else:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND engine_version=? "
                                    "AND parameters_json=?",
                                    (now, now, engine_version, params_json))
        return cursor.rowcount

    def analysis_job_counts(self, *, engine_version: str | None = None,
                            parameters: dict | None = None) -> dict[str, int]:
        clauses: list[str] = []
        values: list[str] = []
        if engine_version is not None:
            clauses.append("engine_version=?")
            values.append(engine_version)
        if parameters is not None:
            clauses.append("parameters_json=?")
            values.append(json.dumps(parameters, sort_keys=True, separators=(",", ":")))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as db:
            rows = db.execute("SELECT status,COUNT(*) AS count FROM analysis_jobs" + where +
                              " GROUP BY status", tuple(values)).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
