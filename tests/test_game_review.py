import chess

from chess_coach.ui.game_review import GameReviewWorkspace, ReviewGraph
from chess_coach.lichess.game_review import (
    ACCEPTANCE_POLICY_VERSION, eligible_error_moves, load_game_review,
    verified_improved_moves,
)
from chess_coach.lichess.repository import GameRepository


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
        workspace.moves_list.setCurrentRow(2)
        assert workspace.selected_ply == 2
        assert workspace.review_board._board.fen() == workspace.review["positions"][2].fen()

        workspace.start_training()
        assert workspace.training_mode
        assert not workspace.graph.isVisible()
        assert not workspace.tabs.isVisible()
        assert workspace.review_board._board.fen() == workspace.review["moves"][1].before_fen
        workspace._training_move_intent(chess.Move.from_uci("c7c5"))
        assert workspace.check_button.isEnabled()
        workspace.check_button.click()
        assert "Найден улучшенный ход" in workspace.training_status.text()
        assert repo.export_pgn(game["game_id"]) == before_pgn
        workspace.continue_button.click()
        assert not workspace.training_mode
        assert "проверено 1, улучшено 1" in workspace.training_status.text()
        assert workspace.review_board._board.fen() == workspace.review["positions"][2].fen()
        assert repo.export_pgn(game["game_id"]) == before_pgn
    finally:
        repo.shutdown()


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
        assert "Сохранённый PV: c7c5 g1f3" in workspace.event_detail.text()
        workspace.set_safety_pending("Активная человеческая партия")
        assert workspace.event_list.count() == 0
        assert "Активная человеческая партия" in workspace.event_detail.text()
        assert "Сохранённый PV" not in workspace.event_detail.text()
    finally:
        repo.shutdown()
