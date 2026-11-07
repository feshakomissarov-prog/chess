from __future__ import annotations

import chess
from datetime import datetime, timedelta, timezone
import threading
import pytest
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtTest import QTest

from chess_coach.infrastructure.sqlite.jobs import AnalysisJobsStoreMixin
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.move_table import MoveTable
from chess_coach.ui.pages.play import PlayPage
from chess_coach.window import MainWindow


def test_play_active_panel_remains_hidden_for_non_active_mode(qtbot):
    page = PlayPage({})
    qtbot.addWidget(page)
    page.set_active_games([{"gameId": "synthetic", "fen": chess.STARTING_FEN}],
                          status="updated")
    assert page.mode_selector.currentData() == "local"
    assert page.active_panel.isHidden()


def test_play_keeps_mode_cards_accessible_in_active_mode(qtbot):
    page = PlayPage({})
    qtbot.addWidget(page)
    page.mode_selector.setCurrentIndex(page.mode_selector.findData("active"))
    assert all(not card.isHidden() for card in page.cards)


def test_compact_play_mode_cards_can_be_reached_and_selected_by_mouse(qtbot):
    page = PlayPage({})
    qtbot.addWidget(page)
    page.resize(1120, 640)
    page.show()
    qtbot.wait(20)
    assert page.form_rows["Режим"].isHidden()
    from PySide6.QtTest import QSignalSpy
    create_spy = QSignalSpy(page.create_requested)
    for mode in ("local", "ai", "friend", "challenge", "active"):
        button = page.buttons[mode]
        page.content_scroll.ensureWidgetVisible(button)
        qtbot.wait(5)
        assert button.isVisible(), mode
        assert page.content_scroll.viewport().rect().contains(
            button.mapTo(page.content_scroll.viewport(), button.rect().center())), mode
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert page.mode_selector.currentData() == mode
        assert all(not card.isHidden() for card in page.cards)
        assert page.active_panel.isVisible() == (mode == "active")
        assert create_spy.count() == 0


def test_active_preview_clears_previous_board_and_requires_confirmed_fen(qtbot):
    page = PlayPage({})
    qtbot.addWidget(page)
    page.set_active_games([
        {"gameId": "a", "color": "white", "fen": chess.Board().fen()},
        {"gameId": "b", "color": "black", "fen": "invalid"},
    ])
    page.active_games.setCurrentRow(0)
    assert not page.active_preview_board.isHidden()
    page.active_games.setCurrentRow(1)
    assert page.active_preview_board.isHidden()
    assert not page.active_open.isEnabled()
    page.set_active_games([])
    assert page.active_preview_board.isHidden()
    assert not page.active_open.isEnabled()


def test_late_active_games_do_not_reveal_panel_after_mode_switch(qtbot):
    page = PlayPage({})
    qtbot.addWidget(page)
    page.mode_selector.setCurrentIndex(page.mode_selector.findData("active"))
    page.set_active_games(status="Обновляю список…")
    page.mode_selector.setCurrentIndex(page.mode_selector.findData("ai"))
    page.set_active_games([{"gameId": "late", "fen": chess.STARTING_FEN}])
    assert page.mode_selector.currentData() == "ai"
    assert page.active_panel.isHidden()
    assert all(not card.isHidden() for card in page.cards)


def test_offline_play_modes_remain_selectable_without_sending_network_request(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "offline-play-selectable.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    try:
        page = window._play_mode_page
        for mode in ("local", "ai", "friend", "challenge", "active"):
            button = page.buttons[mode]
            assert button.isEnabled()
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            assert page.mode_selector.currentData() == mode
        page.select_mode("ai")
        QTest.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
        assert window.lichess is None
        assert "подключ" in page.form_status.text().casefold()
        assert window._pending_play_request_id is None
    finally:
        window.game_repository.shutdown()
        window.close()


def test_compact_training_board_never_overlaps_solution_or_actions(qtbot):
    from chess_coach.ui.pages.puzzles import PuzzleHistoryPage
    page = PuzzleHistoryPage([], "synthetic", refresh_enabled=False)
    qtbot.addWidget(page)
    page.resize(1012, 500)  # Main content area of a 1120x640 window with sidebar.
    page.solution.setText(
        "Задача synthetic-1 · решено · темы: связка, тактика. "
        "Практика по сохранённой попытке; это длинное объяснение для проверки переноса текста.")
    page.history.addItem("Synthetic puzzle · решено · рейтинг 1300 · темы: fork, pin")
    page.history.setCurrentRow(0)
    page.replay.setEnabled(True)
    page.reveal.setEnabled(True)
    page.reset_replay.setEnabled(True)
    page.show()
    qtbot.wait(30)
    board_rect = page.board.geometry()
    solution_rect = page.solution.geometry()
    assert not board_rect.intersects(solution_rect), (board_rect.getRect(), solution_rect.getRect())
    assert all(button.isVisible() and button.height() >= 24
               for button in (page.replay, page.reveal, page.reset_replay, page.close_button))


def test_visual_gate_rejects_truncated_action_and_overlapping_regions(qtbot):
    from PySide6.QtWidgets import QLabel, QPushButton, QWidget
    from scripts.capture_visual_master import assert_action_target, assert_no_widget_overlap
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(100, 80)
    button = QPushButton("Открыть", host)
    button.setGeometry(76, 58, 30, 30)
    left = QLabel("Board", host)
    right = QLabel("Moves", host)
    left.setGeometry(0, 0, 60, 60)
    right.setGeometry(50, 50, 40, 25)
    host.show()
    qtbot.wait(10)
    with pytest.raises(AssertionError, match="outside the actual window"):
        assert_action_target(host, button, "fixture action")
    with pytest.raises(AssertionError, match="overlaps"):
        assert_no_widget_overlap(left, right, "fixture board/history")


def test_compact_library_row_keeps_complete_details_in_tooltip(qtbot):
    from chess_coach.ui.pages.library import LibraryPage
    page = LibraryPage(accounts=[])
    qtbot.addWidget(page)
    row_text = "Победа · Очень длинное имя соперника · 06.11.2026 · 3 мин + 2 сек"
    page.set_results([({"game_id": "synthetic"}, row_text, "Lichess archive")],
                     page=0, pages=1, total=1)
    item = page.games.item(0)
    assert item.toolTip() == f"{row_text}\nИсточник: Lichess archive"
    assert item.data(Qt.ItemDataRole.AccessibleDescriptionRole) == row_text
    assert page.search.toolTip() == "Поиск по сопернику, дебюту или дате"


def test_move_severity_glyphs_do_not_claim_good_move_for_inaccuracy(qtbot):
    table = MoveTable()
    qtbot.addWidget(table)
    board = chess.Board()
    move = chess.Move.from_uci("e2e4")
    table.set_rows([{"ply": 1, "mover": "white", "san": board.san(move),
                     "severity": "inaccuracy"}], enabled=True)
    item = table.item_by_ply(1)
    assert item is not None
    assert item.text().startswith("△ ")
    assert item.toolTip().startswith("Неточность")


def test_all_move_severity_symbols_are_unambiguous_and_accessible(qtbot):
    table = MoveTable()
    qtbot.addWidget(table)
    expected = {"good": "✓", "inaccuracy": "△", "mistake": "✕",
                "blunder": "‼", "strong": "★", "unknown": "e4"}
    for severity, glyph in expected.items():
        table.set_rows([{"ply": 1, "mover": "white", "san": "e4",
                         "severity": severity}], enabled=True)
        item = table.item_by_ply(1)
        assert item is not None
        assert item.text().startswith(glyph)
        assert item.toolTip(), f"{severity} needs an explanatory tooltip"
        description = item.data(Qt.ItemDataRole.AccessibleDescriptionRole)
        assert description, f"{severity} needs an accessible description"


def _archive_game(repository, account, game_id: str, *, started_days: int,
                  last_move_days: int | None):
    repository.import_archive_record(
        account, {"id": game_id, "pgn": '[White "Me"]\n[Black "Opponent"]\n'
                 '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    with repository._connection() as db:
        row = db.execute("SELECT game_id FROM games WHERE source_game_id=?", (game_id,)).fetchone()
        started = (datetime.now(timezone.utc) - timedelta(days=started_days)).isoformat()
        last_move = (int((datetime.now(timezone.utc) - timedelta(days=last_move_days)).timestamp()
                         * 1000) if last_move_days is not None else None)
        db.execute("UPDATE games SET started_at=?,last_move_at=? WHERE game_id=?",
                   (started, last_move, row["game_id"]))


def test_ready_backfill_prioritizes_recent_completion_over_recent_start(tmp_path):
    repository = GameRepository(tmp_path / "completion-priority.sqlite3")
    account = "lichess:synthetic"
    try:
        _archive_game(repository, "Synthetic", "finished-now", started_days=40,
                      last_move_days=0)
        _archive_game(repository, "Synthetic", "finished-last-week", started_days=1,
                      last_move_days=7)
        repository.enqueue_lichess_analysis_backfill(account)
        selected = repository.list_lichess_analysis_backfill(account, limit=1)
        assert selected and selected[0]["source_game_id"] == "finished-now"
    finally:
        repository.shutdown()


def test_manual_global_backfill_check_does_not_clear_account_wide_ttls(tmp_path):
    repository = GameRepository(tmp_path / "respect-global-ttl.sqlite3")
    account = repository.ensure_archive_account("Synthetic")
    try:
        ids = []
        for index in range(100):
            game_id = f"ttl-{index}"
            _archive_game(repository, "Synthetic", game_id, started_days=1,
                          last_move_days=1)
            ids.append(game_id)
        repository.enqueue_lichess_analysis_backfill(account)
        retry_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for game_id in ids:
            row = repository.lichess_analysis_backfill_state(account, game_id)
            repository.record_lichess_analysis_backfill(
                account, game_id, accuracy_status="no_server_data", evals_status="no_server_data",
                line_revision=row["line_revision"], line_hash=row["line_hash"],
                retry_after_seconds=86400)
        before = {game_id: repository.lichess_analysis_backfill_state(account, game_id)
                  for game_id in ids}
        assert repository.list_lichess_analysis_backfill(
            account, limit=10, force_retry=True) == []
        after = {game_id: repository.lichess_analysis_backfill_state(account, game_id)
                 for game_id in ids}
        assert {game_id: row["accuracy_next_retry_at"] for game_id, row in after.items()} == {
            game_id: row["accuracy_next_retry_at"] for game_id, row in before.items()}
        selected_source = "ttl-0"
        with repository._connection() as db:
            selected_game_id = db.execute(
                "SELECT game_id FROM games WHERE source_game_id=?", (selected_source,)
            ).fetchone()[0]
        selected_state = before[selected_source]
        assert repository.retry_lichess_analysis_game(
            account, selected_game_id, line_revision=selected_state["line_revision"])
        preferred = repository.list_lichess_analysis_backfill(
            account, limit=1, preferred_game_id=selected_game_id)
        assert preferred and preferred[0]["game_id"] == selected_game_id
        # An item-level 429 is never cleared by an explicit per-game no-data retry.
        limited_game_id = ids[1]
        limited_row = repository.lichess_analysis_backfill_state(account, limited_game_id)
        repository.record_lichess_analysis_backfill(
            account, limited_game_id, accuracy_status="rate_limited", evals_status="rate_limited",
            line_revision=limited_row["line_revision"], line_hash=limited_row["line_hash"],
            retry_after_seconds=3600)
        limited_before = repository.lichess_analysis_backfill_state(account, limited_game_id)
        assert not repository.retry_lichess_analysis_game(
            account, limited_game_id, line_revision=limited_row["line_revision"])
        limited_after = repository.lichess_analysis_backfill_state(account, limited_game_id)
        assert limited_after["accuracy_status"] == "rate_limited"
        assert limited_after["accuracy_next_retry_at"] == limited_before["accuracy_next_retry_at"]
    finally:
        repository.shutdown()


def test_finished_timed_game_with_invalid_clock_keeps_known_time_control(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "finished-clock.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    from chess_coach.lichess.session import LichessGameSession
    session = LichessGameSession("synthetic-clock", window.game_repository)
    session.speed = "blitz"
    session.clock_control = "180+2"
    session.clock_state_valid = False
    session.status = "mate"
    window.online_session = session
    window._update_clock()
    assert "без часов" not in window.clock_label.text().lower()
    assert "3+2" in window.clock_label.text().replace(" ", "") or "180+2" in window.clock_label.text()
    window.game_repository.shutdown()
    window.close()


def test_game_full_alone_restores_authoritative_speed_and_correspondence_days(tmp_path):
    repository = GameRepository(tmp_path / "game-full-metadata.sqlite3")
    from chess_coach.lichess.session import LichessGameSession
    session = LichessGameSession("metadata-only-full", repository)
    try:
        session._apply_game_full_metadata({
            "speed": "correspondence", "daysPerTurn": 3,
            "clock": {}, "rated": False,
        }, "startpos")
        assert session.speed == "correspondence"
        assert session.days_per_turn == 3
        assert session.clock_control == "-"
        # Missing optional metadata in a subsequent partial snapshot is not a
        # reason to erase the last authoritative classification.
        session._apply_game_full_metadata({"clock": {}}, "startpos")
        assert session.speed == "correspondence"
        assert session.days_per_turn == 3
    finally:
        repository.shutdown()


def test_populated_review_renders_full_move_table_and_selected_ply(qtbot, tmp_path):
    from chess_coach.ui.game_review import GameReviewWorkspace
    repository = GameRepository(tmp_path / "review-17-ply.sqlite3")
    moves = "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3"
    repository.save_snapshot("synthetic-review", "startpos", moves, "white", "Opponent",
                             False, "5+0", "mate", "white")
    game = repository.get_game("synthetic-review")
    workspace = GameReviewWorkspace(repository, training_allowed=True)
    qtbot.addWidget(workspace)
    try:
        workspace.load_game(game)
        assert len(workspace.review["moves"]) == 17
        assert workspace.moves_list.table.rowCount() == 9
        last = workspace.moves_list.item_by_ply(17)
        assert last is not None and last.text().endswith("h3")
        workspace.select_ply(17)
        assert workspace.selected_ply == 17
        assert workspace.review_board._board.fen() == workspace.review["positions"][17].fen()
    finally:
        repository.shutdown()


def test_open_review_from_home_keeps_home_as_back_destination(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "home-review-route.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    try:
        window.game_repository.save_snapshot(
            "home-route-game", "startpos", "e2e4 e7e5", "white", "Synthetic",
            False, "5+0", "resign", "white")
        game = window.game_repository.get_game("home-route-game")
        assert game
        window.show_home()
        assert window._open_saved_game(game)
        assert window.workspace_navigator.can_go_back
        window.navigate_back()
        assert window.screen_stack.currentWidget() is window.home_page
    finally:
        window.game_repository.shutdown()
        window.close()


def test_home_game_open_is_async_and_discards_late_account_switch_result(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "home-async-review.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    repository = window.game_repository
    account_a = repository.ensure_archive_account("Synthetic A")
    account_b = repository.ensure_archive_account("Synthetic B")
    repository.import_archive_record(
        "Synthetic A", {"id": "home-async", "pgn": '[White "A"]\n[Black "B"]\n'
         '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    with repository._connection() as db:
        game_id = db.execute(
            "SELECT game_id FROM games WHERE source_game_id='home-async'"
        ).fetchone()[0]
        game = repository.get_game(game_id)
        assert game
        db.execute("INSERT OR IGNORE INTO account_games(account_id,game_id,remote_id,imported_at) "
                   "VALUES(?,?,?,?)", (account_a, game_id, "home-async",
                                        datetime.now(timezone.utc).isoformat()))

    for account_id in (account_a, account_b):
        if window.dashboard_account.findData(account_id) < 0:
            window.dashboard_account.addItem(account_id, account_id)
    window.dashboard_account.setCurrentIndex(window.dashboard_account.findData(account_a))
    window._latest_dashboard_snapshot = {"filter": {"account_id": account_a}}
    window.show_home()
    entered = threading.Event()
    release = threading.Event()
    original_get = repository.get_game
    worker_ident = []

    def blocked_get(selected_id):
        worker_ident.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original_get(selected_id)

    monkeypatch.setattr(repository, "get_game", blocked_get)
    main_ident = threading.get_ident()
    try:
        window._open_home_game_details({**game, "account_id": account_a})
        assert entered.wait(1)
        timer_fired = threading.Event()
        QTimer.singleShot(0, timer_fired.set)
        qtbot.wait(250)
        assert timer_fired.is_set(), "Pending SQLite read must not block the Qt event loop."
        assert worker_ident and worker_ident[0] != main_ident
        window.dashboard_account.setCurrentIndex(window.dashboard_account.findData(account_b))
        release.set()
        handle = window._home_game_read_handle
        if handle is not None:
            handle.join(2)
        qtbot.wait(50)
        assert window.screen_stack.currentWidget() is window.home_page
        assert not window.review_workspace.review
    finally:
        release.set()
        repository.shutdown()
        window.close()


def test_backfill_large_archive_reserves_recently_completed_games(tmp_path):
    repository = GameRepository(tmp_path / "backfill-3510.sqlite3")
    account = repository.ensure_archive_account("Synthetic large")
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=40)).isoformat()
    recent_start = (now - timedelta(days=1)).isoformat()
    completed_now = int(now.timestamp() * 1000)
    with repository._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for index in range(3510):
            game_id = f"scale-{index:04d}"
            start = recent_start if index >= 3500 else stale
            last_move = completed_now - (3509 - index) * 1000 if index >= 3500 else None
            db.execute(
                "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                "updated_at,pgn,source,source_game_id,started_at,last_move_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (game_id, "startpos", "e2e4", "white", "Synthetic", 0, "5+0", "mate",
                 stale, "[Result \"*\"]\\n\\n1. e4 *", "lichess_archive", game_id,
                 start, last_move))
            db.execute(
                "INSERT INTO game_revisions(game_id,line_revision,initial_fen,moves,pgn,line_hash,"
                "created_at,is_current) VALUES(?,1,'startpos','e2e4','*','synthetic-line',?,1)",
                (game_id, stale))
            db.execute(
                "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                "VALUES(?,?,?,?,?,?)", (account, game_id, game_id, stale, "white", "Synthetic"))
        db.commit()
    try:
        repository.enqueue_lichess_analysis_backfill(account, limit=4000)
        selected = repository.list_lichess_analysis_backfill(account, limit=10)
        ids = [row["source_game_id"] for row in selected]
        assert len(ids) == 10 and len(set(ids)) == 10
        assert sum(game_id.startswith("scale-35") for game_id in ids) >= 8
        assert any(game_id.startswith("scale-00") for game_id in ids), (
            "the reserved old-backlog slice must not starve on recent completion influx")
    finally:
        repository.shutdown()


@pytest.mark.parametrize("ack_delay", [0.0, 0.5, 1.5])
def test_online_move_is_pending_immediately_but_confirmed_sound_waits_for_server(
        qtbot, tmp_path, monkeypatch, ack_delay):
    from chess_coach.lichess.session import LichessGameSession
    from types import SimpleNamespace

    window = MainWindow(QSettings(str(tmp_path / "move-pending-feedback.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    session = LichessGameSession("synthetic-pending", window.game_repository)
    session.status = "playing"
    session.awaiting_authoritative_snapshot = False
    session.owner_username = "synthetic"
    session.auth_generation = 3
    session.board = chess.Board()
    calls = []
    monkeypatch.setattr(window.sound_player, "play_move", lambda *a, **k: calls.append("sound"))
    started = threading.Event()
    release = threading.Event()

    def slow_submit(game_id, uci, request_id):
        started.set()
        threading.Thread(target=release.wait, daemon=True).start()

    window.lichess = SimpleNamespace(
        auth_generation=3, send_move=slow_submit,
        is_auth_generation_current=lambda generation: int(generation) == 3)
    window.online_session = session
    window._screen_mode = "online"
    try:
        window.on_move_intent(chess.Move.from_uci("e2e4"))
        assert started.wait(1)
        assert "ожидаем подтверждения" in window.game_status.text().lower()
        assert session.moves == ""  # Server-authoritative state did not advance.
        assert calls == []  # No confirmed move cue is played while the POST is pending.
        qtbot.wait(round(ack_delay * 1000))
        window._lichess_session_move_accepted(
            3, session.game_id, "e2e4", session.pending_request_id)
        assert calls == ["sound"]
        # A duplicate REST/stream acceptance can never duplicate the cue.
        window._lichess_session_move_accepted(
            3, session.game_id, "e2e4", session.pending_request_id)
        assert calls == ["sound"]
    finally:
        release.set()
        window.game_repository.shutdown()
        window.close()


def test_long_running_backfill_request_is_not_reclaimed_while_worker_is_alive(tmp_path):
    repository = GameRepository(tmp_path / "backfill-live-worker.sqlite3")
    account_id = repository.ensure_archive_account("Me")
    repository.import_archive_record(
        "Me", {"id": "long-request", "pgn": '[White "Me"]\n[Black "Them"]\n'
         '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill(account_id)
    row = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    worker_entered = threading.Event()
    release_worker = threading.Event()
    live_worker = threading.Thread(
        target=lambda: (worker_entered.set(), release_worker.wait(2)), daemon=True)
    assert repository.claim_lichess_analysis_backfill(
        account_id, row["game_id"], lease_owner="live-worker",
        line_revision=row["line_revision"], line_hash=row["line_hash"])
    assert repository.heartbeat_lichess_analysis_backfill(
        account_id, row["game_id"], lease_owner="live-worker")
    live_worker.start()
    try:
        assert worker_entered.wait(1)
        with repository._connection() as db:
            db.execute(
                "UPDATE lichess_analysis_backfill SET updated_at=? WHERE account_id=? AND game_id=?",
                ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),
                 account_id, row["game_id"]))
        repository.enqueue_lichess_analysis_backfill(account_id)
        state = repository.lichess_analysis_backfill_state(account_id, row["game_id"])
        assert state["accuracy_status"] == "running", (
            "a live request must retain the single-writer claim even when its start time is old")
    finally:
        release_worker.set()
        live_worker.join(timeout=1)
        repository.shutdown()


def test_backfill_claim_is_compare_and_set_for_two_workers(tmp_path):
    repository = GameRepository(tmp_path / "backfill-two-workers.sqlite3")
    account_id = repository.ensure_archive_account("Me")
    repository.import_archive_record(
        "Me", {"id": "single-claim", "pgn": '[White "Me"]\n[Black "Them"]\n'
         '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill(account_id)
    row = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    barrier = threading.Barrier(3)
    results = []

    def claim(owner):
        barrier.wait()
        results.append(repository.claim_lichess_analysis_backfill(
            account_id, row["game_id"], lease_owner=owner,
            line_revision=row["line_revision"], line_hash=row["line_hash"]))

    workers = [threading.Thread(target=claim, args=(f"worker-{n}",)) for n in range(2)]
    try:
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=2)
        assert results.count(True) == 1
        assert results.count(False) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=1)
        repository.shutdown()


def test_crashed_backfill_owner_is_reclaimed_only_after_heartbeat_expires(tmp_path):
    repository = GameRepository(tmp_path / "backfill-crash-recovery.sqlite3")
    account_id = repository.ensure_archive_account("Me")
    repository.import_archive_record(
        "Me", {"id": "crashed-worker", "pgn": '[White "Me"]\n[Black "Them"]\n'
         '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill(account_id)
    row = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    try:
        assert repository.claim_lichess_analysis_backfill(
            account_id, row["game_id"], lease_owner="crashed",
            line_revision=row["line_revision"], line_hash=row["line_hash"])
        with repository._connection() as db:
            expired = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            db.execute("UPDATE lichess_analysis_backfill SET lease_heartbeat_at=?,updated_at=? "
                       "WHERE account_id=? AND game_id=?",
                       (expired, expired, account_id, row["game_id"]))
        repository.enqueue_lichess_analysis_backfill(account_id)
        state = repository.lichess_analysis_backfill_state(account_id, row["game_id"])
        assert state["accuracy_status"] == "queued"
        assert state["lease_owner"] is None
        assert state["lease_heartbeat_at"] is None
        assert repository.claim_lichess_analysis_backfill(
            account_id, row["game_id"], lease_owner="recovered",
            line_revision=row["line_revision"], line_hash=row["line_hash"])
    finally:
        repository.shutdown()


def test_manual_analysis_handoff_pauses_batch_and_resumes_durable_cursor(tmp_path):
    from chess_coach.engine_manager import StockfishManager
    from chess_coach.lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions

    repository = GameRepository(tmp_path / "manual-batch-handoff.sqlite3")
    engine_path = tmp_path / "fake-engine.exe"
    engine_path.write_bytes(b"fake engine path; factory below never executes it")
    for game_id in ("handoff-first", "handoff-second"):
        repository.save_snapshot(game_id, "startpos", "e2e4 e7e5", "white", "Synthetic",
                                 False, "5+0", "mate", "white")
    first_search_entered = threading.Event()
    release_first_search = threading.Event()
    manual_acquired = threading.Event()
    release_manual = threading.Event()
    lock = threading.Lock()
    active = maximum_active = 0
    engines_created = []

    class FakeAnalysis:
        def __init__(self, board, *, block=False):
            self.info = {
                "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                "depth": 1, "nodes": 1,
                "pv": [next(iter(board.legal_moves))] if board.legal_moves else [],
            }
            self.block = block
            self.multipv = [dict(self.info)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            if self.block:
                first_search_entered.set()
                release_first_search.wait(3)
            yield self.info

        def stop(self):
            release_first_search.set()

    class FakeEngine:
        id = {"name": "Synthetic handoff engine", "author": "tests"}

        def __init__(self, *, block_first=False):
            nonlocal active, maximum_active
            self.block_first = block_first
            self.searches = 0
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)

        def configure(self, _options):
            pass

        def analysis(self, board, _limit, **_kwargs):
            self.searches += 1
            return FakeAnalysis(board, block=self.block_first and self.searches == 1)

        def quit(self):
            nonlocal active
            with lock:
                active -= 1

    def factory(*_args, **_kwargs):
        engine = FakeEngine(block_first=not engines_created)
        engines_created.append(engine)
        return engine

    manager = StockfishManager(factory, max_concurrent=1)
    runner = BatchAnalysisRunner(repository, engine_path,
                                 options=FastPassOptions(nodes=1000, hash_mb=16),
                                 engine_manager=manager)
    assert runner.enqueue_missing(limit=10)["queued"] == 2
    pause = threading.Event()
    stop = threading.Event()
    safety_checks = []
    batch_results = []

    def run_batch():
        batch_results.append(runner.run(
            stop_event=stop, pause_event=pause,
            safety_check=lambda _job: (safety_checks.append("batch") or True)))

    batch_thread = threading.Thread(target=run_batch, daemon=True)
    manual_errors = []

    def run_manual():
        try:
            engine = manager.open_engine(
                engine_path, stop_event=threading.Event(),
                safety_check=lambda: safety_checks.append("manual-after-wait") or True)
            manual_acquired.set()
            release_manual.wait(2)
            engine.quit()
        except Exception as exc:
            manual_errors.append(exc)

    manual_thread = None
    try:
        batch_thread.start()
        assert first_search_entered.wait(2), "Batch must own the shared engine lease first."
        manual_thread = threading.Thread(target=run_manual, daemon=True)
        manual_thread.start()
        # User manual analysis requests priority: the current batch search must
        # stop and release its lease before the manual caller can create/use UCI.
        pause.set()
        assert manual_acquired.wait(2), "Manual analysis must acquire after bounded batch pause."
        batch_thread.join(2)
        assert not batch_thread.is_alive()
        assert batch_results and batch_results[0]["paused"]
        assert repository.analysis_job_counts(
            engine_version=runner.engine_key, parameters=runner.parameters
        ) == {"paused": 1, "queued": 1}
        assert safety_checks.count("manual-after-wait") == 1
        release_manual.set()
        manual_thread.join(2)
        assert not manual_thread.is_alive() and not manual_errors
        resumed = repository.resume_paused_analysis_jobs(
            engine_version=runner.engine_key, parameters=runner.parameters)
        assert resumed == 1
        result = runner.run(stop_event=threading.Event(), safety_check=lambda _job: True)
        assert result["processed"] == 2 and not result["paused"]
        assert repository.list_analyses("handoff-first")
        assert repository.list_analyses("handoff-second")
        assert maximum_active == 1, "Batch/manual handoff must never overlap engine processes."
    finally:
        pause.set()
        release_first_search.set()
        release_manual.set()
        if batch_thread.is_alive():
            batch_thread.join(2)
        if manual_thread is not None and manual_thread.is_alive():
            manual_thread.join(2)
        manager.close_all()
        repository.shutdown()
