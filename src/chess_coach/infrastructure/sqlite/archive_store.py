"""Account links, archive import and durable archive-sync state mixin."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import sqlite3
import uuid

import chess
import chess.pgn

class ArchiveStoreMixin:
    @staticmethod

    def _phase_ply(value: object) -> int | None:
        if isinstance(value, dict):
            value = value.get("begin")
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

    def ensure_archive_account(self, username: str) -> str:
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("Lichess username is required")
        account_id = f"lichess:{normalized}"
        if normalized in self._archive_account_cache:
            return account_id
        with self._connection() as db:
            db.execute("INSERT INTO accounts(account_id,provider,username,created_at) "
                       "VALUES (?,'lichess',?,?) ON CONFLICT(account_id) DO UPDATE SET "
                       "username=excluded.username", (account_id, username.strip(),
                       datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR IGNORE INTO archive_sync_state(account_id,updated_at) "
                       "VALUES (?,?)", (account_id, datetime.now(timezone.utc).isoformat()))
        self._archive_account_cache[normalized] = account_id
        return account_id

    def archive_sync_state(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM archive_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return dict(row) if row else {}

    def begin_archive_pilot_run(self, account_id: str, requested_limit: int) -> str:
        if not 5 <= int(requested_limit) <= 10:
            raise ValueError("Пробный импорт должен быть ограничен 5–10 партиями.")
        run_id = uuid.uuid4().hex
        with self._connection() as db:
            db.execute("INSERT INTO archive_pilot_runs(run_id,account_id,requested_limit,status,started_at) "
                       "VALUES (?,?,?,'running',?)",
                       (run_id, account_id, int(requested_limit), datetime.now(timezone.utc).isoformat()))
        return run_id

    def record_archive_pilot_received(self, run_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_pilot_runs SET received_count=received_count+1 WHERE run_id=?",
                       (run_id,))

    def finish_archive_pilot_run(self, run_id: str, status: str, error: str | None = None) -> None:
        if status not in {"completed", "interrupted", "error"}:
            raise ValueError("Unknown pilot run status")
        with self._connection() as db:
            db.execute("UPDATE archive_pilot_runs SET status=?,finished_at=?,last_error=? WHERE run_id=?",
                       (status, datetime.now(timezone.utc).isoformat(),
                        error[:500] if error else None, run_id))

    def list_archive_pilot_runs(self, account_id: str, limit: int = 10) -> list[dict]:
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM archive_pilot_runs WHERE account_id=? ORDER BY started_at DESC LIMIT ?",
                (account_id, max(1, min(int(limit), 100))))]

    def import_archive_record(self, username: str, record: dict, *, checkpoint_ms: int | None,
                              backfill: bool, pilot_run_id: str | None = None,
                              _return_inserted: bool = False) -> str | tuple[str, bool]:
        """Upsert one exported PGN, link it to its account, then advance the durable cursor."""
        import io

        game_id = str(record.get("id") or "").strip()
        raw_pgn = str(record.get("pgn") or "")
        if not game_id or not raw_pgn:
            raise ValueError("Archive record requires id and pgn")
        parsed = chess.pgn.read_game(io.StringIO(raw_pgn))
        variant = str(record.get("variant") or
                      (parsed.headers.get("Variant") if parsed else None) or "standard").lower()
        if parsed is None or parsed.errors:
            details = "; ".join(str(error) for error in (parsed.errors if parsed else []))
            error = "Malformed PGN quarantined" + (f": {details[:300]}" if details else "")
            return self._import_raw_only_record(username, record, parsed, variant, raw_pgn,
                                                error, checkpoint_ms, backfill,
                                                pilot_run_id=pilot_run_id,
                                                _return_inserted=_return_inserted)
        if variant != "standard":
            # Keep non-standard games searchable/exportable without pretending their
            # move rules or initial position are standard chess.
            return self._import_raw_only_record(
                username, record, parsed, variant, raw_pgn,
                f"Unsupported variant: {variant}; board viewing and analysis disabled",
                checkpoint_ms, backfill, pilot_run_id=pilot_run_id,
                _return_inserted=_return_inserted)
        initial_fen = parsed.headers.get("FEN", "startpos")
        uci_moves = []
        for move in parsed.mainline_moves():
            uci_moves.append(move.uci())
        moves = " ".join(uci_moves)
        from ...lichess.analysis_json import ANALYTICS_SCHEMA_VERSION, normalize_game_analytics
        normalized_analytics = normalize_game_analytics(record, len(uci_moves))
        white = parsed.headers.get("White", "?")
        black = parsed.headers.get("Black", "?")
        is_white = white.casefold() == username.casefold()
        player_color = "white" if is_white else "black"
        opponent = black if is_white else white
        result = parsed.headers.get("Result", "*")
        winner = "white" if result == "1-0" else "black" if result == "0-1" else None
        status = ("draw" if result == "1/2-1/2" else "finished"
                  if result in {"1-0", "0-1"} else "unknownFinish")
        clock = parsed.headers.get("TimeControl", "-")
        header_date = parsed.headers.get("UTCDate") or parsed.headers.get("Date")
        started_at = None
        time_precision = "unknown"
        if header_date and len(header_date) == 10 and header_date[4] in {".", "-"}:
            try:
                parsed_date = datetime.strptime(header_date, "%Y.%m.%d" if header_date[4] == "."
                                                else "%Y-%m-%d").date()
                started_at = parsed_date.isoformat() + "T00:00:00+00:00"
                time_precision = "date_only"
            except ValueError:
                pass
        # PGN Date is often only day-precision. Prefer the authoritative
        # millisecond timestamp from Lichess whenever it is valid, upgrading
        # date_only instead of leaving the archive chronologically ambiguous.
        if time_precision in {"unknown", "date_only"} and record.get("createdAt") is not None:
            date_only_fallback = started_at
            try:
                started_at = datetime.fromtimestamp(int(record["createdAt"]) / 1000,
                    timezone.utc).isoformat()
                time_precision = "timestamp"
            except (TypeError, ValueError, OverflowError, OSError):
                started_at = date_only_fallback
                time_precision = "date_only" if date_only_fallback else "unknown"
        lichess_assessments = []
        initial_evaluation = parsed.eval()
        if initial_evaluation is not None:
            initial_score = initial_evaluation.pov(chess.WHITE)
            lichess_assessments.append({
                "ply": 0, "white_cp": initial_score.score(mate_score=None),
                "mate_white": initial_score.mate(), "depth": parsed.eval_depth(),
            })
        for ply, node in enumerate(parsed.mainline(), start=1):
            evaluation = node.eval()
            if evaluation is None:
                continue
            score = evaluation.pov(chess.WHITE)
            lichess_assessments.append({
                "ply": ply, "white_cp": score.score(mate_score=None),
                "mate_white": score.mate(), "depth": node.eval_depth(),
            })
        for item in normalized_analytics.assessments:
            lichess_assessments.append(dict(item))
        raw_hash = hashlib.sha256(raw_pgn.encode("utf-8")).hexdigest()
        players = record.get("players") if isinstance(record.get("players"), dict) else {}
        white_data = players.get("white") if isinstance(players.get("white"), dict) else {}
        black_data = players.get("black") if isinstance(players.get("black"), dict) else {}
        opening = record.get("opening") if isinstance(record.get("opening"), dict) else {}
        account_id = self.ensure_archive_account(username)
        imported_at = datetime.now(timezone.utc).isoformat()
        phase = record.get("division")
        middle_start = end_start = None
        from ...lichess.phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries
        if isinstance(phase, dict):
            middle_start = self._phase_ply(phase.get("middle"))
            end_start = self._phase_ply(phase.get("end"))
        fallback_middle = fallback_end = None
        if middle_start is None and end_start is None:
            phase_board = (chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen))
            phase_boards = [phase_board.copy(stack=False)]
            for raw_move in moves.split():
                move = chess.Move.from_uci(raw_move)
                phase_board.push(move)
                phase_boards.append(phase_board.copy(stack=False))
            fallback_middle, fallback_end = infer_phase_boundaries(phase_boards)

        inserted_link = {"value": False}

        def commit_archive(db: sqlite3.Connection, revision: int, line_hash: str) -> None:
            db.execute("UPDATE games SET source='lichess_archive', source_game_id=?, variant=?, "
                       "started_at=COALESCE(started_at,?), "
                       "game_time_precision=CASE WHEN ?='unknown' THEN game_time_precision "
                       "ELSE ? END, "
                       "raw_checksum=?, raw_pgn=?, last_move_at=COALESCE(?,last_move_at), "
                       "lichess_accuracy_white=COALESCE(?,lichess_accuracy_white), "
                       "lichess_accuracy_black=COALESCE(?,lichess_accuracy_black), "
                       "white_rating=COALESCE(?,white_rating),black_rating=COALESCE(?,black_rating), "
                       "opening_eco=COALESCE(?,opening_eco),opening_name=COALESCE(?,opening_name) "
                       "WHERE game_id=?",
                       (game_id, variant, started_at, time_precision, time_precision,
                        raw_hash, raw_pgn, record.get("lastMoveAt"),
                       normalized_analytics.accuracy.get("white"),
                       normalized_analytics.accuracy.get("black"), white_data.get("rating"),
                        black_data.get("rating"), opening.get("eco"), opening.get("name"), game_id))
            player_rating = white_data.get("rating") if player_color == "white" else black_data.get("rating")
            opponent_rating = black_data.get("rating") if player_color == "white" else white_data.get("rating")
            existing_account_link = db.execute(
                "SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                (account_id, game_id)).fetchone()
            inserted_link["value"] = not bool(existing_account_link)
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,player_rating,opponent_rating,opponent,player_name) "
                       "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,game_id) DO UPDATE SET "
                       "remote_id=excluded.remote_id, imported_at=excluded.imported_at,color=excluded.color,"
                       "player_rating=COALESCE(excluded.player_rating,account_games.player_rating),"
                       "opponent_rating=COALESCE(excluded.opponent_rating,account_games.opponent_rating),"
                       "opponent=excluded.opponent,player_name=excluded.player_name",
                       (account_id, game_id, game_id, imported_at, player_color, player_rating,
                        opponent_rating, opponent, white if player_color == "white" else black))
            db.execute("""INSERT INTO lichess_ready_analysis_metadata(
                account_id,game_id,line_revision,source,schema_version,white_analysis_json,
                black_analysis_json,move_annotations_json,validation_issues_json,updated_at)
                VALUES (?,?,?,'lichess-json',?,?,?,?,?,?) ON CONFLICT(account_id,game_id,line_revision)
                DO UPDATE SET source=excluded.source,schema_version=excluded.schema_version,
                white_analysis_json=excluded.white_analysis_json,
                black_analysis_json=excluded.black_analysis_json,
                move_annotations_json=excluded.move_annotations_json,
                validation_issues_json=excluded.validation_issues_json,updated_at=excluded.updated_at""",
                       (account_id, game_id, revision,
                        ANALYTICS_SCHEMA_VERSION,
                        json.dumps(normalized_analytics.player_analysis.get("white", {}), sort_keys=True,
                                   separators=(",", ":")),
                        json.dumps(normalized_analytics.player_analysis.get("black", {}), sort_keys=True,
                                   separators=(",", ":")),
                        json.dumps(list(normalized_analytics.move_annotations), sort_keys=True,
                                   separators=(",", ":")),
                        json.dumps(list(normalized_analytics.issues), sort_keys=True,
                                   separators=(",", ":")), imported_at))
            eval_parameters = json.dumps({"format": "pgn-eval-v1"}, sort_keys=True)
            existing_eval_run = db.execute(
                "SELECT * FROM analysis_runs WHERE game_id=? AND line_revision=? AND line_hash=? "
                "AND source='lichess-pgn' AND parameters_json=? "
                "ORDER BY finished_at DESC,run_id DESC LIMIT 1",
                (game_id, revision, line_hash, eval_parameters)).fetchone()
            if lichess_assessments or existing_eval_run:
                now = datetime.now(timezone.utc).isoformat()
                if existing_eval_run:
                    run_id = existing_eval_run["run_id"]
                    changed = False
                else:
                    run_id = uuid.uuid4().hex
                    db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (run_id, game_id, revision, line_hash, "lichess-pgn", "Lichess", None,
                                eval_parameters, now, now, "partial", None))
                    changed = True
                for item in lichess_assessments:
                    old = db.execute("SELECT white_cp,mate_white,depth,nodes,pv_uci "
                                     "FROM position_assessments WHERE run_id=? AND ply=?",
                                     (run_id, item["ply"])).fetchone()
                    incoming_has_score = (item["white_cp"] is not None or
                                          item["mate_white"] is not None)
                    if old:
                        old_has_score = old["white_cp"] is not None or old["mate_white"] is not None
                        incoming_depth = item["depth"]
                        old_depth = old["depth"]
                        # Preserve an existing higher-quality assessment when a later
                        # export is shallower or omits depth; equal/newer depth replaces it.
                        if not incoming_has_score or (old_has_score and old_depth is not None and
                                (incoming_depth is None or incoming_depth < old_depth)):
                            continue
                        values = (item["white_cp"], item["mate_white"], incoming_depth)
                        if tuple(old[:3]) != values:
                            db.execute("UPDATE position_assessments SET white_cp=?,mate_white=?,depth=? "
                                       "WHERE run_id=? AND ply=?",
                                       (*values, run_id, item["ply"]))
                            changed = True
                    else:
                        db.execute("INSERT INTO position_assessments VALUES (?,?,?,?,?,?,?,?,?)",
                                   (run_id, game_id, revision, item["ply"], item["white_cp"],
                                    item["mate_white"], item["depth"], None, ""))
                        changed = True
                expected_positions = db.execute(
                    "SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
                    (game_id, revision)).fetchone()[0]
                scored_positions = db.execute(
                    "SELECT COUNT(*) FROM position_assessments pa JOIN game_positions gp "
                    "ON gp.game_id=pa.game_id AND gp.line_revision=pa.line_revision AND gp.ply=pa.ply "
                    "WHERE pa.run_id=? AND pa.ply>0 "
                    "AND (white_cp IS NOT NULL OR mate_white IS NOT NULL)", (run_id,)).fetchone()[0]
                eval_status = ("complete" if expected_positions and
                               scored_positions >= expected_positions else "partial")
                if existing_eval_run and existing_eval_run["status"] != eval_status:
                    changed = True
                if changed:
                    db.execute("UPDATE analysis_runs SET status=?,finished_at=?,error=NULL WHERE run_id=?",
                               (eval_status, now, run_id))
                existing_metrics = db.execute(
                    "SELECT 1 FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v3' LIMIT 1",
                    (run_id,)).fetchone()
                if changed or not existing_metrics:
                    self._insert_derived_metrics_for_run(
                        db, run_id, user_color=player_color, replace=bool(changed))
            if middle_start is not None or end_start is not None:
                db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?) "
                           "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                           "middle_start_ply=excluded.middle_start_ply,end_start_ply=excluded.end_start_ply,"
                           "source=excluded.source,method_version=excluded.method_version",
                           (game_id, revision, middle_start, end_start, "lichess", "lichess-division-v1"))
                self._apply_phase_labels(db, game_id, revision, middle_start, end_start)
            elif fallback_middle is not None or fallback_end is not None:
                existing_phase = db.execute(
                    "SELECT source FROM game_phase_boundaries WHERE game_id=? AND line_revision=?",
                    (game_id, revision)).fetchone()
                if not existing_phase or existing_phase["source"] != "lichess":
                    db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?) "
                               "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                               "middle_start_ply=excluded.middle_start_ply,end_start_ply=excluded.end_start_ply,"
                               "source=excluded.source,method_version=excluded.method_version",
                               (game_id, revision, fallback_middle, fallback_end,
                                "material-fallback", PHASE_FALLBACK_VERSION))
                    self._apply_phase_labels(db, game_id, revision, fallback_middle, fallback_end)
            if pilot_run_id:
                db.execute("UPDATE archive_pilot_runs SET new_count=new_count+? WHERE run_id=?",
                           (int(not existing_account_link), pilot_run_id))
            elif backfill and checkpoint_ms is not None:
                db.execute("UPDATE archive_sync_state SET backfill_before=?,"
                           "imported_count=imported_count+?,updated_at=? WHERE account_id=?",
                           (checkpoint_ms + 1, int(not existing_account_link), imported_at, account_id))
            else:
                db.execute("UPDATE archive_sync_state SET imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (int(not existing_account_link), imported_at, account_id))

        args = (game_id, initial_fen, moves, player_color, opponent,
                bool(record.get("rated", False)), clock, status, winner)
        kwargs = {"white_player": white, "black_player": black, "started_at": started_at,
                  "transaction_hook": commit_archive, "preserve_existing_color": True}
        if self._writer is not None:
            # Archive workers wait for the durable single-writer transaction before
            # requesting the next NDJSON record/checkpoint.
            self._submit_writer(self._save_snapshot_sync, *args, wait=True, **kwargs)
        else:
            self._save_snapshot_sync(*args, **kwargs)
        return (game_id, inserted_link["value"]) if _return_inserted else game_id

    def _import_raw_only_record(self, username: str, record: dict, parsed,
                                variant: str, raw_pgn: str, import_error: str,
                                checkpoint_ms: int | None, backfill: bool, *,
                                pilot_run_id: str | None = None,
                                _return_inserted: bool = False) -> str | tuple[str, bool]:
        args = (username, record, parsed, variant, raw_pgn, import_error,
                checkpoint_ms, backfill, pilot_run_id)
        if self._writer is not None:
            return self._submit_writer(self._import_raw_only_record_sync, *args,
                                       _return_inserted=_return_inserted, wait=True)
        return self._import_raw_only_record_sync(*args, _return_inserted=_return_inserted)

    def _import_raw_only_record_sync(self, username: str, record: dict, parsed,
                                     variant: str, raw_pgn: str, import_error: str,
                                     checkpoint_ms: int | None, backfill: bool,
                                     pilot_run_id: str | None = None, *,
                                     _return_inserted: bool = False) -> str | tuple[str, bool]:
        game_id = str(record.get("id") or "").strip()
        if not game_id or not raw_pgn:
            raise ValueError("Raw-only archive record requires id and PGN")
        account_id = self.ensure_archive_account(username)
        headers = parsed.headers if parsed is not None else {}
        white, black = headers.get("White", "?"), headers.get("Black", "?")
        color = "white" if white.casefold() == username.casefold() else "black"
        opponent = black if color == "white" else white
        result = headers.get("Result", "*")
        winner = "white" if result == "1-0" else "black" if result == "0-1" else None
        now = datetime.now(timezone.utc).isoformat()
        raw_hash = hashlib.sha256(raw_pgn.encode("utf-8")).hexdigest()
        players = record.get("players") if isinstance(record.get("players"), dict) else {}
        white_user = players.get("white") if isinstance(players.get("white"), dict) else {}
        black_user = players.get("black") if isinstance(players.get("black"), dict) else {}
        white_rating = white_user.get("rating")
        black_rating = black_user.get("rating")
        with self._connection() as db:
            existing_account_link = db.execute(
                "SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                (account_id, game_id)).fetchone()
            db.execute("INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,winner,"
                       "updated_at,pgn,white_player,black_player,source,source_game_id,variant,import_error,"
                       "raw_checksum,raw_pgn,last_move_at,white_rating,black_rating) "
                       "VALUES(?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lichess_archive', ?, ?, ?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(game_id) DO UPDATE SET source='lichess_archive',source_game_id=excluded.source_game_id,"
                       "variant=excluded.variant,import_error=excluded.import_error,raw_checksum=excluded.raw_checksum,"
                       "raw_pgn=excluded.raw_pgn,last_move_at=excluded.last_move_at",
                       (game_id, color, opponent, int(bool(record.get("rated", False))),
                        headers.get("TimeControl", "-"),
                        "draw" if result == "1/2-1/2" else "finished" if winner else "unknownFinish",
                        winner, now, raw_pgn, white, black,
                        game_id, variant, import_error, raw_hash,
                        raw_pgn, record.get("lastMoveAt"), white_rating, black_rating))
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,player_rating,opponent_rating,opponent,player_name) "
                       "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,game_id) DO UPDATE SET color=excluded.color,"
                       "player_rating=COALESCE(excluded.player_rating,account_games.player_rating),"
                       "opponent_rating=COALESCE(excluded.opponent_rating,account_games.opponent_rating),"
                       "opponent=excluded.opponent,player_name=excluded.player_name,imported_at=excluded.imported_at",
                       (account_id, game_id, game_id, now, color,
                        white_rating if color == "white" else black_rating,
                        black_rating if color == "white" else white_rating, opponent,
                        white if color == "white" else black))
            if pilot_run_id:
                db.execute("UPDATE archive_pilot_runs SET new_count=new_count+? WHERE run_id=?",
                           (int(not existing_account_link), pilot_run_id))
            elif backfill and checkpoint_ms is not None:
                db.execute("UPDATE archive_sync_state SET backfill_before=?,imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (checkpoint_ms + 1, int(not existing_account_link), now, account_id))
            else:
                db.execute("UPDATE archive_sync_state SET imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (int(not existing_account_link), now, account_id))
        self.invalidate_statistics_cache()
        return (game_id, not bool(existing_account_link)) if _return_inserted else game_id

    def begin_archive_backfill(self, account_id: str, started_at_ms: int) -> int:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET backfill_started_at=COALESCE(backfill_started_at,?),"
                       "backfill_before=COALESCE(backfill_before,?),updated_at=? WHERE account_id=?",
                       (int(started_at_ms), int(started_at_ms), datetime.now(timezone.utc).isoformat(), account_id))
            row = db.execute("SELECT backfill_started_at FROM archive_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return int(row[0])

    def finish_archive_backfill(self, account_id: str, last_sync_new_count: int = 0) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET backfill_complete=1,incremental_since=MAX("
                       "COALESCE(incremental_since,0),COALESCE(backfill_started_at,0)),"
                       "updated_at=? WHERE account_id=?",
                       (datetime.now(timezone.utc).isoformat(), account_id))

    def finish_archive_incremental(self, account_id: str, cursor_ms: int,
                                   last_sync_new_count: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET incremental_since=MAX(COALESCE(incremental_since,0),?), "
                       "last_sync_new_count=?,last_success_at=?,last_error=NULL,updated_at=? WHERE account_id=?",
                       (int(cursor_ms), int(last_sync_new_count), now, now, account_id))

    def record_archive_interrupted(self, account_id: str) -> None:
        self.record_archive_error(account_id, "Синхронизация остановлена; сохранённый курсор можно возобновить.")

    def archive_game_linked(self, account_id: str, game_id: str) -> bool:
        with self._connection() as db:
            return bool(db.execute("SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                                   (account_id, game_id)).fetchone())

    def record_archive_error(self, account_id: str, error: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET last_error=?,updated_at=? WHERE account_id=?",
                       (error[:500], datetime.now(timezone.utc).isoformat(), account_id))
