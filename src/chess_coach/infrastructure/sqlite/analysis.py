"""Versioned game-analysis and position-assessment SQLite store mixin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
import json
import sqlite3
import uuid

import chess
from ...lichess.event_detectors import (
    DETECTOR_VERSION, detect_registered_events, select_review_positions,
)
from ...lichess.move_metrics import METRIC_VERSION, build_move_facts
from ...lichess.phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries, phase_at_ply

class AnalysisStoreMixin:

    def save_analysis(self, game_id: str, *, line_revision: int, line_hash: str,
                      source: str, engine_name: str | None, engine_version: str | None,
                      parameters: dict, assessments: list[dict],
                      classifications: list[dict], status: str = "complete",
                      error: str | None = None,
                      alternatives: list[dict] | None = None,
                      cancel_check: Callable[[], bool] | None = None) -> str:
        from ...lichess.move_metrics import build_move_facts

        self._wait_before_read(game_id)
        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, game_id, line_revision, line_hash, source, engine_name,
                        engine_version, json.dumps(parameters, sort_keys=True), now, now,
                        status, error))
            db.executemany("INSERT INTO position_assessments VALUES (?,?,?,?,?,?,?,?,?)",
                           [(run_id, game_id, line_revision, item["ply"], item.get("white_cp"),
                             item.get("mate_white"), item.get("depth"), item.get("nodes"),
                             " ".join(item.get("pv", []))) for item in assessments])
            db.executemany("""INSERT INTO analysis_alternatives (
                run_id,game_id,line_revision,ply,multipv_rank,white_cp,mate_white,
                depth,nodes,pv_uci
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(run_id, game_id, line_revision, item["ply"], item["multipv_rank"],
                  item.get("white_cp"), item.get("mate_white"), item.get("depth"),
                  item.get("nodes"), " ".join(item.get("pv", [])))
                 for item in alternatives or []])
            db.executemany("INSERT INTO move_classifications VALUES (?,?,?,?,?,?,?,?,?)",
                           [(run_id, game_id, line_revision, item["ply"], item["mover"],
                             item.get("phase", "unknown"), item.get("eval_loss_cp"),
                             item.get("label", "unknown"), item.get("algorithm_version", ""))
                            for item in classifications])
            moves = [dict(row) for row in db.execute(
                "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
                (game_id, line_revision))]
            revision_row = db.execute("SELECT initial_fen FROM game_revisions WHERE game_id=? "
                                      "AND line_revision=?", (game_id, line_revision)).fetchone()
            metrics, events = build_move_facts(
                assessments, moves, source=source, run_id=run_id,
                classifications=classifications,
                initial_fen=revision_row["initial_fen"] if revision_row else "startpos")
            for event in events:
                event.setdefault("tags", ["opportunity"])
            from ...lichess.event_detectors import detect_registered_events
            events.extend(detect_registered_events(
                assessments, moves, metrics, run_id=run_id, source=source))
            db.executemany("""INSERT INTO move_metrics (
                run_id,game_id,line_revision,ply,metric_version,source,
                white_cp_before,mate_white_before,white_cp_after,mate_white_after,
                cp_loss,mate_discontinuity,wdl_before_json,wdl_after_json,wdl_model,
                accuracy,accuracy_source,accuracy_formula_id,severity,
                severity_policy_version,legacy_label
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(run_id, game_id, line_revision, row["ply"], row["metric_version"], row["source"],
                  row["white_cp_before"], row["mate_white_before"], row["white_cp_after"],
                  row["mate_white_after"], row["cp_loss"], row["mate_discontinuity"],
                  row["wdl_before_json"], row["wdl_after_json"], row["wdl_model"],
                  row["accuracy"], row["accuracy_source"], row["accuracy_formula_id"],
                  row["severity"], row["severity_policy_version"], row["legacy_label"])
                 for row in metrics])
            self._insert_event_rows(db, events, run_id, game_id, line_revision)
            # This final check runs inside the same SQLite transaction. If the
            # worker was cancelled before the commit boundary, raising here
            # rolls back only this new run and leaves prior valid runs intact.
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Analysis cancelled before SQLite commit.")
        self.invalidate_statistics_cache()
        return run_id

    @staticmethod

    def _insert_event_rows(db: sqlite3.Connection, events: list[dict], run_id: str,
                           game_id: str, line_revision: int, *, ignore: bool = False) -> None:
        insert = "INSERT OR IGNORE" if ignore else "INSERT"
        db.executemany(f"""{insert} INTO analysis_events (
            event_key,run_id,game_id,line_revision,ply,event_type,confidence_state,
            source,detector_version,evidence_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(row["event_key"], run_id, game_id, line_revision, row["ply"],
              row["event_type"], row["confidence_state"], row["source"],
              row["detector_version"], row["evidence_json"]) for row in events])
        db.executemany("""INSERT OR IGNORE INTO event_tags (
            event_key,tag,detector_version,confidence_state,evidence_json
        ) VALUES (?,?,?,?,?)""",
            [(row["event_key"], tag, row["detector_version"], row["confidence_state"],
              row["evidence_json"]) for row in events for tag in row.get("tags", [])])

    @classmethod

    def _insert_derived_metrics_for_run(cls, db: sqlite3.Connection, run_id: str,
                                        *, user_color: str | None = None,
                                        replace: bool = False) -> int:
        """Materialize canonical v2 move facts from one persisted source run, without UCI."""
        from ...lichess.move_metrics import build_move_facts
        from ...lichess.event_detectors import detect_registered_events

        run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            return 0
        assessments = [dict(row) for row in db.execute(
            "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run_id,))]
        for item in assessments:
            item["pv"] = str(item.get("pv_uci") or "").split()
        moves = [dict(row) for row in db.execute(
            "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
            (run["game_id"], run["line_revision"]))]
        revision = db.execute("SELECT initial_fen FROM game_revisions WHERE game_id=? "
                              "AND line_revision=?", (run["game_id"], run["line_revision"])).fetchone()
        classifications = [dict(row) for row in db.execute(
            "SELECT * FROM move_classifications WHERE run_id=? ORDER BY ply", (run_id,))]
        metrics, events = build_move_facts(
            assessments, moves, source=run["source"], run_id=run_id,
            classifications=classifications,
            initial_fen=revision["initial_fen"] if revision else "startpos")
        for event in events:
            event.setdefault("tags", ["opportunity"])
        events.extend(detect_registered_events(
            assessments, moves, metrics, run_id=run_id, source=run["source"]))
        if replace:
            from ...lichess.move_metrics import METRIC_VERSION
            db.execute("DELETE FROM move_metrics WHERE run_id=? AND metric_version=?",
                       (run_id, METRIC_VERSION))
            db.execute("DELETE FROM analysis_events WHERE run_id=? AND "
                       "(detector_version='cc-opportunity-candidate-v1' "
                       "OR detector_version LIKE 'cc-base-events-v1:%')", (run_id,))
        db.executemany("""INSERT OR IGNORE INTO move_metrics (
            run_id,game_id,line_revision,ply,metric_version,source,
            white_cp_before,mate_white_before,white_cp_after,mate_white_after,
            cp_loss,mate_discontinuity,wdl_before_json,wdl_after_json,wdl_model,
            accuracy,accuracy_source,accuracy_formula_id,severity,
            severity_policy_version,legacy_label
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, run["game_id"], run["line_revision"], row["ply"],
              row["metric_version"], row["source"], row["white_cp_before"],
              row["mate_white_before"], row["white_cp_after"], row["mate_white_after"],
              row["cp_loss"], row["mate_discontinuity"], row["wdl_before_json"],
              row["wdl_after_json"], row["wdl_model"], row["accuracy"],
              row["accuracy_source"], row["accuracy_formula_id"], row["severity"],
              row["severity_policy_version"], row["legacy_label"]) for row in metrics])
        cls._insert_event_rows(db, events, run_id, run["game_id"], run["line_revision"], ignore=True)
        return len(metrics)

    @staticmethod

    def _load_analysis_events(db: sqlite3.Connection, run_id: str) -> list[dict]:
        from ...lichess.event_detectors import DETECTOR_VERSION

        # Retain superseded detector evidence in SQLite for audit, but expose only
        # the active base-detector version in reports and review planning.
        events = [dict(row) for row in db.execute(
            "SELECT * FROM analysis_events WHERE run_id=? AND "
            "(detector_version NOT LIKE 'cc-base-events-v%' OR detector_version LIKE ?) "
            "ORDER BY ply,event_type,detector_version",
            (run_id, DETECTOR_VERSION + ":%"))]
        for event in events:
            event["tags"] = [row[0] for row in db.execute(
                "SELECT tag FROM event_tags WHERE event_key=? ORDER BY tag",
                (event["event_key"],))]
        return events

    @staticmethod

    def _load_move_metrics(db: sqlite3.Connection, run_id: str) -> list[dict]:
        rows = db.execute(
            "SELECT mm.*,gm.mover,CASE WHEN pb.middle_start_ply IS NOT NULL "
            "AND mm.ply<pb.middle_start_ply THEN 'opening' WHEN pb.end_start_ply IS NOT NULL "
            "AND mm.ply>=pb.end_start_ply THEN 'endgame' WHEN pb.middle_start_ply IS NOT NULL "
            "AND mm.ply>=pb.middle_start_ply THEN 'middlegame' ELSE 'unknown' END phase "
            "FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
            "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
            "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id "
            "AND pb.line_revision=mm.line_revision WHERE mm.run_id=? ORDER BY mm.ply",
            (run_id,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod

    def _load_review_coverage(db: sqlite3.Connection, game_id: str,
                              line_revision: int, line_hash: str) -> dict | None:
        row = db.execute(
            "SELECT ar.run_id,aj.parameters_json,aj.status FROM analysis_jobs aj "
            "JOIN analysis_runs ar ON ar.run_id=aj.result_run_id WHERE aj.game_id=? "
            "AND aj.line_revision=? AND aj.line_hash=? AND aj.analysis_pass='review' "
            "AND aj.status='complete' ORDER BY aj.updated_at DESC,aj.job_id DESC LIMIT 1",
            (game_id, line_revision, line_hash)).fetchone()
        if not row:
            return None
        params = json.loads(row["parameters_json"])
        reviewed = db.execute(
            "SELECT COUNT(*) FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
            "AND cp_loss IS NOT NULL", (row["run_id"],)).fetchone()[0]
        total_moves = db.execute(
            "SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
            (game_id, line_revision)).fetchone()[0]
        return {"status": row["status"], "reviewed_moves": int(reviewed),
                "selected_moves": len(params.get("review_move_plies", [])),
                "total_moves": int(total_moves), "source": "stockfish-review"}

    def find_analysis_cache(self, game_id: str, line_revision: int, line_hash: str,
                            source: str, engine_version: str | None,
                            parameters: dict, *, account_id: str | None = None,
                            user_color: str | None = None) -> dict | None:
        params_json = json.dumps(parameters, sort_keys=True)
        usable_statuses = ("('complete','partial')" if source == "lichess-pgn" or
                           parameters.get("analysis_pass") == "review" else "('complete')")
        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE game_id=? AND line_revision=? "
                             "AND line_hash=? AND source=? AND engine_version IS ? "
                             f"AND parameters_json=? AND status IN {usable_statuses} "
                             "ORDER BY finished_at DESC LIMIT 1",
                             (game_id, line_revision, line_hash, source, engine_version,
                              params_json)).fetchone()
            if not run:
                return None
            result = dict(run)
            result["assessments"] = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["classifications"] = [dict(row) for row in db.execute(
                "SELECT * FROM move_classifications WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["move_metrics"] = self._load_move_metrics(db, run["run_id"])
            result["alternatives"] = [dict(row) for row in db.execute(
                "SELECT * FROM analysis_alternatives WHERE run_id=? ORDER BY ply,multipv_rank",
                (run["run_id"],))]
            result["events"] = self._load_analysis_events(db, run["run_id"])
            coverage = db.execute("SELECT * FROM analysis_coverage WHERE run_id=?",
                                  (run["run_id"],)).fetchone()
            result["coverage"] = dict(coverage) if coverage else None
            result["decision_coverage"] = self._decision_coverage(
                db, result, game_id, account_id=account_id, user_color=user_color)
            result["review_coverage"] = self._load_review_coverage(
                db, game_id, int(run["line_revision"]), str(run["line_hash"]))
            self._attach_official_accuracy(db, result, game_id, account_id=account_id,
                                           user_color=user_color)
            result["local_game_accuracy"] = None
            result["local_game_accuracy_formula_id"] = None
            result["local_accuracy_status"] = {
                "status": "unsupported",
                "reason": "Локальная accuracy не рассчитывается: CPL и категории ошибок не являются процентом точности.",
                "coverage": {"comparable_own_moves": result["decision_coverage"]["comparable_own_moves"],
                             "own_moves": result["decision_coverage"]["own_moves"]},
            }
            return result

    def list_analyses(self, game_id: str, line_revision: int | None = None) -> list[dict]:
        sql = "SELECT * FROM analysis_runs WHERE game_id=?"
        params: list[object] = [game_id]
        if line_revision is not None:
            sql += " AND line_revision=?"
            params.append(line_revision)
        sql += " ORDER BY finished_at DESC"
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def latest_analysis_detail(self, game_id: str, line_revision: int, *,
                               account_id: str | None = None,
                               user_color: str | None = None) -> dict | None:
        """Load one current analysis and its plotted points/classifications for a detail view."""
        with self._connection() as db:
            materialize = db.execute(
                "SELECT ar.run_id FROM analysis_runs ar JOIN game_revisions gr "
                "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                "WHERE ar.game_id=? AND ar.line_revision=? AND gr.is_current=1 "
                "AND ar.status IN ('complete','partial') AND NOT EXISTS "
                "(SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id "
                "AND aj.analysis_pass='review') ORDER BY "
                "CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,ar.finished_at DESC,"
                "ar.run_id DESC LIMIT 1", (game_id, line_revision)).fetchone()
            if materialize:
                has_metrics = db.execute("SELECT 1 FROM move_metrics WHERE run_id=? LIMIT 1",
                                         (materialize["run_id"],)).fetchone()
            else:
                has_metrics = True
        if materialize and not has_metrics:
            # Legacy reports are hydrated from saved assessments only; this never opens UCI.
            self.materialize_analysis_metrics(materialize["run_id"])
        with self._connection() as db:
            run = db.execute(
                "SELECT ar.* FROM analysis_runs ar JOIN game_revisions gr "
                "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                "WHERE ar.game_id=? AND ar.line_revision=? AND gr.is_current=1 "
                "AND ar.status IN ('complete','partial') AND NOT EXISTS "
                "(SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id "
                "AND aj.analysis_pass='review') "
                "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,"
                "ar.finished_at DESC,ar.run_id DESC LIMIT 1",
                (game_id, line_revision)).fetchone()
            if not run:
                return None
            result = dict(run)
            result["assessments"] = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["classifications"] = [dict(row) for row in db.execute(
                "SELECT * FROM move_classifications WHERE run_id=? AND ply>0 ORDER BY ply",
                (run["run_id"],))]
            result["move_metrics"] = self._load_move_metrics(db, run["run_id"])
            result["alternatives"] = [dict(row) for row in db.execute(
                "SELECT * FROM analysis_alternatives WHERE run_id=? ORDER BY ply,multipv_rank",
                (run["run_id"],))]
            result["events"] = self._load_analysis_events(db, run["run_id"])
            coverage = db.execute("SELECT * FROM analysis_coverage WHERE run_id=?",
                                  (run["run_id"],)).fetchone()
            result["coverage"] = dict(coverage) if coverage else None
            result["decision_coverage"] = self._decision_coverage(
                db, result, game_id, account_id=account_id, user_color=user_color)
            result["review_coverage"] = self._load_review_coverage(
                db, game_id, int(run["line_revision"]), str(run["line_hash"]))
            self._attach_official_accuracy(db, result, game_id, account_id=account_id,
                                           user_color=user_color)
            result["local_game_accuracy"] = None
            result["local_game_accuracy_formula_id"] = None
            result["local_accuracy_status"] = {
                "status": "unsupported",
                "reason": "Локальная accuracy не рассчитывается: CPL и категории ошибок не являются процентом точности.",
                "coverage": {"comparable_own_moves": result["decision_coverage"]["comparable_own_moves"],
                             "own_moves": result["decision_coverage"]["own_moves"]},
            }
            return result

    @staticmethod

    def _decision_coverage(db: sqlite3.Connection, analysis: dict, game_id: str, *,
                           account_id: str | None, user_color: str | None) -> dict:
        game = db.execute("SELECT color FROM games WHERE game_id=?", (game_id,)).fetchone()
        linked = (db.execute("SELECT color FROM account_games WHERE account_id=? AND game_id=?",
                             (account_id, game_id)).fetchone() if account_id else None)
        color = (linked["color"] if linked else None) or (
            user_color if user_color in {"white", "black"} else
            game["color"] if game else None)
        if color not in {"white", "black"}:
            return {"owner_color": None, "own_moves": 0, "comparable_own_moves": 0,
                    "missing_own_move_plies": [], "first_own_move_missing_baseline": False}
        run_id = str(analysis["run_id"])
        revision = int(analysis["line_revision"])
        own_moves = int(db.execute(
            "SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=? AND mover=?",
            (game_id, revision, color)).fetchone()[0])
        comparable = int(db.execute(
            "SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
            "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
            "WHERE mm.run_id=? AND gm.mover=? AND mm.cp_loss IS NOT NULL",
            (run_id, color)).fetchone()[0])
        missing = [int(row[0]) for row in db.execute(
            "SELECT gm.ply FROM game_moves gm WHERE gm.game_id=? AND gm.line_revision=? "
            "AND gm.mover=? AND NOT EXISTS (SELECT 1 FROM position_assessments pre "
            "WHERE pre.run_id=? AND pre.ply=gm.ply-1 AND "
            "(pre.white_cp IS NOT NULL OR pre.mate_white IS NOT NULL)) ORDER BY gm.ply",
            (game_id, revision, color, run_id))]
        source_post_scores = int(db.execute(
            "SELECT COUNT(*) FROM position_assessments WHERE run_id=? AND ply>0 "
            "AND (white_cp IS NOT NULL OR mate_white IS NOT NULL)", (run_id,)).fetchone()[0])
        expected = int(db.execute("SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
                                  (game_id, revision)).fetchone()[0])
        return {"owner_color": color, "own_moves": own_moves,
                "comparable_own_moves": comparable, "missing_own_move_plies": missing,
                "first_own_move_missing_baseline": bool(missing and min(missing) == (
                    1 if color == "white" else 2)),
                "source_eval_scored_post_moves": source_post_scores,
                "source_eval_expected_post_moves": expected,
                "source_eval_complete": source_post_scores >= expected if expected else False}

    @staticmethod

    def _attach_official_accuracy(db: sqlite3.Connection, result: dict, game_id: str, *,
                                  account_id: str | None, user_color: str | None) -> None:
        game = db.execute("SELECT source,source_game_id,color,lichess_accuracy_white,"
                          "lichess_accuracy_black FROM games WHERE game_id=?", (game_id,)).fetchone()
        linked_color = None
        if account_id:
            link = db.execute("SELECT color FROM account_games WHERE account_id=? AND game_id=?",
                              (account_id, game_id)).fetchone()
            linked_color = link["color"] if link else None
        color = (linked_color if account_id else
                 user_color if user_color in {"white", "black"} else
                 game["color"] if game else None)
        is_lichess = bool(game and (game["source"] in {"lichess", "lichess_archive"}
                                    or game["source_game_id"]
                                    or db.execute("SELECT 1 FROM account_games WHERE game_id=? LIMIT 1",
                                                  (game_id,)).fetchone()))
        value = None
        if is_lichess and (not account_id or linked_color):
            column = "lichess_accuracy_white" if color == "white" else (
                "lichess_accuracy_black" if color == "black" else None)
            if column:
                value = game[column]
        result["official_game_accuracy"] = value
        result["official_game_accuracy_source"] = "lichess" if is_lichess else None
        result["official_game_accuracy_color"] = color
        result["official_game_accuracy_n"] = int(value is not None)

    def materialize_analysis_metrics(self, run_id: str) -> int:
        """Atomically replace v2 facts from persisted assessments; no engine is started."""
        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return 0
            db.execute("BEGIN IMMEDIATE")
            count = self._insert_derived_metrics_for_run(db, run_id, replace=True)
        self.invalidate_statistics_cache()
        return count

    def reclassify_analysis_events(self, run_id: str) -> int:
        """Run registered pure detectors over one saved run, with no Stockfish access."""
        from ...lichess.event_detectors import detect_registered_events

        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return 0
            assessments = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run_id,))]
            for row in assessments:
                row["pv"] = row.get("pv_uci", "").split()
            moves = [dict(row) for row in db.execute(
                "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
                (run["game_id"], run["line_revision"]))]
            metrics = [dict(row) for row in db.execute(
                "SELECT * FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
                "ORDER BY ply", (run_id,))]
            before = int(db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                                    (run_id,)).fetchone()[0])
        events = detect_registered_events(
            assessments, moves, metrics, run_id=run_id, source=run["source"])
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_event_rows(db, events, run_id, run["game_id"],
                                    run["line_revision"], ignore=True)
            after = int(db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                                   (run_id,)).fetchone()[0])
        return max(0, after - before)

    def plan_review_positions(self, run_id: str, *, budget_per_game: int = 12,
                              budget_total: int = 100) -> list[dict]:
        """Return a capped review plan from stored metrics; this method never starts UCI."""
        from ...lichess.event_detectors import select_review_positions

        with self._connection() as db:
            metrics = [dict(row) for row in db.execute(
                "SELECT * FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
                "ORDER BY ply", (run_id,))]
            events = self._load_analysis_events(db, run_id)
        return select_review_positions(metrics, events, budget_per_game=budget_per_game,
                                       budget_total=budget_total)

    def phase_for_ply(self, game_id: str, line_revision: int, ply: int) -> str:
        from ...lichess.phases import phase_at_ply

        with self._connection() as db:
            row = db.execute("SELECT middle_start_ply,end_start_ply FROM game_phase_boundaries "
                             "WHERE game_id=? AND line_revision=?", (game_id,line_revision)).fetchone()
        return phase_at_ply(row["middle_start_ply"], row["end_start_ply"], int(ply)) if row else "unknown"

    def phase_boundaries(self, game_id: str, line_revision: int) -> dict | None:
        """Return phase boundaries together with their source and method version."""
        with self._connection() as db:
            row = db.execute("SELECT * FROM game_phase_boundaries WHERE game_id=? AND line_revision=?",
                             (game_id, line_revision)).fetchone()
        return dict(row) if row else None

    def infer_missing_phase_boundaries(self, limit: int = 25) -> int:
        """Explicit bounded phase-fallback backfill for current standard lines only."""
        from ...lichess.phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries

        limit = max(1, min(int(limit), 100))
        with self._connection() as db:
            ids = [str(row[0]) for row in db.execute(
                "SELECT g.game_id FROM games g LEFT JOIN game_phase_boundaries p "
                "ON p.game_id=g.game_id AND p.line_revision=g.line_revision "
                "WHERE g.variant='standard' AND p.game_id IS NULL "
                "AND g.status NOT IN ('created','started','started_soon','playing') "
                "AND g.import_error IS NULL ORDER BY COALESCE(g.started_at,g.updated_at) DESC,"
                "g.game_id LIMIT ?", (limit,))]
        saved = 0
        for game_id in ids:
            line = self.current_line(game_id)
            if not line:
                continue
            try:
                board = chess.Board() if line["initial_fen"] == "startpos" else chess.Board(line["initial_fen"])
                if not board.is_valid():
                    continue
                boards = [board.copy(stack=False)]
                for raw in str(line["moves"]).split():
                    move = chess.Move.from_uci(raw)
                    if move not in board.legal_moves:
                        boards = []
                        break
                    board.push(move)
                    boards.append(board.copy(stack=False))
            except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
                continue
            if not boards:
                continue
            middle, end = infer_phase_boundaries(boards)
            if middle is None and end is None:
                continue
            with self._connection() as db:
                cur = db.execute("INSERT OR IGNORE INTO game_phase_boundaries VALUES (?,?,?,?,?,?)",
                    (game_id, int(line["line_revision"]), middle, end,
                     "material-fallback", PHASE_FALLBACK_VERSION))
                saved += int(cur.rowcount == 1)
                if cur.rowcount == 1:
                    self._apply_phase_labels(db, game_id, int(line["line_revision"]), middle, end)
        return saved

    @staticmethod

    def _apply_phase_labels(db: sqlite3.Connection, game_id: str, line_revision: int,
                            middle: int | None, end: int | None) -> None:
        from ...lichess.phases import phase_at_ply

        rows = db.execute("SELECT mc.run_id,mc.ply FROM move_classifications mc "
                          "JOIN analysis_runs ar ON ar.run_id=mc.run_id "
                          "WHERE mc.game_id=? AND mc.line_revision=?",
                          (game_id, line_revision)).fetchall()
        db.executemany("UPDATE move_classifications SET phase=? WHERE run_id=? AND ply=?",
                       [(phase_at_ply(middle, end, int(row["ply"])), row["run_id"], row["ply"])
                        for row in rows])

    def save_opening_match(self, game_id: str, line_revision: int, match: dict | None) -> None:
        """Store a versioned opening reference without replacing Lichess metadata."""
        self._wait_before_read(game_id)
        with self._connection() as db:
            if match is None:
                db.execute("DELETE FROM opening_matches WHERE game_id=? AND line_revision=?",
                           (game_id, line_revision))
                return
            game = db.execute("SELECT opening_eco,opening_name FROM games WHERE game_id=?",
                              (game_id,)).fetchone()
            if not game:
                raise KeyError(game_id)
            conflict = bool((game["opening_eco"] and game["opening_eco"] != match["eco"]) or
                            (game["opening_name"] and game["opening_name"] != match["name"]))
            db.execute("INSERT INTO opening_matches VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                       "dataset_revision=excluded.dataset_revision,algorithm_version=excluded.algorithm_version,"
                       "eco=excluded.eco,name=excluded.name,family=excluded.family,variation=excluded.variation,"
                       "matched_ply=excluded.matched_ply,lichess_conflict=excluded.lichess_conflict,"
                       "matched_at=excluded.matched_at",
                       (game_id, line_revision, match["dataset_revision"], match["algorithm_version"],
                        match["eco"], match["name"], match["family"], match.get("variation"),
                        match["matched_ply"], int(conflict), datetime.now(timezone.utc).isoformat()))
        self.invalidate_statistics_cache()

    def opening_match(self, game_id: str, line_revision: int | None = None) -> dict | None:
        with self._connection() as db:
            if line_revision is None:
                row = db.execute("SELECT om.* FROM opening_matches om JOIN games g "
                                 "ON g.game_id=om.game_id AND g.line_revision=om.line_revision "
                                 "WHERE om.game_id=?", (game_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM opening_matches WHERE game_id=? AND line_revision=?",
                                 (game_id, line_revision)).fetchone()
        return dict(row) if row else None
