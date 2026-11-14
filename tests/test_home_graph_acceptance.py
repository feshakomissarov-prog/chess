from __future__ import annotations

import math

import chess
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip

from chess_coach.application.dashboard import _home_graph_points
from chess_coach.lichess.phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries
from chess_coach.ui.pages.home import HomeEvaluationGraph, PuzzleRatingSparkline


def _scores(first: int, last: int) -> list[dict]:
    return [
        {"ply": ply,
         "white_cp": round(420 * math.sin(ply / 50) + 90 * math.cos(ply / 13)),
         "mate_white": None}
        for ply in range(first, last + 1)
    ]


def _paint(qtbot, graph: HomeEvaluationGraph, width: int = 760) -> None:
    qtbot.addWidget(graph)
    graph.resize(width, 190)
    graph.show()
    graph.repaint()
    qtbot.wait(10)
    graph.repaint()


def test_long_saved_game_keeps_all_600_consecutive_plies_and_tooltip_ply(qtbot):
    points = _home_graph_points(_scores(1, 600))
    assert len(points) == 600
    assert points[0]["connect_from_previous"] is False
    assert all(point["connect_from_previous"] for point in points[1:])

    graph = HomeEvaluationGraph(points, total_plies=600)
    _paint(qtbot, graph)
    assert len(graph._drawn_cp_segments) == 599
    assert graph._drawn_cp_segments[0] == (1, 2)
    assert graph._drawn_cp_segments[-1] == (599, 600)
    assert graph._hit_targets[-1][2] == 600
    assert "Полуход 600 (ход 300)" in graph._hit_targets[-1][3]
    assert "Чёрные +" in graph._hit_targets[-1][3]

    graph.resize(420, 190)
    graph.repaint()
    assert graph._hit_targets[-1][2] == 600
    assert "Полуход 600 (ход 300)" in graph._hit_targets[-1][3]


def test_graph_hover_reports_original_ply_after_resize(qtbot):
    graph = HomeEvaluationGraph(_home_graph_points(_scores(1, 600)), total_plies=600)
    _paint(qtbot, graph)
    for width in (760, 420):
        graph.resize(width, 190)
        graph.repaint()
        qtbot.wait(10)
        targets = {target[2]: target for target in graph._hit_targets}
        for ply in (1, 301, 600):
            target = targets[ply]
            QToolTip.hideText()
            QTest.mouseMove(graph, QPoint(round(target[0]), round(target[1])))
            qtbot.waitUntil(lambda: QToolTip.text() == target[3], timeout=500)
            assert f"Полуход {ply} " in QToolTip.text()
            assert f"Ход {round(target[0])}" not in QToolTip.text()
    QToolTip.hideText()


def test_bounded_sampling_keeps_long_run_continuous_and_preserves_extrema():
    assessments = _scores(1, 2400)
    for row in assessments:
        if 1100 <= row["ply"] <= 1110:
            row["white_cp"] = 2500

    points = _home_graph_points(assessments)

    assert len(points) <= 1024
    assert points[0]["ply"] == 1
    assert points[-1]["ply"] == 2400
    assert max(point["white_cp"] for point in points) == 2500
    assert all(point["connect_from_previous"] for point in points[1:])


def test_long_game_preserves_real_evaluation_gap_without_bridging(qtbot):
    assessments = [row for row in _scores(1, 600)
                   if not 281 <= row["ply"] <= 300]
    points = _home_graph_points(assessments)
    assert len(points) == 580
    assert points[280]["ply"] == 301
    assert points[280]["connect_from_previous"] is False
    assert points[281]["connect_from_previous"] is True

    graph = HomeEvaluationGraph(points, total_plies=600)
    _paint(qtbot, graph)
    assert len(graph._drawn_cp_segments) == 578
    assert not any(start <= 280 < end for start, end in graph._drawn_cp_segments)
    assert (300, 301) not in graph._drawn_cp_segments


def test_mate_and_out_of_axis_curve_keeps_exact_values_without_edge_markers(qtbot):
    assessments = [row for row in _scores(1, 600)
                   if not 350 <= row["ply"] <= 369]
    assessments.extend({"ply": ply, "white_cp": None, "mate_white": 25 - (ply - 350)}
                       for ply in range(350, 370))
    points = _home_graph_points(assessments)
    mate_points = [point for point in points if point.get("mate_white") is not None]
    assert len(mate_points) == 1
    assert mate_points[0]["ply"] == 369
    assert mate_points[0]["mate_start_ply"] == 350
    assert mate_points[0]["mate_sample_count"] == 20

    graph = HomeEvaluationGraph(points, total_plies=600)
    _paint(qtbot, graph)
    assert len(graph._mate_markers) == 1
    assert graph._mate_markers[0] == (369, 6, 350, 20)
    assert "Полуход 369 (ход 185)" in graph._hit_targets[-1][3] or any(
        target[2] == 369 and "Полуход 369 (ход 185)" in target[3]
        for target in graph._hit_targets)
    assert "350–369 (20 позиций)" in next(
        target[3] for target in graph._hit_targets if target[2] == 369)
    mate_target = next(target for target in graph._hit_targets if target[2] == 369)
    assert "M+" not in mate_target[3]
    assert "Мат: белые через 6" in mate_target[3]
    graph.setFocus()
    mate_index = next(index for index, target in enumerate(graph._hit_targets)
                      if target[2] == 369)
    qtbot.keyPress(graph, Qt.Key.Key_Home)
    for _ in range(mate_index):
        qtbot.keyPress(graph, Qt.Key.Key_Right)
    assert "Мат: белые через 6" in graph.accessibleDescription()

    clipped_rows = [row for row in assessments if row["mate_white"] is None]
    clipped_rows = [dict(row, white_cp=800 if 40 <= row["ply"] <= 59 else
                         -900 if 500 <= row["ply"] <= 519 else row["white_cp"])
                    for row in clipped_rows]
    clipped_graph = HomeEvaluationGraph(
        _home_graph_points(clipped_rows), total_plies=600)
    _paint(qtbot, clipped_graph)
    assert clipped_graph._clipped_markers == []
    assert {ply: cp for ply, cp, *_ in clipped_graph._raw_cp_points}[40] == 800
    assert {ply: cp for ply, cp, *_ in clipped_graph._raw_cp_points}[500] == -900
    assert all(clipped_graph._plot_rect().top() <= target[1] <=
               clipped_graph._plot_rect().bottom()
               for target in clipped_graph._hit_targets)


def test_off_axis_curve_is_clipped_without_plateau_gap_bridge_or_marker(qtbot):
    rows = [
        {"ply": 1, "white_cp": 800, "mate_white": None},
        {"ply": 2, "white_cp": 900, "mate_white": None},
        {"ply": 3, "white_cp": 1200, "mate_white": None},
        {"ply": 4, "white_cp": 200, "mate_white": None},
        {"ply": 5, "white_cp": -900, "mate_white": None},
        {"ply": 6, "white_cp": -900, "mate_white": None},
        {"ply": 7, "white_cp": -100, "mate_white": None},
        # Missing plies 8–10 form a genuine gap.
        {"ply": 11, "white_cp": 1200, "mate_white": None},
        {"ply": 12, "white_cp": 200, "mate_white": None},
        {"ply": 13, "white_cp": None, "mate_white": 4},
        {"ply": 14, "white_cp": -900, "mate_white": None},
        {"ply": 15, "white_cp": -200, "mate_white": None},
    ]
    graph = HomeEvaluationGraph(_home_graph_points(rows), total_plies=15)
    _paint(qtbot, graph, width=700)
    plot = graph._plot_rect()

    raw = {ply: (cp, y, tooltip) for ply, cp, _x, y, tooltip in graph._raw_cp_points}
    assert raw[1][0] == 800 and raw[3][0] == 1200
    assert raw[5][0] == -900 and raw[14][0] == -900
    assert raw[1][1] < plot.top() and raw[5][1] > plot.bottom()
    assert "+8.00 пешки" in raw[1][2]
    assert graph._clipped_markers == []
    assert (3, 4) in graph._drawn_cp_segments
    assert (11, 12) in graph._drawn_cp_segments
    assert (7, 11) not in graph._drawn_cp_segments
    assert (12, 14) not in graph._drawn_cp_segments  # mate breaks the CP line

    # Render the real widget. Off-axis runs wholly beyond the viewport have no
    # colored plateau; only the returning/entering segments can touch the clip.
    image = QImage(graph.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    graph.render(painter, QPoint())
    painter.end()
    outside = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            vivid = (color.green() > color.red() * 1.35 and color.green() > 120) or (
                color.red() > color.green() * 1.35 and color.red() > 120)
            if vivid and not (plot.left() <= x <= plot.right() and
                              plot.top() <= y <= plot.bottom()):
                outside.append((x, y))
    assert outside == []
    first_offscreen_x = next(x for ply, _cp, x, _y, _tip in graph._raw_cp_points
                             if ply == 3)
    assert not any(
        (lambda color: color.green() > color.red() * 1.35 and color.green() > 120)(
            image.pixelColor(x, y))
        for x in range(max(plot.left(), first_offscreen_x - 2),
                       min(plot.right(), first_offscreen_x + 3) + 1)
        for y in range(plot.top(), min(plot.bottom(), plot.top() + 4) + 1))


def test_black_mate_and_terminal_mate_zero_do_not_change_white_perspective(qtbot):
    assessments = [row for row in _scores(1, 60)
                   if not 20 <= row["ply"] <= 34 and row["ply"] != 40]
    assessments.extend({"ply": ply, "white_cp": None, "mate_white": -3}
                       for ply in range(20, 35))
    assessments.append({"ply": 40, "white_cp": None, "mate_white": 0})
    graph = HomeEvaluationGraph(_home_graph_points(assessments), total_plies=60)
    _paint(qtbot, graph)

    assert graph._mate_markers == [(34, -3, 20, 15), (40, 0, 40, 1)]
    black_tooltip = next(target[3] for target in graph._hit_targets if target[2] == 34)
    terminal_tooltip = next(target[3] for target in graph._hit_targets if target[2] == 40)
    assert "Мат: чёрные через 3" in black_tooltip
    assert "сторона не выводится" in terminal_tooltip


def test_graph_uses_only_source_backed_phases_and_never_invents_endgame(qtbot):
    # This 19-move Ruy Lopez line reaches the source-backed opening/middlegame
    # boundary, but its material never qualifies as an endgame.
    sans = "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O Re1 a6 Bb3 Ba7 " \
        "Nbd2 h6 Nf1 Re8 Ng3 Be6 Bc2 Qd7 Be3 Bxe3 Rxe3 d5 exd5 Nxd5 " \
        "Re1 Nf4 d4 exd4 cxd4"
    board = chess.Board()
    boards = [board.copy(stack=False)]
    for san in sans.split():
        board.push_san(san)
        boards.append(board.copy(stack=False))
    # Add three quiet, legal knight plies so the completed miniature is exactly
    # 19 moves. No material change means the source-backed phase result remains
    # a two-stage game rather than gaining a fabricated endgame.
    for _ in range(3):
        move = next(move for move in board.legal_moves
                    if board.piece_at(move.from_square).piece_type == chess.KNIGHT
                    and not board.is_capture(move))
        board.push(move)
        boards.append(board.copy(stack=False))
    assert len(boards) - 1 == 38
    middle, end = infer_phase_boundaries(boards)
    assert middle is not None and end is None

    trusted = {"middle_start_ply": middle, "end_start_ply": end,
               "source": PHASE_FALLBACK_VERSION,
               "method_version": PHASE_FALLBACK_VERSION}
    graph = HomeEvaluationGraph(_home_graph_points(_scores(1, len(boards) - 1)),
                                total_plies=len(boards) - 1, phases=trusted)
    _paint(qtbot, graph)
    assert [label for _ply, label in graph._phase_labels] == ["Дебют", "Миттельшпиль"]
    assert "Эндшпиль" not in {label for _ply, label in graph._phase_labels}

    unknown = HomeEvaluationGraph(_home_graph_points(_scores(1, 38)), total_plies=38,
                                  phases={"middle_start_ply": 6, "end_start_ply": 30})
    _paint(qtbot, unknown)
    assert [label for _ply, label in unknown._phase_labels] == ["Начало", "Конец"]
    assert not unknown._phase_marks


def test_two_source_backed_boundaries_produce_exactly_three_real_phases(qtbot):
    phases = {"middle_start_ply": 18, "end_start_ply": 44,
              "source": "material-fallback", "method_version": "v1"}
    graph = HomeEvaluationGraph(_home_graph_points(_scores(1, 80)),
                                total_plies=80, phases=phases)
    _paint(qtbot, graph)
    assert [label for _ply, label in graph._phase_labels] == [
        "Дебют", "Миттельшпиль", "Эндшпиль"]
    assert graph._phase_marks == [18, 44]


def test_explicit_unscored_position_breaks_cp_line_even_if_neighbors_are_adjacent():
    points = _home_graph_points([
        {"ply": 1, "white_cp": 20},
        {"ply": 2, "white_cp": None, "mate_white": None},
        {"ply": 3, "white_cp": -20},
    ])
    assert [point["ply"] for point in points] == [1, 3]
    assert points[0]["connect_from_previous"] is False
    assert points[1]["connect_from_previous"] is False
