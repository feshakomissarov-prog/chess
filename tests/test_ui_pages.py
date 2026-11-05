import ast
import chess
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from chess_coach.ui.pages import LibraryPage, PlayPage, ResultPage
from chess_coach.ui.pages.background import BackgroundOperationsPage
from chess_coach.ui.pages.position_setup import PositionSetupWidget
from chess_coach.ui.dialogs import SettingsDialog
from PySide6.QtCore import QSettings


def test_play_page_keeps_each_action_connected_and_reflows_cards(qtbot):
    calls = []
    actions = {key: (lambda key=key: calls.append(key))
               for key in ("local", "ai", "friend", "challenge", "active")}
    page = PlayPage(actions)
    qtbot.addWidget(page)

    page.buttons["ai"].click()
    page.buttons["local"].click()
    assert calls == ["ai", "local"]
    page.layout_modes(900)
    assert page._columns == 1
    assert page.grid.getItemPosition(0) == (0, 0, 1, 1)
    assert page.grid.getItemPosition(1) == (1, 0, 1, 1)
    page.layout_modes(1280)
    assert page.grid.getItemPosition(0) == (0, 0, 1, 1)
    assert page.grid.getItemPosition(1) == (0, 1, 1, 1)
    assert not page.start_button.isHidden()
    requests = []
    page.create_requested.connect(requests.append)
    page.mode_selector.setCurrentIndex(page.mode_selector.findData("ai"))
    page.level.setCurrentIndex(page.level.findData(6))
    page.start_button.click()
    assert requests[-1]["mode"] == "ai"
    assert requests[-1]["level"] == 6
    assert not page.start_button.isEnabled()
    page.start_button.click()
    assert len(requests) == 1
    page.set_status("Ошибка 429: исход запроса неизвестен.")
    assert not page.start_button.isEnabled()
    page.set_request_pending(False)
    assert page.start_button.isEnabled()
    page.clock.setCurrentIndex(page.clock.findText("Свой…"))
    assert not page.custom_clock_row.isHidden()
    page.custom_minutes.setValue(7)
    page.custom_increment.setValue(3)
    page.start_button.click()
    assert requests[-1]["clock_limit"] == 420
    assert requests[-1]["clock_increment"] == 3
    page.mode_selector.setCurrentIndex(page.mode_selector.findData("local"))
    assert not page.form_rows["Ваш цвет"].isVisible()
    assert not page.form_rows["Контроль времени"].isVisible()
    assert not page.form_rows["Уровень Lichess AI"].isVisible()
    assert page.mode_selector.findData("friend") >= 0


def test_result_page_exposes_four_explicit_completion_actions(qtbot):
    calls = []
    page = ResultPage({key: (lambda key=key: calls.append(key))
                       for key in ("review", "export", "new", "home")})
    qtbot.addWidget(page)
    page.summary.setText("Синтетическая завершённая партия")
    for button in (page.review_button, page.export_button,
                   page.new_button, page.home_button):
        button.click()
    assert page.summary.text() == "Синтетическая завершённая партия"
    assert calls == ["review", "export", "new", "home"]


def test_library_page_emits_selected_game_actions_and_pagination(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    selected, page_requests = [], []
    page.open_requested.connect(selected.append)
    page.query_requested.connect(lambda query, index: page_requests.append((query, index)))
    game = {"game_id": "synthetic-game"}
    page.set_results([(game, "Synthetic game", "Local")], page=1, pages=3, total=101)
    page.games.setCurrentRow(0)
    page.open_game.click()
    assert selected == [game]
    page.search.setText("synthetic")
    page.opponent_filter.setText("Alpha")
    page.opening_filter.setText("B20")
    page.following.click()
    qtbot.waitUntil(lambda: len(page_requests) == 2, timeout=1000)
    assert page_requests[0][1] == 2
    assert page_requests[1] == (page.current_filters(), 0)
    assert page.refresh_game.isHidden()
    assert page.current_filters()["period_days"] == 0
    assert page.current_filters()["opponent"] == "Alpha"
    assert page.current_filters()["opening"] == "B20"
    assert page.sync_button.text() == "Синхронизировать"
    assert page.background_button.text() == "Фоновые операции"


def test_library_typing_filters_coalesce_to_one_query(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    page.set_results([], page=0, pages=1, total=0)
    requests = []
    page.query_requested.connect(lambda filters, index: requests.append((filters, index)))
    for char in "synthetic-search-15-chars":
        page.search.setText(page.search.text() + char)
        qtbot.wait(8)
    qtbot.waitUntil(lambda: len(requests) == 1, timeout=1200)
    assert requests[0][0]["query"] == "synthetic-search-15-chars"


def test_library_note_draft_stays_bound_to_the_game_where_it_was_typed(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    first = {"game_id": "game-a"}
    second = {"game_id": "game-b"}
    page.set_results([(first, "First", "Local"), (second, "Second", "Local")],
                     page=0, pages=1, total=2)
    page.games.setCurrentRow(0)
    saved = []
    page.note_requested.connect(lambda game, text: saved.append((game["game_id"], text)))
    page.note.setText("position note")
    assert page.save_note.isEnabled()
    page.games.setCurrentRow(1)
    assert not page.save_note.isEnabled()
    page.save_note.click()
    assert saved == []
    page.games.setCurrentRow(0)
    assert page.save_note.isEnabled()
    page.save_note.click()
    assert saved == [("game-a", "position note")]


def test_library_keeps_independent_note_drafts_per_selected_game(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    first = {"game_id": "draft-a", "line_revision": 1}
    second = {"game_id": "draft-b", "line_revision": 4}
    page.set_results([(first, "First", "Local"), (second, "Second", "Local")],
                     page=0, pages=1, total=2)
    page.games.setCurrentRow(0)
    page.note.setText("note for A")
    page.games.setCurrentRow(1)
    page.note.setText("note for B")
    page.games.setCurrentRow(0)
    assert page.note.text() == "note for A"
    page.games.setCurrentRow(1)
    assert page.note.text() == "note for B"
    assert page._note_draft_game_id == "draft-b"


def test_play_page_exposes_cancel_only_for_cancellable_pending_challenge(qtbot):
    page = PlayPage({key: (lambda: None) for key in ("local", "ai", "friend", "challenge", "active")})
    qtbot.addWidget(page)
    page.show()
    page.set_request_pending(True, cancellable=True)
    assert page.cancel_pending_button.isVisible()
    assert not page.start_button.isEnabled()
    cancelled = []
    page.cancel_requested.connect(lambda: cancelled.append(True))
    page.cancel_pending_button.click()
    assert cancelled == [True]
    page.set_request_pending(True)
    assert not page.cancel_pending_button.isVisible()


def test_library_uses_bounded_three_zone_layout_at_laptop_size(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    page.resize(1280, 720)
    page.show()
    qtbot.wait(30)

    assert page.body_splitter.count() == 2
    aside_scroll = page.body_splitter.widget(1)
    assert isinstance(aside_scroll, QScrollArea)
    aside = aside_scroll.widget()
    assert page.filter_panel.parentWidget() is aside
    assert page.detail_panel.parentWidget() is aside
    assert page.body_splitter.widget(0).width() >= 420
    assert page.body_splitter.widget(1).width() >= 320
    assert not isinstance(page, QScrollArea)
    assert page.summary.text().startswith("Завершённые партии")
    assert page.search.height() >= 28
    assert page.account_filter.height() >= 28
    assert page.detail_open.height() > 0


def test_settings_tab_contents_stay_top_aligned_and_reachable(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings-layout.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.resize(900, 650)
    dialog.show()
    qtbot.wait(20)

    for index in range(dialog.settings_tabs.count()):
        scroll = dialog.settings_tabs.widget(index)
        assert isinstance(scroll, QScrollArea)
        content = scroll.widget()
        assert content.layout().alignment() & Qt.AlignmentFlag.AlignTop
        assert content.geometry().top() == 0


def test_background_operations_page_renders_shared_registry_and_pause_state(qtbot):
    page = BackgroundOperationsPage()
    qtbot.addWidget(page)
    operation = SimpleNamespace(
        context=SimpleNamespace(kind="archive_sync"),
        lane=SimpleNamespace(value="network_background"),
        status=SimpleNamespace(value="running"),
        progress_done=3, progress_total=8,
    )
    page.set_snapshot((operation,), source_summary="Источник: Lichess · 3/8",
                      backfill_paused=True, can_check=True,
                      can_pause=True,
                      retry_status="Retry-After учитывается")
    assert page.operation_list.count() == 1
    assert "archive_sync" in page.operation_list.item(0).text()
    assert "3/8" in page.operation_list.item(0).text()
    assert page.pause_button.text() == "Возобновить Lichess"
    assert not page.manual_check_button.isEnabled()
    assert "Retry-After" in page.retry_status.text()


def test_position_setup_uses_shared_board_and_emits_ephemeral_fen(qtbot):
    page = PositionSetupWidget()
    qtbot.addWidget(page)
    page.show()
    page.setChecked(True)
    positions = []
    page.position_requested.connect(positions.append)
    page.open_button.click()
    assert positions[-1] == chess.STARTING_FEN
    assert chess.Board(positions[-1]).castling_rights == chess.Board().castling_rights
    page.clear_button.click()
    page._board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    page._board.set_piece_at(chess.H1, chess.Piece(chess.ROOK, chess.WHITE))
    page._board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
    page._board.set_piece_at(chess.H8, chess.Piece(chess.ROOK, chess.BLACK))
    page._board.set_piece_at(chess.E4, chess.Piece(chess.PAWN, chess.WHITE))
    page.castling_controls["Белые K"].setChecked(True)
    page.castling_controls["Чёрные k"].setChecked(True)
    page.board_view.set_position(page._board)
    page.piece_selector.setCurrentIndex(page.piece_selector.findData("Q"))
    center = page.board_view._get_square_center(chess.D4)
    QTest.mouseClick(page.board_view, Qt.MouseButton.LeftButton,
                     pos=QPoint(round(center.x()), round(center.y())))
    assert page._board.piece_at(chess.D4) == chess.Piece(chess.QUEEN, chess.WHITE)
    page.turn_selector.setCurrentIndex(page.turn_selector.findData(chess.BLACK))
    page.open_button.click()
    assert len(positions) == 2
    assert chess.Board(positions[-1]).piece_at(chess.D4) == chess.Piece(chess.QUEEN, chess.WHITE)
    assert chess.Board(positions[-1]).turn == chess.BLACK
    assert chess.Board(positions[-1]).has_kingside_castling_rights(chess.WHITE)
    assert chess.Board(positions[-1]).has_kingside_castling_rights(chess.BLACK)
    page.ep_selector.setCurrentIndex(page.ep_selector.findData(chess.E3))
    page.open_button.click()
    assert chess.Board(positions[-1]).ep_square == chess.E3
    page.close()


def test_position_setup_rejects_invalid_fen_and_clears_castling_after_rook_removal(qtbot):
    page = PositionSetupWidget()
    qtbot.addWidget(page)
    page.setChecked(True)
    page.piece_selector.setCurrentIndex(page.piece_selector.findData(""))
    page._place_selected_piece(chess.H1)
    assert not page.castling_controls["Белые K"].isChecked()
    page._clear()
    positions = []
    page.position_requested.connect(positions.append)
    page.open_button.click()
    assert positions == []
    assert "некорректна" in page.status.text()


def test_library_detail_reuses_review_graph_and_masks_until_safety_gate(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    game = {"game_id": "synthetic", "opponent": "Synthetic", "color": "white",
            "status": "mate", "source": "lichess_archive", "clock": "300+0",
            "started_at": "2026-11-03T12:00:00Z", "game_time_precision": "timestamp",
            "last_move_at": 1793779200000}
    review = {
        "analysis": {"assessments": [
            {"ply": 0, "white_cp": 10, "mate_white": None},
            {"ply": 1, "white_cp": -25, "mate_white": None},
        ], "move_metrics": [{"ply": 1, "severity": "inaccuracy"}],
            "source": "lichess", "status": "complete"},
        "coverage": {"scored": 2, "total": 2},
        "per_side": {"white": {"assessed": 1, "mistake": 0, "blunder": 0},
                     "black": {"assessed": 1}},
        "moves": [type("Move", (), {"ply": 1, "san": "e4"})()],
        "owner_color": "white",
        "official_accuracy": {"white": 95.0, "black": 90.0},
    }
    page.show_game_detail(game, review, safety_allowed=False)
    assert not page.detail_graph._values()
    assert "скрыты" in page.detail_status.text()
    assert not page.detail_metrics.text()

    page.show_game_detail(game, review, safety_allowed=True)
    assert "Завершена: 04.11.2026" in page.detail_metadata.text()
    assert page.detail_graph._values() == [(0, 10, None), (1, -25, None)]
    assert page.detail_graph.full_ply_count == 1
    assert "95.0 / 90.0" in page.detail_metrics.text()
    positions = []
    page.detail_position_requested.connect(lambda selected, ply: positions.append((selected, ply)))
    page.detail_graph.selected_ply.emit(1)
    assert positions == [(game, 1)]


def test_legacy_window_imports_are_compatibility_reexports():
    from chess_coach import window
    from chess_coach.ui import analytics_dialogs, dialogs

    assert window.SettingsDialog is dialogs.SettingsDialog
    assert window.LichessConnectionDialog is dialogs.LichessConnectionDialog
    assert window.StatisticsDialog is analytics_dialogs.StatisticsDialog
    assert window.EvaluationGraph is analytics_dialogs.EvaluationGraph


def test_extracted_ui_components_do_not_import_the_root_window():
    root = Path(__file__).resolve().parents[1] / "src" / "chess_coach" / "ui"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name == "chess_coach.window" or name.endswith(".window")
                       for name in imported), f"{path.name} imports the root window"
