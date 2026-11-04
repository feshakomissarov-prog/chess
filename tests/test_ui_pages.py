import ast
from pathlib import Path

from chess_coach.ui.pages import LibraryPage, PlayPage, ResultPage


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
    page.following.click()
    assert page_requests[-2:] == [
        ({**page.current_filters(), "query": "synthetic"}, 0),
        ({**page.current_filters(), "query": "synthetic"}, 2),
    ]
    assert page.refresh_game.isHidden()
    assert page.current_filters()["period_days"] == 0


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
