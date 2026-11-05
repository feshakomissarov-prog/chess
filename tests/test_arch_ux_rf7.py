from __future__ import annotations

import chess
import pytest

from chess_coach.lichess.move_metrics import METRIC_VERSION, build_move_facts
from chess_coach.lichess.repository import GameRepository


def _create_saved_run(repo: GameRepository, game_id: str, moves: str,
                      assessments: list[dict], *, winner: str = "white") -> str:
    repo.save_snapshot(game_id, "startpos", moves, "white", "Synthetic opponent",
                       False, "300+0", "mate", winner)
    line = repo.current_line(game_id)
    repo.save_analysis(game_id, line_revision=line["line_revision"],
                       line_hash=line["line_hash"], source="stockfish",
                       engine_name="SyntheticFish", engine_version="fixture",
                       parameters={"depth": 20}, assessments=assessments,
                       classifications=[], status="complete")
    with repo._connection() as db:
        return str(db.execute("SELECT run_id FROM analysis_runs WHERE game_id=?",
                              (game_id,)).fetchone()[0])


@pytest.mark.parametrize(("moves", "winner", "expected_white"), [
    ("e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7", "white", {"win": 1000, "draw": 0, "loss": 0}),
    ("f2f3 e7e5 g2g4 d8h4", "black", {"win": 0, "draw": 0, "loss": 1000}),
])
def test_terminal_mate_zero_wdl_uses_legal_mated_side(moves, winner, expected_white):
    board = chess.Board()
    rows = [{"ply": 0, "white_cp": 0, "depth": 20}]
    played = []
    for ply, uci in enumerate(moves.split(), 1):
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        mover = "white" if board.turn else "black"
        played.append({"ply": ply, "mover": mover, "uci": uci})
        board.push(move)
        rows.append({"ply": ply, "white_cp": 0, "depth": 20})
    assert board.is_checkmate()
    rows[-1] = {"ply": len(played), "mate_white": 0, "depth": 20}

    facts, events = build_move_facts(rows, played, source="stockfish", run_id="terminal-mate-zero")

    assert facts[-1]["wdl_after_json"]
    import json
    assert json.loads(facts[-1]["wdl_after_json"]) == expected_white
    assert facts[-1]["wdl_model"].startswith("python-chess-sf-v2-terminal-aware:")
    assert facts[-1]["accuracy"] == pytest.approx(100.0)
    assert any(event["event_type"] == "checkmate_terminal" for event in events)


def test_nonterminal_mate_zero_stays_unknown_in_wdl_and_accuracy():
    facts, _events = build_move_facts(
        [{"ply": 0, "white_cp": 0, "depth": 20},
         {"ply": 1, "mate_white": 0, "depth": 20}],
        [{"ply": 1, "mover": "white", "uci": "e2e4"}],
        source="stockfish", run_id="nonterminal-zero")
    assert facts[0]["accuracy"] is None
    assert facts[0]["wdl_after_json"] == "null"
    assert facts[0]["wdl_model"] is None


@pytest.mark.parametrize("assessments", [
    [{"ply": 1, "white_cp": 100, "depth": 20}],  # missing before score
    [{"ply": 0, "white_cp": 0, "depth": 20}],   # nonterminal missing after
    [{"ply": 0, "white_cp": 0, "depth": None},
     {"ply": 1, "white_cp": 100, "depth": 20}],  # missing before depth
])
def test_move_accuracy_remains_unknown_without_a_valid_nonterminal_pair(assessments):
    facts, _events = build_move_facts(
        assessments, [{"ply": 1, "mover": "white", "uci": "e2e4"}],
        source="stockfish", run_id="incomplete-pair")
    assert facts[0]["accuracy"] is None
    assert facts[0]["accuracy_source"] is None
    assert facts[0]["accuracy_formula_id"] is None


@pytest.mark.parametrize("terminal_assessment", [None, {"depth": None, "mate_white": 0}])
@pytest.mark.parametrize("moves", [
    "f2f3 e7e5 g2g4 d8h4",
    "e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7",
])
def test_terminal_board_supplies_last_move_accuracy_without_terminal_engine_depth(
        terminal_assessment, moves):
    board = chess.Board()
    played = []
    assessments = [{"ply": 0, "white_cp": 0, "depth": 20}]
    for ply, uci in enumerate(moves.split(), 1):
        move = chess.Move.from_uci(uci)
        mover = "white" if board.turn else "black"
        played.append({"ply": ply, "mover": mover, "uci": uci})
        board.push(move)
        if ply < len(moves.split()):
            assessments.append({"ply": ply, "white_cp": 0, "depth": 20})
    final_ply = len(played)
    if terminal_assessment is not None:
        assessments.append({"ply": final_ply, **terminal_assessment})
    assert board.is_checkmate()

    facts, events = build_move_facts(
        assessments, played, source="stockfish", run_id="terminal-no-depth")

    assert facts[-1]["accuracy"] == pytest.approx(100.0)
    assert facts[-1]["accuracy_source"].endswith(":board-terminal")
    assert facts[-1]["cp_loss"] is None
    import json
    assert json.loads(facts[-1]["wdl_after_json"]) == (
        {"win": 1000, "draw": 0, "loss": 0} if board.turn == chess.BLACK else
        {"win": 0, "draw": 0, "loss": 1000})
    evidence = next(json.loads(event["evidence_json"]) for event in events
                    if event["event_type"] == "checkmate_terminal")
    assert evidence["terminal_accuracy_derived"] is True
    assert evidence["accuracy_source"].endswith(":board-terminal")


def test_legacy_sqlite_rows_are_rematerialized_from_saved_assessments_and_resume(
        tmp_path, monkeypatch):
    path = tmp_path / "legacy-metrics.sqlite3"
    repo = GameRepository(path)
    game_ids = ["legacy-a", "legacy-b"]
    run_ids = []
    for game_id in game_ids:
        run_ids.append(_create_saved_run(
            repo, game_id, "e2e4 e7e5",
            [{"ply": 0, "white_cp": 0, "depth": 20},
             {"ply": 1, "white_cp": -100, "depth": 20},
             {"ply": 2, "white_cp": 0, "depth": 20}]))
    original_pgn = repo.get_game(game_ids[0])["pgn"]
    account_id = repo.ensure_archive_account("SyntheticOwner")
    with repo._connection() as db:
        db.execute("UPDATE games SET source='lichess',source_game_id='synthetic-linked',"
                   "lichess_accuracy_white=81.25 WHERE game_id=?", (game_ids[0],))
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,"
                   "player_name,opponent) VALUES (?,?,?,?,?,?,?)",
                   (account_id, game_ids[0], game_ids[0], "synthetic", "white",
                    "SyntheticOwner", "Synthetic opponent"))
        db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                   "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL,"
                   "wdl_model='python-chess-sf-v1:legacy' WHERE run_id IN (?,?)", run_ids)
        # Model the exact previous released schema/data, including its user_version.
        db.execute("PRAGMA user_version=19")
    repo.shutdown()

    repo = GameRepository(path)
    try:
        assert repo.count_stale_analysis_metrics() == 2
        before = repo.latest_analysis_detail(game_ids[0], 1)
        assert before["metrics_materialization_pending"] is True
        assert [row["accuracy"] for row in before["move_metrics"]] == pytest.approx(
            [67.2397212280, 67.2397212280])
        with repo._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? "
                              "AND metric_version='cc-move-metrics-v3'", (run_ids[0],)).fetchone()[0] == 0

        def no_engine(*_args, **_kwargs):
            raise AssertionError("Legacy rematerialization must not start Stockfish.")
        monkeypatch.setattr("chess.engine.SimpleEngine.popen_uci", no_engine)

        cancelled = repo.materialize_stale_analysis_metrics_batch(
            limit=1, cancel_check=lambda: True)
        assert cancelled == {"processed": 0, "total": 2, "remaining": 2}
        first = repo.materialize_stale_analysis_metrics_batch(limit=1)
        assert first == {"processed": 1, "total": 2, "remaining": 1}
        second = repo.materialize_stale_analysis_metrics_batch(limit=1)
        assert second == {"processed": 1, "total": 1, "remaining": 0}
        repeated = repo.materialize_stale_analysis_metrics_batch(limit=1)
        assert repeated == {"processed": 0, "total": 0, "remaining": 0}

        detail = repo.latest_analysis_detail(game_ids[0], 1)
        assert detail["metrics_materialization_pending"] is False
        assert [row["metric_version"] for row in detail["move_metrics"]] == [METRIC_VERSION] * 2
        assert [row["accuracy"] for row in detail["move_metrics"]] == pytest.approx(
            [67.2397212280, 67.2397212280])
        assert detail["official_game_accuracy"] == 81.25
        stats = __import__("chess_coach.lichess.statistics", fromlist=["StatisticsService"]
                           ).StatisticsService(repo).summary(
                               account_id=account_id, analysis_source="stockfish")
        assert stats["local_move_accuracy_n"] == 1
        assert stats["local_move_accuracy"] == pytest.approx(67.2)
        with repo._connection() as db:
            coverage = db.execute("SELECT measured_moves FROM analysis_coverage WHERE run_id=?",
                                  (run_ids[0],)).fetchone()
            assert coverage[0] == 2
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? "
                              "AND metric_version='cc-move-metrics-v2'", (run_ids[0],)).fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM position_assessments WHERE run_id=?",
                              (run_ids[0],)).fetchone()[0] == 3
            assert db.execute("SELECT moves,pgn FROM games WHERE game_id=?",
                              (game_ids[0],)).fetchone()[1] == original_pgn
    finally:
        repo.shutdown()


def test_rematerialization_preserves_superseded_events_for_audit_but_hides_them(
        tmp_path):
    import json

    repo = GameRepository(tmp_path / "legacy-events.sqlite3")
    try:
        run_id = _create_saved_run(
            repo, "legacy-events", "e2e4 e7e5",
            [{"ply": 0, "white_cp": 200, "depth": 20},
             {"ply": 1, "white_cp": 0, "depth": 20, "pv_uci": "e7e6"},
             {"ply": 2, "white_cp": 200, "depth": 20}])
        with repo._connection() as db:
            db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                       "accuracy=NULL WHERE run_id=?", (run_id,))
            db.execute("INSERT INTO analysis_events(event_key,run_id,game_id,line_revision,"
                       "ply,event_type,confidence_state,source,detector_version,evidence_json) "
                       "SELECT 'legacy-event-v1',run_id,game_id,line_revision,1,"
                       "'missed_opportunity','candidate','stockfish',"
                       "'cc-opportunity-candidate-v1',? FROM analysis_runs WHERE run_id=?",
                       (json.dumps({"legacy": True}), run_id))

        repo.materialize_analysis_metrics(run_id)

        with repo._connection() as db:
            legacy = db.execute("SELECT detector_version,evidence_json FROM analysis_events "
                                "WHERE event_key='legacy-event-v1'").fetchone()
            current_rows = db.execute("SELECT event_key,detector_version FROM analysis_events "
                                      "WHERE run_id=? AND detector_version="
                                      "'cc-opportunity-candidate-v2'", (run_id,)).fetchall()
        assert legacy[0] == "cc-opportunity-candidate-v1"
        assert json.loads(legacy[1]) == {"legacy": True}
        assert any(row[0].endswith(":v2") for row in current_rows)
        visible = repo.latest_analysis_detail("legacy-events", 1)["events"]
        assert all(event["detector_version"] != "cc-opportunity-candidate-v1"
                   for event in visible)
        assert any(event["detector_version"] == "cc-opportunity-candidate-v2"
                   for event in visible)
    finally:
        repo.shutdown()


@pytest.mark.parametrize(("moves", "winner", "expected"), [
    ("e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7", "white",
     {"win": 1000, "draw": 0, "loss": 0}),
    ("f2f3 e7e5 g2g4 d8h4", "black",
     {"win": 0, "draw": 0, "loss": 1000}),
])
def test_old_sqlite_mate_zero_wdl_rematerializes_with_both_winners(
        tmp_path, moves, winner, expected):
    import json

    path = tmp_path / f"legacy-mate-{winner}.sqlite3"
    repo = GameRepository(path)
    board = chess.Board()
    assessments = [{"ply": 0, "white_cp": 0, "depth": 20}]
    for ply, uci in enumerate(moves.split(), 1):
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        board.push(move)
        assessments.append({"ply": ply, "white_cp": 0, "depth": 20})
    assert board.is_checkmate()
    assessments[-1] = {"ply": len(moves.split()), "mate_white": 0, "depth": 20}
    run_id = _create_saved_run(repo, f"legacy-mate-{winner}", moves,
                               assessments, winner=winner)
    with repo._connection() as db:
        db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                   "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL "
                   "WHERE run_id=?", (run_id,))
        db.execute("PRAGMA user_version=19")
    repo.shutdown()

    repo = GameRepository(path)
    try:
        repo.materialize_stale_analysis_for_game(f"legacy-mate-{winner}")
        detail = repo.latest_analysis_detail(f"legacy-mate-{winner}", 1)
        last = detail["move_metrics"][-1]
        assert json.loads(last["wdl_after_json"]) == expected
        assert last["wdl_model"].startswith("python-chess-sf-v2-terminal-aware:")
        assert last["accuracy"] == pytest.approx(100.0)
        assert any(event["event_type"] == "checkmate_terminal"
                   and event["confidence_state"] == "verified"
                   for event in detail["events"])
        with repo._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? "
                              "AND metric_version='cc-move-metrics-v2'", (run_id,)).fetchone()[0] == len(moves.split())
    finally:
        repo.shutdown()


@pytest.mark.parametrize(("moves", "winner"), [
    ("f2f3 e7e5 g2g4 d8h4", "black"),
    ("e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7", "white"),
])
def test_review_persists_last_mate_accuracy_when_terminal_assessment_is_missing(
        tmp_path, moves, winner):
    from chess_coach.lichess.game_review import load_game_review

    repo = GameRepository(tmp_path / f"terminal-review-{winner}.sqlite3")
    try:
        board = chess.Board()
        assessments = [{"ply": 0, "white_cp": 0, "depth": 20}]
        for ply, uci in enumerate(moves.split(), 1):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            board.push(move)
            if ply < len(moves.split()):
                assessments.append({"ply": ply, "white_cp": 0, "depth": 20})
        assert board.is_checkmate()
        run_id = _create_saved_run(repo, f"terminal-review-{winner}", moves,
                                   assessments, winner=winner)
        with repo._connection() as db:
            db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                       "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL "
                       "WHERE run_id=?", (run_id,))

        progress = repo.materialize_stale_analysis_for_game(f"terminal-review-{winner}")
        assert progress["processed"] == 1

        review = load_game_review(repo, repo.get_game(f"terminal-review-{winner}"))

        final_move = review["moves"][-1]
        assert final_move.local_accuracy == pytest.approx(100.0)
        assert review["local_accuracy_status"]["terminal_positions_derived"] == 1
        assert review["analysis"]["move_metrics"][-1]["accuracy_source"].endswith(
            ":board-terminal")
        with repo._connection() as db:
            metric = db.execute("SELECT accuracy,accuracy_source,wdl_after_json "
                                 "FROM move_metrics WHERE run_id=? AND ply=? "
                                 "AND metric_version=?", (run_id, len(moves.split()),
                                                            METRIC_VERSION)).fetchone()
            assert metric[0] == pytest.approx(100.0)
            assert metric[1].endswith(":board-terminal")
            assert metric[2] != "null"
    finally:
        repo.shutdown()


def test_qt_review_shows_legacy_terminal_mate_accuracy_and_advantage(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from chess_coach.ui.game_review import GameReviewWorkspace

    path = tmp_path / "legacy-terminal-qt.sqlite3"
    repo = GameRepository(path)
    moves = "f2f3 e7e5 g2g4 d8h4"
    assessments = [
        {"ply": 0, "white_cp": 0, "depth": 20},
        {"ply": 1, "white_cp": 0, "depth": 20},
        {"ply": 2, "white_cp": 0, "depth": 20},
        {"ply": 3, "white_cp": 0, "depth": 20},
    ]
    run_id = _create_saved_run(repo, "legacy-terminal-qt", moves,
                               assessments, winner="black")
    with repo._connection() as db:
        db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                   "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL "
                   "WHERE run_id=?", (run_id,))
        db.execute("PRAGMA user_version=19")
    repo.shutdown()

    repo = GameRepository(path)
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True,
            settings=QSettings(str(tmp_path / "review.ini"), QSettings.Format.IniFormat))
        qtbot.addWidget(workspace)
        progress = repo.materialize_stale_analysis_for_game("legacy-terminal-qt")
        assert progress["processed"] == 1
        workspace.load_game(repo.get_game("legacy-terminal-qt"), training_allowed=True)
        workspace.select_ply(4)
        workspace.show()
        qtbot.waitUntil(lambda: "100.0%" in workspace.move_detail.text(), timeout=1000)
        assert "мат подтверждён доской" in workspace.move_detail.text()
        assert "Мат · чёрные победили" == workspace.advantage_label.text()
        assert workspace.advantage_bar.terminal_checkmate is True
        assert workspace.advantage_bar.terminal_winner_white is False
        with repo._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? "
                              "AND metric_version='cc-move-metrics-v2'", (run_id,)).fetchone()[0] == 4
    finally:
        repo.shutdown()


def test_main_window_background_review_worker_persists_legacy_metrics_and_reports_progress(
        qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from chess_coach.window import MainWindow

    window = MainWindow(QSettings(str(tmp_path / "legacy-worker.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game_id = "legacy-worker-game"
    run_id = _create_saved_run(
        window.game_repository, game_id, "e2e4 e7e5",
        [{"ply": 0, "white_cp": 0, "depth": 20},
         {"ply": 1, "white_cp": -100, "depth": 20},
         {"ply": 2, "white_cp": 0, "depth": 20}])
    with window.game_repository._connection() as db:
        db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                   "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL "
                   "WHERE run_id=?", (run_id,))
    game = window.game_repository.get_game(game_id)
    window.show_saved_games()
    qtbot.waitUntil(lambda: getattr(window, "library_page", None) is not None, timeout=3000)
    qtbot.waitUntil(lambda: window.library_page.games.count() > 0, timeout=5000)
    query_handle = window._library_query_handle
    if query_handle is not None:
        qtbot.waitUntil(lambda: not query_handle.is_alive(), timeout=5000)

    window._load_library_game_detail(game, allow_not_selected=True)

    qtbot.waitUntil(lambda: not window._library_detail_handle
                    or not window._library_detail_handle.is_alive(), timeout=5000)
    qtbot.waitUntil(lambda: "Метрики сохранённых анализов: пересчитано" in
                    window.library_page.detail_status.text(), timeout=5000)
    assert "1/1" in window.library_page.detail_status.text()
    assert window.library_page.detail_graph.safety_allowed is True
    assert window._dashboard_analysis_safety_allowed is True  # isolated offline lease
    with window.game_repository._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? "
                          "AND metric_version=?", (run_id, METRIC_VERSION)).fetchone()[0] == 2
    window.close()


@pytest.mark.parametrize(("archive", "puzzles", "expected"), [
    (False, False, (False, False, False)),
    (True, False, (True, False, True)),
    (False, True, (False, True, True)),
    (True, True, (True, True, True)),
])
def test_background_schedule_treats_archive_and_puzzles_independently(
        qtbot, tmp_path, archive, puzzles, expected):
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings
    from chess_coach.window import MainWindow

    window = MainWindow(QSettings(str(tmp_path / f"flags-{archive}-{puzzles}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    calls = []
    service = SimpleNamespace(
        username="Synthetic", auth_generation=4,
        client=SimpleNamespace(rate_limit_remaining=0),
        is_auth_generation_current=lambda generation: generation == 4,
        check_active_games_for_analysis=lambda _request_id: None,
        sync_puzzle_activity_background=lambda repo: calls.append(("puzzles", repo)))
    window.archive_sync.start_full_sync = lambda _service, *, background: calls.append(
        ("archive", background))
    window.lichess = service
    window.offline_mode = False
    window._lichess_account_authenticated = True
    window._lichess_session_selected = True
    window._lichess_connected = True
    window._lichess_stream_ready_generation = 4
    window.settings.setValue("lichess/auto_archive_sync", archive)
    window.settings.setValue("lichess/auto_puzzle_history_sync", puzzles)
    window.settings.setValue("lichess/auto_ready_analytics", False)

    window._lichess_event_stream_ready(4, "Synthetic")
    timer_active = window._auto_sync_timer.isActive()
    window._run_background_sync()

    assert (any(call[0] == "archive" for call in calls),
            any(call[0] == "puzzles" for call in calls), timer_active) == expected
    window.close()


def test_enabling_puzzle_history_after_connect_starts_scheduler_without_archive(
        qtbot, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings
    from chess_coach.window import MainWindow

    window = MainWindow(QSettings(str(tmp_path / "puzzle-toggle.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    calls = []
    service = SimpleNamespace(
        username="Synthetic", auth_generation=9,
        client=SimpleNamespace(rate_limit_remaining=0),
        is_auth_generation_current=lambda generation: generation == 9,
        check_active_games_for_analysis=lambda _request_id: None,
        sync_puzzle_activity_background=lambda repo: calls.append(("puzzles", repo)),
        cancel_lichess_analysis_backfill=lambda: None)
    window.archive_sync.start_full_sync = lambda *_args, **_kwargs: calls.append(("archive", None))
    window.lichess = service
    window.offline_mode = False
    window.settings.setValue("lichess/auto_archive_sync", False)
    window.settings.setValue("lichess/auto_puzzle_history_sync", False)
    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_account_authenticated = True
    window._lichess_session_selected = True
    window._lichess_connected = True
    window._lichess_stream_ready_generation = 9
    window._lichess_event_stream_ready(9, "Synthetic")
    assert not window._auto_sync_timer.isActive()

    window.open_settings()
    dialog = window._settings_dialog
    dialog.auto_lichess_archive.setChecked(False)
    dialog.auto_lichess_puzzles.setChecked(True)
    dialog.accept()

    qtbot.waitUntil(lambda: any(kind == "puzzles" for kind, _value in calls), timeout=1500)
    assert not any(kind == "archive" for kind, _value in calls)
    assert window._auto_sync_timer.isActive()
    assert calls == [("puzzles", window.game_repository)]
    window.close()
