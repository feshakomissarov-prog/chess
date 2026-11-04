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
    assert page_requests[-2:] == [
        ({**page.current_filters(), "query": "synthetic"}, 0),
        ({**page.current_filters(), "query": "synthetic"}, 2),
    ]
    assert page.refresh_game.isHidden()
    assert page.current_filters()["period_days"] == 0
    assert page.current_filters()["opponent"] == "Alpha"
    assert page.current_filters()["opening"] == "B20"
    assert page.sync_button.text() == "Синхронизировать"
    assert page.background_button.text() == "Фоновые операции"


def test_library_uses_bounded_three_zone_layout_at_laptop_size(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    page.resize(1280, 720)
    page.show()
    qtbot.wait(30)

    assert page.body_splitter.count() == 2
    assert page.filter_panel.parentWidget() is page.body_splitter.widget(1)
    assert page.detail_panel.parentWidget() is page.body_splitter.widget(1)
    assert page.body_splitter.widget(0).width() >= 420
    assert page.body_splitter.widget(1).width() >= 320
    assert not isinstance(page, QScrollArea)
    assert page.summary.text().startswith("Завершённые партии")


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
    page.piece_selector.setCurrentIndex(page.piece_selector.findData("Q"))
    center = page.board_view._get_square_center(chess.E2)
    QTest.mouseClick(page.board_view, Qt.MouseButton.LeftButton,
                     pos=QPoint(round(center.x()), round(center.y())))
    assert page._board.piece_at(chess.E2) == chess.Piece(chess.QUEEN, chess.WHITE)
    page.turn_selector.setCurrentIndex(page.turn_selector.findData(chess.BLACK))
    positions = []
    page.position_requested.connect(positions.append)
    page.open_button.click()
    assert len(positions) == 1
    assert chess.Board(positions[0]).piece_at(chess.E2) == chess.Piece(chess.QUEEN, chess.WHITE)
    assert chess.Board(positions[0]).turn == chess.BLACK
    page.close()


def test_library_detail_reuses_review_graph_and_masks_until_safety_gate(qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    game = {"game_id": "synthetic", "opponent": "Synthetic", "color": "white",
            "status": "mate", "source": "lichess_archive", "clock": "300+0"}
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
