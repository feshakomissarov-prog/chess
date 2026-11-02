import chess
import pytest

from chess_coach.ui.game_review import GameReviewWorkspace, ReviewGraph
from chess_coach.lichess.game_review import (
    ACCEPTANCE_POLICY_VERSION, eligible_error_moves, load_game_review,
    verified_improved_moves,
)
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.move_table import MoveTable


def _review_repo(path, *, severity="blunder", depth=20, nodes=100_000,
                source="stockfish", status="complete",
                owner_color="black"):
    repo = GameRepository(path)
    repo.save_snapshot("review-test", "startpos", "e2e4 e7e5 g1f3", owner_color,
                       "Opponent", False, "5+0", "mate", "white",
                       white_player="Me" if owner_color == "white" else "Opponent",
                       black_player="Me" if owner_color == "black" else "Opponent",
                       started_at="2026-11-02T12:00:00Z")
    line = repo.current_line("review-test")
    repo.save_analysis(
        "review-test", line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="FixtureFish", engine_version="19-test",
        parameters={"nodes": nodes}, status=status,
        assessments=[
            {"ply": 0, "white_cp": 20, "depth": depth, "nodes": nodes, "pv": ["e2e4"]},
            {"ply": 1, "white_cp": 0, "depth": depth, "nodes": nodes,
             "pv": ["c7c5", "g1f3"]},
            {"ply": 2, "white_cp": 250, "depth": depth, "nodes": nodes, "pv": ["g1f3"]},
            {"ply": 3, "white_cp": 30, "depth": depth, "nodes": nodes, "pv": ["b8c6"]},
        ],
        classifications=[{"ply": 2, "mover": "black", "eval_loss_cp": 80,
                          "label": severity, "phase": "opening",
                          "algorithm_version": "fixture-v1"}])
    return repo, repo.get_game("review-test")


def _metrics_text(table):
    return "\n".join(
        table.item(row, col).text()
        for row in range(table.rowCount())
        for col in range(table.columnCount())
        if table.item(row, col) is not None)


def test_read_model_is_current_revision_source_aware_and_contains_two_sided_counts(tmp_path):
    repo, game = _review_repo(tmp_path / "review-model.sqlite3")
    try:
        with repo._connection() as db:
            db.execute("UPDATE games SET source='local',source_game_id=NULL WHERE game_id=?",
                       (game["game_id"],))
        game = repo.get_game(game["game_id"])
        view = load_game_review(repo, game)
        assert len(view["moves"]) == 3
        assert view["moves"][1].san == "e5"
        assert view["moves"][1].before_fen == view["positions"][1].fen()
        assert view["per_side"]["black"]["blunder"] == 1
        assert view["per_side"]["white"]["assessed"] == 2
        assert view["official_accuracy"]["source"] is None
        assert view["coverage"]["total"] >= 4
    finally:
        repo.shutdown()


def test_saved_stockfish_pv_is_accepted_only_when_legal_same_run_and_better(tmp_path):
    repo, game = _review_repo(tmp_path / "review-pv.sqlite3")
    try:
        view = load_game_review(repo, game)
        error = view["moves"][1]
        improved = verified_improved_moves(view, error)
        assert "c7c5" in improved
        assert improved["c7c5"]["improvement_cp"] == 250
        assert improved["c7c5"]["policy_version"] == ACCEPTANCE_POLICY_VERSION
        assert eligible_error_moves(view) == [error]
    finally:
        repo.shutdown()


def test_partial_or_low_quality_analysis_does_not_create_training_puzzles(tmp_path):
    partial, game = _review_repo(tmp_path / "review-partial.sqlite3", status="partial")
    try:
        assert eligible_error_moves(load_game_review(partial, game)) == []
    finally:
        partial.shutdown()
    shallow, game = _review_repo(tmp_path / "review-shallow.sqlite3", depth=8, nodes=100)
    try:
        view = load_game_review(shallow, game)
        assert verified_improved_moves(view, view["moves"][1]) == {}
        assert eligible_error_moves(view) == []
    finally:
        shallow.shutdown()


def test_training_rejects_ownerless_and_non_stockfish_results(tmp_path):
    repo, game = _review_repo(tmp_path / "review-lichess.sqlite3", source="lichess")
    try:
        assert eligible_error_moves(load_game_review(repo, game)) == []
    finally:
        repo.shutdown()


def test_review_moves_graph_and_training_share_position_without_mutating_saved_pgn(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-ui.sqlite3")
    try:
        before_pgn = repo.export_pgn(game["game_id"])
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert "1–0" in workspace.title.text()
        assert "Stockfish" not in workspace.subtitle.text()
        workspace.moves_list.setCurrentRow(2)
        assert workspace.selected_ply == 2
        assert workspace.review_board._board.fen() == workspace.review["positions"][2].fen()

        workspace.start_training()
        assert workspace.training_mode
        assert not workspace.graph.isVisible()
        assert not workspace.tabs.isVisible()
        assert workspace.left_metrics.isHidden()
        assert workspace.review_inspector.isHidden()
        assert not workspace.training_prompt.isHidden()
        assert not workspace.training_status.isHidden()
        assert workspace.previous_error_button.isHidden()
        assert workspace.next_error_button.isHidden()
        assert workspace.analysis_button.isHidden()
        assert "Найди улучшение" in workspace.training_prompt.text()
        assert workspace.review_board._board.fen() == workspace.review["moves"][1].before_fen
        workspace._training_move_intent(chess.Move.from_uci("c7c5"))
        assert workspace.check_button.isEnabled()
        workspace.check_button.click()
        assert "Найден улучшенный ход" in workspace.training_status.text()
        assert repo.export_pgn(game["game_id"]) == before_pgn
        workspace.continue_button.click()
        assert not workspace.training_mode
        assert not workspace.left_metrics.isHidden()
        assert not workspace.review_inspector.isHidden()
        assert "проверено 1, улучшено 1" in workspace.training_status.text()
        assert workspace.review_board._board.fen() == workspace.review["positions"][2].fen()
        assert repo.export_pgn(game["game_id"]) == before_pgn
    finally:
        repo.shutdown()


def test_move_table_groups_white_and_black_in_fullmove_rows_and_keeps_ply(qtbot):
    from types import SimpleNamespace
    table = MoveTable()
    qtbot.addWidget(table)
    board = chess.Board()
    moves_with_positions = []
    for ply, uci, severity in ((1, "e2e4", "good"), (2, "e7e5", "unknown"),
                               (3, "g1f3", "blunder")):
        move = chess.Move.from_uci(uci)
        san, before_fen = board.san(move), board.fen()
        moves_with_positions.append((ply, san, board.turn, before_fen, severity))
        board.push(move)
    moves = [
        SimpleNamespace(ply=ply, mover="white" if turn else "black", san=san,
                        severity=severity, before_fen=before_fen)
        for ply, san, turn, before_fen, severity in moves_with_positions
    ]
    table.set_rows(moves, scores={1: {"white_cp": 25}, 2: {"white_cp": 10}})
    assert table.table.rowCount() == 2
    assert table.table.item(0, 1).text().endswith("+0.3")
    assert table.table.item(0, 2).text().startswith("? e5 +0.1")
    assert table.table.item(0, 1).foreground().color().name() == "#58a978"
    assert table.table.item(0, 2).foreground().color().name() == "#858b93"
    assert table.table.item(1, 1).text().startswith("?? ")
    selected = []
    table.ply_selected.connect(selected.append)
    table._cell_clicked(0, 2)
    table._cell_clicked(1, 1)
    assert selected == [2, 3]
    assert table.table.item(1, 1).toolTip() == "Зевок"
    assert table.set_score(3, white_cp=75, source="Stockfish", depth=18)
    assert table.item_by_ply(3).text().endswith("+0.8")
    assert table.set_score(3, mate_white=-2, source="Stockfish", depth=20)
    assert "Δб" not in table.item_by_ply(3).text()
    assert table.item_by_ply(3).text().endswith("−M2")


def test_move_table_black_to_move_fen_uses_fullmove_number_and_handles_empty_side(qtbot):
    from types import SimpleNamespace
    board = chess.Board("4k3/8/8/8/8/8/4P3/4K3 b - - 0 25")
    move = chess.Move.from_uci("e8d7")
    assert move in board.legal_moves
    table = MoveTable()
    qtbot.addWidget(table)
    table.set_rows([SimpleNamespace(ply=1, mover="black", san=board.san(move),
                                    severity="unknown", before_fen=board.fen())])
    assert table.table.rowCount() == 1
    assert table.table.item(0, 0).text() == "25"
    assert table.table.item(0, 1).text() == ""
    assert table.table.item(0, 2).text() == "? Kd7"


def test_move_table_keeps_both_san_columns_visible_without_horizontal_scroll(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHeaderView
    from types import SimpleNamespace

    board = chess.Board()
    moves = []
    for ply, uci in enumerate(("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"), start=1):
        move = chess.Move.from_uci(uci)
        moves.append(SimpleNamespace(ply=ply, mover="white" if board.turn else "black",
                                     san=board.san(move), severity="unknown",
                                     before_fen=board.fen()))
        board.push(move)
    table = MoveTable()
    qtbot.addWidget(table)
    table.set_rows(moves, scores={1: {"white_cp": 30}, 2: {"white_cp": -20},
                                  3: {"white_cp": 1500}})
    table.resize(360, 280)
    table.show()
    qtbot.wait(10)

    assert table.table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert table.table.horizontalScrollBar().maximum() == 0
    assert table.table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert table.table.horizontalHeader().sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    assert table.item_by_ply(1).text() == "? e4 +0.3"
    assert table.item_by_ply(2).text() == "? e5 -0.2"
    assert table.item_by_ply(3).text() == "? Nf3 +15.0"
    for ply in (1, 2, 3):
        item = table.item_by_ply(ply)
        assert item is not None and item.text()
        column = 1 if ply in (1, 3) else 2
        assert table.table.columnWidth(column) > 80
        assert table.table.fontMetrics().horizontalAdvance(item.text()) <= table.table.columnWidth(column)


def test_review_safety_button_does_not_reveal_solution_before_preflight(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-safety.sqlite3")
    try:
        workspace = GameReviewWorkspace(
            repo, training_allowed=False,
            training_block_reason="Проверьте активные партии Lichess.")
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert not workspace.training_button.isEnabled()
        assert not workspace.training_safety_button.isHidden()
        requested = []
        workspace.safety_requested.connect(requested.append)
        workspace.training_safety_button.click()
        assert requested == ["review-test"]
        assert "пока скрыты" in workspace.training_status.text()
        assert not workspace.graph.isHidden()  # graph stays visible; no solution/PV is exposed
    finally:
        repo.shutdown()


def test_saved_finished_review_exposes_explicit_stockfish_request(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-engine-cta.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=False)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert not workspace.analysis_button.isHidden()
        assert workspace.analysis_button.text() == "Проанализировать Stockfish"
        requested = []
        workspace.analysis_requested.connect(requested.append)
        workspace.analysis_button.click()
        assert requested == [game["game_id"]]
    finally:
        repo.shutdown()


def test_review_graph_supports_keyboard_position_navigation(qtbot):
    from PySide6.QtCore import Qt

    graph = ReviewGraph([{"ply": 0, "white_cp": 0}, {"ply": 3, "white_cp": 40}], [])
    qtbot.addWidget(graph)
    graph.show()
    graph.setFocus()
    selected = []
    graph.selected_ply.connect(selected.append)
    graph.selected_ply.connect(graph.select_ply)
    qtbot.keyClick(graph, Qt.Key.Key_End)
    qtbot.keyClick(graph, Qt.Key.Key_Left)
    assert selected == [3, 2]


def test_review_graph_uses_full_game_axis_and_never_interpolates_sparse_gaps(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    graph = ReviewGraph([
        {"ply": 0, "white_cp": 0},
        {"ply": 4, "white_cp": 60},
        {"ply": 37, "white_cp": -120, "mate_white": -3},
        {"ply": 70, "white_cp": 15},
    ], [])
    graph.full_ply_count = 80
    assert ReviewGraph._continuous_segments(graph._values()) == []
    qtbot.addWidget(graph)
    graph.resize(700, 190)
    graph.show()
    graph.setFocus()
    selected = []
    graph.selected_ply.connect(selected.append)
    qtbot.keyClick(graph, Qt.Key.Key_End)
    assert selected == [80]

    # A click near a missing ply selects an actual stored assessment, never a
    # fabricated unscored ply. Right-side axis remains the actual game length.
    rect = graph.rect().adjusted(42, 12, -16, -30)
    target_x = rect.left() + round(rect.width() * 55 / 80)
    QTest.mouseClick(graph, Qt.MouseButton.LeftButton, pos=__import__(
        "PySide6.QtCore", fromlist=["QPoint"]).QPoint(target_x, rect.center().y()))
    assert selected[-1] in {37, 70}

    QTest.mouseMove(graph, __import__("PySide6.QtCore", fromlist=["QPoint"]).QPoint(
        target_x, rect.center().y()))
    assert "Охват:" in graph.toolTip()
    assert "не интерполируются" in graph.toolTip()


def test_review_events_and_saved_pv_follow_selection_and_fair_play_gate(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-events.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert [workspace.tabs.tabText(i) for i in range(workspace.tabs.count())] == [
            "Ходы", "Разбор", "События", "Заметки"]
        workspace.select_ply(1)
        assert workspace.review_board._hint_arrows == {(chess.C7, chess.C5)}
        assert "Сохранённое продолжение: c5 Nf3" in workspace.event_detail.text()
        workspace.set_safety_pending("Активная человеческая партия")
        assert workspace.review_board._hint_arrows == set()
        assert "Активная человеческая партия" in workspace.selected_move_summary.text()
        assert workspace.moves_list.item(0).text() == "e4"
        assert workspace.event_list.count() == 0
        assert "Активная человеческая партия" in workspace.event_detail.text()
        assert "Сохранённый PV" not in workspace.event_detail.text()
    finally:
        repo.shutdown()


def test_phase_summary_shows_both_sides_coverage_and_never_fabricates_accuracy(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-phases.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert workspace.side_metrics_summary.rowCount() == 7
        assert [workspace.side_metrics_summary.horizontalHeaderItem(i).text()
                for i in range(3)] == ["Показатель", "Я", "Соперник"]
        assert "Зевки" in _metrics_text(workspace.side_metrics_summary)
        assert "н/д" in _metrics_text(workspace.side_metrics_summary)
        assert "S · 4/4" in workspace.phase_summary.text()
        assert "Stockfish" in workspace.phase_summary.toolTip()
        workspace.select_ply(2)
        assert workspace.phase_selector.currentData() == "opening"
        assert "Зевки" in _metrics_text(workspace.side_metrics_summary)
        assert "1" in _metrics_text(workspace.side_metrics_summary)
        assert workspace.side_metrics_summary.item(0, 1).text() == "н/д"
        assert "дебют" in workspace.phase_summary.toolTip()
    finally:
        repo.shutdown()


@pytest.mark.parametrize("transition", [
    "pending", "revoke", "denial", "reconnect", "human-gameStart",
])
def test_fair_play_revocation_masks_every_rendered_analysis_surface(
        qtbot, tmp_path, transition):
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QLabel, QTableWidget, QToolTip

    repo, game = _review_repo(tmp_path / f"review-mask-{transition}.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        workspace.select_ply(2)
        workspace.show()
        QCoreApplication.processEvents()

        assert "1" in _metrics_text(workspace.side_metrics_summary)
        assert "+2.5" in workspace.moves_list.item_by_ply(2).text()
        assert workspace.graph._values()
        assert workspace.review_board._hint_arrows
        assert "Сохранённое продолжение" in workspace.event_detail.text()
        assert workspace.side_metrics_summary.rowCount() == 7

        reason = {
            "pending": "Проверяем активные партии; оценки скрыты.",
            "revoke": "Разрешение Fair Play отозвано.",
            "denial": "Обнаружена человеческая партия.",
            "reconnect": "Переподключение к Lichess.",
            "human-gameStart": "Lichess сообщил о партии с человеком.",
        }[transition]
        if transition in {"pending", "reconnect"}:
            workspace.set_safety_pending(reason)
        elif transition == "revoke":
            workspace.revoke_safety(reason)
        else:
            workspace.set_training_safety_result(False, reason)

        QCoreApplication.processEvents()
        screenshot = workspace.grab()
        assert not screenshot.isNull()
        visible_strings = []
        for label in workspace.findChildren(QLabel):
            visible_strings.extend((label.text(), label.toolTip(),
                                    label.accessibleName(), label.accessibleDescription()))
        for widget in workspace.findChildren(QTableWidget):
            visible_strings.extend((widget.toolTip(), widget.accessibleName(),
                                    widget.accessibleDescription()))
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        visible_strings.extend((item.text(), item.toolTip(),
                                                str(item.data(Qt.ItemDataRole.AccessibleTextRole) or ""),
                                                str(item.data(Qt.ItemDataRole.AccessibleDescriptionRole) or "")))
        visible_strings.extend((workspace.graph.toolTip(), workspace.graph.accessibleName(),
                                workspace.graph.accessibleDescription(), QToolTip.text()))
        rendered = "\n".join(value for value in visible_strings if value)
        for secret in ("+2.5", "ошибок 1", "зевков 1", "неточн. 0",
                       "Сохранённое продолжение", "Сохранённые варианты", "Зевок",
                       "охват 1/", "Stockfish", "phase coverage"):
            assert secret.casefold() not in rendered.casefold(), (transition, secret, rendered)

        assert workspace.phase_summary.text() == reason
        assert workspace.side_metrics_summary.rowCount() == 0
        assert workspace.graph._values() == []
        assert workspace.graph.assessments == []
        assert workspace.graph.markers == {}
        assert workspace.moves_list.item_by_ply(2).text() == "e5"
        assert not workspace.advantage_bar.isVisible()
        assert workspace.advantage_label.text() == "Оценки скрыты"
        assert workspace.review_board._hint_arrows == set()
        assert workspace.event_list.count() == 0

        # A stale/denied result leaves the surfaces masked; only a new accepted
        # complete preflight can repopulate the view from persisted assessments.
        if transition == "pending":
            workspace.set_training_safety_result(False, reason)
            QCoreApplication.processEvents()
            assert workspace.phase_summary.text() == reason
            workspace.set_training_safety_result(True)
            QCoreApplication.processEvents()
            assert "1" in _metrics_text(workspace.side_metrics_summary)
            assert workspace.graph._values()
    finally:
        repo.shutdown()


def test_review_layout_has_no_outer_scroll_and_renders_at_desktop_sizes(qtbot, tmp_path):
    from PySide6.QtCore import QCoreApplication, QPoint
    from PySide6.QtWidgets import QScrollArea

    repo, game = _review_repo(tmp_path / "review-layout.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assert not workspace.findChildren(QScrollArea)
        for width, height in ((1024, 700), (1280, 720), (1360, 860), (1920, 1080)):
            workspace.setFixedSize(width, height)
            workspace.show()
            qtbot.wait(10)
            assert workspace.review_board.width() > 0
            assert workspace.moves_list.width() > 0
            assert workspace.graph.width() > 0
            shot = workspace.grab()
            assert shot.width() == width and shot.height() == height
            QCoreApplication.processEvents()
            board_rect = workspace.review_board.geometry()
            board_x = workspace.review_board.mapTo(workspace, QPoint(0, 0)).x()
            table_x = workspace.moves_list.mapTo(workspace, QPoint(0, 0)).x()
            assert abs(board_rect.width() - board_rect.height()) <= 1
            assert workspace.board_column.width() - board_rect.width() <= 24
            assert table_x >= board_x + board_rect.width()
            assert table_x - (board_x + board_rect.width()) <= 24
            assert workspace.moves_list.table.horizontalScrollBar().maximum() == 0
            metric_headers = workspace.side_metrics_summary.horizontalHeader()
            for column in range(workspace.side_metrics_summary.columnCount()):
                label = workspace.side_metrics_summary.horizontalHeaderItem(column).text()
                assert workspace.side_metrics_summary.fontMetrics().horizontalAdvance(label) <= \
                    metric_headers.sectionSize(column)
            assert workspace.left_metrics.width() >= 360
            assert workspace.selected_move_summary.maximumHeight() <= 62
            for row in range(workspace.moves_list.table.rowCount()):
                for column in (1, 2):
                    item = workspace.moves_list.table.item(row, column)
                    if item and item.text():
                        assert workspace.moves_list.table.fontMetrics().horizontalAdvance(
                            item.text()) <= workspace.moves_list.table.columnWidth(column)
        assert workspace.workspace_splitter.orientation().name == "Horizontal"
    finally:
        repo.shutdown()


def test_board_and_move_table_wheel_keep_position_score_and_notation_synchronized(qtbot, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    repo, game = _review_repo(tmp_path / "review-wheel.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        def wheel(widget, delta):
            event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                                QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate,
                                False)
            widget.wheelEvent(event)
            assert event.isAccepted()
        wheel(workspace.review_board, -120)
        assert workspace.selected_ply == 1
        assert workspace.review_board._board.fen() == workspace.review["positions"][1].fen()
        wheel(workspace.moves_list.table, -120)
        assert workspace.selected_ply == 2
        assert workspace.moves_list.table.currentItem().data(Qt.ItemDataRole.UserRole) == 2
        assert workspace.advantage_bar.white_cp == 250
        assert workspace.graph.current_ply == 2
        wheel(workspace.review_board, 120)
        assert workspace.selected_ply == 1
        workspace.select_ply(3)
        wheel(workspace.moves_list.table, -120)
        assert workspace.selected_ply == 3  # no wrap past the final ply
        workspace.select_ply(0)
        wheel(workspace.review_board, 120)
        assert workspace.selected_ply == 0  # no wrap before the initial position
    finally:
        repo.shutdown()


def test_review_graph_delta_omits_missing_and_mate_neighbors_and_markers_default_off(qtbot):
    graph = ReviewGraph([
        {"ply": 0, "white_cp": 0},
        {"ply": 1, "white_cp": 100},
        {"ply": 2, "white_cp": 100, "mate_white": 3},
        {"ply": 3, "white_cp": 200},
        {"ply": 5, "white_cp": 250},
    ], [{"ply": 1, "severity": "blunder"}])
    qtbot.addWidget(graph)
    assert graph.markers_visible is False
    graph.set_delta_mode(True)
    assert graph._values() == [(1, 100, None)]
    graph.set_delta_mode(False)
    assert (2, 0, 3) in graph._values()  # mate is a terminal marker, not centipawns


def test_review_graph_scales_large_scores_and_delta_bars_inside_plot(qtbot):
    from PySide6.QtCore import QPointF

    assessments = [{"ply": ply, "white_cp": 20 if ply % 2 else -25}
                   for ply in range(40)]
    assessments.extend((
        {"ply": 40, "white_cp": 200}, {"ply": 41, "white_cp": -200},
        {"ply": 42, "white_cp": 1500}, {"ply": 43, "white_cp": -1500},
        {"ply": 44, "white_cp": None, "mate_white": 3},
        {"ply": 45, "white_cp": None},
    ))
    graph = ReviewGraph(assessments, [])
    graph.full_ply_count = 45
    qtbot.addWidget(graph)
    graph.resize(900, 240)
    graph.show()
    screenshot = graph.grab()
    assert not screenshot.isNull()
    rect = graph._last_plot_rect
    assert rect is not None
    assert graph._nice_axis_limit([20, -200, 1500]) == 1500
    assert graph._last_rendered_points
    assert all(rect.left() <= x <= rect.right() and rect.top() <= y <= rect.bottom()
               for _ply, x, y, _clipped in graph._last_rendered_points)
    assert any(clipped for _ply, _x, _y, clipped in graph._last_rendered_points)
    assert (44, 0, 3) in graph._values()  # terminal marker is not included in the scale

    outlier_x = rect.left() + round(rect.width() * 42 / graph.full_ply_count)
    class MoveEvent:
        def position(self): return QPointF(outlier_x, rect.center().y())
    graph.mouseMoveEvent(MoveEvent())
    assert "+15.00" in graph.toolTip()

    graph.assessments = [
        {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 20},
        {"ply": 2, "white_cp": -180}, {"ply": 3, "white_cp": 1320},
        {"ply": 4, "white_cp": None, "mate_white": -2},
    ]
    graph.full_ply_count = 4
    graph.set_delta_mode(True)
    assert graph._values() == [(1, 20, None), (2, -200, None), (3, 1500, None)]
    graph.grab()
    rect = graph._last_plot_rect
    assert all(rect.left() <= x <= rect.right() and rect.top() <= y <= rect.bottom()
               for _ply, x, y, _clipped in graph._last_rendered_points)
    assert not any(mate is not None for _ply, _score, mate in graph._values())


def test_review_advantage_bar_uses_mate_status_without_centipawn_fill(qtbot, tmp_path):
    repo, game = _review_repo(tmp_path / "review-mate-bar.sqlite3")
    try:
        workspace = GameReviewWorkspace(repo, training_allowed=True)
        qtbot.addWidget(workspace)
        workspace.load_game(game)
        assessment = next(row for row in workspace.review["analysis"]["assessments"]
                          if int(row["ply"]) == 3)
        assessment["white_cp"] = 180
        assessment["mate_white"] = 3
        workspace.select_ply(3)
        assert workspace.advantage_bar.white_cp is None
        assert "белые ставят мат через 3" in workspace.advantage_label.text()
    finally:
        repo.shutdown()
