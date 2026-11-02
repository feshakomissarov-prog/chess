import chess
import chess.engine
import threading
import time
import pytest
from pathlib import Path
from PySide6.QtCore import QPoint, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel, QInputDialog,
                               QMessageBox, QPushButton)

from chess_coach.board_view import BoardView
from chess_coach.window import (ActiveGamesDialog, BatchAnalysisDialog, EvaluationGraph, MainWindow,
                                OnlineGameDialog,
                                SettingsDialog, StatisticsDialog, StatisticsGameDialog,
                                _archive_row_presentation, _batch_safety_from_games, _clock_filter_sort_key,
                                _puzzle_attempt_summary)
from chess_coach.lichess.session import LichessGameSession
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.workspace_state import geometry_intersects_available, reposition_visible_frame


@pytest.fixture(autouse=True)
def avoid_background_auth_restore(monkeypatch, tmp_path):
    # Window tests exercise presentation and event handling, not stored credentials.
    monkeypatch.setattr("chess_coach.lichess.service.LichessService.restore", lambda self: None)
    # Never let GUI tests initialize or migrate the owner's persistent SQLite.
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda _location: str(tmp_path / "ChessCoach"))


def select_online(window, session):
    window.online_sessions[session.game_id] = session
    window.online_session = session
    window._selected_live_game_id = session.game_id
    window._screen_mode = "online"
    window.screen_stack.setCurrentWidget(window.game_page)


@pytest.mark.parametrize(("color", "expected_flipped"), [
    (chess.WHITE, False), (chess.BLACK, True),
])
def test_online_board_orientation_follows_player_color_not_turn_or_global_setting(
    qtbot, tmp_path, color, expected_flipped
):
    settings = QSettings(str(tmp_path / f"orientation-{color}.ini"), QSettings.Format.IniFormat)
    settings.setValue("board_flipped", not expected_flipped)
    window = MainWindow(settings, offline_mode=True)
    qtbot.addWidget(window)
    session = LichessGameSession(f"orientation-{color}", window.game_repository)
    session.color = color
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    window.refresh()
    assert window.board._flipped is expected_flipped
    for moves in ("e2e4", "e2e4 e7e5"):
        window._lichess_game_event(session.game_id, {
            "type": "gameState", "moves": moves, "status": "started"})
        assert window.board._flipped is expected_flipped
    window.flip_board()
    manual = not expected_flipped
    assert window.board._flipped is manual
    window.refresh()
    assert window.board._flipped is manual


def test_saved_game_orientation_uses_selected_account_color(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "saved-orientation.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot("saved-black", "startpos", "e2e4", "white",
        "Opponent", False, "-", "mate", "white", white_player="Owner", black_player="Opponent")
    game = window.game_repository.get_game("saved-black")
    game["account_color"] = "black"
    game["account_opponent"] = "Opponent"
    assert window._open_saved_game(game)
    assert window.online_session.color == chess.BLACK
    assert window.board._flipped
    assert window.screen_title.isHidden()
    assert window.review_workspace.title.text().startswith("Owner — Opponent · 1–0 ·")
    window.show_home()
    assert not window.screen_title.isHidden()


def test_workspace_review_resumes_same_revision_ply_and_inspector_tab(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot(
        "resume-review", "startpos", "e2e4 e7e5 g1f3", "white", "Friend", False,
        "5+3", "mate", "white", white_player="Owner", black_player="Friend")
    game = window.game_repository.get_game("resume-review")
    assert window._open_saved_game(game)
    window.review_workspace.select_ply(2)
    window.review_workspace.tabs.setCurrentIndex(1)
    window._active_route = "games"
    window._persist_workspace_state()
    assert settings.value("workspace/review_viewed_ply", type=int) == 2
    assert settings.value("workspace/review_inspector_tab", type=int) == 1
    revision = settings.value("workspace/review_line_revision", type=int)
    line_hash = settings.value("workspace/review_line_hash", type=str)
    assert revision == window.game_repository.current_line("resume-review")["line_revision"]
    assert line_hash == window.game_repository.current_line("resume-review")["line_hash"]

    window.close()
    resumed = MainWindow(QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat),
                         manual_connect_mode=True)
    qtbot.addWidget(resumed)
    assert resumed.resume_saved_review(show_message=False)
    assert resumed.review_workspace.selected_ply == 2
    assert resumed.review_workspace.tabs.currentIndex() == 1
    assert resumed.screen_stack.currentWidget() is resumed.review_workspace


def test_workspace_changed_line_falls_back_to_start_and_restores_visible_geometry(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "workspace-changed.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot(
        "changed-review", "startpos", "e2e4 e7e5", "white", "Friend", False,
        "5+3", "mate", "white", white_player="Owner", black_player="Friend")
    game = window.game_repository.get_game("changed-review")
    assert window._open_saved_game(game)
    window.review_workspace.select_ply(2)
    window._active_route = "games"
    window._persist_workspace_state()
    window.close()

    repository = GameRepository(Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)) / "games.sqlite3")
    repository.save_snapshot(
        "changed-review", "startpos", "d2d4 d7d5", "white", "Friend", False,
        "5+3", "mate", "white", white_player="Owner", black_player="Friend")
    repository.shutdown()
    resumed = MainWindow(QSettings(str(tmp_path / "workspace-changed.ini"),
                                   QSettings.Format.IniFormat), manual_connect_mode=True)
    qtbot.addWidget(resumed)
    assert resumed.resume_saved_review(show_message=False)
    assert resumed.review_workspace.selected_ply == 0
    assert "изменилась" in resumed.statusBar().currentMessage()


def test_delayed_workspace_restore_never_steals_a_page_already_opened_by_user(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "workspace-no-steal.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/last_route", "games")
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)
    window.start_local_game()
    qtbot.wait(120)
    assert window.screen_stack.currentWidget() is window.game_page
    assert window._screen_mode == "local"


def test_offscreen_saved_window_geometry_is_repositioned(qtbot):
    from PySide6.QtCore import QRect
    visible = QRect(0, 0, 1280, 720)
    offscreen = QRect(-2200, 150, 900, 700)
    assert not geometry_intersects_available(offscreen, [visible])
    corrected = reposition_visible_frame(offscreen, [visible])
    assert geometry_intersects_available(corrected, [visible])
    assert corrected.x() >= visible.x() and corrected.y() >= visible.y()
    oversized = reposition_visible_frame(QRect(0, 0, 1360, 860), [visible])
    assert oversized == visible


def test_statistics_dialog_restores_and_exports_small_filter_state(qtbot, tmp_path):
    repository = GameRepository(tmp_path / "filters.sqlite3")
    saved = {"period_days": 30, "side": "black", "result": "draw",
             "game_source": "local", "analysis_source": "stockfish",
             "opponent": "Rival", "opening": "C20", "advanced": True, "tab": 1}
    dialog = StatisticsDialog(repository, saved_filters=saved)
    qtbot.addWidget(dialog)
    dialog._refresh_timer.stop()
    assert dialog.period.currentData() == 30
    assert dialog.side.currentData() == "black"
    assert dialog.result_filter.currentData() == "draw"
    assert dialog.game_source.currentData() == "local"
    assert dialog.analysis_source.currentData() == "stockfish"
    assert dialog.opponent.text() == "Rival"
    assert dialog.opening.text() == "C20"
    assert dialog.advanced_filters_group.isChecked()
    state = dialog.persisted_filter_state()
    assert state["period_days"] == 30 and state["tab"] == 1
    assert state["opponent"] == "Rival" and state["opening"] == "C20"
    repository.shutdown()


def test_statistics_empty_states_and_account_action_are_truthful(qtbot, tmp_path):
    repository = GameRepository(tmp_path / "stats-empty.sqlite3")
    dialog = StatisticsDialog(repository)
    qtbot.addWidget(dialog)
    dialog._refresh_timer.stop()
    assert "sqlite" in dialog.overview_description.text().casefold()
    assert "локальной классификации" in dialog.openings_description.text().casefold()
    assert "активным фильтрам" in dialog.games_description.text().casefold()
    assert "подтверждённые события" in dialog.critical_description.text().casefold()
    assert not dialog.critical_select_account.isEnabled()
    assert "аккаунт" in dialog.critical_summary.text().casefold()
    assert "локального анализа" in dialog.critical_summary.text().casefold()
    repository.shutdown()


def test_archive_row_presentation_localizes_date_status_and_source():
    text, source = _archive_row_presentation({
        "started_at": "2026-11-02T12:30:00Z", "status": "mate",
        "source": "lichess_archive", "opponent_type": "official_ai",
        "account_opponent": "Lichess AI level 3", "white_player": "Me",
        "black_player": "Lichess AI level 3", "variant": "standard",
    })
    assert "2026" in text and "Мат" in text and "Lichess AI" in text
    assert source == "Lichess AI"
    assert "mate" not in text


def test_completed_analysis_uses_unified_review_route_and_inline_notice(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "analysis-notice.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot(
        "analysis-complete", "startpos", "e2e4 e7e5", "white", "Friend", False,
        "5+3", "mate", "white", white_player="Owner", black_player="Friend")
    window._analysis_finished("analysis-complete", {
        "assessments": [{"ply": 0, "white_cp": 0}], "source": "stockfish"})
    assert window.screen_stack.currentWidget() is window.review_workspace
    assert "общий разбор" in window.statusBar().currentMessage()


def test_lichess_failure_is_inline_and_does_not_open_modal_warning(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "inline-error.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: pytest.fail("modal warning opened"))
    window._lichess_error("Ошибка HTTP 429")
    assert window.statusBar().currentMessage() == "Ошибка HTTP 429"


def test_free_board_orientation_is_separate_from_online_auto_orientation(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "free-orientation.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.settings.setValue("board_flipped", False)
    window.start_local_game()
    assert not window.board._flipped
    window.flip_board()
    assert window.board._flipped
    assert window.settings.value("free_board_flipped", type=bool) is True
    assert window.settings.value("board_flipped", type=bool) is False
    window._local_board_mode = "premove_training"
    window.controller.player_color = chess.BLACK
    assert window._effective_board_orientation()


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


def test_m46_shell_has_stable_routes_and_collapses_for_board_screens(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "m46-shell.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    assert window.sidebar.width() == (60 if window.width() < 1160 else 212)
    assert set(window._nav_buttons) == {"home", "play", "games", "analytics", "puzzles"}
    assert window._nav_buttons["home"].property("active") is True
    window.start_local_game()
    assert window.sidebar.width() == 60
    assert window._nav_buttons["play"].property("active") is True

    window._set_sidebar_collapsed(False, explicit=True)
    assert window.sidebar.width() == 212
    assert window.screen_stack.currentWidget() is window.game_page
    assert window.sidebar.width() == 212  # explicit session override is preserved
    window._set_sidebar_collapsed(True, explicit=True)
    assert window.sidebar.width() == 60


def test_m46_shell_uses_expanded_sidebar_at_wide_home_and_compact_board(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "m46-responsive.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.resize(1280, 800)
    window._sidebar_explicit_override = False
    window._shell_screen_changed(window.screen_stack.currentIndex())
    assert window.sidebar.width() == 212
    window.start_local_game()
    assert window.sidebar.width() == 60


@pytest.mark.parametrize("size", [400, 480, 560, 720, -1])
def test_m46_board_size_presets_are_available_and_persisted(qtbot, tmp_path, size):
    settings = QSettings(str(tmp_path / f"board-size-{size}.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings, offline_mode=True)
    qtbot.addWidget(window)
    assert [window.board_size_combo.itemData(i) for i in range(window.board_size_combo.count())] == [
        400, 480, 560, 720, -1]
    window.board_size_combo.setCurrentIndex(window.board_size_combo.findData(size))
    assert window.settings.value("board_target_size", type=int) == size
    if size > 0:
        assert window.board.width() <= size
        assert window.board.height() <= size
    else:
        assert window.settings.value("board_target_size", type=int) == -1
    assert window.board.width() >= 320


def test_clock_filter_sort_uses_raw_control_time_and_keeps_daily_last():
    values = ["daily:86400", "15+10", "300+3", "3+2", "bad"]
    assert sorted(values, key=_clock_filter_sort_key) == [
        "3+2", "15+10", "300+3", "daily:86400", "bad"]


@pytest.mark.parametrize(("width", "height", "minimum"), [(1024, 700, 320), (1920, 1080, 800)])
def test_board_fit_uses_available_window_area_and_focus_hides_inspector(
        qtbot, tmp_path, width, height, minimum):
    window = MainWindow(QSettings(str(tmp_path / f"board-fit-{width}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.resize(width, height)
    window.board_size_combo.setCurrentIndex(window.board_size_combo.findData(-1))
    window._apply_board_size()
    assert window.board.width() >= minimum
    if width == 1920:
        before = window.board.width()
        window.focus_mode_button.setChecked(True)
        assert window.game_splitter.widget(1).isHidden()
        assert window.board.width() >= before
    window.close()


@pytest.mark.parametrize(("games", "allowed"), [
    ([{"source": "ai", "opponent": {"ai": 3, "id": None}}], True),
    ([{"source": "friend", "opponent": {"id": "human"}}], False),
    ([{"source": "ai", "opponent": {"ai": 9, "id": None}}], False),
])
def test_review_training_safety_requires_complete_ai_only_snapshot(
        qtbot, tmp_path, monkeypatch, games, allowed):
    window = MainWindow(QSettings(str(tmp_path / "review-safety.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot("review-id", "startpos", "e2e4 e7e5", "white",
        "Opponent", False, "5+0", "mate", "white")
    assert window._open_saved_game(window.game_repository.get_game("review-id"))
    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        client = FakeClient()
        def check_active_games_for_analysis(self, request_id):
            self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    window._request_review_training_safety("review-id")
    request_id = window.lichess.request_id
    window._analysis_safety_games_received(request_id, games)
    assert window.review_workspace.training_allowed is allowed


def test_review_training_safety_rejects_stale_response_after_auth_generation_change(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-safety-stale.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot("review-id", "startpos", "e2e4 e7e5", "white",
        "Opponent", False, "5+0", "mate", "white")
    assert window._open_saved_game(window.game_repository.get_game("review-id"))
    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        client = FakeClient()
        def check_active_games_for_analysis(self, request_id):
            self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    window._request_review_training_safety("review-id")
    request_id = window.lichess.request_id
    window._invalidate_analysis_preflight()
    window._analysis_safety_games_received(
        request_id, [{"source": "ai", "opponent": {"ai": 3, "id": None}}])
    assert not window.review_workspace.training_allowed
    assert "состояние подключения" in window.review_workspace.training_block_reason


def _saved_review_with_engine_facts(window, game_id):
    window.game_repository.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", "white",
        "Opponent", False, "5+0", "mate", "white")
    line = window.game_repository.current_line(game_id)
    window.game_repository.save_analysis(game_id, line_revision=line["line_revision"],
        line_hash=line["line_hash"], source="stockfish", engine_name="FakeFish",
        engine_version="test", parameters={"nodes": 100000}, status="complete",
        assessments=[
            {"ply": 0, "white_cp": 20, "depth": 20, "nodes": 100000, "pv": ["e2e4"]},
            {"ply": 1, "white_cp": -80, "depth": 20, "nodes": 100000, "pv": ["c7c5"]},
            {"ply": 2, "white_cp": 80, "depth": 20, "nodes": 100000, "pv": ["g1f3"]},
            {"ply": 3, "white_cp": 20, "depth": 20, "nodes": 100000, "pv": ["b8c6"]},
        ],
        classifications=[{"ply": 2, "mover": "black", "eval_loss_cp": 80,
                          "label": "blunder", "algorithm_version": "fixture-v1"}])
    return window.game_repository.get_game(game_id)


def test_human_game_start_revokes_safety_and_masks_saved_review_analysis(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-safety-revoke.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "safe-then-human")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeService:
        username = "Owner"
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("safe-then-human")
    window._review_safety_games_received(window.lichess.request_id, [])
    assert window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values()

    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "new-human", "source": "friend", "color": "white",
        "opponent": {"id": "friend", "username": "Friend"}}})

    assert not window.review_workspace.training_allowed
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values() == []
    assert "blunder" not in window.review_workspace.moves_list.item(2).text().lower()
    assert "stockfish" not in window.review_workspace.subtitle.text().lower()
    assert "cp" not in window.review_workspace.metrics_label.text().lower()
    assert "· 1" not in window.review_workspace.training_button.text()
    window.review_workspace.select_ply(2)
    assert window.review_workspace.move_detail.text() == window.review_workspace.training_block_reason


def test_human_game_start_stops_active_review_exercise_before_hint_or_check(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-training-stop.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "training-then-human")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeService:
        username = "Owner"
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("training-then-human")
    window._review_safety_games_received(window.lichess.request_id, [])
    window.review_workspace.start_training()
    assert window.review_workspace.training_mode
    window.review_workspace._training_move_intent(chess.Move.from_uci("e7e6"))

    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "new-human-active-exercise", "source": "friend", "color": "white",
        "opponent": {"id": "friend", "username": "Friend"}}})
    previous_status = window.review_workspace.training_status.text()
    window.review_workspace.show_hint()
    window.review_workspace.check_training_move()

    assert not window.review_workspace.training_mode
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.training_proven == {}
    assert window.review_workspace.training_status.text() == previous_status
    assert "e7e5" not in window.review_workspace.training_status.text()


def test_late_safe_reply_for_game_a_cannot_authorize_open_game_b(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-switch-game.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game_a = _saved_review_with_engine_facts(window, "review-a")
    game_b = _saved_review_with_engine_facts(window, "review-b")
    assert window._open_saved_game(game_a)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeService:
        username = "Owner"
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("review-a")
    request_a = window.lichess.request_id
    assert window._open_saved_game(game_b)

    assert not window._review_safety_games_received(
        request_a, [{"source": "ai", "opponent": {"ai": 3, "id": None}}])
    assert window.review_workspace.review["game"]["game_id"] == "review-b"
    assert not window.review_workspace.training_allowed
    assert not window.review_workspace.evaluation_allowed


def test_disconnect_revokes_a_previously_successful_review_safety_lease(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-disconnect-revoke.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "review-disconnect")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeService:
        username = "Owner"
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def disconnect_account(self): pass
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("review-disconnect")
    window._review_safety_games_received(window.lichess.request_id, [])
    assert window.review_workspace.evaluation_allowed

    window.disconnect_lichess()

    assert not window.review_workspace.evaluation_allowed
    assert not window.review_workspace.training_allowed
    assert window.review_workspace.graph._values() == []


@pytest.mark.parametrize("status", [
    "Не подключено",
    "Ограничение Lichess (429): повторите через 60 с",
    "Переподключение…", "Ошибка подключения", "Ошибка проверки аккаунта",
    "Проверка аккаунта отклонена Lichess (HTTP 403)",
    "Требуется повторное подключение Lichess (HTTP 401)",
])
def test_unsafe_lichess_status_revokes_review_lease_and_recovery_requires_new_preflight(
        qtbot, tmp_path, monkeypatch, status):
    window = MainWindow(QSettings(str(tmp_path / f"review-status-{status[:8]}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "status-revoke")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        client = FakeClient()
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("status-revoke")
    window._review_safety_games_received(window.lichess.request_id, [])
    assert window.review_workspace.evaluation_allowed
    assert "Сохранённое продолжение:" in window.review_workspace.event_detail.text()
    window.review_workspace.start_training()
    assert window.review_workspace.training_mode

    window._lichess_status(status)

    review = window.review_workspace
    assert not review.training_allowed
    assert not review.evaluation_allowed
    assert not review.training_mode
    assert review.graph._values() == []
    assert "Сохранённое продолжение:" not in review.event_detail.text()
    previous = review.training_status.text()
    review.show_hint()
    assert review.training_status.text() == previous

    # Transport/auth recovery alone must not restore the old lease.
    window._lichess_connected = True
    window._lichess_status("Подключено: Owner")
    assert not review.training_allowed
    assert not review.evaluation_allowed
    assert review.graph._values() == []

    # A fresh, explicit complete safety check can grant a new lease.
    window._request_review_training_safety("status-revoke")
    window._review_safety_games_received(window.lichess.request_id, [])
    assert review.training_allowed
    assert review.evaluation_allowed


def test_oauth_revoke_revokes_review_safety_before_service_request_and_never_revokes_pat(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "oauth-revoke-safety.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "oauth-revoke-safety")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        client = FakeClient()
        pat_revoke_calls = 0
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def revoke_oauth_credential(self):
            self.state_when_started = (
                window.review_workspace.training_allowed,
                window.review_workspace.evaluation_allowed,
                window.review_workspace.graph._values(),
                window._lichess_connected,
            )
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("oauth-revoke-safety")
    window._review_safety_games_received(window.lichess.request_id, [])
    assert window.review_workspace.evaluation_allowed
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window._revoke_oauth_lichess()

    assert window.lichess.state_when_started == (False, False, [], False)
    assert window.lichess.pat_revoke_calls == 0
    # Neither success nor failure from the async revoke grants safety back.
    window._lichess_status("Не удалось отозвать OAuth-токен: fake failure")
    window._lichess_status("OAuth-токен отозван и удалён локально.")
    assert not window.review_workspace.training_allowed
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values() == []


def test_late_review_safety_reply_after_line_revision_change_is_rejected(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "review-line-stale.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "review-line-stale")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        client = FakeClient()
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    window.lichess = FakeService()
    window._request_review_training_safety("review-line-stale")
    request_id = window.lichess.request_id
    window.game_repository.save_snapshot("review-line-stale", "startpos", "e2e4 e7e5",
        "white", "Opponent", False, "5+0", "mate", "white")

    window._review_safety_games_received(
        request_id, [{"source": "ai", "opponent": {"ai": 3, "id": None}}])

    assert not window.review_workspace.training_allowed
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values() == []


def test_transient_event_stream_recovery_restores_availability_not_review_lease(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "event-stream-recovery.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "event-stream-recovery")
    assert window._open_saved_game(game)
    window.offline_mode = False

    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        auth_generation = 77
        client = FakeClient()
        def is_auth_generation_current(self, generation):
            return generation == self.auth_generation
        def check_active_games_for_analysis(self, request_id): self.request_id = request_id
        def close(self, timeout=1.0): return True
    service = FakeService()
    window.lichess = service
    window._lichess_connected = True
    window._request_review_training_safety("event-stream-recovery")
    window._review_safety_games_received(service.request_id, [])
    assert window.review_workspace.evaluation_allowed
    window.review_workspace.start_training()
    assert window.review_workspace.training_mode

    window._lichess_session_status(service.auth_generation, "Переподключение…")

    assert not window._lichess_connected
    assert not window._online_actions_allowed()
    assert not window.review_workspace.training_mode
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values() == []

    # A stale generation cannot bring the account UI back.
    window._lichess_session_status(service.auth_generation - 1,
                                   "Поток восстановлен: Owner")
    assert not window._lichess_connected
    assert not window._online_actions_allowed()

    # The current generation may restore transport availability, but not the
    # safety lease that was revoked when the stream was lost.
    window._lichess_session_status(service.auth_generation, "Поток восстановлен: Owner")
    assert window._lichess_connected
    assert window._online_actions_allowed()
    assert not window.review_workspace.training_allowed
    assert not window.review_workspace.evaluation_allowed
    assert window.review_workspace.graph._values() == []

    window._request_review_training_safety("event-stream-recovery")
    window._review_safety_games_received(service.request_id, [])
    assert window.review_workspace.evaluation_allowed


@pytest.mark.parametrize("status", [
    "Потеряно соединение с партией; синхронизация…",
    "Финальная синхронизация партии game-1 ожидает повторного подключения.",
])
def test_single_game_stream_retry_does_not_clear_authenticated_account(
        qtbot, tmp_path, status):
    window = MainWindow(QSettings(str(tmp_path / f"game-stream-{status[:7]}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "game-stream-retry")
    assert window._open_saved_game(game)
    window.offline_mode = False
    window._lichess_connected = True

    window._lichess_status(status)

    assert window._lichess_connected
    assert not window.review_workspace.evaluation_allowed
    assert not window.review_workspace.training_allowed
    assert window.review_workspace.graph._values() == []


def test_stream_429_retry_after_never_reenables_actions_before_stream_ready(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "event-stream-429-ready.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    game = _saved_review_with_engine_facts(window, "event-stream-429-ready")
    assert window._open_saved_game(game)
    window.offline_mode = False

    class FakeClient:
        rate_limit_remaining = 0
    class FakeService:
        username = "Owner"
        token = "fake-token"
        auth_generation = 82
        client = FakeClient()
        def __init__(self): self.safety_requests = []
        def is_auth_generation_current(self, generation):
            return generation == self.auth_generation
        def check_active_games_for_analysis(self, request_id):
            self.safety_requests.append(request_id)
        def close(self, timeout=1.0): return True
    service = FakeService()
    window.lichess = service
    window._lichess_connected = True
    window._request_review_training_safety("event-stream-429-ready")
    window._review_safety_games_received(service.safety_requests[-1], [])
    assert window.review_workspace.evaluation_allowed

    window._lichess_status(
        "Ограничение Lichess (429): переподключение через 60 с")
    service.client.rate_limit_remaining = 0  # Retry-After elapsed.
    window._enable_restore_retry()
    window._request_review_training_safety("event-stream-429-ready")

    assert not window._lichess_connected
    assert window._event_stream_recovery_pending
    assert not window._online_actions_allowed()
    assert not window.connect_button.isEnabled()
    assert len(service.safety_requests) == 1
    assert not window.review_workspace.evaluation_allowed

    window._lichess_session_status(service.auth_generation,
                                   "Поток восстановлен: Owner")
    assert window._lichess_connected
    assert not window._event_stream_recovery_pending
    assert window._online_actions_allowed()
    assert not window.review_workspace.evaluation_allowed
    assert len(service.safety_requests) == 1

    window._request_review_training_safety("event-stream-429-ready")
    assert len(service.safety_requests) == 2
    window._review_safety_games_received(service.safety_requests[-1], [])
    assert window.review_workspace.evaluation_allowed


def test_online_move_sound_waits_for_authoritative_ack_and_deduplicates_replays(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "sound-ack.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    class FakeLichess:
        def __init__(self): self.sent = []
        def send_move(self, *args): self.sent.append(args)
        def close(self, timeout=1.0): return True
    class FakeSound:
        def __init__(self): self.moves = []
        def play_move(self, *args, **kwargs): self.moves.append((args, kwargs))
        def play_game_end(self, *args): return False
    window.lichess = FakeLichess()
    window.sound_player = FakeSound()
    session = LichessGameSession("sound-game", window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    move = chess.Move.from_uci("e2e4")
    window.on_move_intent(move)
    assert len(window.lichess.sent) == 1
    assert window.sound_player.moves == []  # pending move is not yet confirmed
    window._lichess_move_failed(session.game_id, "e2e4", session.pending_request_id, 400)
    assert window.sound_player.moves == []

    window.on_move_intent(move)
    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4", "status": "started"})
    assert len(window.sound_player.moves) == 1
    window._lichess_game_event(session.game_id, {
        "type": "gameState", "moves": "e2e4", "status": "started"})
    assert len(window.sound_player.moves) == 1


def test_browsing_move_history_does_not_emit_sounds(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "history-sound.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    class FakeSound:
        def __init__(self): self.moves = []
        def play_move(self, *args, **kwargs): self.moves.append((args, kwargs))
    window.sound_player = FakeSound()
    window._screen_mode = "local"
    window._local_board_mode = "free"
    window.controller.board.push_uci("e2e4")
    window.controller.board.push_uci("e7e5")
    window.refresh()
    window.navigate_to(1)
    window.navigate_by(1)
    window.navigate_to(0)
    assert window.sound_player.moves == []


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


def test_batch_analysis_controls_are_explicit_and_resume_after_pause(qtbot, tmp_path):
    dialog = BatchAnalysisDialog()
    qtbot.addWidget(dialog)
    started = []
    paused = []
    dialog.run_requested.connect(lambda *args: started.append(args))
    dialog.pause_requested.connect(lambda: paused.append(True))
    assert not dialog.pause_button.isEnabled()
    assert not dialog.resume_button.isEnabled()
    engine = tmp_path / "stockfish.exe"
    engine.write_bytes(b"verified synthetic executable")
    dialog.set_readiness(str(engine), True, True)
    assert dialog.start_button.isEnabled() and not dialog.resume_button.isEnabled()
    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    assert started == [(2, 20_000, 64, False, False, False)]
    dialog.set_running(True)
    assert dialog.close() is False
    qtbot.mouseClick(dialog.pause_button, Qt.MouseButton.LeftButton)
    assert paused == [True]
    dialog.worker_finished.emit(True, "Пауза")
    assert dialog.resume_button.isEnabled()
    dialog.selective_review.setChecked(True)
    qtbot.mouseClick(dialog.resume_button, Qt.MouseButton.LeftButton)
    assert started[-1] == (2, 20_000, 64, True, False, True)
    dialog.worker_finished.emit(False, "Завершено")
    assert not dialog.pause_button.isEnabled()
    assert dialog.start_button.isEnabled() and not dialog.resume_button.isEnabled()
    dialog.set_readiness("", False, False)
    assert not dialog.start_button.isEnabled() and not dialog.resume_button.isEnabled()


def test_batch_dialog_readiness_tracks_account_429_and_removed_engine(qtbot, tmp_path):
    engine = tmp_path / "verified-stockfish.exe"
    engine.write_bytes(b"synthetic verified engine")
    settings = QSettings(str(tmp_path / "batch-live-readiness.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.show_batch_analysis()
    dialog = window._batch_dialog
    assert dialog is not None
    assert not dialog.start_button.isEnabled()

    class Client:
        rate_limit_remaining = 0
    class Service:
        username = "ReadyUser"
        client = Client()
        def close(self, timeout=None): return True
    window.lichess = Service()
    window._lichess_connected = True
    window._refresh_batch_readiness()
    assert dialog.start_button.isEnabled()
    live_human = LichessGameSession("active-human", window.game_repository)
    live_human.status = "playing"
    window.online_sessions[live_human.game_id] = live_human
    window._refresh_batch_readiness()
    assert not dialog.start_button.isEnabled() and "активная партия с человеком" in dialog.account_status.text()
    window.online_sessions.clear()
    window._refresh_batch_readiness()
    assert dialog.start_button.isEnabled()
    dialog.set_running(True)
    window.lichess.client.rate_limit_remaining = 60
    window._lichess_status("Ограничение Lichess (429): повтор через 60 с")
    assert not dialog.start_button.isEnabled() and not dialog.resume_button.isEnabled()
    dialog.worker_finished.emit(True, "Ожидание Retry-After")
    assert not dialog.resume_button.isEnabled()

    window.lichess.client.rate_limit_remaining = 0
    window._refresh_batch_readiness()
    assert dialog.resume_button.isEnabled()
    engine.unlink()
    window._refresh_batch_readiness()
    assert not dialog.start_button.isEnabled() and not dialog.resume_button.isEnabled()
    dialog.reject()
    window.close()


def test_statistics_unknown_own_coverage_and_real_collapsed_advanced_filters(qtbot, tmp_path):
    repo = GameRepository(tmp_path / "stats-unknown.sqlite3")
    dialog = StatisticsDialog(repo)
    qtbot.addWidget(dialog)

    assert dialog.advanced_filters_content.isHidden()
    dialog.advanced_filters_group.setChecked(True)
    assert not dialog.advanced_filters_content.isHidden()
    dialog.opponent.setText("remember me")
    dialog.advanced_filters_group.setChecked(False)
    assert dialog.advanced_filters_content.isHidden()
    assert dialog.opponent.text() == "remember me"

    summary = {
        "games": 9, "wins": 5, "draws": 1, "losses": 3, "unknown_or_aborted": 0,
        "analysis_games": 9, "move_coverage_percent": 0, "evaluated_moves": 0,
        "total_own_moves": 465, "scored_positions": 922,
        "classifications_by_phase": {"opening": {"white": {"inaccuracy": 0,
            "mistake": 0, "blunder": 0}, "black": {"inaccuracy": 0,
            "mistake": 0, "blunder": 0}}},
        "own_error_rate_per_100": None, "avg_cpl_cp": None, "swings_200cp": 0,
        "lichess_accuracy": {"white": None, "black": None}, "rating_trend": [],
    }
    game = {"game_id": "synthetic-9", "account_color": "white", "status": "mate",
            "winner": "white", "opponent": "Synthetic", "analysis_status": "partial",
            "scored_positions": 922, "total_positions": 922,
            "own_assessed_moves": 0, "total_own_moves": 465,
            "own_inaccuracies": 0, "own_mistakes": 0, "own_blunders": 0}
    dialog._apply_result(dialog._generation, summary,
                         {"items": [game], "total": 1, "pages": 1}, None)

    text = dialog.summary_label.text()
    row_text = dialog.games.item(0).text()
    assert "ошибки на 100 оценённых моих ходов: нет данных" in text
    assert "Классификация собственных ходов: нет данных" in text
    assert "Официальная точность Lichess" in text and "нет данных" in text
    assert "Нет оценённых моих ходов (0/465)" in row_text
    assert "свои н/о/з 0/0/0" not in row_text


def test_official_engine_install_requires_explicit_confirm_and_persists_verified_path(
    qtbot, tmp_path, monkeypatch
):
    from chess_coach import window as window_module

    window = MainWindow(QSettings(str(tmp_path / "install-ui.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    dialog = SettingsDialog(window.settings, window)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(window_module.QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    installed = tmp_path / "local" / "ChessCoach" / "engines" / "stockfish" / "19" / "stockfish.exe"

    def fake_install(*, destination, stop_event, progress):
        path = Path(destination) / "stockfish.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic UCI engine")
        return {"path": str(path), "name": "Stockfish synthetic", "sha256": "abc123"}

    monkeypatch.setattr(window_module, "install_official_stockfish", fake_install)
    monkeypatch.setattr(window_module.os, "environ",
                        {**window_module.os.environ, "LOCALAPPDATA": str(tmp_path / "local")})
    window._install_official_stockfish(dialog)
    qtbot.waitUntil(lambda: window._engine_install_thread is None, timeout=3000)
    assert installed.exists()
    assert window.settings.value("stockfish_path", type=str) == str(installed)
    assert window.settings.value("stockfish_verified_path", type=str) == str(installed)
    assert "Stockfish synthetic" in dialog.engine_test_status.text()


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
    assert window.home_batch_analysis_button.isEnabled()  # setup/status remains discoverable offline
    window.show_batch_analysis()
    assert not window._batch_dialog.start_button.isEnabled()
    assert "подключите аккаунт lichess" in window._batch_dialog.account_status.text().lower()
    window._batch_dialog.reject()
    assert window._batch_thread is None


def test_sidebar_analytics_and_puzzles_navigation_open_real_screens(qtbot, tmp_path, monkeypatch):
    window = MainWindow(offline_mode=True)
    qtbot.addWidget(window)
    window.show()
    calls = []
    monkeypatch.setattr(window, "show_statistics", lambda: calls.append("analytics"))
    monkeypatch.setattr(window, "open_puzzle_history_local", lambda: calls.append("training"))
    qtbot.mouseClick(window._nav_buttons["analytics"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window._nav_buttons["puzzles"], Qt.MouseButton.LeftButton)
    assert calls == ["analytics", "training"]
    window.close()


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
                                              {"ply": 1, "white_cp": -100}],
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
    assert "Неточности 0" in win_item.text()
    assert "ошибки 1" in win_item.text()
    assert "зевки 0" in win_item.text()
    assert "Оценено позиций: 2/2" in win_item.text()
    assert "источник S" in win_item.text()
    assert "Stockfish" in win_item.toolTip()
    assert dialog.period.currentData() == 90
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Обзор", "Партии", "Дебюты/фазы", "Критические моменты"]
    assert dialog.account.currentData() is None  # no known account; mixed view is explicit
    dialog.resize(1280, 720)
    assert dialog.size().width() == 1280 and dialog.size().height() == 720
    dialog.resize(1360, 860)
    assert dialog.size().width() == 1360 and dialog.size().height() == 860
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


def test_game_detail_explains_missing_initial_eval_for_first_own_move(qtbot, tmp_path):
    repo = GameRepository(tmp_path / "decision-coverage-ui.sqlite3")
    pgn = ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
           '1. e4 { [%eval 0.20] } e5 { [%eval 0.10] } '
           '2. Nf3 { [%eval 0.30] } Nc6 { [%eval 0.20] } *\n')
    game_id, _ = repo.import_archive_record("Me", {"id": "post-only-ui", "pgn": pgn},
                                            checkpoint_ms=None, backfill=False,
                                            _return_inserted=True)
    dialog = StatisticsGameDialog(repo, repo.get_game(game_id))
    qtbot.addWidget(dialog)
    labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Сравнимость собственных ходов: 1/2" in labels
    assert "первый ход не оценён: нет оценки позиции до хода" in labels
    assert "CPL и категории ошибок не являются процентом точности" in labels
    assert "статус: complete" in labels  # source scores remain separately complete


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
    assert not window.home_ai_button.isHidden()
    assert not window.home_ai_button.isEnabled()
    assert "Подключите Lichess" in window.home_ai_button.toolTip()
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


def test_position_evaluation_is_explicit_offline_only_and_stale_result_is_hidden(
        qtbot, tmp_path, monkeypatch):
    engine = tmp_path / "stockfish.exe"
    engine.write_bytes(b"fake fixture")
    settings = QSettings(str(tmp_path / "position-eval.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings, offline_mode=True)
    qtbot.addWidget(window)
    window.settings.setValue("stockfish_path", str(engine))
    window.settings.setValue("stockfish_verified_path", str(engine))
    window.show()
    window.start_local_game()
    assert window.position_eval_button.isVisible()
    calls = []
    def fake_eval(_path, board, _stop, _active):
        calls.append(board.fen())
        return {"score_white_cp": 35, "mate_white": None,
                "best_move": "e2e4", "depth": 12}
    monkeypatch.setattr("chess_coach.window.evaluate_position", fake_eval)
    qtbot.mouseClick(window.position_eval_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(calls) == 1 and window._position_eval_thread is None,
                    timeout=3000)
    assert "+0.35" in window.position_eval_label.text()
    assert "лучший ход e4" in window.position_eval_label.text()
    assert "e2e4" not in window.position_eval_label.text()

    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        window.on_move_intent(chess.Move.from_uci(uci))
    final_fen = window.controller.board.fen()
    qtbot.waitUntil(lambda: len(calls) == 2 and window._position_eval_thread is None,
                    timeout=3000)
    assert calls[-1] == final_fen  # debounce evaluates only the final position
    assert window.moves.item_by_ply(4).text().endswith("+0.4")

    window._lichess_connected = True
    window.analyze_local_position()
    assert len(calls) == 2
    assert "приостановлена" in window.position_eval_label.text()
    window.close()


def test_position_evaluation_in_normal_mode_requires_connected_liveness_check(qtbot, tmp_path):
    engine = tmp_path / "not-connected-engine.exe"
    engine.write_bytes(b"synthetic engine")
    settings = QSettings(str(tmp_path / "position-normal.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.show()
    window.start_local_game()
    assert window.position_eval_button.isVisible()
    assert not window.position_eval_button.isEnabled()
    assert "подключите Lichess" in window.position_eval_label.text()
    window.close()


def test_position_evaluation_normal_mode_requires_safe_preflight_and_rejects_stale_or_429(
        qtbot, tmp_path, monkeypatch):
    engine = tmp_path / "stockfish.exe"
    engine.write_bytes(b"synthetic engine")
    settings = QSettings(str(tmp_path / "position-normal-safe.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)

    class Client:
        rate_limit_remaining = 0

    class Service:
        username = "StudyUser"
        client = Client()
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=None): return True

    service = Service()
    window.lichess = service
    window._lichess_connected = True
    window.show()
    window.start_local_game()
    assert window.position_eval_button.isVisible() and window.position_eval_button.isEnabled()
    calls = []
    def fake_eval(_path, board, stop, _active):
        calls.append(board.fen())
        return {"score_white_cp": -25, "mate_white": None,
                "best_move": "e2e4", "pv": ["e2e4"], "depth": 9}
    monkeypatch.setattr("chess_coach.window.evaluate_position", fake_eval)

    games_before = window.game_repository.search_games(completed_only=False)["total"]
    window.position_eval_button.click()
    assert len(service.requests) == 1 and not calls
    request = service.requests[-1]
    window._analysis_safety_games_received(request, [])
    qtbot.waitUntil(lambda: len(calls) == 1 and window._position_eval_thread is None,
                    timeout=3000)
    assert "-0.25" in window.position_eval_label.text()
    assert "вариант e4" in window.position_eval_label.text()
    assert window.game_repository.search_games(completed_only=False)["total"] == games_before

    # HTTP 429/error while asking for a new safety snapshot fails closed.
    window.position_eval_button.setChecked(False)
    window.position_eval_button.setChecked(True)
    request = service.requests[-1]
    window._analysis_safety_error(request, "HTTP 429")
    assert len(calls) == 1
    assert not window._position_eval_mode

    # A late empty snapshot after account disconnect cannot start UCI.
    window.position_eval_button.setChecked(True)
    stale_request = service.requests[-1]
    window._lichess_connected = False
    window._lichess_status("Не подключено")
    window._analysis_safety_games_received(stale_request, [])
    assert len(calls) == 1
    assert not window._position_eval_mode

    window._lichess_connected = True
    window.position_eval_button.setChecked(True)
    active_request = service.requests[-1]
    window._analysis_safety_games_received(active_request, [{
        "gameId": "live-human", "opponent": {"id": "human-account", "username": "Opponent"},
    }])
    assert len(calls) == 1
    assert not window._position_eval_mode
    assert "активная партия с человеком" in window.position_eval_label.text()
    window.close()


def test_position_evaluation_stops_on_human_game_start_and_hides_stale_pv(
        qtbot, tmp_path, monkeypatch):
    engine = tmp_path / "stockfish-running.exe"
    engine.write_bytes(b"synthetic engine")
    settings = QSettings(str(tmp_path / "position-active-game.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)

    class Client:
        rate_limit_remaining = 0
    class Service:
        username = "StudyUser"
        client = Client()
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=None): return True

    service = Service()
    window.lichess = service
    window._lichess_connected = True
    window.show()
    window.start_local_game()
    started = threading.Event()
    def blocked_eval(_path, _board, stop, _active):
        started.set()
        stop.wait(3)
        return {"cancelled": stop.is_set(), "score_white_cp": 900,
                "mate_white": None, "best_move": "e2e4", "pv": ["e2e4"], "depth": 30}
    monkeypatch.setattr("chess_coach.window.evaluate_position", blocked_eval)
    window.position_eval_button.click()
    request = service.requests[-1]
    window._analysis_safety_games_received(request, [])
    qtbot.waitUntil(started.is_set, timeout=2000)
    active = window._position_eval_stop
    assert active is not None and not active.is_set()

    window._lichess_event({"type": "gameStart", "game": {
        "gameId": "human-live", "color": "white",
        "opponent": {"id": "human-player", "username": "Opponent"},
    }})
    assert active.is_set()
    assert not window.position_eval_button.isEnabled()
    qtbot.waitUntil(lambda: window._position_eval_thread is None, timeout=3000)
    assert not window._position_eval_mode
    assert not window.position_eval_button.isChecked()
    assert "+9.00" not in window.position_eval_label.text()
    window.close()


def test_finished_game_review_position_uses_same_live_safety_preflight(qtbot, tmp_path, monkeypatch):
    engine = tmp_path / "review-stockfish.exe"
    engine.write_bytes(b"synthetic engine")
    settings = QSettings(str(tmp_path / "review-position.ini"), QSettings.Format.IniFormat)
    settings.setValue("stockfish_path", str(engine))
    settings.setValue("stockfish_verified_path", str(engine))
    window = MainWindow(settings, manual_connect_mode=True)
    qtbot.addWidget(window)

    class Client:
        rate_limit_remaining = 0
    class Service:
        username = "ReviewUser"
        client = Client()
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=None): return True

    service = Service()
    session = LichessGameSession("completed-review", window.game_repository)
    session.status = "mate"
    window.lichess = service
    window._lichess_connected = True
    window.online_session = session
    window._review_session = session
    window._screen_mode = "review"
    window.screen_stack.setCurrentWidget(window.game_page)
    window.show()
    window.refresh()
    assert window.position_eval_button.isVisible() and window.position_eval_button.isEnabled()
    seen = []
    monkeypatch.setattr("chess_coach.window.evaluate_position",
                        lambda _path, board, _stop, _active: (
                            seen.append(board.fen()) or {"score_white_cp": 0,
                                "mate_white": None, "best_move": "e2e4",
                                "pv": ["e2e4"], "depth": 8}))
    window.position_eval_button.click()
    request = service.requests[-1]
    assert not seen
    window._analysis_safety_games_received(request, [])
    qtbot.waitUntil(lambda: len(seen) == 1 and window._position_eval_thread is None,
                    timeout=3000)
    assert seen == [session.render_board.fen()]
    window.close()


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

    class Signal:
        def connect(self, _callback): pass

    class Store:
        def get(self): return None

    class ExplicitlyCreatedService:
        oauth_token_store = Store()
        personal_token_store = Store()
        selected_token_store = oauth_token_store
        auth_method = "oauth"
        status_changed = Signal()
        session_status_changed = Signal()
        def connect_account(self): calls.append("oauth")
        def set_auth_method(self, method): self.auth_method = method
        def close(self, timeout=None): return True

    service = ExplicitlyCreatedService()
    monkeypatch.setattr(window, "_create_lichess_service", lambda: service)
    monkeypatch.setattr(window, "_attach_lichess_service", lambda _service: calls.append("attach"))
    qtbot.mouseClick(window.connect_button, Qt.MouseButton.LeftButton)

    assert window.lichess is service
    assert calls == ["attach"]
    assert window._connection_dialog is not None
    window._connection_dialog.oauth_button.click()
    assert calls == ["attach", "oauth"]
    window.close()


def test_manual_connect_restores_saved_token_only_after_explicit_button(qtbot, tmp_path, monkeypatch):
    calls = []

    class Signal:
        def connect(self, _callback): pass

    class Store:
        def __init__(self, value): self.value = value
        def get(self): return self.value

    class SavedTokenService:
        def __init__(self):
            self.oauth_token_store = Store("stored-token")
            self.personal_token_store = Store(None)
            self.selected_token_store = self.oauth_token_store
            self.auth_method = "oauth"
            self.status_changed = Signal()
            self.session_status_changed = Signal()
            self.client = type("Client", (), {"rate_limit_remaining": 0})()
        @property
        def token(self): return self.selected_token_store.get()
        def set_auth_method(self, method):
            self.auth_method = method
            self.selected_token_store = (self.personal_token_store if method == "personal_token"
                                         else self.oauth_token_store)
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

    assert calls == ["attach"]
    assert window._connection_dialog is not None
    window._connection_dialog.restore_button.click()
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

    # Pilot import remains available as a bounded advanced action, but no longer
    # occupies a permanent home-screen button.
    window.start_archive_pilot()
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
    def inspect_dialog(dialog):
        messages.append(dialog.findChild(QLabel).text())
        sync = next(button for button in dialog.findChildren(QPushButton)
                    if button.text().startswith("Синхронизировать"))
        assert not sync.isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", inspect_dialog)

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
    assert calls[-1] == ("history", "lichess:pilotuser", None)
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


def test_my_puzzles_home_action_opens_local_history_without_network_or_oauth(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(QSettings(str(tmp_path / "puzzle-local-nav.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.import_puzzle_activities("LocalUser", [{
        "date": 1_700_000_000_000, "win": True,
        "puzzle": {"id": "local-puzzle", "fen": chess.STARTING_FEN,
                   "solution": ["e2e4"], "themes": ["fork"], "rating": 1500, "plays": 1},
    }])
    opened = []
    monkeypatch.setattr(window, "show_puzzle_history", lambda account, **kwargs: opened.append(account))
    window.open_puzzle_history_local()
    assert opened == ["lichess:localuser"]
    assert window.lichess is None
    assert window._puzzle_progress_dialog is None
    window.close()


def test_puzzle_history_falls_back_from_empty_connected_account_and_has_local_selector(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "puzzle-accounts.ini"),
                                  QSettings.Format.IniFormat), manual_connect_mode=True)
    qtbot.addWidget(window)
    window.game_repository.import_puzzle_activities("OlderUser", [{
        "date": 1_700_000_000_000, "win": True,
        "puzzle": {"id": "older-puzzle", "fen": chess.STARTING_FEN,
                   "solution": ["d2d4", "e7e5"], "themes": ["fork"],
                   "rating": 1500, "plays": 1},
    }])
    window.lichess = type("Service", (), {
        "username": "NewEmptyUser", "puzzle_username": None,
        "client": type("Client", (), {"rate_limit_remaining": 0})(),
        "close": lambda self, timeout=None: True,
    })()
    window._lichess_connected = True
    seen = {}
    def capture_exec(dialog):
        seen["selector"] = next(widget for widget in dialog.findChildren(QComboBox)
                                if widget.count() == 1
                                and str(widget.itemData(0)) == "lichess:olderuser")
        seen["summary"] = dialog.findChildren(QLabel)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", capture_exec)
    window.open_puzzle_history_local()
    selector = seen["selector"]
    assert selector.currentData() == "lichess:olderuser"
    assert selector.count() == 1  # no implicit mixing or empty-account fallback to online data
    assert window._puzzle_progress_dialog is None
    window.close()


def test_puzzle_replay_is_local_legal_and_does_not_change_history_or_send_requests(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(QSettings(str(tmp_path / "puzzle-replay.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.import_puzzle_activities("ReplayUser", [{
        "date": 1_700_000_000_000, "win": False,
        "puzzle": {"id": "replay-puzzle", "fen": chess.STARTING_FEN,
                   "solution": ["d2d4", "e7e5"], "themes": ["fork"],
                   "rating": 1600, "plays": 3},
    }])
    before = window.game_repository.puzzle_statistics("lichess:replayuser")["attempts"]
    calls = []
    monkeypatch.setattr("chess_coach.lichess.api.LichessClient.stream_puzzle_activity",
                        lambda *args, **kwargs: calls.append(args))

    def exercise(dialog):
        replay = next(button for button in dialog.findChildren(QPushButton)
                      if button.text() == "Решить повторно")
        reveal = next(button for button in dialog.findChildren(QPushButton)
                      if button.text() == "Показать решение…")
        reset = next(button for button in dialog.findChildren(QPushButton)
                     if button.text() == "Сбросить упражнение")
        board = dialog.findChild(BoardView)
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert not any("e5" in label or "e7e5" in label or "Решение:" in label
                       for label in labels)
        assert board._board.turn == chess.BLACK  # Lichess FEN is before setup move d4.
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        reveal.click()
        assert any("Решение: e5" in label for label in
                   (widget.text() for widget in dialog.findChildren(QLabel)))
        replay.click()
        reveal.click()
        assert any("Решение: e5" in label for label in
                   (widget.text() for widget in dialog.findChildren(QLabel)))
        reset.click()
        assert not any("Решение: e5" in label for label in
                       (widget.text() for widget in dialog.findChildren(QLabel)))
        replay.click()
        assert board._interaction_enabled
        board.move_intent.emit(chess.Move.from_uci("e7e5"))
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("Вариант завершён" in label for label in labels)
        assert not board._interaction_enabled
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", exercise)
    window.show_puzzle_history("lichess:replayuser")
    assert window.game_repository.puzzle_statistics("lichess:replayuser")["attempts"] == before
    assert not calls
    window.close()


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
    assert window.moves.table.rowCount() == 1
    assert window.moves.table.item(0, 1).text() == "e4"
    assert window.moves.table.item(0, 2).text() == "e5"
    white_item = next(
        window.moves.item(index)
        for index in range(window.moves.count())
        if window.moves.item(index).data(Qt.ItemDataRole.UserRole) == 1
    )
    assert white_item.text() == "e4"
    assert window.moves.table.item(0, 0).text() == "1"
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
    dialog.sound_moves.setChecked(False)
    dialog.sound_notifications.setChecked(False)
    dialog.sound_history.setChecked(True)
    dialog.sound_volume.setValue(73)
    preview = []
    dialog.sound_preview_requested.connect(preview.append)
    dialog.sound_preview.click()
    dialog.sound_capture_preview.click()
    assert preview == ["move", "capture"]
    dialog.save(settings)

    loaded = QSettings(path, QSettings.Format.IniFormat)
    assert loaded.value("player_color", type=int) == 0
    assert loaded.value("board_flipped", type=bool) is True
    assert loaded.value("show_legal_moves", type=bool) is False
    assert loaded.value("show_last_move", type=bool) is False
    assert loaded.value("annotations_enabled", type=bool) is True
    assert loaded.value("sound_moves_enabled", type=bool) is False
    assert loaded.value("sound_notifications_enabled", type=bool) is False
    assert loaded.value("sound_history_enabled", type=bool) is True
    assert loaded.value("sound_volume", type=int) == 73
    preset = SettingsDialog(loaded)
    preset.sound_moves.setChecked(False)
    preset.sound_volume.setValue(0)
    preset.sound_classic_preset.click()
    assert preset.sound_moves.isChecked()
    assert preset.sound_volume.value() == 45


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


def test_stockfish_uci_probe_uses_hidden_worker_and_reports_timeout(qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_bytes(b"fake")
    calls = []
    class FakeEngine:
        id = {"name": "FakeFish"}
        def ping(self): raise TimeoutError("UCI не ответил за 3 секунды")
        def quit(self): calls.append("quit")
    monkeypatch.setattr(window_module, "popen_uci_hidden",
                        lambda *args, **kwargs: (calls.append((args, kwargs)) or FakeEngine()))
    dialog = SettingsDialog(QSettings(str(tmp_path / "probe-timeout.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(dialog)
    dialog.engine_path.setText(str(engine_path))
    dialog.engine_test.click()
    qtbot.waitUntil(lambda: dialog.engine_test.isEnabled(), timeout=3000)
    assert calls[0][0][0] == str(engine_path.resolve())
    assert calls[0][1]["timeout"] == 3.0
    assert calls[-1] == "quit"
    assert "не пройдена" in dialog.engine_test_status.text()
    assert not dialog._engine_verified_path


def test_stockfish_uci_probe_cancel_closes_hidden_engine_worker(qtbot, tmp_path, monkeypatch):
    import threading
    import chess_coach.window as window_module

    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_bytes(b"fake")
    started, stopped = threading.Event(), threading.Event()
    class FakeEngine:
        id = {"name": "FakeFish"}
        def ping(self):
            started.set()
            stopped.wait(2)
            raise RuntimeError("cancelled")
        def quit(self): stopped.set()
    monkeypatch.setattr(window_module, "popen_uci_hidden", lambda *args, **kwargs: FakeEngine())
    dialog = SettingsDialog(QSettings(str(tmp_path / "probe-close.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(dialog)
    dialog.engine_path.setText(str(engine_path))
    dialog.engine_test.click()
    qtbot.waitUntil(started.is_set, timeout=1000)
    worker = dialog._engine_probe_thread
    dialog.reject()
    assert stopped.is_set()
    qtbot.waitUntil(lambda: not worker.is_alive(), timeout=1000)
    assert not dialog._engine_verified_path


def test_stockfish_uci_probe_marks_only_tested_path_verified(qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_bytes(b"fake")
    class FakeEngine:
        id = {"name": "FakeFish"}
        def ping(self): return None
        def quit(self): return None
    monkeypatch.setattr(window_module, "popen_uci_hidden", lambda *args, **kwargs: FakeEngine())
    dialog = SettingsDialog(QSettings(str(tmp_path / "probe-success.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(dialog)
    dialog.engine_path.setText(str(engine_path))
    dialog.engine_test.click()
    qtbot.waitUntil(lambda: dialog.engine_test.isEnabled(), timeout=2000)
    assert dialog.engine_test_status.text() == "UCI готов: FakeFish"
    assert dialog._engine_verified_path == str(engine_path.resolve())
    dialog.engine_path.setText(str(tmp_path / "different.exe"))
    assert not dialog._engine_verified_path


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


@pytest.mark.parametrize("gesture", ["click", "drag"])
def test_online_premove_can_be_entered_on_optimistic_board_during_delayed_ack(
    qtbot, tmp_path, monkeypatch, gesture
):
    """A slow move acknowledgement must not freeze the next premove interaction."""
    from PySide6.QtCore import QPoint

    window = MainWindow(QSettings(str(tmp_path / f"pending-{gesture}.ini"),
                                  QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: calls.append(args))
    session = LichessGameSession(f"pending-{gesture}",
                                 GameRepository(tmp_path / f"pending-{gesture}.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    select_online(window, session)
    window.online_view_ply = None
    window.show()

    window.on_move_intent(chess.Move.from_uci("e2e4"))
    assert len(calls) == 1
    assert session.pending_uci == "e2e4"
    assert window.board._interaction_enabled
    qtbot.wait(2000)  # deterministic reproduction of the reported delayed ACK window
    assert session.pending_uci == "e2e4"
    assert window.board._interaction_enabled

    rect = window.board._get_board_rect()
    side = rect.width() / 8

    def center(square):
        row, col = window.board._renderer.get_visual_coordinates(square, window.board._flipped)
        return QPoint(int(rect.x() + (col + .5) * side), int(rect.y() + (row + .5) * side))

    start, end = center(chess.G1), center(chess.F3)
    if gesture == "click":
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=end)
    else:
        qtbot.mousePress(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(window.board, end, delay=20)
        qtbot.mouseRelease(window.board, Qt.MouseButton.LeftButton, pos=end)

    assert session.premove == chess.Move.from_uci("g1f3")
    assert session.moves == ""  # neither optimistic move mutates authoritative history
    assert len(calls) == 1  # no duplicate real POST before authoritative ACK
    assert window.cancel_premove_button.isVisible()

    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4",
                                                  "status": "started"})
    assert session.pending_uci is None
    assert session.premove == chess.Move.from_uci("g1f3")
    assert len(calls) == 1
    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4 e7e5",
                                                  "status": "started"})
    assert session.pending_uci == "g1f3"
    assert len(calls) == 2


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
    assert window.screen_stack.currentWidget() is window.review_workspace
    assert not window.review_workspace.review_board._interaction_enabled
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


def test_m46_home_has_play_review_context_and_collapsed_secondary_actions(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "home-primary.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    assert window.home_ai_button.text() == "Играть против Lichess AI"
    assert window.home_local_button.text() == "Свободная доска"
    assert window.home_review_button.text() == "Выбрать партию"
    assert window.resume_review_button.text() == "Продолжить последний разбор"
    assert window.home_more_toggle.isCheckable()
    assert not window.home_more_toggle.isChecked()
    assert window.home_more_content.isHidden()
    assert {"games", "accuracy", "analysis", "errors"} == set(window.dashboard_metrics)
    assert window.dashboard_account.count() >= 1
    assert window.dashboard_clock.count() >= 1
    assert window.home_friend_button.parentWidget() is not None
    assert window.home_ai_button.parentWidget() is not None
    assert window.home_batch_analysis_button.parentWidget() is window.home_more_content
    window.home_more_toggle.setChecked(True)
    assert not window.home_more_content.isHidden()
    assert window.home_batch_analysis_button.isEnabled()
    window.game_repository.shutdown()


def test_home_has_two_columns_and_three_recent_game_cards(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "home-columns.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    # The window starts an asynchronous real repository snapshot during init.
    # Let that generation finish before installing this test's synthetic data,
    # otherwise its later render can delete the card wrappers retained below.
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=3000)
    assert window.home_primary_actions.itemAt(0).widget() is window.home_play_card
    assert window.home_primary_actions.itemAt(1).widget() is window.home_recent_card
    assert window.home_friend_button in window.home_more.findChildren(QPushButton)
    games = [{
        "game_id": f"synthetic-{index}", "status": "mate", "winner": "white",
        "color": "white", "opponent": f"Opponent {index}",
        "started_at": "2026-11-02", "clock": "300+3", "eco": "B20",
        "opening_name": "Sicilian Defense",
        "review_metrics": {"status": "без анализа", "coverage": "0/12",
                            "opponent_coverage": "0/12", "source": "нет данных"},
    } for index in range(3)]
    window._render_dashboard_snapshot({"metrics": [], "recent_games": games})
    assert window._recent_games_layout.count() == 3
    cards = [window._recent_games_layout.itemAt(i).widget() for i in range(3)]
    from PySide6.QtWidgets import QLabel, QWidget
    headings = [card.findChild(QLabel).text() for card in cards]
    assert all("Победа с Opponent" in heading for heading in headings)
    assert all("Queen's Pawn Game" not in heading for heading in headings)
    assert window.dashboard_account.currentIndex() == 0
    assert window.dashboard_clock.currentIndex() == 0
    window.setFixedSize(1360, 860)
    window.show()
    qtbot.wait(20)
    assert window.home_page.horizontalScrollBar().maximum() == 0
    # The page can keep a small vertical safety margin at this logical size;
    # the three cards remain compact and do not introduce horizontal scrolling.
    assert window.home_page.verticalScrollBar().maximum() <= 100
    assert all(card.sizeHint().height() <= 200 for card in cards)
    assert window.home_play_card.maximumHeight() <= 230
    opened = []
    window._open_saved_game = lambda game: opened.append(game["game_id"]) or True
    qtbot.mouseClick(cards[1].findChildren(QLabel)[1], Qt.MouseButton.LeftButton)
    assert opened == ["synthetic-1"]
    window.close()


def test_sidebar_play_opens_mode_picker_without_resetting_local_game(qtbot, tmp_path):
    from PySide6.QtWidgets import QFrame
    window = MainWindow(QSettings(str(tmp_path / "play-picker.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.start_local_game()
    for uci in ("e2e4", "e7e5"):
        window.on_move_intent(chess.Move.from_uci(uci))
    before = tuple(window.controller.board.move_stack)
    window._nav_buttons["play"].click()
    assert window.screen_stack.currentWidget() is window.play_page
    assert tuple(window.controller.board.move_stack) == before
    assert sum(widget.objectName() == "metricCard"
               for widget in window.play_page.findChildren(QFrame)) == 5
    assert window.play_local_button.text() == "Свободная доска"
    assert not window.play_ai_button.isEnabled()
    assert window.play_active_button.toolTip()
    window.setFixedSize(1000, 720)
    window._layout_play_mode_cards()
    assert window._play_mode_columns == 1
    window.setFixedSize(1360, 860)
    window._layout_play_mode_cards()
    assert window._play_mode_columns == 2
    window.close()


def test_ai_dialog_has_russian_controls_and_all_eight_visible_levels(qtbot):
    dialog = OnlineGameDialog("Играть против Lichess AI", include_level=True,
                              defaults={"color": "black", "clock_limit": 600,
                                        "clock_increment": 0, "level": 7})
    qtbot.addWidget(dialog)
    buttons = dialog.level_buttons.buttons()
    assert len(buttons) == 8
    assert dialog.color.currentData() == "black"
    assert dialog.clock.currentData() == (600, 0)
    assert dialog.level.currentData() == 7
    for level in range(1, 9):
        button = dialog.level_buttons.button(level)
        assert button.isVisible() or not dialog.isVisible()
        button.click()
        assert dialog.level.currentData() == level
        assert dialog.selection()[3] == level
    box = dialog.findChild(QDialogButtonBox)
    assert box.button(QDialogButtonBox.StandardButton.Ok).text() == "Начать"
    assert box.button(QDialogButtonBox.StandardButton.Cancel).text() == "Отмена"
    box.button(QDialogButtonBox.StandardButton.Cancel).click()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_cancel_ai_dialog_has_no_network_or_saved_preference_side_effect(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "ai-cancel.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._online_actions_allowed = lambda: True
    calls = []
    class FakeService:
        def create_ai_game(self, *args):
            calls.append(args)
        def close(self):
            return True
    window.lichess = FakeService()
    monkeypatch.setattr(OnlineGameDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
    before = window.settings.value("workspace/ai_level")
    window.play_ai()
    assert calls == []
    assert window.settings.value("workspace/ai_level") == before
    window.close()


def test_home_dashboard_refresh_is_background_and_drops_stale_generation(
        qtbot, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def fake_snapshot(_repository, *, account_id=None, clock=None):
        calls.append((account_id, clock))
        started.set()
        release.wait(2)
        tag = "старая" if len(calls) == 1 else "новая"
        return {"metrics": [{"key": key, "value": tag, "detail": tag, "source": "synthetic"}
                           for key in ("games", "accuracy", "analysis", "errors")],
                "recent_games": [], "filter": {"account_id": account_id, "clock": clock}}

    monkeypatch.setattr("chess_coach.window.dashboard_snapshot", fake_snapshot)
    window = MainWindow(QSettings(str(tmp_path / "dashboard-worker.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(started.is_set, timeout=1000)
    # Route switching completes while the database read remains blocked off-thread.
    window.show_home()
    assert window.screen_stack.currentWidget() is window.home_page
    assert not release.is_set()
    window._refresh_home_dashboard()  # supersede generation one while it is in flight
    release.set()
    qtbot.waitUntil(lambda: len(calls) == 2 and window._dashboard_thread is None,
                    timeout=3000)
    assert all(value.text() == "новая" for value in window._dashboard_metric_values.values())
    window.close()


def test_recent_game_card_does_not_show_zero_coverage_when_analysis_is_missing(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "missing-card-analysis.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=3000)
    game = {"game_id": "synthetic-card", "status": "mate", "winner": "white",
            "color": "white", "opponent": "Synthetic", "started_at": "2026-11-02",
            "clock": "300+3", "eco": "B20", "opening_name": "Sicilian Defense",
            "review_metrics": {"status": "без анализа", "coverage": "0/12",
                               "opponent_coverage": "0/12", "source": "нет данных"}}
    window._render_dashboard_snapshot({"metrics": [], "recent_games": [game]})
    labels = [label.text() for label in window.home_page.findChildren(QLabel)]
    card_detail = next(value for value in labels if "анализа пока нет" in value)
    assert "0/12" not in card_detail
    assert "охват Я / соперник" in card_detail
    window.close()


def test_local_move_sound_deduplication_uses_a_new_identity_for_each_game(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "local-sound-games.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    class FakeSound:
        def __init__(self): self.events = []
        def play_move(self, game_id, ply, *_args, **_kwargs):
            self.events.append((game_id, ply))
            return True
    sound = FakeSound()
    window.sound_player = sound
    window.start_local_game()
    first_game_id = window._local_sound_game_id
    window.on_move_intent(chess.Move.from_uci("e2e4"))
    window.start_local_game()
    second_game_id = window._local_sound_game_id
    window.on_move_intent(chess.Move.from_uci("e2e4"))
    assert first_game_id != second_game_id
    assert sound.events == [(first_game_id, 1), (second_game_id, 1)]
    window.close()


def test_game_finish_for_selected_review_does_not_replace_review(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "review-not-stolen.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    live = LichessGameSession("review-id", window.game_repository)
    live.color = chess.WHITE
    live.apply_event({"type": "gameFull", "initialFen": "startpos",
                      "state": {"moves": "e2e4", "status": "started"}})
    window.online_sessions[live.game_id] = live
    window.game_repository.save_snapshot(live.game_id, "startpos", "d2d4", "black",
        "Archive", False, "-", "mate", "black")
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

    window.lichess.username = "Owner"
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
    assert "уже отправлен Lichess" in window.game_status.text()


def test_online_own_piece_click_cancels_local_premove_before_opponent_event(qtbot, tmp_path,
                                                                            monkeypatch):
    session_id = "cancel-before-state"
    window = MainWindow(QSettings(str(tmp_path / f"{session_id}.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    posts, sounds = [], []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: posts.append(args))
    monkeypatch.setattr(window.sound_player, "play_move", lambda *args, **kwargs:
                        sounds.append(("move", args, kwargs)))
    monkeypatch.setattr(window.sound_player, "play_premove", lambda *args, **kwargs:
                        sounds.append(("premove", args, kwargs)))
    session = LichessGameSession(session_id, window.game_repository)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    select_online(window, session)
    window.show()
    window._refresh_online()
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    window._refresh_online()
    before_moves = session.moves
    rect = window.board._get_board_rect()
    side = rect.width() / 8
    row, col = window.board._renderer.get_visual_coordinates(chess.G1, window.board._flipped)
    start = QPoint(int(rect.x() + (col + .5) * side), int(rect.y() + (row + .5) * side))

    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=start)

    assert session.premove is None
    assert session.moves == before_moves
    assert posts == []
    assert sounds == []
    assert "Предход отменён" in window.game_status.text()


def test_local_training_own_piece_click_cancels_premove_without_playing_a_move(qtbot, tmp_path,
                                                                               monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "local-premove-click.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    sound_events = []
    monkeypatch.setattr(window.sound_player, "play_move", lambda *args, **kwargs:
                        sound_events.append(("move", args, kwargs)))
    monkeypatch.setattr(window.sound_player, "play_premove", lambda *args, **kwargs:
                        sound_events.append(("premove", args, kwargs)))
    window.start_local_game()
    window.on_move_intent(chess.Move.from_uci("e2e4"))
    window.local_mode_combo.setCurrentIndex(window.local_mode_combo.findData("premove_training"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    before_moves = tuple(window.controller.board.move_stack)
    before_notation = tuple(window.controller.san_moves())
    before_sounds = list(sound_events)
    rect = window.board._get_board_rect()
    side = rect.width() / 8
    row, col = window.board._renderer.get_visual_coordinates(chess.G1, window.board._flipped)
    start = QPoint(int(rect.x() + (col + .5) * side), int(rect.y() + (row + .5) * side))

    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=start)

    assert window.controller.premove is None
    assert tuple(window.controller.board.move_stack) == before_moves
    assert tuple(window.controller.san_moves()) == before_notation
    assert sound_events == before_sounds
    assert "Предход отменён" in window.game_status.text()
    window.close()


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
    class Signal:
        def connect(self, _callback): pass
    class Store:
        def get(self): return "saved-token"
    class Service:
        token = "saved-token"
        oauth_token_store = Store()
        personal_token_store = Store()
        selected_token_store = oauth_token_store
        auth_method = "oauth"
        status_changed = Signal()
        session_status_changed = Signal()
        client = type("Client", (), {"rate_limit_remaining": 0})()
        def __init__(self): self.restore_calls = 0
        def restore(self): self.restore_calls += 1
        def set_auth_method(self, _method): pass
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
    assert window._connection_dialog is not None
    window._connection_dialog.restore_button.click()
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
    monkeypatch.setattr(analysis_module, "popen_uci_hidden", make_engine)
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
    window.offline_mode = False
    requests = []
    class ConnectedService:
        username = "AccountA"
        def check_active_games_for_analysis(self, request_id): requests.append(request_id)
        def close(self, timeout=1.0): return True
    window.lichess = ConnectedService()
    window.offline_mode = False
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
    window.offline_mode = False
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
    window.offline_mode = False
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
    window.offline_mode = False
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
    window.offline_mode = False

    class ConnectedService:
        username = "AccountA"
        token = "saved-fake-token"
        class Signal:
            def connect(self, _callback): pass
        class Store:
            def get(self): return "saved-fake-token"
        oauth_token_store = Store()
        personal_token_store = Store()
        selected_token_store = oauth_token_store
        auth_method = "oauth"
        status_changed = Signal()
        session_status_changed = Signal()
        client = type("Client", (), {"rate_limit_remaining": 0})()
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def disconnect_account(self): pass
        def set_auth_method(self, _method): pass
        def restore(self): self.restored = True
        def close(self, timeout=1.0): return True

    service = ConnectedService()
    window.lichess = service
    window.offline_mode = False
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
        assert window._connection_dialog is not None
        window._connection_dialog.restore_button.click()
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


def test_window_discards_queued_lichess_status_from_previous_auth_generation(qtbot, tmp_path):
    from chess_coach.lichess.service import LichessService

    class Store:
        def get(self): return "synthetic-token"
        def set(self, value): pass
        def delete(self): pass

    window = MainWindow(QSettings(str(tmp_path / "generation-ui.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    service = LichessService(client=object(), token_store=Store(),
                             personal_token_store=Store(), auth_method="oauth")
    window.lichess = service
    stale_generation = service.auth_generation
    service.set_auth_method("personal_token")
    current_generation = service.auth_generation
    before = window.connection.text()

    window._lichess_session_status(stale_generation, "Не подключено")
    assert window.connection.text() == before
    window._lichess_session_status(current_generation, "Подключено: SameName")
    assert window.connection.text() == "●  Подключено: SameName"

    window.lichess = None
    service.close()
    window.close()


@pytest.mark.parametrize("state", ["no-service", "disconnected", "429", "manual-connect"])
def test_normal_mode_never_uses_user_confirmation_as_stockfish_preflight(
        qtbot, tmp_path, monkeypatch, state):
    window = MainWindow(QSettings(str(tmp_path / f"analysis-no-bypass-{state}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    if state != "no-service":
        class Service:
            username = "AccountA"
            client = type("Client", (), {"rate_limit_remaining": 90 if state == "429" else 0})()
            def close(self, timeout=1.0): return True
        window.lichess = Service()
    window._lichess_connected = state == "429"
    window.manual_connect_mode = state == "manual-connect"
    launches, prompts = [], []
    monkeypatch.setattr(window, "_launch_game_analysis",
                        lambda *args, **kwargs: launches.append((args, kwargs)))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: prompts.append(args))

    window._start_game_analysis("finished-game", "fake-stockfish")

    assert not launches
    assert prompts
    assert window._analysis_safety_phase is None
    assert window._analysis_safety_request_id is None
    assert "не заменяет проверку" in window.home_status.text()
    assert window.close() is True


@pytest.mark.parametrize("unsafe_status", [
    "Переподключение к Lichess…", "Ограничение Lichess (429): через 90 с",
    "Ошибка подключения", "Не подключено", "Отзываем сохранённый OAuth-токен…",
])
def test_fair_play_revocation_stops_running_game_analysis_and_discards_late_success(
        qtbot, tmp_path, monkeypatch, unsafe_status):
    window = MainWindow(QSettings(str(tmp_path / f"analysis-revoke-{unsafe_status[:3]}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    window._lichess_connected = True

    class ConnectedService:
        username = "AccountA"
        client = type("Client", (), {"rate_limit_remaining": 0})()
        def __init__(self): self.requests = []
        def check_active_games_for_analysis(self, request_id): self.requests.append(request_id)
        def close(self, timeout=1.0): return True

    service = ConnectedService()
    window.lichess = service
    started, stopped = threading.Event(), threading.Event()
    opened = []

    class BlockingAnalyzer:
        def __init__(self, _repository): pass
        def analyze(self, _game_id, _engine_path, _options, **kwargs):
            stop_event = kwargs["stop_event"]
            started.set()
            assert stop_event.wait(3)
            stopped.set()
            # Simulate an analyzer that raced and returned a success payload
            # despite cancellation. The UI must reject it by generation.
            return {"run_id": "stale", "assessments": [{"ply": 0, "white_cp": 500}]}

    monkeypatch.setattr("chess_coach.window.CompletedGameAnalyzer", BlockingAnalyzer)
    monkeypatch.setattr(window, "_open_analysis_position", lambda *args: opened.append(args) or True)
    window._start_game_analysis("finished-game", "fake-stockfish")
    request_id = service.requests[-1]
    window._analysis_safety_games_received(request_id, [])
    qtbot.waitUntil(started.is_set, timeout=2000)
    stop = window._analysis_stop
    assert stop is not None and not stop.is_set()

    window._lichess_status(unsafe_status)

    assert stop.is_set()
    assert "Остановлено: нужна повторная проверка безопасности" in window._analysis_dialog.labelText()
    qtbot.waitUntil(stopped.is_set, timeout=3000)
    qtbot.waitUntil(lambda: window._analysis_thread is not None
                    and not window._analysis_thread.is_alive(), timeout=3000)
    qtbot.waitUntil(lambda: window._analysis_worker_context is None, timeout=3000)
    assert not opened
    assert "Анализ остановлен" in window.home_status.text()
    assert window.game_repository.list_analyses("finished-game") == []
    assert window.close() is True
