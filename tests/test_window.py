import chess
import chess.engine
import threading
import time
import pytest
from pathlib import Path
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QInputDialog, QMessageBox, QPushButton

from chess_coach.board_view import BoardView
from chess_coach.window import (ActiveGamesDialog, BatchAnalysisDialog, EvaluationGraph, MainWindow,
                                SettingsDialog, StatisticsDialog, StatisticsGameDialog,
                                _batch_safety_from_games, _puzzle_attempt_summary)
from chess_coach.lichess.session import LichessGameSession
from chess_coach.lichess.repository import GameRepository


@pytest.fixture(autouse=True)
def avoid_background_auth_restore(monkeypatch):
    # Window tests exercise presentation and event handling, not stored credentials.
    monkeypatch.setattr("chess_coach.lichess.service.LichessService.restore", lambda self: None)


def select_online(window, session):
    window.online_sessions[session.game_id] = session
    window.online_session = session
    window._selected_live_game_id = session.game_id
    window._screen_mode = "online"
    window.screen_stack.setCurrentWidget(window.game_page)


def test_main_window_smoke_and_game_controller_integration(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.start_local_game()
    window.show()
    assert isinstance(window.board, BoardView)
    assert window.local_mode_combo.currentData() == "free"
    assert not window.premove_title.isVisible()
    assert "Не подключено" in window.connection.text()
    assert window.controller.ply == 0
    e2 = window.board._get_board_rect()
    side = e2.width() / 8
    center_e2 = QPoint(int(e2.x() + 4.5 * side), int(e2.y() + 6.5 * side))
    center_e4 = QPoint(int(e2.x() + 4.5 * side), int(e2.y() + 4.5 * side))
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=center_e2)
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=center_e4)
    assert window.controller.ply == 1
    assert window.controller.san_moves() == [(1, "e4", "")]
    assert window.board._board.fen() == window.controller.board.fen()
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    assert window.board._queued_premove == chess.Move.from_uci("g1f3")
    index = window.opponent_move.findData("e7e5")
    assert index >= 0
    window.opponent_move.setCurrentIndex(index)
    qtbot.mouseClick(window.apply_opponent_button, Qt.MouseButton.LeftButton)
    assert window.controller.san_moves() == [(1, "e4", "e5"), (2, "Nf3", "")]
    assert window.controller.premove is None
    window.navigate_to(1)
    assert window.controller.ply == 3
    assert window.controller.snapshot().board.piece_at(chess.E4) == chess.Piece(
        chess.PAWN, chess.WHITE
    )
    rect = window.board._get_board_rect()
    side = rect.width() / 8
    e7 = QPoint(int(rect.x() + 4.5 * side), int(rect.y() + 1.5 * side))
    e5 = QPoint(int(rect.x() + 4.5 * side), int(rect.y() + 3.5 * side))
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=e7)
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=e5)
    assert window.controller.ply == 3
    window.navigate_live()
    assert window.controller.viewing_live_position


def test_evaluation_graph_click_emits_nearest_ply(qtbot):
    graph = EvaluationGraph([
        {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 30},
        {"ply": 2, "white_cp": -20}, {"ply": 3, "mate_white": 2},
    ])
    graph.resize(460, 220)
    selected = []
    graph.selected_ply.connect(selected.append)
    qtbot.addWidget(graph)
    graph.show()
    qtbot.wait(20)
    qtbot.mouseClick(graph, Qt.MouseButton.LeftButton, pos=QPoint(250, 100))
    assert selected == [2]


def test_evaluation_graph_keeps_phase_boundaries_for_rendering(qtbot):
    graph = EvaluationGraph([{"ply": 0, "white_cp": 0}, {"ply": 4, "white_cp": 10}],
                            phase_boundaries={"middle_start_ply": 2, "end_start_ply": 4,
                                              "source": "lichess"})
    qtbot.addWidget(graph)
    graph.resize(460, 220)
    graph.show()
    qtbot.wait(20)
    assert graph.phase_boundaries["source"] == "lichess"


def test_batch_analysis_controls_are_explicit_and_resume_after_pause(qtbot):
    dialog = BatchAnalysisDialog()
    qtbot.addWidget(dialog)
    started = []
    paused = []
    dialog.run_requested.connect(lambda *args: started.append(args))
    dialog.pause_requested.connect(lambda: paused.append(True))
    assert not dialog.pause_button.isEnabled()
    assert dialog.resume_button.isEnabled()
    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    assert started == [(1000, 20_000, 64, False)]
    dialog.set_running(True)
    assert dialog.close() is False
    qtbot.mouseClick(dialog.pause_button, Qt.MouseButton.LeftButton)
    assert paused == [True]
    dialog.worker_finished.emit(True, "Пауза")
    assert dialog.resume_button.isEnabled()
    qtbot.mouseClick(dialog.resume_button, Qt.MouseButton.LeftButton)
    assert started[-1] == (1000, 20_000, 64, True)
    dialog.worker_finished.emit(False, "Завершено")
    assert not dialog.pause_button.isEnabled()


def test_batch_analysis_safety_snapshot_fails_closed_and_allows_builtin_ai_only():
    ai = {"source": "ai", "opponent": {"ai": 3, "id": None}}
    bot_account = {"source": "friend", "opponent": {"ai": None, "id": "enginebot",
                                                       "title": "BOT"}}
    assert _batch_safety_from_games([]) is True
    assert _batch_safety_from_games([ai]) is True
    assert _batch_safety_from_games([bot_account]) is False
    assert _batch_safety_from_games(None) is None
    assert _batch_safety_from_games([ai] * 50) is None


def test_offline_window_never_enables_or_starts_batch_engine(qtbot, tmp_path):
    window = MainWindow(offline_mode=True)
    qtbot.addWidget(window)
    assert window.lichess is None
    assert not window.home_batch_analysis_button.isEnabled()
    assert window._batch_thread is None


def test_batch_queue_pauses_on_new_human_game_but_not_builtin_ai(qtbot):
    human_window = MainWindow(offline_mode=True)
    qtbot.addWidget(human_window)
    human_window._batch_pause = threading.Event()
    human_window._lichess_event({"type": "gameStart", "game": {
        "gameId": "human-during-batch", "source": "friend", "color": "white",
        "opponent": {"id": "friend", "username": "Friend"}}})
    assert human_window._batch_pause.is_set()

    ai_window = MainWindow(offline_mode=True)
    qtbot.addWidget(ai_window)
    ai_window._batch_pause = threading.Event()
    ai_window._lichess_event({"type": "gameStart", "game": {
        "gameId": "ai-during-batch", "source": "ai", "color": "white",
        "opponent": {"ai": 3, "id": None}}})
    assert not ai_window._batch_pause.is_set()


def test_window_close_cancels_and_waits_for_batch_worker(qtbot):
    window = MainWindow(offline_mode=True)
    qtbot.addWidget(window)
    stop = threading.Event()
    window._batch_stop = stop
    worker = threading.Thread(target=stop.wait, name="fake-batch-worker", daemon=True)
    window._batch_thread = worker
    worker.start()
    assert window.close() is False
    assert stop.is_set()
    qtbot.waitUntil(lambda: not worker.is_alive(), timeout=2000)
    assert window.close() is True


def test_statistics_screen_filters_asynchronously_and_resets(qtbot, tmp_path):
    repo = GameRepository(tmp_path / "statistics-screen.sqlite3")
    repo.save_snapshot("screen-win", "startpos", "e2e4", "white", "Alice",
                       False, "180+2", "finished", "white",
                       started_at="2026-10-31T12:00:00+00:00")
    repo.save_snapshot("screen-draw", "startpos", "d2d4", "black", "Bob",
                       False, "600+0", "draw", None,
                       started_at="2026-10-30T12:00:00+00:00")
    line = repo.current_line("screen-win")
    repo.save_analysis("screen-win", line_revision=1, line_hash=line["line_hash"],
        source="stockfish", engine_name="FixtureFish", engine_version="1",
        parameters={"nodes": 1}, assessments=[{"ply": 0, "white_cp": 0},
                                              {"ply": 1, "white_cp": -40}],
        classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 100,
                          "label": "mistake", "phase": "opening",
                          "algorithm_version": "test"}])
    dialog = StatisticsDialog(repo)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog._query_thread is None
                    and dialog.summary_label.text().startswith("Партии:"), timeout=5000)
    assert dialog.games.count() == 2
    win_item = next(dialog.games.item(index) for index in range(dialog.games.count())
                    if dialog.games.item(index).data(Qt.ItemDataRole.UserRole) == "screen-win")
    assert "н/о/з 0/1/0" in win_item.text()
    assert "2/2 поз." in win_item.text()
    assert "stockfish" in win_item.text()
    assert dialog.period.currentData() == 90
    dialog.result_filter.setCurrentIndex(dialog.result_filter.findData("draw"))
    qtbot.waitUntil(lambda: dialog._query_thread is None and dialog.games.count() == 1
                    and dialog.games.item(0).data(Qt.ItemDataRole.UserRole) == "screen-draw",
                    timeout=5000)
    assert dialog.games.item(0).data(Qt.ItemDataRole.UserRole) == "screen-draw"
    dialog.result_filter.setCurrentIndex(dialog.result_filter.findData(""))
    dialog.analysis_source.setCurrentIndex(dialog.analysis_source.findData("stockfish"))
    qtbot.waitUntil(lambda: dialog._query_thread is None and dialog.games.count() == 1
                    and dialog.games.item(0).data(Qt.ItemDataRole.UserRole) == "screen-win",
                    timeout=5000)
    assert dialog.games.item(0).data(Qt.ItemDataRole.UserRole) == "screen-win"
    dialog.reset_filters()
    qtbot.waitUntil(lambda: dialog._query_thread is None and dialog.games.count() == 2,
                    timeout=5000)
    assert "Смешанные локальные аккаунты" in dialog.mixed_warning.text()


def test_opening_phase_refresh_is_explicit_bounded_and_runs_in_worker(qtbot, tmp_path,
                                                                      monkeypatch):
    repo = GameRepository(tmp_path / "opening-worker.sqlite3")
    repo.save_snapshot("worker-game", "startpos", "e2e4", "white", "Friend", False,
                       "180+2", "finished", "white")
    repo.infer_missing_phase_boundaries = lambda limit=25: (1 if limit == 25 else 0)

    class FakeOpeningMatcher:
        def match_recent(self, repository, limit=10):
            assert repository is repo
            assert limit == 25
            return 1

    monkeypatch.setattr("chess_coach.lichess.opening_matcher.OpeningMatcher", FakeOpeningMatcher)
    dialog = StatisticsDialog(repo)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog._query_thread is None, timeout=5000)
    qtbot.mouseClick(dialog.opening_refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog._opening_thread is None
                    and "новых совпадений: 1" in dialog.opening_summary.text(), timeout=5000)
    assert "границ фаз: 1" in dialog.opening_summary.text()


def test_opening_refresh_defers_dialog_close_until_worker_finishes(qtbot, tmp_path,
                                                                    monkeypatch):
    import time

    repo = GameRepository(tmp_path / "opening-close.sqlite3")
    repo.infer_missing_phase_boundaries = lambda limit=25: 0

    class SlowOpeningMatcher:
        def match_recent(self, _repository, limit=10):
            time.sleep(0.1)
            return 0

    monkeypatch.setattr("chess_coach.lichess.opening_matcher.OpeningMatcher", SlowOpeningMatcher)
    dialog = StatisticsDialog(repo)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog._query_thread is None, timeout=5000)
    qtbot.mouseClick(dialog.opening_refresh_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog._opening_thread is not None, timeout=5000)
    dialog.accept()
    assert dialog._close_when_idle
    qtbot.waitUntil(lambda: dialog._opening_thread is None, timeout=5000)
    assert not dialog.isVisible()


def test_statistics_game_detail_move_selection_emits_position(qtbot, tmp_path):
    repo = GameRepository(tmp_path / "statistics-detail.sqlite3")
    repo.save_snapshot("detail-game", "startpos", "e2e4 e7e5", "white", "Friend",
                       False, "180+2", "finished", "white")
    line = repo.current_line("detail-game")
    run_id = repo.save_analysis("detail-game", line_revision=line["line_revision"],
        line_hash=line["line_hash"], source="stockfish", engine_name="FixtureFish",
        engine_version="fixture", parameters={"nodes": 1},
        assessments=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 15},
                     {"ply": 2, "white_cp": 5}],
        classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 10,
                          "label": "good", "phase": "opening",
                          "algorithm_version": "test"}])
    with repo._connection() as db:
        db.execute("INSERT INTO analysis_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("fixture-event", run_id, "detail-game", 1, 1, "missed_opportunity",
                    "candidate", "stockfish", "fixture-v1", '{"before_move":"e2e4"}'))
    dialog = StatisticsGameDialog(repo, repo.get_game("detail-game"))
    qtbot.addWidget(dialog)
    selected = []
    dialog.position_requested.connect(lambda game_id, ply: selected.append((game_id, ply)))
    dialog.moves.setCurrentRow(1)
    dialog.open_position.click()
    assert selected == [("detail-game", 2)]
    assert dialog.graph.assessments[1]["ply"] == 1
    assert dialog.events.count() == 1
    assert "before_move" in dialog.events.item(0).toolTip()
    dialog._open_event_position(dialog.events.item(0))
    assert selected[-1] == ("detail-game", 0)


def test_free_local_board_moves_both_sides_by_clicks_and_drag(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "free-board.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    window.start_local_game()
    window.resize(1100, 760)
    window.show()

    def pos(square):
        rect = window.board._get_board_rect()
        side = rect.width() / 8
        row, col = window.board._renderer.get_visual_coordinates(square, window.board._flipped)
        return QPoint(int(rect.x() + (col + 0.5) * side),
                      int(rect.y() + (row + 0.5) * side))

    def click_move(start, end):
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=pos(start))
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=pos(end))

    def drag_move(start, end):
        qtbot.mousePress(window.board, Qt.MouseButton.LeftButton, pos=pos(start))
        qtbot.mouseMove(window.board, pos(end), delay=20)
        qtbot.mouseRelease(window.board, Qt.MouseButton.LeftButton, pos=pos(end))

    assert not window.board._is_premove_turn()
    click_move(chess.E2, chess.E4)
    assert window.controller.ply == 1
    assert window.turn_label.text().startswith("Сейчас ход чёрных")
    assert "Вы играете белыми" not in window.game_status.text()

    window.board._annotation_arrows.add((chess.E7, chess.E5))
    click_move(chess.E7, chess.E5)
    assert window.controller.ply == 2
    assert not window.board._annotation_arrows

    window.board._annotation_arrows.add((chess.G1, chess.F3))
    drag_move(chess.G1, chess.F3)
    assert window.controller.ply == 3
    assert not window.board._annotation_arrows

    window.board._annotation_arrows.add((chess.B8, chess.C6))
    drag_move(chess.B8, chess.C6)
    assert window.controller.ply == 4
    assert not window.board._annotation_arrows
    assert window.controller.san_moves() == [(1, "e4", "e5"), (2, "Nf3", "Nc6")]
    assert window.board._board.fen() == window.controller.board.fen()
    assert not window.premove_title.isVisible()
    assert not window.apply_opponent_button.isVisible()
    assert not window.controller.premove_training


def test_local_mode_change_discards_premove_but_preserves_move_history(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "local-mode-switch.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.start_local_game()
    window.show()
    window.on_move_intent(chess.Move.from_uci("e2e4"))
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.premove_training
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()

    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("free"))

    assert window.controller.premove is None
    assert window.board._queued_premove is None
    assert window.controller.san_moves() == [(1, "e4", "")]
    assert "предход снят" in window.game_status.text()
    assert not window.premove_title.isVisible()
    assert window.turn_label.text().startswith("Сейчас ход чёрных")
    assert window.controller.apply_move_intent(chess.Move.from_uci("e7e5"))


def test_explicit_offline_window_never_constructs_or_uses_lichess_credentials(qtbot, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline mode must not construct Lichess or read credentials")

    monkeypatch.setattr("chess_coach.window.WindowsCredentialStore", forbidden)
    monkeypatch.setattr("chess_coach.window.LichessService", forbidden)
    live_settings_path = tmp_path / "user-settings.ini"
    live_settings = QSettings(str(live_settings_path), QSettings.Format.IniFormat)
    live_settings.setValue("board_flipped", True)
    live_settings.sync()
    original_settings = live_settings_path.read_bytes()
    window = MainWindow(live_settings, offline_mode=True)
    qtbot.addWidget(window)

    assert window.lichess is None
    assert "Офлайн-режим" in window.connection.text()
    assert not window.connect_button.isEnabled()
    assert not window.home_ai_button.isVisible()
    offline_dir = Path(window.game_repository.path).parent
    assert offline_dir.name.startswith("chess-coach-offline-")
    assert window.settings is not live_settings
    assert Path(window.settings.fileName()).parent == offline_dir
    assert window.controller.ply == 0
    assert window.screen_stack.currentWidget() is window.home_page
    assert not (tmp_path / "games.sqlite3").exists()
    window.settings.setValue("board_flipped", False)
    window.settings.sync()
    live_settings.sync()
    assert live_settings.value("board_flipped", type=bool) is True
    assert live_settings_path.read_bytes() == original_settings
    window.start_local_game()
    assert window.controller.ply == 0
    window.close()
    assert not offline_dir.exists()


def test_manual_connect_defers_lichess_and_credentials_until_button(qtbot, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Manual mode must not initialize OAuth before explicit user intent")

    monkeypatch.setattr("chess_coach.window.WindowsCredentialStore", forbidden)
    monkeypatch.setattr("chess_coach.window.LichessService", forbidden)
    settings = QSettings(str(tmp_path / "manual-settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("board_flipped", True)
    settings.sync()
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)

    assert window.lichess is None
    assert not window.offline_mode
    assert window.manual_connect_mode
    assert "Ручной режим" in window.connection.text()
    assert window.connect_button.isEnabled()
    assert window.connect_button.text() == "Подключиться вручную"
    assert "до нажатия" in window.home_status.text()
    assert Path(window.game_repository.path).parent.name == "ChessCoach"
    assert window.settings is settings
    assert settings.value("board_flipped", type=bool) is True

    calls = []

    class ExplicitlyCreatedService:
        token = None
        def connect_account(self): calls.append("oauth")
        def close(self, timeout=None): return True

    service = ExplicitlyCreatedService()
    monkeypatch.setattr(window, "_create_lichess_service", lambda: service)
    monkeypatch.setattr(window, "_attach_lichess_service", lambda _service: calls.append("attach"))
    qtbot.mouseClick(window.connect_button, Qt.MouseButton.LeftButton)

    assert window.lichess is service
    assert calls == ["attach", "oauth"]
    window.close()


def test_manual_connect_restores_saved_token_only_after_explicit_button(qtbot, tmp_path, monkeypatch):
    calls = []

    class SavedTokenService:
        token = "stored-token"
        def restore(self): calls.append("restore")
        def connect_account(self): calls.append("oauth")
        def close(self, timeout=None): return True

    window = MainWindow(QSettings(str(tmp_path / "manual-saved.ini"),
                                  QSettings.Format.IniFormat), manual_connect_mode=True)
    qtbot.addWidget(window)
    service = SavedTokenService()
    monkeypatch.setattr(window, "_create_lichess_service", lambda: service)
    monkeypatch.setattr(window, "_attach_lichess_service", lambda _service: calls.append("attach"))

    assert window.lichess is None
    assert calls == []
    window.connect_lichess()

    assert calls == ["attach", "restore"]
    window.close()


def test_archive_pilot_ui_requires_confirmation_and_shows_received_vs_new(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "pilot-ui.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    calls = []

    class Client:
        rate_limit_remaining = 0

    class Service:
        client = Client()
        username = "Alice"
        def pilot_import_archive(self, *, max_games): calls.append(("pilot", max_games))
        def cancel_archive_sync(self): calls.append(("cancel",))
        def close(self, timeout=None): return True

    window.lichess = Service()
    window.offline_mode = False
    window._lichess_connected = True
    window.show_home()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("5", True))
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs:
                        (confirmations.append(args[2]) or QMessageBox.StandardButton.Yes))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))

    qtbot.mouseClick(window.home_pilot_import_button, Qt.MouseButton.LeftButton)
    assert calls == [("pilot", 5)]
    window._archive_pilot_progress(5, 3, "pilot-4")
    assert "Получено: 5 · новых: 3" in window._archive_progress_dialog.labelText()
    window._archive_pilot_finished(5, 3, "Пробный импорт завершён.")
    assert "Записей получено: 5" in messages[0]
    assert "Новых партий: 3" in messages[0]
    assert "Курсор полной синхронизации не изменён" in messages[0]
    assert window._archive_progress_dialog is None
    window.close()


def test_archive_status_discloses_offline_database_and_local_count_on_request(qtbot, monkeypatch):
    window = MainWindow(offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot("local-status-game", "startpos", "", "white",
                                        "Local", False, "-", "playing")
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))

    window.show_archive_status()

    assert "Изолированная временная база --offline" in messages[0]
    assert "Режим запуска: --offline" in messages[0]
    assert "Python:" in messages[0] and "PySide6:" in messages[0]
    assert "Состояние Lichess: не подключено" in messages[0]
    assert "Локальных партий: 1" in messages[0]
    assert window.game_repository.path in messages[0]
    assert "Lichess аккаунт: не подключён" in messages[0]
    window.close()


def test_puzzle_pilot_ui_requires_bounded_choice_and_confirmation(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "puzzle-pilot-ui.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []

    class Client:
        rate_limit_remaining = 0

    class Service:
        client = Client()
        puzzle_username = "PilotUser"
        username = None
        def pilot_puzzle_activity(self, limit, repository):
            calls.append((limit, repository))
        def cancel_puzzle_sync(self):
            calls.append(("cancel",))
        def close(self, timeout=None): return True

    window.lichess = Service()
    window._lichess_connected = True
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("10", True))
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs:
                        (confirmations.append(args[2]) or QMessageBox.StandardButton.Yes))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(window, "show_puzzle_history", lambda account, **kwargs:
                        calls.append(("history", account, kwargs.get("pilot_run_id"))))

    window.start_puzzle_pilot()
    assert calls == [(10, window.game_repository)]
    assert "полный курсор синхронизации" in confirmations[0].lower()
    window._puzzle_pilot_progress(10, 6)
    assert "Получено попыток: 10 · новых: 6" in window._puzzle_pilot_progress_dialog.labelText()
    window._puzzle_pilot_finished(10, 6, "pilot-run", "Пробная загрузка задач завершена.")
    assert "Курсор полной синхронизации не изменён" in messages[0]
    assert calls[-1] == ("history", "lichess:pilotuser", "pilot-run")
    window.close()


def test_puzzle_history_summary_uses_attempt_denominators_and_available_fields():
    summary = _puzzle_attempt_summary([
        {"win": 1, "puzzle_rating": 1500, "themes_json": '["fork", "pin"]'},
        {"win": 0, "puzzle_rating": 1700, "themes_json": '["fork"]'},
        {"win": 1, "puzzle_rating": None, "themes_json": '["pin"]'},
    ])
    assert "3 попыток · решено 2 (66.7%)" in summary
    assert "средний рейтинг задачи 1600" in summary
    assert "fork: 1/2 (50%)" in summary
    assert "pin: 2/2 (100%)" in summary
    assert "учитываются отдельно" in summary


def test_active_games_dialog_shows_server_confirmed_turn_and_game_type(qtbot):
    dialog = ActiveGamesDialog()
    qtbot.addWidget(dialog)

    dialog.set_games([
        {"gameId": "corr-1", "color": "white", "speed": "correspondence",
         "isMyTurn": False, "opponent": {"username": "Correspondent"}},
        {"gameId": "fast-1", "color": "black", "speed": "blitz",
         "opponent": {"username": "Blitzer"}},
    ])

    assert dialog.games.count() == 2
    assert "заочная" in dialog.games.item(0).text()
    assert "ход соперника" in dialog.games.item(0).text()
    assert "реальное время" in dialog.games.item(1).text()
    assert "чей ход не указан" in dialog.games.item(1).text()


def test_open_active_game_clears_old_premove_and_background_events_do_not_submit(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "active-open.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    existing = LichessGameSession("background-game", window.game_repository)
    existing.color = chess.WHITE
    existing.apply_event({"type": "gameFull", "initialFen": "startpos",
                          "state": {"moves": "e2e4", "status": "started"}})
    existing.premove = chess.Move.from_uci("g1f3")
    window.online_sessions[existing.game_id] = existing
    window.online_session = existing
    window._lichess_connected = True
    window._selected_live_game_id = existing.game_id
    window._screen_mode = "online"
    starts = []
    sent = []
    monkeypatch.setattr(window.lichess, "start_game", starts.append)
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: sent.append(args))

    window.open_active_game({
        "gameId": "chosen-game", "color": "black", "speed": "rapid",
        "opponent": {"username": "Chosen"},
    })

    assert starts == ["chosen-game"]
    assert window._selected_live_game_id == "chosen-game"
    assert window.online_session.game_id == "chosen-game"
    assert existing.premove == chess.Move.from_uci("g1f3")
    window._lichess_game_event(existing.game_id, {
        "type": "gameState", "moves": "e2e4 e7e5", "status": "started"
    })
    assert sent == []


def test_active_game_blocks_all_actions_until_valid_game_full(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "active-sync.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window._lichess_connected = True
    started = []
    sent = []
    takebacks = []
    monkeypatch.setattr(window.lichess, "start_game", started.append)
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: sent.append(args))
    monkeypatch.setattr(window.lichess, "takeback", lambda *args: takebacks.append(args))

    window.open_active_game({
        "gameId": "twenty-ply-game", "color": "black", "speed": "rapid",
        "opponent": {"username": "Opponent"},
    })
    session = window.online_session
    assert session is not None and session.awaiting_authoritative_snapshot
    assert started == ["twenty-ply-game"]
    assert not window.board._interaction_enabled
    assert not window.takeback_button.isEnabled()

    window.on_move_intent(chess.Move.from_uci("e7e5"))
    window.on_premove_intent(chess.Move.from_uci("g8f6"))
    window.request_takeback()
    assert sent == []
    assert takebacks == []
    assert session.pending_uci is None
    assert session.premove is None
    assert session.takeback_action_pending is None

    window._lichess_status("Ограничение Lichess 429; запросы приостановлены")
    assert session.awaiting_authoritative_snapshot
    assert not window.board._interaction_enabled
    with pytest.raises(ValueError, match="полный начальный снимок"):
        session.apply_event({"type": "gameFull", "initialFen": "startpos"})
    assert session.awaiting_authoritative_snapshot

    moves = (
        "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 "
        "f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3 c8b7 d2d4 c6a5"
    )
    window._lichess_game_event("twenty-ply-game", {
        "type": "gameFull", "initialFen": "startpos",
        "state": {"moves": moves, "status": "started"},
    })

    assert len(session.board.move_stack) == 20
    assert session.color == chess.BLACK
    assert session.board.turn == chess.WHITE
    assert not session.awaiting_authoritative_snapshot
    assert window.board._board.fen() == session.board.fen()
    assert window.board._interaction_enabled

    window._lichess_game_event("twenty-ply-game", {
        "type": "gameState", "moves": moves + " b3c2", "status": "started",
    })
    assert len(session.board.move_stack) == 21
    assert session.board.turn == chess.BLACK
    window.on_move_intent(chess.Move.from_uci("e5d4"))
    assert len(sent) == 1 and sent[0][1] == "e5d4"


def test_confirmed_local_move_and_undo_clear_manual_marks_with_old_position_context(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "local-annotations.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window.start_local_game()
    actions = []
    window.board.annotation_action.connect(actions.append)
    window.board._annotation_arrows.add((chess.E2, chess.E4))

    window.on_move_intent(chess.Move.from_uci("e2e4"))

    assert not window.board._annotation_arrows
    assert actions[-1]["action"] == "clear"
    assert actions[-1]["game_id"] == "__local__"
    assert actions[-1]["ply"] == 0
    window.board._annotation_squares.add(chess.A1)
    window.undo_local_last_move()
    assert not window.board._annotation_squares
    assert actions[-1]["action"] == "clear"
    assert actions[-1]["ply"] == 1


def test_online_confirmed_moves_clear_marks_but_duplicates_and_clocks_do_not(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "online-annotations.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("annotation-game", window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    window.refresh()
    actions = []
    window.board.annotation_action.connect(actions.append)
    window.board._annotation_arrows.add((chess.E2, chess.E4))

    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4", "status": "started"
    })
    assert not window.board._annotation_arrows
    assert actions[-1]["action"] == "clear" and actions[-1]["ply"] == 0

    window.board._annotation_squares.add(chess.D4)
    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4", "status": "started", "wtime": 10_000
    })
    assert chess.D4 in window.board._annotation_squares
    assert len(actions) == 1

    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4 e7e5", "status": "started"
    })
    assert not window.board._annotation_squares
    assert actions[-1]["action"] == "clear" and actions[-1]["ply"] == 1


def test_selected_takeback_clears_marks_but_rejected_pending_move_preserves_them(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "branch-annotations.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("branch-annotations", window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    select_online(window, session)
    window.refresh()
    actions = []
    window.board.annotation_action.connect(actions.append)
    window.board._annotation_squares.add(chess.D4)
    window._lichess_game_event(session.game_id, {
        "type": "gameFull", "initialFen": "startpos",
        "state": {"moves": "e2e4", "status": "started"}
    })
    assert not window.board._annotation_squares
    assert actions[-1]["action"] == "clear"

    session.color = chess.BLACK
    assert session.begin_submission(chess.Move.from_uci("e7e5"))
    window.board._annotation_squares.add(chess.A1)
    window._lichess_move_failed(session.game_id, "e7e5", session.pending_request_id, 400)
    assert chess.A1 in window.board._annotation_squares


def test_completed_pair_allows_selecting_white_halfmove(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    for uci in ("e2e4", "e7e5"):
        assert window.controller.apply_move_intent(chess.Move.from_uci(uci))
    live_fen = window.controller.board.fen()
    window.refresh()
    assert window.moves.count() == 2
    white_item = next(
        window.moves.item(index)
        for index in range(window.moves.count())
        if window.moves.item(index).data(Qt.ItemDataRole.UserRole) == 1
    )
    assert white_item.text() == "1. e4"
    qtbot.mouseClick(
        window.moves.viewport(),
        Qt.MouseButton.LeftButton,
        pos=window.moves.visualItemRect(white_item).center(),
    )
    assert window.controller.view_ply == 1
    assert window.controller.snapshot().board.fen().split()[1] == "b"
    assert window.controller.board.fen() == live_fen
    assert window.controller.ply == 2


def test_visual_preferences_persist_in_qsettings(tmp_path) -> None:
    path = str(tmp_path / "settings.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    dialog.color.setCurrentIndex(1)
    dialog.flipped.setChecked(True)
    dialog.legal.setChecked(False)
    dialog.last.setChecked(False)
    dialog.annotations.setChecked(True)
    dialog.save(settings)

    loaded = QSettings(path, QSettings.Format.IniFormat)
    assert loaded.value("player_color", type=int) == 0
    assert loaded.value("board_flipped", type=bool) is True
    assert loaded.value("show_legal_moves", type=bool) is False
    assert loaded.value("show_last_move", type=bool) is False
    assert loaded.value("annotations_enabled", type=bool) is True


def test_stockfish_settings_persist_without_launching_engine(tmp_path) -> None:
    from chess_coach.lichess.analysis import AnalysisOptions

    path = str(tmp_path / "stockfish-settings.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    dialog.engine_path.setText(str(tmp_path / "user-installed-stockfish.exe"))
    dialog.analysis_preset.setCurrentIndex(dialog.analysis_preset.findData("detailed"))
    dialog.analysis_limit.setCurrentIndex(dialog.analysis_limit.findData("depth"))
    dialog.analysis_limit_value.setValue(18)
    dialog.engine_threads.setValue(4)
    dialog.engine_hash.setValue(256)
    options = dialog.analysis_options()
    assert options == AnalysisOptions(limit_kind="depth", limit_value=18,
                                     threads=4, hash_mb=256)
    assert dialog.engine_test_status.text().startswith("Проверка запускается")
    assert dialog._engine_process is None
    dialog.save(settings)
    loaded = QSettings(path, QSettings.Format.IniFormat)
    assert loaded.value("stockfish_path") == str(tmp_path / "user-installed-stockfish.exe")
    assert loaded.value("analysis_limit_kind") == "depth"
    assert loaded.value("analysis_limit_value", type=int) == 18
    assert loaded.value("stockfish_threads", type=int) == 4
    assert loaded.value("stockfish_hash_mb", type=int) == 256


def test_saved_custom_node_limit_survives_settings_reopen_and_preset_changes_are_explicit(tmp_path):
    settings = QSettings(str(tmp_path / "custom-node-limit.ini"), QSettings.Format.IniFormat)
    settings.setValue("analysis_preset", "normal")
    settings.setValue("analysis_limit_kind", "nodes")
    settings.setValue("analysis_limit_value", 333_333)
    dialog = SettingsDialog(settings)
    assert dialog.analysis_limit_value.value() == 333_333
    dialog.save(settings)
    reopened = SettingsDialog(settings)
    assert reopened.analysis_limit_value.value() == 333_333
    reopened.analysis_preset.setCurrentIndex(reopened.analysis_preset.findData("quick"))
    assert reopened.analysis_limit_value.value() == 25_000


def test_stockfish_uci_probe_timeout_waits_for_process_finished(qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    class FakeSignal:
        def __init__(self): self.callbacks = []
        def connect(self, callback): self.callbacks.append(callback)
        def emit(self, *args):
            for callback in list(self.callbacks): callback(*args)

    class FakeProcess:
        class ProcessState:
            NotRunning = 0
            Running = 1
        ProcessState = ProcessState
        def __init__(self, _parent):
            created.append(self)
            self.started = FakeSignal()
            self.readyReadStandardOutput = FakeSignal()
            self.errorOccurred = FakeSignal()
            self.finished = FakeSignal()
            self.current_state = self.ProcessState.NotRunning
            self.program = None
            self.killed = False
            self.deleted = False
            self.output = b""
            self.writes = []
        def start(self, program, _args):
            self.program = program
            self.current_state = self.ProcessState.Running
        def state(self): return self.current_state
        def write(self, payload): self.writes.append(payload); return len(payload)
        def readAllStandardOutput(self):
            payload, self.output = self.output, b""
            return payload
        def kill(self): self.killed = True  # completion is intentionally delayed
        def deleteLater(self): self.deleted = True
        def errorString(self): return "test error"
        def complete(self):
            self.current_state = self.ProcessState.NotRunning
            self.finished.emit(1, 0)

    created = []
    monkeypatch.setattr(window_module, "QProcess", FakeProcess)
    dialog = SettingsDialog(QSettings(str(tmp_path / "probe-timeout.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(dialog)
    dialog.engine_path.setText("C:/user-selected/stockfish.exe")
    assert created == []
    dialog.engine_test.click()
    process = created[0]
    process.started.emit()
    assert process.program == "C:/user-selected/stockfish.exe"
    assert process.writes == [b"uci\n"]
    dialog._engine_probe_timeout(process)
    assert process.killed and not process.deleted
    assert dialog._engine_process is process
    assert not dialog.engine_test.isEnabled()
    process.complete()
    assert process.deleted
    assert dialog._engine_process is None
    assert dialog.engine_test.isEnabled()
    assert "5 секунд" in dialog.engine_test_status.text()


def test_stockfish_uci_probe_quits_gracefully_and_close_waits_for_delayed_finished(
        qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    class FakeSignal:
        def __init__(self): self.callbacks = []
        def connect(self, callback): self.callbacks.append(callback)
        def emit(self, *args):
            for callback in list(self.callbacks): callback(*args)

    class FakeProcess:
        class ProcessState:
            NotRunning = 0
            Running = 1
        ProcessState = ProcessState
        def __init__(self, _parent):
            created.append(self)
            self.started = FakeSignal()
            self.readyReadStandardOutput = FakeSignal()
            self.errorOccurred = FakeSignal()
            self.finished = FakeSignal()
            self.current_state = self.ProcessState.NotRunning
            self.output = b""
            self.writes = []
            self.deleted = False
            self.killed = False
        def start(self, _program, _args): self.current_state = self.ProcessState.Running
        def state(self): return self.current_state
        def write(self, payload): self.writes.append(payload); return len(payload)
        def readAllStandardOutput(self):
            payload, self.output = self.output, b""
            return payload
        def kill(self): self.killed = True
        def deleteLater(self): self.deleted = True
        def errorString(self): return "test error"
        def complete(self):
            self.current_state = self.ProcessState.NotRunning
            self.finished.emit(0, 0)

    created = []
    monkeypatch.setattr(window_module, "QProcess", FakeProcess)
    dialog = SettingsDialog(QSettings(str(tmp_path / "probe-close.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(dialog)
    dialog.engine_path.setText("fake-stockfish.exe")
    dialog.engine_test.click()
    process = created[0]
    process.output = b"id name FakeFish\nuciok\n"
    process.readyReadStandardOutput.emit()
    assert process.writes[-1] == b"quit\n"
    assert dialog._engine_process is process and not process.deleted
    assert not dialog.engine_test.isEnabled()

    dialog.reject()  # repeated close/cancel while graceful shutdown is pending is idempotent
    assert dialog._engine_process is process and not process.deleted
    process.complete()
    assert dialog._engine_process is None and process.deleted
    assert dialog.engine_test.isEnabled()
    assert dialog.engine_test_status.text() == "UCI отвечает: FakeFish"

    dialog.engine_test.click()
    second = created[1]
    dialog.reject()  # cancellation kills the child but does not destroy its live wrapper
    assert second.killed and not second.deleted
    assert dialog._engine_process is second
    second.complete()
    assert dialog._engine_process is None and second.deleted


def test_cancel_premove_button_clears_queued_intent(qtbot, tmp_path) -> None:
    import chess

    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.start_local_game()
    window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    assert window.cancel_premove_button.isEnabled()
    qtbot.mouseClick(window.cancel_premove_button, Qt.MouseButton.LeftButton)
    assert window.controller.premove is None
    assert not window.cancel_premove_button.isEnabled()


def test_new_game_clears_premove_after_confirmation(qtbot, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.start_local_game()
    window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    window.new_game()
    assert window.controller.ply == 0
    assert window.controller.premove is None
    assert window.board._queued_premove is None


@pytest.mark.parametrize("gesture", ["click", "drag"])
def test_online_premove_is_available_on_opponent_turn_and_sent_once_after_state(
    qtbot, tmp_path, monkeypatch, gesture
) -> None:
    from PySide6.QtCore import QPoint

    window = MainWindow(QSettings(str(tmp_path / f"{gesture}.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda game_id, uci, request_id: calls.append(
        (game_id, uci, request_id)
    ))
    session = LichessGameSession(gesture, GameRepository(tmp_path / f"{gesture}.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, session)
    window.online_view_ply = None
    window.show()
    window._refresh_online()
    assert window.board._interaction_enabled  # permits premove gestures during opponent turn
    assert not session.is_my_turn

    rect = window.board._get_board_rect()
    side = rect.width() / 8
    def center(square):
        row, col = window.board._renderer.get_visual_coordinates(square, window.board._flipped)
        return QPoint(int(rect.x() + (col + .5) * side), int(rect.y() + (row + .5) * side))
    start, end = center(chess.G1), center(chess.F3)
    window.board.show()
    if gesture == "click":
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=end)
    else:
        qtbot.mousePress(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(window.board, end, delay=20)
        qtbot.mouseRelease(window.board, Qt.MouseButton.LeftButton, pos=end)
    assert session.premove == chess.Move.from_uci("g1f3")
    assert session.moves == "e2e4"  # queue does not mutate confirmed history
    assert window.cancel_premove_button.isVisible()
    qtbot.mouseClick(window.cancel_premove_button, Qt.MouseButton.LeftButton)
    # Restore the intent after also verifying explicit UI cancellation.
    assert session.premove is None
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    window._refresh_online()
    window._lichess_game_event(gesture, {"type": "gameState", "moves": "e2e4 e7e5",
                                         "status": "started"})
    assert calls == [(gesture, "g1f3", session.pending_request_id)]
    window._lichess_game_event(gesture, {"type": "gameState", "moves": "e2e4 e7e5",
                                         "status": "started"})
    assert len(calls) == 1  # repeated state must not submit the same premove again


def test_online_premove_is_discarded_when_server_position_makes_it_illegal(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "illegal.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: calls.append(args))
    session = LichessGameSession("illegal", GameRepository(tmp_path / "illegal.sqlite3"))
    session.color = chess.WHITE
    fen = "4k3/8/8/2b5/8/8/8/4K1N1 b - - 0 1"
    session.apply_event({"type": "gameFull", "initialFen": fen,
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    window.show()
    window._refresh_online()
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    window._lichess_game_event("illegal", {"type": "gameState", "moves": "c5f2",
                                             "status": "started"})
    assert session.premove is None
    assert not calls


def test_online_pending_move_is_rendered_optimistically_without_persisting_until_confirmation(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(QSettings(str(tmp_path / "optimistic-ui.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: calls.append(args))
    session = LichessGameSession("optimistic-ui", GameRepository(tmp_path / "optimistic-ui.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    window.online_view_ply = None
    window.show()

    window.on_move_intent(chess.Move.from_uci("e2e4"))
    assert window.board._board.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert window.board._board.piece_at(chess.E2) is None
    assert session.board.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert session.moves == ""
    assert session.repository.get_game(session.game_id)["moves"] == ""
    assert window.moves.count() == 0  # the move is not yet confirmed SAN history
    assert "подтвержд" in window.game_status.text()

    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4",
                                                  "status": "started"})
    assert session.pending_uci is None
    assert window.board._board.fen() == session.board.fen()
    assert session.moves == "e2e4"
    assert session.repository.get_game(session.game_id)["moves"] == "e2e4"
    assert window.moves.count() == 1
    assert len(calls) == 1


def test_takeback_offer_ui_accept_and_server_rewind_refresh_history_and_pgn(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "takeback-ui.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    requests = []
    monkeypatch.setattr(window.lichess, "takeback", lambda game_id, action, request_id:
                        requests.append((game_id, action, request_id)))
    session = LichessGameSession("takeback-ui", GameRepository(tmp_path / "takeback-ui.sqlite3"))
    session.color = chess.WHITE
    line = "e2e4 e7e5 g1f3 b8c6"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    select_online(window, session)
    window.online_view_ply = None
    window.show()
    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": line,
                                                  "btakeback": True, "status": "started"})
    assert session.takeback_requested_by_opponent
    assert window.accept_takeback_button.isVisible()
    assert "Соперник предлагает вернуть ход" in window.game_status.text()

    confirmed_before = session.board.fen()
    qtbot.mouseClick(window.accept_takeback_button, Qt.MouseButton.LeftButton)
    assert requests[0][1] == "accept"
    assert session.board.fen() == confirmed_before
    assert session.moves == line
    assert session.takeback_accept_pending
    window._lichess_takeback_completed(session.game_id, requests[0][2], 0)
    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4 e7e5",
                                                  "status": "started"})
    assert session.moves == "e2e4 e7e5"
    assert session.board.ply() == 2
    assert window.moves.count() == 2
    assert window.board._board.fen() == session.board.fen()
    assert not window.accept_takeback_button.isVisible()
    assert session.repository.get_game(session.game_id)["moves"] == "e2e4 e7e5"
    pgn = session.repository.export_pgn(session.game_id)
    assert "Nf3" not in pgn and "Nc6" not in pgn


def test_home_game_result_review_home_and_new_game_flow(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "flow.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window.show()
    assert window.screen_stack.currentWidget() is window.home_page

    window._awaiting_game_start = True
    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "flow-game", "color": "white", "opponent": {"username": "AI"}
    }})
    assert window.screen_stack.currentWidget() is window.game_page
    assert window._screen_mode == "online"
    session = window.online_session
    assert session is not None
    window._lichess_game_event("flow-game", {
        "type": "gameFull", "initialFen": "startpos", "rated": False,
        "state": {"moves": "e2e4 e7e5", "status": "started"},
        "white": {"name": "Coach"}, "black": {"name": "AI"},
    })
    window.show_home()
    assert window.screen_stack.currentWidget() is window.home_page
    assert window.online_session is session and session.status == "started"
    assert window.resume_game_button.isVisible()
    window.resume_current_game()
    assert window.screen_stack.currentWidget() is window.game_page
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": "flow-game", "status": {"name": "mate"}, "winner": "white"
    }})
    assert window.screen_stack.currentWidget() is window.result_page
    assert "Победа" in window.result_summary.text()
    assert "ожидаем подтверждённую финальную историю" in window.result_saved_label.text()
    assert not window.resign_button.isVisible()
    assert window.game_repository.get_game("flow-game")["status"] == "mate"

    qtbot.mouseClick(window.result_review_button, Qt.MouseButton.LeftButton)
    assert window._screen_mode == "review"
    assert window.screen_stack.currentWidget() is window.game_page
    assert not window.board._interaction_enabled
    assert not window.resign_button.isVisible()
    window.leave_online_game()
    assert window.screen_stack.currentWidget() is window.home_page
    window.start_local_game()
    assert window.screen_stack.currentWidget() is window.game_page
    assert window._screen_mode == "local"
    assert window.controller.ply == 0


def test_online_history_position_stays_selected_until_explicit_live_navigation(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "history-stays.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("history-stays", GameRepository(tmp_path / "history-stays.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    select_online(window, session)
    window.show()
    window.navigate_to(1)
    assert window.online_view_ply == 1
    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4 e7e5 g1f3 b8c6", "status": "started"
    })
    assert window.online_view_ply == 1
    assert window.board._board.ply() == 1
    assert session.board.ply() == 4
    window.navigate_live()
    assert window.online_view_ply is None
    assert window.board._board.ply() == 4


def test_events_from_other_games_do_not_switch_review_or_result(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "parallel.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    a = LichessGameSession("game-a", GameRepository(tmp_path / "parallel.sqlite3"))
    a.color = chess.WHITE
    a.apply_event({"type": "gameFull", "initialFen": "startpos",
                   "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, a)
    window.show()
    window.navigate_to(0)
    selected_ply = window.online_view_ply
    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "game-b", "color": "black", "opponent": {"username": "Other"}
    }})
    window._lichess_game_event("game-b", {
        "type": "gameFull", "initialFen": "startpos",
        "state": {"moves": "d2d4", "status": "started"}
    })
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": "game-b", "status": {"name": "resign"}, "winner": "white"
    }})
    assert window.online_session is a
    assert window._selected_live_game_id == "game-a"
    assert window.online_view_ply == selected_ply
    assert window._screen_mode == "online"
    assert a.status == "started"
    assert window.online_sessions["game-b"].status == "resign"


def test_background_game_finish_cannot_replace_selected_result(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "parallel-result.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    a = LichessGameSession("result-a", GameRepository(tmp_path / "parallel-result.sqlite3"))
    a.color = chess.WHITE
    a.apply_event({"type": "gameFull", "initialFen": "startpos",
                   "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, a)
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": "result-a", "status": {"name": "mate"}, "winner": "white"
    }})
    assert window._screen_mode == "result"
    result_text = window.result_summary.text()
    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "result-b", "color": "black", "opponent": {"username": "Other"}
    }})
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": "result-b", "status": {"name": "resign"}, "winner": "white"
    }})
    assert window._screen_mode == "result"
    assert window.online_session is a
    assert window.result_summary.text() == result_text


def test_late_active_game_state_cannot_reopen_finished_session_or_overwrite_result(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "finish-order.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("finish-order", GameRepository(tmp_path / "finish-order.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, session)
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": session.game_id, "status": {"name": "mate"}, "winner": "white"
    }})
    saved_before = session.repository.get_game(session.game_id)
    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4 e7e5", "status": "started"
    })
    saved_after = session.repository.get_game(session.game_id)
    assert window._screen_mode == "result"
    assert session.status == "mate"
    assert session.moves == "e2e4"
    assert saved_after["status"] == saved_before["status"] == "mate"
    assert saved_after["moves"] == saved_before["moves"] == "e2e4"


def test_game_finished_while_home_updates_resume_action_without_switching_screen(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "home-finish.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window.show()
    session = LichessGameSession("home-finish", GameRepository(tmp_path / "home-finish.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, session)
    window.show_home()
    assert window.resume_game_button.isVisible()
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": session.game_id, "status": {"name": "resign"}, "winner": "black"
    }})
    assert window.screen_stack.currentWidget() is window.home_page
    assert window._screen_mode == "home"
    assert session.status == "resign"
    assert not window.resume_game_button.isVisible()


def test_game_finish_for_selected_review_does_not_replace_review(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "review-not-stolen.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    live = LichessGameSession("review-id", GameRepository(tmp_path / "review-not-stolen.sqlite3"))
    live.color = chess.WHITE
    live.apply_event({"type": "gameFull", "initialFen": "startpos",
                      "state": {"moves": "e2e4", "status": "started"}})
    window.online_sessions[live.game_id] = live
    saved = {"game_id": live.game_id, "initial_fen": "startpos", "moves": "d2d4",
             "color": "black", "opponent": "Archive", "status": "mate",
             "winner": "black", "clock": "-"}
    window._open_saved_game(saved)
    review = window.online_session
    window._lichess_game_event(live.game_id, {
        "type": "gameState", "moves": "e2e4 e7e5", "status": "started"
    })
    assert window._screen_mode == "review"
    assert window.online_session is review
    assert window.online_session.moves == "d2d4"
    assert live.moves == "e2e4 e7e5"


def test_raw_only_variant_is_marked_and_open_does_not_create_standard_board(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "raw-variant.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    pgn = ('[Event "Variant"]\n[Variant "Crazyhouse"]\n[White "Me"]\n'
           '[Black "Friend"]\n[Result "*"]\n\n1. e4 *\n')
    window.game_repository.import_archive_record(
        "Me", {"id": "raw-variant", "variant": "crazyhouse", "pgn": pgn},
        checkpoint_ms=99, backfill=True)
    game = window.game_repository.get_game("raw-variant")

    assert window._open_saved_game(game) is False
    assert window.online_session is None
    assert window._screen_mode == "home"
    assert "Crazyhouse" in window.home_status.text()
    assert "Экспорт PGN доступен" in window.home_status.text()
    assert window.game_repository.export_pgn("raw-variant") == pgn


def test_finish_before_final_state_keeps_result_screen_then_updates_saved_pgn(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "finish-late-ply.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("finish-late-ply", window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "f2f3 e7e5 g2g4", "status": "started"}})
    window.lichess.start_game = lambda game_id: None
    window.lichess.mark_game_finished = lambda game_id: None
    select_online(window, session)
    window._lichess_event({"type": "gameFinish", "game": {
        "gameId": session.game_id, "status": {"name": "mate"}, "winner": "black"
    }})
    assert window._screen_mode == "result"
    assert session.final_sync_pending
    assert "ожидаем подтверждённую финальную историю" in window.result_saved_label.text()

    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "f2f3 e7e5 g2g4 d8h4", "status": "mate", "winner": "black"
    })
    assert window._screen_mode == "result"
    assert window.online_session is session
    assert not session.final_sync_pending
    assert "Партия сохранена локально" in window.result_saved_label.text()
    assert "Qh4#" in session.repository.export_pgn(session.game_id)


def test_authentication_resumes_pending_final_sync_without_screen_takeover(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "resume-final-sync.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("resume-final", window.game_repository)
    session.color = chess.BLACK
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    session.finish("resign", "white")
    started = []
    marked = []
    monkeypatch.setattr(window.lichess, "start_game", lambda game_id: started.append(game_id))
    monkeypatch.setattr(window.lichess, "mark_game_finished", lambda game_id: marked.append(game_id))

    window._lichess_authenticated("Owner")

    assert "resume-final" in started
    assert "resume-final" in marked
    assert window._screen_mode == "home"
    assert window.online_sessions["resume-final"].final_sync_pending


def test_takeback_button_is_clear_for_ai_and_sends_one_yes_request(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "ai-takeback.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("ai-takeback", window.game_repository)
    session.opponent = "Lichess AI level 4"
    session.set_server_game_identity({"source": "ai", "opponent": {
        "id": None, "username": "Lichess AI level 4", "ai": 4}})
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    select_online(window, session)
    requests = []
    monkeypatch.setattr(window.lichess, "takeback", lambda *args: requests.append(args))
    window.refresh()
    assert window.takeback_button.text() == "Вернуть ход"
    assert "Lichess AI" in window.takeback_button.toolTip()

    window.request_takeback()

    assert len(requests) == 1
    assert requests[0][0:2] == (session.game_id, "offer")
    assert session.moves == "e2e4 e7e5"  # request never rewinds local server state
    assert session.takeback_requested_by_me
    window._lichess_takeback_completed(session.game_id, requests[0][2], 0)
    assert "Lichess AI" in window.game_status.text()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "отклонил запрос возврата хода (400)"),
        (429, "ограничил запросы (429)"),
    ],
)
def test_ai_takeback_errors_never_rewind_server_position(qtbot, tmp_path, monkeypatch, status, expected):
    window = MainWindow(QSettings(str(tmp_path / f"ai-takeback-{status}.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession(f"ai-takeback-{status}", window.game_repository)
    session.opponent = "Lichess AI level 4"
    session.set_server_game_identity({"source": "ai", "opponent": {
        "id": None, "username": "Lichess AI level 4", "ai": 4}})
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    select_online(window, session)
    request_ids = []
    monkeypatch.setattr(window.lichess, "takeback", lambda *args: request_ids.append(args[2]))

    window.request_takeback()
    assert len(request_ids) == 1
    window._lichess_takeback_completed(session.game_id, request_ids[0], status)

    assert session.moves == "e2e4 e7e5"
    assert expected in window.game_status.text()


def test_human_takeback_button_remains_an_offer_and_429_is_explicit(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "human-takeback.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("human-takeback", window.game_repository)
    session.opponent = "Friend"
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, session)
    window.refresh()
    assert window.takeback_button.text() == "Предложить вернуть ход"
    monkeypatch.setattr(window.lichess, "takeback", lambda *args: None)
    window.request_takeback()
    window._lichess_takeback_completed(session.game_id, session.takeback_request_id, 429)
    assert "429" in window.game_status.text()
    assert session.moves == "e2e4"


def test_cancel_premove_does_not_undo_a_move_already_submitted_to_lichess(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "sent-premove.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("sent-premove", window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
    submitted = session.take_ready_premove()
    assert submitted == chess.Move.from_uci("g1f3")
    assert session.begin_submission(submitted)
    sent_request_id = session.pending_request_id
    window.online_session = session

    window.cancel_premove()

    assert session.pending_uci == "g1f3"
    assert session.pending_request_id == sent_request_id


def test_offline_undo_button_is_separate_from_premove_cancel(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "offline-undo.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window.show()
    window.start_local_game()
    assert window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    window.refresh()
    assert window.undo_local_button.isVisible()
    assert window.undo_local_button.text() == "Отменить последний ход"

    window.undo_local_button.click()

    assert window.controller.ply == 0
    assert "отменён" in window.game_status.text()


def test_premove_setting_is_persisted_and_disabling_clears_queued_move(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "premove-setting.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    assert dialog.allow_premoves.isChecked()
    dialog.allow_premoves.setChecked(False)
    dialog.save(settings)
    assert not settings.value("allow_premoves", True, type=bool)

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.start_local_game()
    assert window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.board.set_premove(chess.Move.from_uci("g1f3"), chess.WHITE)
    window.refresh()
    assert window.controller.premove is None
    assert window.board._queued_premove is None


def test_online_history_refresh_is_incremental_and_handles_takeback(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "incremental-history.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    session = LichessGameSession("incremental-history", window.game_repository)
    line = "e2e4 e7e5 g1f3 b8c6 f1b5"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    select_online(window, session)
    window.refresh()
    removed = []
    window.moves.model().rowsRemoved.connect(lambda *args: removed.append(args))

    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": line, "status": "started", "wtime": 1000, "btime": 1000
    })
    assert not removed
    assert window.moves.count() == 5

    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": line + " a7a6", "status": "started"
    })
    assert not removed
    assert window.moves.count() == 6

    window._lichess_game_event(session.game_id, {
        "type": "gameFull", "initialFen": "startpos",
        "state": {"moves": "e2e4 e7e5 g1f3 b8c6", "status": "started"}
    })
    assert removed
    assert window.moves.count() == 4


def test_fifty_clock_events_on_200_ply_history_keep_history_rows_and_refresh_under_budget(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "long-history-refresh.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    line = " ".join(["g1f3", "g8f6", "f3g1", "f6g8"] * 50)
    session = LichessGameSession("long-history-refresh", window.game_repository)
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    select_online(window, session)
    window.refresh()
    removed = []
    window.moves.model().rowsRemoved.connect(lambda *args: removed.append(args))
    started = time.perf_counter()
    for tick in range(50):
        window._lichess_game_event(session.game_id, {
            "type": "gameState", "moves": line, "status": "started",
            "wtime": 200_000 - tick, "btime": 200_000 - tick,
        })
    elapsed = time.perf_counter() - started
    assert not removed
    assert window.moves.count() == 200
    assert elapsed < 3.0


def test_429_restore_retry_button_waits_for_cooldown_and_uses_saved_token(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "429-retry.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    class Service:
        token = "saved-token"
        def __init__(self): self.restore_calls = 0
        def restore(self): self.restore_calls += 1
        def close(self): pass
    service = Service()
    window.lichess = service
    window.offline_mode = False
    window._lichess_connected = False
    timers = []
    monkeypatch.setattr("chess_coach.window.QTimer.singleShot",
                        lambda milliseconds, callback: timers.append((milliseconds, callback)))
    window._lichess_status("Ограничение Lichess (429): повторная попытка не раньше чем через 75 с")
    assert not window.connect_button.isEnabled()
    assert timers[0][0] == 75_000
    timers[0][1]()
    assert window.connect_button.isEnabled()
    assert window.connect_button.text() == "Повторить подключение"
    window.connect_lichess()
    assert service.restore_calls == 1
    assert not window.connect_button.isEnabled()


def test_online_controls_retry_after_rate_limit_cooldown(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "429-actions.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)

    class Client:
        rate_limit_remaining = 3

    class Service:
        client = Client()
        def close(self): pass

    window.lichess = Service()
    window.offline_mode = False
    window._lichess_connected = True
    timers = []
    monkeypatch.setattr("chess_coach.window.QTimer.singleShot",
                        lambda milliseconds, callback: timers.append((milliseconds, callback)))

    window._refresh_online_controls()
    assert timers[0][0] == 3000
    assert not window.ai_button.isEnabled()

    window.lichess.client.rate_limit_remaining = 0
    timers[0][1]()
    assert window.ai_button.isEnabled()
    assert window.home_archive_button.isEnabled()


def test_window_close_waits_for_workers_before_shutting_down_sqlite(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "close-workers.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)

    class Service:
        def __init__(self): self.close_calls = 0
        def close(self, timeout=1.0):
            self.close_calls += 1
            return self.close_calls > 1

    service = Service()
    shutdowns = []
    window.lichess = service
    monkeypatch.setattr(window.game_repository, "has_pending_writes", lambda: False)
    monkeypatch.setattr(window.game_repository, "shutdown", lambda: shutdowns.append(True))

    first = QCloseEvent()
    window.closeEvent(first)
    assert not first.isAccepted()
    assert not shutdowns
    assert window._close_retry_scheduled
    window._close_retry_timer.stop()

    final = QCloseEvent()
    window.closeEvent(final)
    assert final.isAccepted()
    assert service.close_calls == 2
    assert shutdowns == [True]


def test_window_close_cancels_analysis_and_waits_for_engine_quit_before_sqlite(
        qtbot, tmp_path, monkeypatch):
    from chess_coach.lichess import analysis as analysis_module

    window = MainWindow(QSettings(str(tmp_path / "close-analysis.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot(
        "close-analysis", "startpos", "", "white", "Friend", False, "-", "finished", "white")
    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_text("fake UCI engine", encoding="utf-8")

    started = threading.Event()
    result_stopped = threading.Event()
    engine_quit = threading.Event()
    shutdown_order = []
    engine_starts = []

    class FakeAnalysisResult:
        @property
        def info(self):
            return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                    "depth": 1, "nodes": 1, "pv": []}
        def __enter__(self): return self
        def __exit__(self, *_args): self.stop()
        def __iter__(self): return self
        def __next__(self):
            started.set()
            if not result_stopped.wait(3):
                raise RuntimeError("test analysis did not receive stop")
            raise StopIteration
        def stop(self): result_stopped.set()

    class FakeEngine:
        id = {"name": "FakeFish", "author": "Test"}
        def configure(self, _options): pass
        def analysis(self, _board, _limit): return FakeAnalysisResult()
        def quit(self):
            shutdown_order.append("engine.quit")
            engine_quit.set()

    engine = FakeEngine()
    def make_engine(_path):
        engine_starts.append(True)
        return engine
    monkeypatch.setattr(analysis_module.chess.engine.SimpleEngine, "popen_uci", make_engine)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args: QMessageBox.StandardButton.Yes)
    repo_shutdown = window.game_repository.shutdown
    def tracked_shutdown():
        assert engine_quit.is_set()
        shutdown_order.append("sqlite.shutdown")
        repo_shutdown()
    monkeypatch.setattr(window.game_repository, "shutdown", tracked_shutdown)

    window._start_game_analysis("close-analysis", str(engine_path))
    worker = window._analysis_thread
    assert worker is not None
    qtbot.waitUntil(started.is_set, timeout=2000)
    window._start_game_analysis("close-analysis", str(engine_path))
    assert len(engine_starts) == 1

    assert window.close() is False
    assert window._analysis_stop is not None and window._analysis_stop.is_set()
    assert not shutdown_order
    window._close_retry_timer.stop()

    qtbot.waitUntil(lambda: not worker.is_alive(), timeout=4000)
    assert result_stopped.is_set()
    assert engine_quit.is_set()
    assert window.game_repository.list_analyses("close-analysis") == []
    assert window.close() is True
    assert shutdown_order[:2] == ["engine.quit", "sqlite.shutdown"]


def test_connected_analysis_allows_only_official_lichess_ai_and_blocks_bot_accounts(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "analysis-fair-play.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True
    requests = []
    class ConnectedService:
        username = "AccountA"
        def check_active_games_for_analysis(self, request_id): requests.append(request_id)
        def close(self, timeout=1.0): return True
    window.lichess = ConnectedService()
    launches = []
    monkeypatch.setattr(window, "_launch_game_analysis", lambda *args, **kwargs: launches.append((args, kwargs)))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Ok)

    window._start_game_analysis("finished-game", "fake-stockfish")
    assert len(requests) == 1 and not launches
    window._analysis_safety_games_received(requests[-1], [
        {"gameId": "unopened-human-game", "source": "friend",
         "opponent": {"id": "friend", "username": "Friend"}}])
    assert not launches
    assert window._analysis_stop is None

    window._start_game_analysis("finished-game", "fake-stockfish")
    window._analysis_safety_games_received(requests[-1], [
        {"gameId": "ai-game", "source": "ai", "opponent": {
            "id": None, "username": "Stockfish level 1", "ai": 1}}])
    assert len(launches) == 1
    assert not window._active_game_blocks_engine({"source": "ai", "opponent": {
        "id": None, "username": "Stockfish level 1", "ai": 1}})
    window._analysis_finished("finished-game", {"error": "fixture complete"})

    window._start_game_analysis("finished-game", "fake-stockfish")
    window._analysis_safety_games_received(requests[-1], [{
        "gameId": "unopened-bot-account", "source": "friend",
        "opponent": {"id": "somebot", "username": "SomeBot", "title": "BOT"}}])
    assert len(launches) == 1
    assert window._analysis_stop is None

    window._start_game_analysis("finished-game", "fake-stockfish")
    window._analysis_safety_games_received(requests[-1], [{
        "gameId": "ambiguous-game", "source": "ai",
        "opponent": {"id": None, "username": "Stockfish", "ai": 9}}])
    assert len(launches) == 1
    assert window._analysis_stop is None

    window._start_game_analysis("finished-game", "fake-stockfish")
    window._analysis_safety_games_received(requests[-1], [{
        "gameId": f"ai-{index}", "source": "ai",
        "opponent": {"id": None, "username": "Stockfish level 1", "ai": 1},
    } for index in range(50)])
    assert len(launches) == 1
    assert window._analysis_stop is None


def test_open_active_game_carries_official_ai_identity_into_session(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "open-active-ai.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True

    class FakeService:
        def start_game(self, _game_id): pass
        def close(self, timeout=1.0): return True

    window.lichess = FakeService()
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    window.open_active_game({
        "gameId": "active-ai", "source": "ai", "color": "white",
        "opponent": {"id": None, "username": "Stockfish level 2", "ai": 2},
    })
    assert window.online_session is not None
    assert window.online_session.is_ai_opponent
    assert window.online_session.game_source == "ai"
    assert window.online_session.opponent_info["ai"] == 2
    assert window.close() is True


@pytest.mark.parametrize(
    ("game", "should_stop", "expected_ai"),
    [
        ({"source": "friend", "opponent": {
            "id": "somebot", "username": "SomeBot", "title": "BOT"}}, True, False),
        ({"source": "ai", "opponent": {
            "id": None, "username": "Stockfish level 1", "ai": 1}}, False, True),
    ],
)
def test_game_start_bot_account_stops_analysis_but_builtin_ai_does_not(
        qtbot, tmp_path, game, should_stop, expected_ai):
    window = MainWindow(QSettings(str(tmp_path / f"game-start-ai-{expected_ai}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    class FakeLichess:
        username = ""
        def close(self, timeout=1.0): return True
    window.lichess = FakeLichess()
    stop = threading.Event()
    window._analysis_stop = stop
    game_start = {"type": "gameStart", "game": {
        "gameId": "live-opponent", "color": "white", **game,
    }}
    window._lichess_event(game_start)
    session = window.online_sessions["live-opponent"]
    assert session.is_ai_opponent is expected_ai
    assert stop.is_set() is should_stop

    # Board gameState is mutable position data and cannot replace the
    # opponent identity established by gameStart.
    window._lichess_game_event("live-opponent", {
        "type": "gameFull", "initialFen": "startpos", "source": "unrecognized",
        "opponent": {"id": "somebot", "username": "SomeBot", "title": "BOT"},
        "state": {"moves": "", "status": "started"},
    })
    assert session.is_ai_opponent is expected_ai
    window._lichess_game_event("live-opponent", {
        "type": "gameState", "moves": "e2e4", "status": "started",
        "source": "friend", "opponent": {
            "id": "somebot", "username": "SomeBot", "title": "BOT"},
    })
    assert session.is_ai_opponent is expected_ai
    assert window.close() is True


def test_connected_analysis_fails_closed_on_snapshot_error_and_new_human_game_start(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "analysis-fail-closed.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True
    requests = []
    class ConnectedService:
        username = "AccountA"
        def check_active_games_for_analysis(self, request_id): requests.append(request_id)
        def close(self, timeout=1.0): return True
    window.lichess = ConnectedService()
    launches = []
    warnings = []
    monkeypatch.setattr(window, "_launch_game_analysis", lambda *args, **kwargs: launches.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    window._start_game_analysis("finished-game", "fake-stockfish")
    window._analysis_safety_error(requests[-1], "HTTP 429")
    assert not launches and warnings

    stop = threading.Event()
    window._analysis_stop = stop
    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "new-human-game", "color": "white",
        "opponent": {"username": "Friend"}}})
    assert stop.is_set()


def test_connected_analysis_cancel_stops_fake_analyzer_without_saving_complete_run(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "analysis-cancel-connected.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True

    class ConnectedService:
        username = "AccountA"
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=1.0): return True

    service = ConnectedService()
    window.lichess = service
    analyzer_started = threading.Event()
    analyzer_stopped = threading.Event()

    class FakeAnalyzer:
        def __init__(self, _repository): pass
        def analyze(self, _game_id, _engine_path, _options, **kwargs):
            stop_event = kwargs["stop_event"]
            analyzer_started.set()
            while not stop_event.wait(0.005):
                pass
            analyzer_stopped.set()
            return {"error": "Анализ отменён."}

    monkeypatch.setattr("chess_coach.window.CompletedGameAnalyzer", FakeAnalyzer)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Ok)
    window._start_game_analysis("cancelled-connected-game", "fake-stockfish")
    request_id = service.requests[-1]
    window._analysis_safety_games_received(request_id, [])
    qtbot.waitUntil(analyzer_started.is_set, timeout=3000)

    dialog = window._analysis_dialog
    assert dialog is not None
    assert window._analysis_safety_phase == "running"
    dialog.findChild(QPushButton).click()

    qtbot.waitUntil(analyzer_stopped.is_set, timeout=3000)
    qtbot.waitUntil(lambda: window._analysis_thread is not None
                    and not window._analysis_thread.is_alive(), timeout=3000)
    qtbot.waitUntil(lambda: window._analysis_stop is None, timeout=3000)
    assert window.game_repository.list_analyses("cancelled-connected-game") == []
    assert window._analysis_safety_phase is None
    assert window.close() is True


def test_preflight_cancel_ignores_late_empty_snapshot_and_http_429_fails_closed(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "analysis-stale-preflight.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True

    class ConnectedService:
        username = "AccountA"
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=1.0): return True

    service = ConnectedService()
    window.lichess = service
    launches = []
    monkeypatch.setattr(window, "_launch_game_analysis",
                        lambda *args, **kwargs: launches.append((args, kwargs)))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Ok)
    window._start_game_analysis("cancelled-before-engine", "fake-stockfish")
    request_id = service.requests[-1]
    dialog = window._analysis_dialog
    assert dialog is not None
    dialog.findChild(QPushButton).click()
    window._analysis_safety_games_received(request_id, [])
    assert not launches
    assert window._analysis_stop is None
    assert window._analysis_safety_request_id is None

    window._start_game_analysis("rate-limited-game", "fake-stockfish")
    rate_limited_request = service.requests[-1]
    window._analysis_safety_error(rate_limited_request, "HTTP 429: retry after 90s")
    assert not launches
    assert window._analysis_stop is None
    assert window._analysis_safety_phase is None
    assert window.close() is True


@pytest.mark.parametrize("transition", ["disconnect", "account-switch", "reconnect-start"])
def test_late_safety_snapshot_cannot_authorize_old_account_session(
        qtbot, tmp_path, monkeypatch, transition):
    window = MainWindow(QSettings(str(tmp_path / f"analysis-session-{transition}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._lichess_connected = True

    class ConnectedService:
        username = "AccountA"
        token = "saved-fake-token"
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def disconnect_account(self): pass
        def restore(self): self.restored = True
        def close(self, timeout=1.0): return True

    service = ConnectedService()
    window.lichess = service
    launches = []
    monkeypatch.setattr(window, "_launch_game_analysis",
                        lambda *args, **kwargs: launches.append((args, kwargs)))
    monkeypatch.setattr(window, "_resume_pending_final_syncs", lambda: None)
    monkeypatch.setattr(window, "_refresh_online_controls", lambda: None)

    window._start_game_analysis("old-account-game", "fake-stockfish")
    old_request = service.requests[-1]
    if transition == "disconnect":
        window.disconnect_lichess()
    elif transition == "account-switch":
        service.username = "AccountB"
        window._lichess_authenticated("AccountB")
    else:
        window._lichess_connected = False
        window.offline_mode = False
        window.manual_connect_mode = True
        window.connect_lichess()
        assert service.restored

    window._analysis_safety_games_received(old_request, [])
    assert not launches
    assert window._analysis_safety_request_id is None
    assert window._analysis_stop is None

    if transition == "account-switch":
        window._start_game_analysis("new-account-game", "fake-stockfish")
        new_request = service.requests[-1]
        assert new_request != old_request
        window._analysis_safety_games_received(new_request, [])
        assert len(launches) == 1
        assert launches[0][0][0] == "new-account-game"
    assert window.close() is True


def test_analysis_shutdown_timeout_enters_read_only_then_repeated_close_finishes(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "close-analysis-timeout.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    stop = threading.Event()
    clock = [100.0]

    class AliveThread:
        def __init__(self): self.alive = True
        def is_alive(self): return self.alive

    worker = AliveThread()
    window._analysis_stop = stop
    window._analysis_thread = worker
    monkeypatch.setattr("chess_coach.window.time.monotonic", lambda: clock[0])
    window._close_retry_timer.stop()

    class Service:
        def __init__(self): self.close_calls = 0
        def close(self, timeout=1.0):
            self.close_calls += 1
            # Simulate the service already being in its graceful close path.
            return self.close_calls >= 3

    service = Service()
    window.lichess = service
    shutdowns = []
    repository_shutdown = window.game_repository.shutdown
    def tracked_shutdown():
        shutdowns.append("sqlite.shutdown")
        repository_shutdown()
    monkeypatch.setattr(window.game_repository, "shutdown", tracked_shutdown)

    assert window.close() is False
    assert stop.is_set()
    assert not shutdowns
    window._close_retry_timer.stop()

    clock[0] += 11.0
    assert window.close() is False
    assert not window._close_retry_scheduled
    assert window.isEnabled()
    assert window._shutdown_read_only
    assert not window.centralWidget().isEnabled()
    assert not window.settings_button.isEnabled()
    assert "действия отключены" in window.home_status.text()
    assert "ожидание завершения Stockfish" in window.windowTitle()
    assert worker.is_alive()  # No forced process kill or hidden worker detachment.

    # The analyzer later stops. A new close attempt completes service and DB shutdown.
    worker.alive = False
    assert window.close() is True
    assert service.close_calls == 3
    assert shutdowns == ["sqlite.shutdown"]


def test_main_window_waits_for_engine_probe_process_before_destroying_qprocess_parent(
        qtbot, tmp_path):
    import chess_coach.window as window_module

    window = MainWindow(QSettings(str(tmp_path / "close-engine-probe.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    class DelayedProcess:
        running = True
        def state(self):
            return (window_module.QProcess.ProcessState.Running if self.running else
                    window_module.QProcess.ProcessState.NotRunning)
    process = DelayedProcess()
    window._track_engine_probe(process)

    assert window.close() is False
    assert "дочерние процессы" in window.home_status.text()
    window._close_retry_timer.stop()
    process.running = False
    window._untrack_engine_probe(process)
    assert window.close() is True


def test_background_refresh_requires_authenticated_connected_account(qtbot, tmp_path):
    from types import SimpleNamespace
    window = MainWindow(QSettings(str(tmp_path / "auto.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    calls = []
    window._run_background_sync()
    window.offline_mode = False
    window.lichess = SimpleNamespace(
        username="Sample",
        sync_archive=lambda **kwargs: calls.append(("archive", kwargs)),
        sync_puzzle_activity_background=lambda repo: calls.append(("puzzles", repo)))
    window._lichess_connected = True
    window._run_background_sync()
    assert calls[0] == ("archive", {"background": True})
    assert calls[1][0] == "puzzles"
    window._lichess_connected = False
    window._run_background_sync()
    assert len(calls) == 2
    window.lichess = None
    window.offline_mode = True
