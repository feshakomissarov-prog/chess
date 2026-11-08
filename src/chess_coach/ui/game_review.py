"""Unified completed-game review workspace and ephemeral mistake exercise."""

from __future__ import annotations

import chess
import math
from PySide6.QtCore import QPointF, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QSplitter, QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..board_view import BoardView
from ..lichess.game_review import (
    eligible_error_moves, load_game_review, verified_improved_moves,
)
from ..domain.accuracy import win_percent_cp
from .display import (
    evaluation_label, format_clock_control, format_game_timeline, game_opening_name,
    opening_tooltip, phase_label,
    severity_label, source_short,
)
from .move_table import MoveTable
from .theme import TOKENS


class AdvantageBar(QWidget):
    """Signed evaluation display; explicitly not a win-probability meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.white_cp: int | None = None
        self.mate_white: int | None = None
        self.terminal_checkmate = False
        self.terminal_winner_white: bool | None = None
        self.allowed = False
        self.setMinimumWidth(24)
        self.setMaximumWidth(30)
        self.setToolTip("Оценка позиции за белых; цветная часть не является вероятностью победы.")

    def set_evaluation(self, white_cp: int | None, *, allowed: bool,
                       mate_white: int | None = None,
                       terminal_checkmate: bool = False,
                       terminal_winner_white: bool | None = None):
        self.white_cp = None if white_cp is None else int(white_cp)
        self.mate_white = None if mate_white is None else int(mate_white)
        self.terminal_checkmate = bool(terminal_checkmate)
        self.terminal_winner_white = terminal_winner_white
        self.allowed = bool(allowed)
        if self.allowed and self.mate_white is not None:
            if self.mate_white == 0 and not self.terminal_checkmate:
                self.setToolTip("Противоречивая оценка Mate(0): позиция не подтверждена как мат.")
            else:
                side = ("белые" if self.mate_white > 0 else "чёрные" if self.mate_white < 0
                        else "победитель по терминальной доске")
                self.setToolTip(
                    f"Форсированный мат стороне {side}; маркер показывает сторону и Win%, "
                    "не фиктивную оценку в сантипешках.")
        else:
            self.setToolTip(
                "Оценка позиции за белых; цветная часть не является вероятностью победы.")
        self.setVisible(self.allowed)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(3, 4, -3, -4)
        painter.setPen(QPen(QColor(TOKENS.border), 1))
        painter.setBrush(QColor(TOKENS.surface))
        painter.drawRoundedRect(rect, 4, 4)
        center = rect.center().y()
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawLine(rect.left() + 2, center, rect.right() - 2, center)
        if not self.allowed or (self.white_cp is None and self.mate_white is None):
            return
        if self.mate_white is not None:
            if self.mate_white == 0 and not self.terminal_checkmate:
                return
            white_wins = (self.mate_white > 0 if self.mate_white != 0
                          else self.terminal_winner_white is True)
            win_percent = win_percent_cp(1000 if white_wins else -1000)
            magnitude = abs(win_percent - 50.0) / 50.0
            sign = 1 if white_wins else -1
        else:
            magnitude = min(600, abs(self.white_cp)) / 600
            sign = 1 if self.white_cp > 0 else -1
        extent = round((rect.height() / 2 - 3) * magnitude)
        color = QColor(TOKENS.success if sign > 0 else TOKENS.danger)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if sign > 0:
            painter.drawRoundedRect(rect.left() + 4, center - extent,
                                    rect.width() - 8, extent, 3, 3)
        else:
            painter.drawRoundedRect(rect.left() + 4, center,
                                    rect.width() - 8, extent, 3, 3)


class ReviewGraph(QWidget):
    selected_ply = Signal(int)

    def __init__(self, assessments: list[dict], metrics: list[dict], parent=None):
        super().__init__(parent)
        self.assessments = assessments
        self.markers = {int(row["ply"]): row for row in metrics}
        self.safety_allowed = True
        self.safety_block_reason = ""
        self.current_ply = 0
        self.full_ply_count = 0
        self.analysis_source = ""
        self.analysis_status = ""
        self.empty_title = "Оценка отсутствует"
        self.empty_detail = "Здесь появится график оценки после запуска анализа Stockfish."
        self.delta_mode = False
        self.compact = False
        self.markers_visible = False
        self.coverage_text = ""
        self.move_san: dict[int, str] = {}
        self.mate_outcome_signs: dict[int, int] = {}
        self.setMinimumHeight(170)
        self.setMaximumHeight(220)
        self.setMinimumWidth(320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Интерактивный график оценки по ходам")
        self.setAccessibleDescription(
            "Горизонтальная ось — номер полухода. Вертикальная — оценка позиции или изменение "
            "оценки за белых. Шкала подбирается по данным; выбросы отмечаются на границе. "
            "Стрелки влево и вправо перемещают выбор между оценёнными позициями.")
        self._last_plot_rect = None
        self._last_rendered_points: list[tuple[int, int, int, bool]] = []

    def _values(self):
        if not self.safety_allowed:
            return []
        if self.delta_mode:
            by_ply = {int(row["ply"]): row for row in self.assessments}
            return [
                (ply, int(row["white_cp"]) - int(by_ply[ply - 1]["white_cp"]), None)
                for ply, row in sorted(by_ply.items())
                if ply > 0 and ply - 1 in by_ply
                and row.get("white_cp") is not None
                and by_ply[ply - 1].get("white_cp") is not None
                and row.get("mate_white") is None
                and by_ply[ply - 1].get("mate_white") is None
            ]
        output = []
        for row in self.assessments:
            score = row.get("white_cp")
            mate = row.get("mate_white")
            if mate is not None:
                # Mate has a separate marker position and label; never encode it
                # as a synthetic centipawn value on the axis.
                score = 0
            if score is not None:
                output.append((int(row["ply"]), int(score),
                               int(mate) if mate is not None else None))
        return output

    @staticmethod
    def _nice_axis_limit(values) -> int:
        magnitudes = sorted(abs(int(value)) for value in values if value is not None)
        if not magnitudes:
            return 100
        # Use a high percentile so one huge swing does not flatten every normal
        # move. Values outside that range remain visible as clipped edge marks.
        index = max(0, math.ceil(len(magnitudes) * 0.95) - 1)
        target = max(25, magnitudes[index])
        steps = (25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000,
                 3000, 5000, 7500, 10000, 15000, 20000, 30000, 50000, 75000,
                 100000, 150000, 200000, 300000, 500000, 750000, 1000000)
        return next((step for step in steps if step >= target),
                    int(math.ceil(target / 1_000_000) * 1_000_000))

    def plot_rect(self):
        if self.compact:
            return self.rect().adjusted(42, 6, -12, -20)
        return self.rect().adjusted(54, 18, -14, -31)

    @staticmethod
    def _score_y(rect, score: int, axis_limit: int) -> tuple[int, bool]:
        clipped = max(-axis_limit, min(axis_limit, int(score)))
        y = rect.center().y() - round(rect.height() * clipped / (2 * axis_limit))
        return max(rect.top(), min(rect.bottom(), y)), clipped != score

    @staticmethod
    def _continuous_segments(values):
        ordered = sorted(values, key=lambda row: int(row[0]))
        return [(first, second) for first, second in zip(ordered, ordered[1:])
                if int(second[0]) == int(first[0]) + 1
                and first[2] is None and second[2] is None]

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot_rect()
        self._last_plot_rect = rect
        self._last_rendered_points = []
        painter.fillRect(rect, QColor(TOKENS.surface))
        painter.setPen(QPen(QColor(TOKENS.border), 1))
        painter.drawRect(rect)
        values = self._values()
        if not values:
            painter.setPen(QPen(QColor(TOKENS.border), 1))
            painter.setBrush(QColor(TOKENS.surface))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 9, 9)
            painter.setPen(QColor(TOKENS.text))
            painter.drawText(self.rect().adjusted(24, 20, -24, -20),
                             Qt.AlignmentFlag.AlignCenter,
                             f"▥\n{self.empty_title}\n{self.empty_detail}")
            return
        max_ply = max(1, self.full_ply_count,
                      max((ply for ply, _score, _mate in values), default=0))
        numeric_values = [score for _ply, score, mate in values if mate is None]
        axis_limit = self._nice_axis_limit(numeric_values)
        axis_label = lambda value: ("−" if value < 0 else "+" if value > 0 else "") + \
            f"{abs(value) / 100:.2f}".rstrip("0").rstrip(".")
        phase_colors = {"opening": QColor(84, 132, 98, 25),
                        "middlegame": QColor(192, 141, 72, 22),
                        "endgame": QColor(86, 122, 167, 24)}
        phase_points = sorted((ply, row.get("phase")) for ply, row in self.markers.items()
                              if row.get("phase") in phase_colors)
        phase_segments = [(first, second) for first, second in zip(phase_points, phase_points[1:])
                          if first[1] == second[1] and second[0] == first[0] + 1]
        painter.save()
        painter.setClipRect(rect.adjusted(1, 1, -1, -1))
        for first, second in phase_segments:
            left = rect.left() + round(rect.width() * first[0] / max_ply)
            right = rect.left() + round(rect.width() * second[0] / max_ply)
            painter.fillRect(left, rect.top(), max(1, right - left), rect.height(),
                             phase_colors[first[1]])
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        half = axis_limit // 2
        for tick in (-axis_limit, -half, 0, half, axis_limit):
            y = rect.center().y() - round(rect.height() * tick / (2 * axis_limit))
            painter.setPen(QPen(QColor(TOKENS.text if tick == 0 else TOKENS.border),
                                1 if tick == 0 else 1,
                                Qt.PenStyle.SolidLine if tick == 0 else Qt.PenStyle.DotLine))
            painter.drawLine(rect.left(), y, rect.right(), y)
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawText(2, rect.top() + 5, axis_label(axis_limit))
        painter.drawText(18, rect.center().y() + 5, "0")
        painter.drawText(2, rect.bottom(), axis_label(-axis_limit))
        if not self.compact:
            painter.drawText(rect.left() + 3, rect.top() + 14,
                             "Δ оценки за белых" if self.delta_mode else "Оценка за белых")
            if not phase_segments:
                painter.drawText(rect.right() - 150, rect.top() + 14, "Фазы не определены")
        if not values:
            message = (self.safety_block_reason if not self.safety_allowed
                       else "Нет сохранённых оценок")
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        else:
            coords = []
            for ply, score, mate in values:
                x = rect.left() + round(rect.width() * ply / max_ply)
                if mate is not None:
                    outcome_sign = self.mate_outcome_signs.get(ply, int(mate))
                    y = (rect.top() if outcome_sign > 0 else rect.bottom()
                         if outcome_sign < 0 else rect.center().y())
                    clipped = False
                else:
                    y, clipped = self._score_y(rect, score, axis_limit)
                coords.append((x, y, ply, mate, score, clipped))
                self._last_rendered_points.append((ply, x, y, clipped))
            if self.delta_mode:
                baseline = rect.center().y()
                width = max(2, min(10, round(rect.width() / max(1, max_ply + 1) * 0.75)))
                for x, y, _ply, mate, _score, _clipped in coords:
                    if mate is not None:
                        continue
                    color = QColor(TOKENS.success if y < baseline else TOKENS.danger)
                    painter.setPen(QPen(color, 1))
                    painter.setBrush(color)
                    painter.drawRect(x - width // 2, min(baseline, y), width,
                                     max(1, abs(baseline - y)))
            else:
                coordinate_by_ply = {row[2]: row for row in coords}
                painter.setPen(QPen(QColor(TOKENS.accent), 2))
                for first, second in self._continuous_segments(values):
                    a, b = coordinate_by_ply[first[0]], coordinate_by_ply[second[0]]
                    if self.compact:
                        color = QColor("#43D98A" if a[4] + b[4] >= 0 else "#ED6A79")
                        shade = QColor(color)
                        shade.setAlpha(42)
                        baseline = rect.center().y()
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(shade)
                        painter.drawPolygon(QPolygonF([
                            QPointF(a[0], baseline), QPointF(a[0], a[1]),
                            QPointF(b[0], b[1]), QPointF(b[0], baseline),
                        ]))
                        painter.setPen(QPen(color, 2))
                    painter.drawLine(a[0], a[1], b[0], b[1])
            mate_label_plies: set[int] = set()
            mate_group: list[tuple[int, int]] = []
            mate_gap = max(4, int(math.ceil(max_ply / 80)))
            for _x, _y, ply, mate, _score, _clipped in coords + [(0, 0, -1, None, 0, False)]:
                if mate is not None and (not mate_group or
                        (ply - mate_group[-1][0] <= mate_gap and (mate > 0) == (mate_group[-1][1] > 0))):
                    mate_group.append((ply, mate))
                    continue
                if mate_group:
                    mate_label_plies.add(mate_group[-1][0])
                    mate_group = []
                if mate is not None:
                    mate_group.append((ply, mate))
            for x, y, ply, mate, score, clipped in coords:
                marker = self.markers.get(ply, {}) if self.markers_visible else {}
                severity = marker.get("severity")
                if severity in {"mistake", "blunder"}:
                    color = QColor(TOKENS.danger)
                elif severity == "inaccuracy":
                    color = QColor(TOKENS.warning)
                elif severity == "good":
                    color = QColor(TOKENS.success)
                elif severity == "strong":
                    color = QColor(TOKENS.info)
                else:
                    color = QColor("#858b93")
                painter.setBrush(color)
                painter.setPen(QPen(color, 1))
                if severity == "blunder":
                    painter.drawPolygon(QPolygonF([QPointF(x, y - 5), QPointF(x - 5, y + 4),
                                                  QPointF(x + 5, y + 4)]))
                elif severity == "mistake":
                    painter.drawRect(x - 4, y - 4, 8, 8)
                elif severity == "inaccuracy":
                    painter.drawPolygon(QPolygonF([QPointF(x, y - 4), QPointF(x + 4, y),
                                                  QPointF(x, y + 4), QPointF(x - 4, y)]))
                elif mate == 0:
                    painter.drawRect(x - 4, y - 4, 8, 8)
                else:
                    painter.drawEllipse(x - 3, y - 3, 6, 6)
                if mate is not None and ply in mate_label_plies:
                    mate_text = ("Мат в позиции" if mate == 0 else
                                 f"M{mate}" if mate > 0 else f"−M{abs(mate)}")
                    painter.drawText(x + 5, max(rect.top() + 12, y - 5), mate_text)
                elif clipped:
                    painter.drawPolygon(QPolygonF([QPointF(x, y - 5 if score > 0 else y + 5),
                                                  QPointF(x - 4, y), QPointF(x + 4, y)]))
            if any(row[5] for row in coords):
                painter.setPen(QPen(QColor(TOKENS.warning), 1))
                painter.drawText(rect.right() - 122, rect.top() + 14,
                                 "▲ за пределом шкалы")
        painter.restore()
        selected_x = rect.left() + round(rect.width() * self.current_ply / max_ply)
        painter.setPen(QPen(QColor(TOKENS.text), 1, Qt.PenStyle.DashLine))
        painter.drawLine(selected_x, rect.top(), selected_x, rect.bottom())
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawText(rect.left(), self.height() - 5, "Полуход 0")
        painter.drawText(rect.right() - 48, self.height() - 5, f"Ход {max_ply}")

    def select_ply(self, ply: int):
        self.current_ply = max(0, int(ply))
        self.update()

    def set_safety_gate(self, allowed: bool, reason: str = "") -> None:
        self.safety_allowed = bool(allowed)
        self.safety_block_reason = reason or "Оценки скрыты правилами Fair Play"
        self.update()

    def set_delta_mode(self, enabled: bool) -> None:
        self.delta_mode = bool(enabled)
        self.update()

    def set_markers_visible(self, enabled: bool) -> None:
        self.markers_visible = bool(enabled)
        self.update()

    def mousePressEvent(self, event):
        values = self._values()
        rect = self.plot_rect()
        max_ply = max(1, self.full_ply_count,
                      max((ply for ply, _score, _mate in values), default=0))
        requested = max(0, min(max_ply, round(
            (event.position().x() - rect.left()) * max_ply / max(1, rect.width()))))
        if values:
            requested = min((ply for ply, _score, _mate in values),
                            key=lambda ply: (abs(ply - requested), ply))
        self.selected_ply.emit(requested)

    def mouseMoveEvent(self, event):
        values = self._values()
        if not values:
            self.setToolTip(self.safety_block_reason if not self.safety_allowed
                            else "Нет сохранённых оценок")
            return
        rect = self.plot_rect()
        max_ply = max(1, self.full_ply_count,
                      max(ply for ply, _score, _mate in values))
        requested = round((event.position().x() - rect.left()) * max_ply /
                          max(1, rect.width()))
        ply, score, mate = min(values, key=lambda row: (abs(row[0] - requested), row[0]))
        assessment = next((row for row in self.assessments if int(row["ply"]) == ply), {})
        marker = self.markers.get(ply, {})
        coverage = self.coverage_text or f"{len(values)}/{self.full_ply_count + 1} сохранённых позиций"
        value = ("Мат в текущей позиции" if mate == 0 else
                 f"Мат {mate}" if mate is not None and mate > 0 else
                 f"−Мат {abs(mate)}" if mate is not None else
                 f"Δ {int(score) / 100:+.2f} за белых" if self.delta_mode else
                 evaluation_label(score))
        before = next((row for row in self.assessments
                       if int(row.get("ply", -1)) == max(0, ply - 1)), None)
        after = next((row for row in self.assessments
                      if int(row.get("ply", -1)) == ply), None)
        before_text = evaluation_label((before or {}).get("white_cp"),
                                       (before or {}).get("mate_white"))
        after_text = evaluation_label((after or {}).get("white_cp"),
                                      (after or {}).get("mate_white"))
        move_text = f"Ход: {self.move_san[ply]} · " if ply in self.move_san else ""
        self.setToolTip(
            f"{move_text}позиция после {ply} полуходов (ближайшая к {requested})\n"
            f"До: {before_text} · после: {after_text}\n"
            f"Оценка: {value} · источник {source_short(assessment.get('source') or self.analysis_source)[1]}\n"
            f"Статус: {assessment.get('status') or self.analysis_status or 'сохранённая оценка'} · "
            f"классификация: {severity_label(marker.get('severity'))}\n"
            f"Охват: {coverage}; отсутствующие позиции не интерполируются")

    def keyPressEvent(self, event):
        values = self._values()
        if not values:
            super().keyPressEvent(event)
            return
        max_ply = max(self.full_ply_count,
                      max((ply for ply, _score, _mate in values), default=0))
        if event.key() == Qt.Key.Key_Left:
            self.selected_ply.emit(max(0, self.current_ply - 1))
        elif event.key() == Qt.Key.Key_Right:
            self.selected_ply.emit(min(max_ply, self.current_ply + 1))
        elif event.key() == Qt.Key.Key_Home:
            self.selected_ply.emit(0)
        elif event.key() == Qt.Key.Key_End:
            self.selected_ply.emit(max_ply)
        else:
            super().keyPressEvent(event)


class GameReviewWorkspace(QWidget):
    position_selected = Signal(str, int)
    return_requested = Signal()
    export_requested = Signal(str)
    safety_requested = Signal(str)
    analysis_requested = Signal(str)
    open_lichess_game_requested = Signal(str)

    def __init__(self, repository, *, training_allowed: bool = False,
                 training_block_reason: str = "", settings=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.settings = settings or QSettings()
        self.training_allowed = training_allowed
        self.evaluation_allowed = training_allowed
        self.position_evaluations_available = False
        self.training_block_reason = training_block_reason
        self.review: dict | None = None
        self.selected_ply = 0
        self.training_mode = False
        self.training_moves = []
        self.training_index = 0
        self.training_checked = 0
        self.training_improved = 0
        self.training_skipped = 0
        self.training_unverified = 0
        self.training_position: chess.Board | None = None
        self.training_candidate: chess.Move | None = None
        self.training_proven: dict[str, dict] = {}
        self._current_training_improved = False
        self._ephemeral_board: chess.Board | None = None
        self._ephemeral_branch: list[chess.Move] = []
        self._ephemeral_undo: list[chess.Board] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(5)
        header = QHBoxLayout()
        self.title = QLabel("Разбор партии")
        self.title.setObjectName("reviewGameLine")
        self.title.setWordWrap(False)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title.setStyleSheet("font-size: 24px; font-weight: 700;")
        header.addWidget(self.title, 1)
        back = QPushButton("К партиям")
        back.clicked.connect(self.return_requested.emit)
        self.export_button = QPushButton("Экспорт PGN")
        self.export_button.clicked.connect(self._export)
        header.addWidget(back)
        header.addWidget(self.export_button)
        root.addLayout(header)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("secondary")
        self.subtitle.setWordWrap(False)
        root.addWidget(self.subtitle)

        split = QSplitter(Qt.Orientation.Horizontal)
        board_column = QWidget()
        board_layout = QVBoxLayout(board_column)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(0)
        self.board_column = board_column
        self.board_layout = board_layout
        self.training_prompt = QLabel()
        self.training_prompt.setObjectName("trainingPrompt")
        self.training_prompt.setWordWrap(True)
        self.training_prompt.setStyleSheet(
            "font-size: 14px; font-weight: 700; padding: 10px 12px; "
            "background: #24333c; border: 1px solid #3d5e68; border-radius: 8px;")
        self.training_prompt.setMaximumHeight(70)
        self.training_prompt.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.training_prompt.setVisible(False)
        board_layout.addWidget(self.training_prompt)
        self.ephemeral_controls = QWidget()
        ephemeral_layout = QHBoxLayout(self.ephemeral_controls)
        ephemeral_layout.setContentsMargins(0, 0, 0, 0)
        self.ephemeral_status = QLabel("Временная ветка.")
        self.ephemeral_status.setWordWrap(True)
        self.ephemeral_status.setToolTip(
            "Ходы исследования не попадут в PGN, SQLite или онлайн-партию.")
        self.ephemeral_undo_button = QPushButton("Отменить ход")
        self.ephemeral_undo_button.setToolTip("Отменить последний ход временной ветки.")
        self.ephemeral_reset_button = QPushButton("Сбросить ветку")
        self.ephemeral_reset_button.setToolTip("Сбросить временную ветку к выбранной позиции.")
        self.ephemeral_undo_button.clicked.connect(self.undo_ephemeral_move)
        self.ephemeral_reset_button.clicked.connect(self.reset_ephemeral_branch)
        ephemeral_layout.addWidget(self.ephemeral_status, 1)
        ephemeral_layout.addWidget(self.ephemeral_undo_button)
        ephemeral_layout.addWidget(self.ephemeral_reset_button)
        self.ephemeral_controls.setVisible(False)
        board_layout.addWidget(self.ephemeral_controls)
        self.white_name = QLabel("Белые")
        self.white_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.white_name.setVisible(False)
        self.review_board = BoardView(chess.Board())
        self.review_board.setAccessibleName("Доска просмотренной партии")
        self.review_board.setMinimumSize(0, 0)
        self.review_board.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.review_board.set_interaction_enabled(False)
        self.review_board.set_premove_allowed(False)
        self.review_board.set_annotations_enabled(False)
        self.review_board.move_intent.connect(self._review_board_move_intent)
        self.review_board.wheel_navigate.connect(self._navigate_wheel)
        self.black_name = QLabel("Чёрные")
        self.black_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.black_name.setVisible(False)
        board_layout.addWidget(self.white_name)
        board_layout.addWidget(self.review_board, 0,
                               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        board_layout.addWidget(self.black_name)

        self.left_metrics = QWidget()
        self.left_metrics.setMinimumWidth(360)
        self.left_metrics.setMaximumWidth(370)
        metrics_column = QVBoxLayout(self.left_metrics)
        metrics_column.setContentsMargins(2, 2, 4, 2)
        metrics_column.setSpacing(5)
        self.phase_selector = QComboBox()
        self.phase_selector.addItem("Вся партия", "all")
        self.phase_selector.addItem("Дебют", "opening")
        self.phase_selector.addItem("Миттельшпиль", "middlegame")
        self.phase_selector.addItem("Эндшпиль", "endgame")
        self.phase_selector.setAccessibleName("Выбор фазы статистики партии")
        self.phase_selector.currentIndexChanged.connect(self._phase_scope_changed)
        metrics_column.addWidget(self.phase_selector)
        self.side_metrics_summary = QTableWidget(0, 3)
        self.side_metrics_summary.setObjectName("reviewSideMetrics")
        self.side_metrics_summary.setHorizontalHeaderLabels(("Показатель", "Я", "Сопер."))
        self.side_metrics_summary.horizontalHeaderItem(2).setToolTip("Соперник")
        self.side_metrics_summary.setAccessibleName("Сравнительная статистика партии")
        self.side_metrics_summary.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.side_metrics_summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.side_metrics_summary.verticalHeader().setVisible(False)
        self.side_metrics_summary.setWordWrap(False)
        self.side_metrics_summary.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side_metrics_summary.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side_metrics_summary.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.side_metrics_summary.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.side_metrics_summary.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        metrics_column.addWidget(self.side_metrics_summary)
        self.left_metrics_label = QLabel()
        self.left_metrics_label.setWordWrap(True)
        self.left_metrics_label.setVisible(False)
        self.phase_summary = QLabel("Фазы: н/д")
        self.phase_summary.setWordWrap(True)
        self.phase_summary.setMinimumHeight(0)
        self.phase_summary.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Minimum)
        self.phase_summary.setToolTip(
            "Официальная Lichess accuracy доступна только для партии целиком. "
            "Фазовая точность не выводится без отдельной доказанной метрики.")
        metrics_column.addWidget(self.phase_summary)
        self.left_metrics_title = QLabel("Сводка по партии")
        self.left_metrics_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        metrics_column.insertWidget(0, self.left_metrics_title)
        self.no_analysis_summary = QFrame()
        self.no_analysis_summary.setObjectName("reviewEmptySummary")
        self.no_analysis_summary.setStyleSheet(
            "QFrame#reviewEmptySummary { background: #242642; border: 1px solid #3b3e62; "
            "border-radius: 10px; padding: 10px; }")
        empty_layout = QVBoxLayout(self.no_analysis_summary)
        empty_layout.setContentsMargins(10, 8, 10, 8)
        empty_layout.setSpacing(5)
        self.no_analysis_title = QLabel("Данные движка отсутствуют")
        self.no_analysis_title.setStyleSheet("font-weight: 700;")
        self.no_analysis_detail = QLabel(
            "У этой партии нет локального анализа. Запустите анализ, чтобы получить точность, "
            "оценки и подробные комментарии.")
        self.no_analysis_detail.setObjectName("secondary")
        self.no_analysis_detail.setWordWrap(True)
        empty_layout.addWidget(self.no_analysis_title)
        empty_layout.addWidget(self.no_analysis_detail)
        metrics_column.insertWidget(1, self.no_analysis_summary)
        split.addWidget(self.left_metrics)
        split.addWidget(board_column)

        inspector = QWidget()
        self.review_inspector = inspector
        inspector.setMinimumWidth(0)
        inspector_layout = QVBoxLayout(inspector)
        self.advantage_bar = AdvantageBar()
        inspector_layout.addWidget(self.advantage_bar, 0, Qt.AlignmentFlag.AlignLeft)
        self.selected_move_summary = QLabel("Выберите ход, чтобы увидеть его оценку.")
        self.selected_move_summary.setWordWrap(True)
        self.selected_move_summary.setMaximumHeight(62)
        inspector_layout.addWidget(self.selected_move_summary)
        right_row = QHBoxLayout()
        self.advantage_label = QLabel("н/д")
        self.advantage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.moves_list = MoveTable()
        self.moves_list.setAccessibleName("Список ходов партии")
        self.moves_list.ply_selected.connect(self.select_ply)
        self.moves_list.wheel_navigate.connect(self._navigate_wheel)
        right_row.addWidget(self.moves_list, 1)
        inspector_layout.addLayout(right_row, 1)
        inspector_layout.addWidget(self.advantage_label)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Панель сведений о партии")
        moves_page = QWidget()
        moves_layout = QVBoxLayout(moves_page)
        moves_layout.addWidget(QLabel("Ходы и оценка показаны в правой панели."))
        self.tabs.addTab(moves_page, "Ходы")
        facts = QWidget()
        self.metrics_layout = QVBoxLayout(facts)
        self.metrics_label = QLabel()
        self.metrics_label.setWordWrap(True)
        self.metrics_layout.addWidget(self.metrics_label)
        self.move_detail = QLabel("Выберите позицию на графике или ход в списке.")
        self.move_detail.setWordWrap(True)
        self.metrics_layout.addWidget(self.move_detail)
        self.tabs.addTab(facts, "Разбор")
        self.event_list = QListWidget()
        events_page = QWidget()
        events_layout = QVBoxLayout(events_page)
        events_layout.addWidget(self.event_list, 1)
        self.event_detail = QLabel("Выберите позицию, чтобы увидеть сохранённые события и PV.")
        self.event_detail.setWordWrap(True)
        events_layout.addWidget(self.event_detail)
        self.tabs.addTab(events_page, "События")
        self.notes_list = QListWidget()
        self.notes_tab_index = self.tabs.addTab(self.notes_list, "Заметки")
        self.advanced_group = QGroupBox("Дополнительные сведения")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced_group)
        advanced_layout.addWidget(self.tabs)
        self.tabs.setVisible(False)
        self.advanced_group.toggled.connect(self.tabs.setVisible)
        inspector_layout.addWidget(self.advanced_group)
        split.addWidget(inspector)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 21)
        split.setStretchFactor(1, 48)
        split.setStretchFactor(2, 31)
        split.setHandleWidth(4)
        split.setSizes([240, 360, 400])
        self.workspace_splitter = split
        self._review_splitter_initialized = False
        self._review_splitter_user_sized = False
        split.splitterMoved.connect(self._review_splitter_moved)
        for key, step in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda value=step: self._navigate_wheel(value))
            setattr(self, f"_review_key_{int(key)}", shortcut)
        root.addWidget(split, 1)

        self.graph_title = QLabel("Оценка позиции за белых · Δ — изменение оценки, не качество хода")
        self.graph_title.setText("Анализ")
        self.graph_title.setStyleSheet("font-size: 12px; font-weight: 700;")
        graph_controls = QHBoxLayout()
        graph_controls.addWidget(self.graph_title, 1)
        self.graph_mode = QComboBox()
        self.graph_mode.addItem("Оценка позиции", "evaluation")
        self.graph_mode.addItem("Изменение за ход", "delta")
        self.graph_mode.currentIndexChanged.connect(self._graph_mode_changed)
        graph_controls.addWidget(self.graph_mode)
        self.error_markers_toggle = QCheckBox("Маркеры ошибок")
        self.error_markers_toggle.setChecked(
            self.settings.value("review/error_markers", False, type=bool))
        self.error_markers_toggle.toggled.connect(self._error_markers_changed)
        graph_controls.addWidget(self.error_markers_toggle)
        root.addLayout(graph_controls)
        self.graph_legend = QLabel("● хороший ход   ◆ неточность   ■ ошибка   ▲ зевок   · неизвестно — источник не классифицировал ход")
        self.graph_legend.setObjectName("secondary")
        root.addWidget(self.graph_legend)
        self.graph = ReviewGraph([], [])
        self.graph.set_markers_visible(self.error_markers_toggle.isChecked())
        mode_index = self.graph_mode.findData(
            self.settings.value("review/graph_mode", "evaluation", type=str))
        if mode_index >= 0:
            self.graph_mode.setCurrentIndex(mode_index)
        self.graph.selected_ply.connect(self.select_ply)
        root.addWidget(self.graph)
        actions = QHBoxLayout()
        self.training_button = QPushButton("Разобрать мои ошибки")
        self.training_button.setObjectName("primaryAction")
        self.training_button.clicked.connect(self.start_training)
        self.previous_error_button = QPushButton("Предыдущая ошибка")
        self.previous_error_button.clicked.connect(lambda: self.navigate_error(-1))
        self.next_error_button = QPushButton("Следующая ошибка")
        self.next_error_button.clicked.connect(lambda: self.navigate_error(1))
        self.training_safety_button = QPushButton("Проверить безопасность")
        self.training_safety_button.clicked.connect(self._request_safety)
        self.training_status = QLabel()
        self.training_status.setWordWrap(True)
        self.training_status.setObjectName("secondary")
        self.training_status.setVisible(False)
        root.addWidget(self.training_status)
        self.check_button = QPushButton("Проверить")
        self.check_button.clicked.connect(self.check_training_move)
        self.hint_button = QPushButton("Подсказка")
        self.hint_button.clicked.connect(self.show_hint)
        self.skip_button = QPushButton("Пропустить")
        self.skip_button.clicked.connect(lambda: self.next_training_error(skipped=True))
        self.continue_button = QPushButton("Следующая ошибка")
        self.continue_button.clicked.connect(lambda: self.next_training_error(skipped=False))
        self.repeat_button = QPushButton("Повторить без подсказки")
        self.repeat_button.clicked.connect(self._repeat_training_error)
        self.return_review_button = QPushButton("Вернуться к разбору")
        self.return_review_button.clicked.connect(self.stop_training)
        self.analysis_button = QPushButton("Проанализировать Stockfish")
        self.analysis_button.setToolTip(
            "Явно запускает сохранённый послепартийный анализ после проверки Fair Play.")
        self.analysis_button.clicked.connect(
            lambda: self.review and self.analysis_requested.emit(str(self.review["game"]["game_id"])))
        self.open_lichess_button = QPushButton("Открыть партию на Lichess для запроса анализа")
        self.open_lichess_button.setToolTip(
            "Откроет подтверждённую страницу партии; запрос анализа остаётся только действием пользователя на сайте.")
        self.open_lichess_button.clicked.connect(
            lambda: self.review and self.open_lichess_game_requested.emit(
                str(self.review["game"].get("source_game_id") or "")))
        for button in (self.previous_error_button, self.next_error_button,
                       self.analysis_button, self.open_lichess_button,
                       self.training_button, self.training_safety_button,
                       self.check_button, self.hint_button,
                       self.skip_button, self.continue_button, self.return_review_button):
            actions.addWidget(button)
            button.setVisible(False)
        actions.addWidget(self.repeat_button)
        self.repeat_button.setVisible(False)
        self.training_button.setVisible(True)
        self.training_safety_button.setVisible(False)
        root.addLayout(actions)
        self.check_button.setEnabled(False)
        self.continue_button.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact_review = self.height() < 620
        # In the desktop shell the review page has less vertical space than
        # its own widget height because the app header/navigation are outside
        # this page. Preserve a usable move viewport at compact 640px windows
        # by yielding chart height before compressing the move table to zero.
        graph = getattr(self, "graph", None)
        if graph is not None:
            graph.setMinimumHeight(68 if compact_review else 170)
            graph.setMaximumHeight(74 if compact_review else 220)
            graph.compact = compact_review
            graph.update()
        splitter = getattr(self, "workspace_splitter", None)
        if splitter is not None:
            stack_only = self.width() < 700
            splitter.setOrientation(Qt.Orientation.Vertical if stack_only else Qt.Orientation.Horizontal)
            if not stack_only and not self._review_splitter_user_sized:
                available = max(1, splitter.width() - 8)
                compact = self.width() < 980
                left_width = (max(190, round(available * .30)) if compact else
                              min(410, max(385, round(available * .38))))
                inspector_width = (max(220, round(available * .34)) if compact else
                                   max(300, round(available * .31)))
                board_side = max(170, min(splitter.height() - 8,
                                          available - left_width - inspector_width - 8))
                board_width = board_side + 8
                splitter.setSizes([left_width, board_width,
                                   max(220 if compact else 300,
                                       available - left_width - board_width)])
            self.left_metrics.setMinimumWidth(0)
            self.left_metrics.setMaximumWidth(16777215)
            self._fit_review_board()

    def showEvent(self, event):
        super().showEvent(event)
        # QStackedWidget can deliver a show before its splitter has received
        # the final height from the shell. Fit once more after that layout pass
        # so a fixed-size board cannot extend beyond the visible board column.
        QTimer.singleShot(0, self._fit_review_board)

    def _review_splitter_moved(self, *_args):
        if self.workspace_splitter.orientation() == Qt.Orientation.Horizontal:
            self._review_splitter_user_sized = True
        self._fit_review_board()

    def _fit_review_board(self):
        if (not hasattr(self, "review_board") or
                self.workspace_splitter.orientation() != Qt.Orientation.Horizontal):
            return
        layout = self.board_column.layout()
        margins = layout.contentsMargins()
        visible_non_board = [
            item.widget() for index in range(layout.count())
            if (item := layout.itemAt(index)).widget() is not None
            and item.widget() is not self.review_board
            and not item.widget().isHidden()]
        available_width = max(0, self.board_column.contentsRect().width()
                              - margins.left() - margins.right())
        occupied_height = sum(
            max(widget.sizeHint().height(), widget.heightForWidth(available_width))
            if widget.hasHeightForWidth() else widget.sizeHint().height()
            for widget in visible_non_board)
        occupied_height += max(0, len(visible_non_board)) * layout.spacing()
        available_height = max(0, self.board_column.height() - margins.top()
                               - margins.bottom() - occupied_height)
        side = min(available_width, available_height)
        if side and self.review_board.width() != side:
            self.review_board.setFixedSize(side, side)

    def load_game(self, game: dict, *, training_allowed: bool | None = None,
                  training_block_reason: str | None = None,
                  ephemeral: bool = False,
                  analysis_source: str | None = None) -> None:
        self._ephemeral_study = bool(ephemeral)
        self._ephemeral_board = None
        self._ephemeral_branch.clear()
        self._ephemeral_undo.clear()
        self.ephemeral_controls.setVisible(self._ephemeral_study)
        self.ephemeral_status.setText(
            "Временная ветка."
            if self._ephemeral_study else "")
        self.ephemeral_status.setToolTip(
            "Ходы исследования не попадут в PGN, SQLite или онлайн-партию.")
        self.review = load_game_review(self.repository, game,
                                       analysis_source=analysis_source)
        if training_allowed is not None:
            self.training_allowed = training_allowed
            self.evaluation_allowed = training_allowed
        if training_block_reason is not None:
            self.training_block_reason = training_block_reason
        self.selected_ply = 0
        analysis = self.review["analysis"] or {}
        if not self.evaluation_allowed:
            # Preserve stored runs in the repository/read model, but do not
            # consult them to build presentation state before a fresh lease.
            analysis = {}
        scored_rows = [row for row in analysis.get("assessments", [])
                       if row.get("white_cp") is not None or row.get("mate_white") is not None]
        self.position_evaluations_available = bool(self.evaluation_allowed and scored_rows)
        official_accuracy = self.review.get("official_accuracy") or {}
        local_accuracy = self.review.get("local_game_accuracy") or {}
        has_accuracy = bool(self.evaluation_allowed and any(
            official_accuracy.get(side) is not None or local_accuracy.get(side) is not None
            for side in ("white", "black")))
        has_summary_metrics = self.position_evaluations_available or has_accuracy
        self.left_metrics_title.setText("Сводка по партии" if has_summary_metrics else
                                        "Оценки скрыты" if not self.evaluation_allowed else
                                        "Сводка по партии")
        self.no_analysis_title.setText(
            "Аналитика скрыта правилами Fair Play." if not self.evaluation_allowed else
            "Данные движка отсутствуют")
        self.no_analysis_detail.setText(
            self.training_block_reason or "Оценки и классификация скрыты до свежей проверки безопасности."
            if not self.evaluation_allowed else
            "У этой партии нет локального анализа. Запустите анализ, чтобы получить точность, "
            "оценки и подробные комментарии.")
        self.no_analysis_summary.setVisible(not has_summary_metrics)
        self.phase_selector.setVisible(has_summary_metrics and self.position_evaluations_available)
        self.side_metrics_summary.setVisible(has_summary_metrics)
        self.phase_summary.setVisible(has_summary_metrics and self.position_evaluations_available)
        self.graph_title.setText("Оценка позиции за белых · Δ — изменение оценки, не качество хода"
                                 if self.position_evaluations_available else "Анализ")
        self.graph_mode.setVisible(self.position_evaluations_available)
        self.error_markers_toggle.setVisible(self.position_evaluations_available)
        self.graph_legend.setVisible(self.position_evaluations_available)
        self.graph.empty_title = (
            "Оценки скрыты правилами Fair Play." if not self.evaluation_allowed else
            "Оценка отсутствует")
        self.graph.empty_detail = (
            "Оценки появятся после безопасной проверки." if not self.evaluation_allowed else
            "Здесь появится график оценки после запуска анализа Stockfish.")
        self.graph.setAccessibleName("Анализ партии" if not self.position_evaluations_available
                                      else "Интерактивный график оценки по ходам")
        self.graph.setAccessibleDescription(
            self.graph.empty_title + ". " + self.graph.empty_detail
            if not self.position_evaluations_available else
            "Интерактивный график сохранённых оценок по полуходам.")
        self.advantage_bar.setVisible(self.position_evaluations_available)
        self.advantage_label.setVisible(self.position_evaluations_available)
        self.selected_move_summary.setVisible(self.position_evaluations_available)
        game_finished = game.get("status") not in {
            "created", "started", "started_soon", "playing"}
        has_engine_analysis = (self.evaluation_allowed and
                               analysis.get("source") == "stockfish" and
                               analysis.get("status") == "complete")
        self.analysis_button.setVisible(game_finished and not self._ephemeral_study)
        self.analysis_button.setText("Повторить анализ Stockfish" if has_engine_analysis
                                     else "Проанализировать Stockfish")
        white = game.get("white_player") or "Белые"
        black = game.get("black_player") or "Чёрные"
        if white.strip() in {"?", "Unknown", "unknown"}:
            white = "Белые"
        if black.strip() in {"?", "Unknown", "unknown"}:
            black = "Чёрные"
        result = ("½–½" if game.get("status") in {"draw", "stalemate", "repetition",
                                                   "50moves", "insufficientMaterial"}
                  else "1–0" if game.get("winner") == "white"
                  else "0–1" if game.get("winner") == "black" else "результат н/д")
        source = analysis.get("source") or ""
        game_details = (f"{format_game_timeline(game)} · "
                        f"{format_clock_control(game.get('clock'))} · "
                        f"{game_opening_name(game)}")
        self.title.setText("Разбор партии")
        self.title.setToolTip(f"{white} — {black} · {result}\n{game_details}")
        self.subtitle.setText(f"{white} — {black} · {result} · {game_details}")
        self.subtitle.setToolTip(opening_tooltip(game.get("opening_name")))
        self.white_name.setText(f"♔  {white}")
        self.black_name.setText(f"♚  {black}")
        color = self.review["owner_color"]
        self.side_metrics_summary.setHorizontalHeaderLabels(
            ("Показатель", "Я", "Сопер.") if color in {"white", "black"}
            else ("Показатель", "Белые", "Чёрные"))
        self.review_board.set_flipped(color == "black")
        self.review_board.set_position(self.review["positions"][0])
        if self._ephemeral_study:
            self._ephemeral_board = self.review["positions"][0].copy(stack=True)
            self.review_board.set_interaction_enabled(True)
            self.ephemeral_undo_button.setEnabled(False)
            self.ephemeral_reset_button.setEnabled(False)
        else:
            self.review_board.set_interaction_enabled(False)
        assessment_by_ply = {int(item["ply"]): dict(item)
                             for item in analysis.get("assessments", [])}
        for move in self.review["moves"]:
            before = assessment_by_ply.get(move.ply - 1, {})
            after = assessment_by_ply.get(move.ply)
            if (after is not None and before.get("white_cp") is not None
                    and after.get("white_cp") is not None
                    and before.get("mate_white") is None
                    and after.get("mate_white") is None):
                delta_white = int(after["white_cp"]) - int(before["white_cp"])
                after["delta_white"] = delta_white
                after["delta_mover"] = delta_white if move.mover == "white" else -delta_white
        self.moves_list.set_rows(
            self.review["moves"],
            scores=assessment_by_ply if self.evaluation_allowed else {},
            enabled=self.evaluation_allowed or self._ephemeral_study,
            show_severity=self.evaluation_allowed)
        metrics = self.review["per_side"]
        accuracy = self.review["official_accuracy"]
        def percent(value):
            return f"{float(value):.1f}%" if value is not None else "нет данных"
        accuracy_text = (f"Точность Lichess · сервер: белые {percent(accuracy['white'])}, "
                         f"чёрные {percent(accuracy['black'])}"
                         if accuracy["source"] else "Точность Lichess · сервер: нет источника")
        local_accuracy = self.review.get("local_game_accuracy") or {}
        local_accuracy_status = self.review.get("local_accuracy_status") or {}
        local_coverage = local_accuracy_status.get("coverage") or {}
        local_accuracy_text = (
            f"Расчёт Chess Coach (методика Lichess, {local_accuracy_status.get('formula_id') or 'версия н/д'}; "
            f"неофициально): белые {percent(local_accuracy.get('white'))}, "
            f"чёрные {percent(local_accuracy.get('black'))} · "
            f"охват {local_coverage.get('scored', 0)}/{local_coverage.get('total', 0)} · "
            f"источник {source_short(local_accuracy_status.get('source'))[1]} · "
            f"глубина {((local_accuracy_status.get('depth_range') or ('н/д', 'н/д'))[0])}–"
            f"{((local_accuracy_status.get('depth_range') or ('н/д', 'н/д'))[1])} · "
            f"терминально доказано позиций: {local_accuracy_status.get('terminal_positions_derived', 0)} · "
            f"{local_accuracy_status.get('reason') or 'сохранённые оценки'}")
        backfill = self.review.get("lichess_backfill_state") or {}
        if game.get("source_game_id"):
            if (backfill.get("accuracy_status") == "no_server_data"
                    and backfill.get("evals_status") == "no_server_data"):
                server_analysis_text = "На Lichess анализ не запрошен или недоступен."
            elif backfill:
                server_analysis_text = "Готовые данные Lichess проверяются или доступны частично."
            else:
                server_analysis_text = "Готовая аналитика Lichess ещё не проверена."
        else:
            server_analysis_text = "Это локальная партия; Lichess-аналитика не связана."
        def side_counts(color: str) -> str:
            side = metrics[color]
            color_label = "Белые" if color == "white" else "Чёрные"
            verified_classes = bool(self.evaluation_allowed and analysis
                                   and analysis.get("status") == "complete"
                                   and any(move.severity != "unknown"
                                           for move in self.review["moves"]))
            strong_count = sum(move.mover == color and move.severity == "strong"
                               for move in self.review["moves"])
            strong_label = str(strong_count) if verified_classes and side["assessed"] else "н/д"
            if not side["assessed"]:
                return (f"{color_label}: нет данных · охват 0/{side['total']} оценённых ходов · "
                        "сильные н/д")
            suffix = "" if side["assessed"] == side["total"] else " · только по оценённым ходам"
            return (f"{color_label}: хорошие {side['good']}, неточности {side['inaccuracy']}, "
                    f"ошибки {side['mistake']}, зевки {side['blunder']}, "
                    f"сильные {strong_label} · "
                    f"охват {side['assessed']}/{side['total']} оценённых ходов{suffix}")
        if self.evaluation_allowed:
            decision = (analysis or {}).get("decision_coverage") or {}
            mate_positions = sum(item.get("mate_white") is not None
                                 for item in analysis.get("assessments", []))
            metrics_text = (
            f"Источник оценки: {source_short(source)[1]}\n"
            f"Позиции оценены: {self.review['coverage']['scored']}/{self.review['coverage']['total']}\n"
            f"Решения с сопоставимым качеством: белые {self.review['per_side']['white']['assessed']}/{self.review['per_side']['white']['total']}, "
            f"чёрные {self.review['per_side']['black']['assessed']}/{self.review['per_side']['black']['total']}\n"
            f"Хорошие среди оценённых: белые {self.review['per_side']['white']['good']}/{self.review['per_side']['white']['assessed'] or 'н/д'}, "
            f"чёрные {self.review['per_side']['black']['good']}/{self.review['per_side']['black']['assessed'] or 'н/д'}\n"
            f"Совпадение с лучшим ходом Stockfish: белые {self.review['per_side']['white']['best_matches']}/{self.review['per_side']['white']['best_known'] or 'н/д'}, "
            f"чёрные {self.review['per_side']['black']['best_matches']}/{self.review['per_side']['black']['best_known'] or 'н/д'}\n"
            f"Матовые оценки позиций: {mate_positions}\n"
            f"{side_counts('white')}\n{side_counts('black')}\n"
            f"{accuracy_text}\n{local_accuracy_text}\n{server_analysis_text}")
        else:
            metrics_text = (self.training_block_reason or
                            "Оценки, варианты и подсказки скрыты до проверки Fair Play.")
        self.metrics_label.setText(metrics_text)
        self.left_metrics_label.setText("")
        self._render_side_metrics(color)
        self.phase_summary.setText(
            (f"{source_short(source)[0]} · {self.review['coverage']['scored']}/"
             f"{self.review['coverage']['total']} позиций"
             if self.evaluation_allowed else self._blocked_analysis_text()))
        self.phase_summary.setToolTip(
            f"Источник: {source_short(source)[1]} · покрытие по оценённым позициям. "
            "Фазовые показатели используют сохранённую классификацию."
            if self.evaluation_allowed else "Фазовая аналитика скрыта правилами Fair Play.")
        self.selected_move_summary.setToolTip(
            source_short(source)[1] if self.evaluation_allowed else "")
        self.notes_list.clear()
        for note in self.review["notes"]:
            self.notes_list.addItem(f"ply {note.get('ply', 0)} · {note.get('body', '')}")
        self.tabs.setTabVisible(self.notes_tab_index, bool(self.review["notes"]))
        self.graph.assessments = analysis.get("assessments", []) if self.evaluation_allowed else []
        self.graph.full_ply_count = len(self.review["moves"])
        self.graph.analysis_source = str(analysis.get("source") or "")
        self.graph.analysis_status = str(analysis.get("status") or "")
        self.graph.move_san = ({int(move.ply): str(move.san)
                                for move in self.review["moves"]}
                               if self.evaluation_allowed else {})
        self.graph.mate_outcome_signs = {
            int(row["ply"]): (1 if self.review["positions"][int(row["ply"])].turn == chess.BLACK
                               else -1)
            for row in analysis.get("assessments", [])
            if self.evaluation_allowed and row.get("mate_white") == 0
            and 0 <= int(row["ply"]) < len(self.review["positions"])
            and self.review["positions"][int(row["ply"])].is_checkmate()}
        coverage = self.review.get("coverage", {}) if self.evaluation_allowed else {}
        self.graph.coverage_text = (
            f"{coverage.get('scored', 0)}/{coverage.get('total', 0)} позиций"
            if self.evaluation_allowed else "")
        phase_by_ply = self._phase_by_ply() if self.evaluation_allowed else {}
        self.graph.markers = ({move.ply: {"severity": move.severity, "mover": move.mover,
                                          "phase": phase_by_ply.get(move.ply)}
                               for move in self.review["moves"]}
                              if self.evaluation_allowed else {})
        self.graph.set_safety_gate(self.evaluation_allowed, self.training_block_reason)
        if self.evaluation_allowed:
            self.graph_legend.setText(
                "● хороший ход   ◆ неточность   ■ ошибка   ▲ зевок   · неизвестно — источник не классифицировал ход")
            self.graph_legend.setToolTip("Источник и покрытие указаны в сводке. Маты показаны отдельными маркерами.")
            self.graph_legend.setAccessibleDescription(
                "Условные обозначения сохранённой аналитики после действующей проверки Fair Play.")
            self.graph.setAccessibleDescription(
                f"{source_short(source)[1]}; оценено позиций "
                f"{self.review['coverage']['scored']} из {self.review['coverage']['total']}. "
                "Матовые позиции показаны отдельными маркерами, без перевода в пешки.")
        else:
            self.graph_legend.setText("Оценки и классификация скрыты правилами Fair Play.")
            self.graph_legend.setToolTip("")
            self.graph_legend.setAccessibleDescription("Аналитика скрыта до свежей проверки Fair Play.")
            self.graph.setAccessibleDescription("Оценки и классификация скрыты правилами Fair Play.")
        self.open_lichess_button.setVisible(
            bool(game.get("source_game_id")) and not self._ephemeral_study)
        self.graph.select_ply(0)
        self._refresh_selected_ply_events()
        eligible = eligible_error_moves(self.review) if self.evaluation_allowed else []
        self.previous_error_button.setVisible(self.evaluation_allowed and bool(eligible))
        self.next_error_button.setVisible(self.evaluation_allowed and bool(eligible))
        self.training_button.setText(
            f"Разобрать мои ошибки · {len(eligible)}" if self.evaluation_allowed
            else "Разобрать мои ошибки")
        reason = self.training_block_reason
        has_errors = bool(eligible)
        has_saved_analysis = bool(self.evaluation_allowed and analysis and (
            analysis.get("assessments") or analysis.get("events") or
            analysis.get("alternatives")))
        allowed = self.training_allowed and has_errors
        self.training_button.setEnabled(allowed)
        self.training_button.setVisible(not self._ephemeral_study)
        self.training_button.setToolTip(reason if not self.training_allowed else
            "Нет завершённых собственных ошибок с проверенным улучшенным ходом." if not eligible else "")
        self.training_safety_button.setVisible(
            game_finished and not self.training_allowed and not self._ephemeral_study)
        self.training_safety_button.setEnabled(
            game_finished and not self.training_allowed and not self._ephemeral_study)
        self.training_safety_button.setToolTip(reason or "Проверить текущие активные партии Lichess.")
        self.training_status.setText("" if allowed else reason or (
            "Нет ошибок, для которых сохранённые оценки позволяют проверить улучшение."))
        if not self.evaluation_allowed:
            self.mask_review_analysis(self.training_block_reason)
        self._fit_review_board()

    def _phase_scope_changed(self, *_args):
        if self.review and self.evaluation_allowed:
            self._render_side_metrics(self.review["owner_color"])

    def _render_side_metrics(self, owner_color: str) -> None:
        table = self.side_metrics_summary
        table.setRowCount(0)
        if not self.review or not self.evaluation_allowed:
            table.setAccessibleName("Аналитика скрыта")
            table.setToolTip("")
            return
        scope = self.phase_selector.currentData() or "all"
        all_moves = self.review["moves"]
        phases = self._phase_by_ply()
        moves = (all_moves if scope == "all" else
                 [move for move in all_moves if phases.get(move.ply) == scope])
        owner, opponent = owner_color, ("black" if owner_color == "white" else "white")
        official = self.review.get("official_accuracy") or {}
        local = (self.review.get("local_game_accuracy") or {} if scope == "all" else
                 (self.review.get("local_phase_accuracy") or {}).get(scope, {}))

        def accuracy_cell(color: str) -> str:
            if scope == "all" and official.get(color) is not None:
                return f"{official[color]:.1f}% · Lichess"
            if local.get(color) is not None:
                return f"{local[color]:.1f}% · локально"
            return "—"

        def count_cell(color: str, key: str) -> str:
            side_moves = [move for move in moves if move.mover == color]
            if not side_moves:
                return "н/д"
            if key == "coverage":
                scored = sum(move.cp_loss is not None and move.severity != "unknown"
                             for move in side_moves)
                return f"{scored}/{len(side_moves)}"
            analysis = self.review.get("analysis") or {}
            if key == "strong":
                verified = analysis.get("source") == "stockfish" and analysis.get("status") == "complete"
                return (str(sum(move.severity == "strong" for move in side_moves))
                        if verified and any(move.severity == "strong" for move in side_moves)
                        else "н/д")
            if key == "good":
                assessed = [m for m in side_moves if m.severity != "unknown"]
                return str(sum(m.severity in {"good", "excellent"} for m in assessed)) if assessed else "н/д"
            assessed = [m for m in side_moves if m.severity != "unknown"]
            return str(sum(m.severity == key for m in assessed)) if assessed else "н/д"

        rows = [
            ("Точность", accuracy_cell(owner), accuracy_cell(opponent)),
            ("Хорошие", count_cell(owner, "good"), count_cell(opponent, "good")),
            ("Неточности", count_cell(owner, "inaccuracy"), count_cell(opponent, "inaccuracy")),
            ("Ошибки", count_cell(owner, "mistake"), count_cell(opponent, "mistake")),
            ("Зевки", count_cell(owner, "blunder"), count_cell(opponent, "blunder")),
            ("Сильные", count_cell(owner, "strong"), count_cell(opponent, "strong")),
            ("Охват", count_cell(owner, "coverage"), count_cell(opponent, "coverage")),
        ]
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if row_index == 0 and scope != "all":
                    item.setToolTip("Локальная точность по сохранённым оценкам выбранной фазы.")
                table.setItem(row_index, col_index, item)
        table.setAccessibleName(f"Статистика: {self.phase_selector.currentText()}")
        source = (self.review.get("analysis") or {}).get("source")
        game = self.review["game"]
        names = (game.get("white_player") or "Белые", game.get("black_player") or "Чёрные")
        table.setToolTip(f"{source_short(source)[1]} · {names[0]} / {names[1]}; официальная и локальная точность подписаны отдельно.")
        row_height = max(table.fontMetrics().height() + 3, 22)
        for row in range(table.rowCount()):
            table.setRowHeight(row, row_height)
        table.setMinimumHeight(min(225, table.horizontalHeader().height() + table.rowCount() * row_height))
        if scope != "all" and not moves:
            self.phase_summary.setText("Фаза не определена для выбранной партии.")
    def navigate_error(self, direction: int):
        if not self.evaluation_allowed or not self.review or self.training_mode:
            return
        errors = eligible_error_moves(self.review)
        if not errors:
            return
        plies = [int(move.ply) for move in errors]
        if direction < 0:
            candidates = [ply for ply in plies if ply < self.selected_ply]
            target = candidates[-1] if candidates else plies[-1]
        else:
            candidates = [ply for ply in plies if ply > self.selected_ply]
            target = candidates[0] if candidates else plies[0]
        self.select_ply(target)

    def _request_safety(self):
        if self.review:
            self.training_safety_button.setEnabled(False)
            self.training_safety_button.setText("Проверяем…")
            self.training_status.setText("Проверяем активные партии; подсказки пока скрыты.")
            self.safety_requested.emit(str(self.review["game"]["game_id"]))

    def _blocked_analysis_text(self) -> str:
        return (self.training_block_reason or
                "Аналитика заблокирована до свежей проверки Fair Play.")

    def mask_review_analysis(self, reason: str = "") -> None:
        """Clear all rendered/accessibility analysis while retaining saved data."""
        if reason:
            self.training_block_reason = reason
        self.evaluation_allowed = False
        self.training_allowed = False
        message = self._blocked_analysis_text()
        if self.training_mode:
            self.stop_training(message)
        if self.review:
            self.graph.set_safety_gate(False, message)
            self.graph.assessments = []
            self.graph.markers = {}
            self.graph.analysis_source = ""
            self.graph.analysis_status = ""
            self.graph.coverage_text = ""
            self.graph.move_san = {}
            self.graph.mate_outcome_signs = {}
            self.graph.setToolTip(message)
            self.graph.setAccessibleDescription("Оценки и классификация скрыты правилами Fair Play.")
            self.graph_legend.setText("Оценки и классификация скрыты правилами Fair Play.")
            self.graph_legend.setToolTip("")
            self.graph_legend.setAccessibleDescription(
                "Сохранённая аналитика недоступна до новой проверки безопасности.")
            self.review_board.set_hint_arrows(())
            self.moves_list.set_rows(self.review["moves"], scores={}, enabled=False,
                                     show_severity=False)
            self.moves_list.setAccessibleDescription(
                "Нотация доступна; оценки и классы ходов скрыты правилами Fair Play.")
            self.event_list.clear()
        self.side_metrics_summary.setRowCount(0)
        self.side_metrics_summary.setToolTip("")
        self.side_metrics_summary.setAccessibleName("Аналитика скрыта")
        for label in (self.left_metrics_label, self.phase_summary, self.metrics_label,
                      self.selected_move_summary, self.move_detail, self.event_detail):
            label.setText(message)
            label.setToolTip("")
            label.setAccessibleDescription("Аналитика скрыта до свежей проверки Fair Play.")
        self.side_metrics_summary.setAccessibleName("Аналитика скрыта")
        self.phase_summary.setStyleSheet("")
        self.advantage_bar.set_evaluation(None, allowed=False)
        self.advantage_label.setText("Оценки скрыты")
        self.advantage_label.setToolTip("")
        self.training_proven = {}
        self.training_candidate = None
        self.training_button.setEnabled(False)
        self.previous_error_button.setVisible(False)
        self.next_error_button.setVisible(False)
        for button in (self.check_button, self.hint_button, self.skip_button,
                       self.continue_button, self.repeat_button):
            button.setVisible(False)
        self.training_status.setText(message)
        self.training_status.setToolTip("")
        self.graph.update()

    def set_training_safety_result(self, allowed: bool, reason: str = ""):
        ephemeral = bool(getattr(self, "_ephemeral_study", False))
        selected_ply = self.selected_ply
        self.training_allowed = bool(allowed)
        self.evaluation_allowed = bool(allowed)
        self.training_block_reason = reason
        if not allowed:
            self.training_proven = {}
            self.training_candidate = None
        if not allowed and self.training_mode:
            self.stop_training(reason or "Упражнение остановлено: безопасность не подтверждена.")
        if not allowed:
            self.mask_review_analysis(reason)
        if self.review:
            game = self.review["game"]
            self.load_game(game, training_allowed=allowed,
                           training_block_reason=reason, ephemeral=ephemeral)
            self.select_ply(selected_ply)
        self.training_safety_button.setText("Проверить безопасность")

    def set_safety_pending(self, reason: str = "Проверяем активные партии; оценки скрыты."):
        self.training_allowed = False
        self.evaluation_allowed = False
        self.training_block_reason = reason
        self.mask_review_analysis(reason)
        self.training_safety_button.setEnabled(False)
        self.training_safety_button.setText("Проверяем…")
        self.training_status.setText(reason or self._blocked_analysis_text())

    def revoke_safety(self, reason: str) -> None:
        self.set_training_safety_result(False, reason)

    def _export(self):
        if self.review:
            self.export_requested.emit(str(self.review["game"]["game_id"]))

    def select_ply(self, ply: int):
        if not self.review or self.training_mode:
            return
        ply = max(0, min(len(self.review["moves"]), int(ply)))
        self.selected_ply = ply
        if self._ephemeral_study:
            self._ephemeral_board = self.review["positions"][ply].copy(stack=True)
            self._ephemeral_branch.clear()
            self._ephemeral_undo.clear()
            self.review_board.set_interaction_enabled(True)
            self.ephemeral_undo_button.setEnabled(False)
            self.ephemeral_reset_button.setEnabled(False)
            self.ephemeral_status.setText(
                f"Временное исследование позиции после {ply} полуходов; PGN не изменяется.")
        phase = self._phase_by_ply().get(ply) if self.evaluation_allowed else None
        phase_name = phase_label(phase)
        phase_index = self.phase_selector.findData(phase or "all")
        if self.evaluation_allowed and phase_index >= 0 and self.phase_selector.currentIndex() != phase_index:
            self.phase_selector.setCurrentIndex(phase_index)
        self.phase_summary.setStyleSheet(
            "font-weight: 700; color: #d7a84b;"
            if self.evaluation_allowed and phase in {"opening", "middlegame", "endgame"} else "")
        self.phase_summary.setToolTip(
            f"Выбранная позиция: {phase_name}. Фазовая точность: н/д без отдельной метрики."
            if self.evaluation_allowed else "Фазовая аналитика скрыта правилами Fair Play.")
        self.review_board.set_position(self.review["positions"][ply])
        self.review_board.set_hint_arrows(())
        position_analysis = self.review.get("analysis") or {}
        position_assessments = {int(item.get("ply", -1)): item
                                for item in position_analysis.get("assessments", [])}
        position_score = position_assessments.get(ply, {})
        terminal_position = self.review["positions"][ply].is_checkmate()
        terminal_winner_white = (self.review["positions"][ply].turn == chess.BLACK
                                 if terminal_position else None)
        score_for_bar = (None if position_score.get("mate_white") is not None
                         else position_score.get("white_cp"))
        self.advantage_bar.set_evaluation(score_for_bar,
                                          allowed=(self.evaluation_allowed
                                                   and self.position_evaluations_available),
                                          mate_white=position_score.get("mate_white"),
                                          terminal_checkmate=terminal_position,
                                          terminal_winner_white=terminal_winner_white)
        self.advantage_label.setText(
            (("Мат · белые победили" if terminal_winner_white else "Мат · чёрные победили")
             if terminal_position else
             evaluation_label(position_score.get("white_cp"), position_score.get("mate_white")))
            if self.evaluation_allowed else "Оценки скрыты")
        position_best_label = ""
        if self.evaluation_allowed:
            analysis_at_position = self.review.get("analysis") or {}
            current_assessment = next((item for item in analysis_at_position.get("assessments", [])
                                       if int(item.get("ply", -1)) == ply), None)
            current_pv = ((current_assessment or {}).get("pv") or
                          (current_assessment or {}).get("pv_uci") or [])
            if isinstance(current_pv, str):
                current_pv = current_pv.split()
            if current_pv:
                try:
                    candidate = chess.Move.from_uci(str(current_pv[0]))
                    board_at_position = self.review["positions"][ply].copy(stack=False)
                    if candidate in board_at_position.legal_moves:
                        position_best_label = board_at_position.san(candidate)
                        self.review_board.set_hint_arrows([candidate])
                except (ValueError, IndexError, chess.InvalidMoveError):
                    pass
        self.graph.select_ply(ply)
        self.moves_list.select_ply(ply)
        if not self.evaluation_allowed:
            self.move_detail.setText(
                self.training_block_reason or
                "Оценка и варианты скрыты до актуальной проверки безопасности.")
        elif ply:
            move = self.review["moves"][ply - 1]
            analysis = self.review.get("analysis") or {}
            assessments = {int(item["ply"]): item for item in analysis.get("assessments", [])}
            before = assessments.get(ply - 1, {})
            after = assessments.get(ply, {})
            before_cp, after_cp = before.get("white_cp"), after.get("white_cp")
            before_label = evaluation_label(before_cp, before.get("mate_white"))
            after_label = evaluation_label(after_cp, after.get("mate_white"))
            delta_label = "Δ: нет сопоставимой численной оценки"
            if self.review["positions"][ply].is_checkmate():
                winner = "белые" if self.review["positions"][ply].turn == chess.BLACK else "чёрные"
                after_label = f"Мат · победили {winner} (по законной позиции)"
                delta_label = "Терминальный мат подтверждён доской; CPL не применяется"
            elif (before_cp is not None and after_cp is not None
                    and before.get("mate_white") is None and after.get("mate_white") is None):
                delta_white = int(after_cp) - int(before_cp)
                delta_mover = delta_white if move.mover == "white" else -delta_white
                delta_label = (f"Δ белых {delta_white / 100:+.2f} · "
                               f"Δ ходившего {delta_mover / 100:+.2f} пешки")
            elif before.get("mate_white") is not None or after.get("mate_white") is not None:
                delta_label = (f"Динамика мата: {before_label} → {after_label}; "
                               "длина мата не переводится в сантипешки")
            best_label = "лучший ход не сохранён"
            pv = before.get("pv") or before.get("pv_uci") or []
            if isinstance(pv, str):
                pv = pv.split()
            if pv:
                try:
                    position = self.review["positions"][ply - 1].copy(stack=False)
                    first = chess.Move.from_uci(str(pv[0]))
                    if first in position.legal_moves:
                        best_label = f"лучший ход по сохранённой оценке: {position.san(first)}"
                except (ValueError, IndexError, chess.InvalidMoveError):
                    pass
            delta_compact = delta_label.replace("Δ белых ", "ΔБ ").replace(
                " · Δ ходившего ", " · Δх ").replace(" пешки", "")
            best_compact = best_label.replace("лучший ход по сохранённой оценке: ", "Лучший: ")
            if position_best_label:
                best_compact += f" · из позиции: {position_best_label}"
            self.selected_move_summary.setText(
                f"Ход {move.san} · {severity_label(move.severity)}\n"
                f"До {before_label} → после {after_label} · {delta_compact}\n"
                f"{best_compact}")
            self.selected_move_summary.setToolTip(
                f"{source_short(analysis.get('source'))[1]} · глубина {move.depth or '—'}\n"
                f"{delta_label}\n{best_label}"
                + (f"\nЛучший ход из выбранной позиции: {position_best_label}"
                   if position_best_label else ""))
            move_metric = next((item for item in analysis.get("move_metrics", [])
                                if int(item.get("ply", -1)) == ply), {})
            move_events = [item for item in analysis.get("events", [])
                           if int(item.get("ply", -1)) == ply
                           and str(item.get("event_type", "")).startswith(("mate", "missed_own", "saved_", "allowed_", "checkmate"))]
            accuracy_value = move_metric.get("accuracy")
            terminal_accuracy = str(move_metric.get("accuracy_source") or "").endswith(
                ":board-terminal")
            accuracy_text = (f"Точность хода: {float(accuracy_value):.1f}% · "
                             "методика Lichess, локальная и неофициальная"
                             if accuracy_value is not None else "Точность хода: н/д")
            if accuracy_value is not None and terminal_accuracy:
                accuracy_text += " · мат подтверждён доской; конечная оценка движка не требовалась"
            event_names = {
                "missed_own_mate": "упущен форсированный мат",
                "saved_against_forced_mate": "отражена форсированная матовая угроза",
                "allowed_opponent_mate": "допущен форсированный мат соперника",
                "mate_preserved": "форсированный мат сохранён",
                "checkmate_terminal": "мат подтверждён терминальной позицией",
            }
            mate_event_text = "; ".join(event_names.get(item.get("event_type"),
                                                     "матовое событие")
                                         for item in move_events)
            cpl_text = (f"потеря {move.cp_loss} сантипешек"
                        if move.cp_loss is not None else
                        "CPL не применим к матовой оценке"
                        if before.get("mate_white") is not None
                        or after.get("mate_white") is not None
                        or self.review["positions"][ply].is_checkmate() else
                        "CPL: нет сравнимых оценок")
            self.move_detail.setText(
                f"{move.san} · {severity_label(move.severity)} · {cpl_text} · "
                f"{phase_label(move_metric.get('phase'))} · {source_short(move.source)[1]} · "
                f"глубина {move.depth or 'н/д'}\n{accuracy_text}"
                + (f"\n{mate_event_text}" if mate_event_text else ""))
            self.move_detail.setToolTip(
                f"{accuracy_text} · формула {move_metric.get('accuracy_formula_id') or 'н/д'}"
                + (f"\nИсточник конечного результата: {move_metric.get('accuracy_source')}"
                   if terminal_accuracy else "")
                + (f"\nСобытие: {mate_event_text}" if mate_event_text else ""))
        else:
            self.move_detail.setText("Начальная позиция партии.")
            initial = (self.review.get("analysis") or {}).get("assessments", [])
            assessment = next((item for item in initial if int(item.get("ply", -1)) == 0), None)
            self.selected_move_summary.setText(
                "Начальная позиция · " + (evaluation_label(assessment.get("white_cp"),
                assessment.get("mate_white")) if self.evaluation_allowed and assessment else
                "оценка отсутствует")
                + (f" · лучший ход: {position_best_label}" if position_best_label else ""))
        self._refresh_selected_ply_events()
        self.position_selected.emit(str(self.review["game"]["game_id"]), ply)

    def _review_board_move_intent(self, move: chess.Move) -> None:
        if self.training_mode:
            self._training_move_intent(move)
            return
        if not self._ephemeral_study or self._ephemeral_board is None:
            return
        if move not in self._ephemeral_board.legal_moves:
            return
        san = self._ephemeral_board.san(move)
        self._ephemeral_undo.append(self._ephemeral_board.copy(stack=True))
        self._ephemeral_board.push(move)
        self._ephemeral_branch.append(move)
        self.review_board.set_position(self._ephemeral_board)
        self.ephemeral_undo_button.setEnabled(True)
        self.ephemeral_reset_button.setEnabled(True)
        self.ephemeral_status.setText(
            f"Временная ветка: {len(self._ephemeral_branch)} ход(а) · последний ход {san}. "
            "Она не изменяет исходную линию и не запускает движок.")

    def undo_ephemeral_move(self) -> None:
        if not self._ephemeral_study or not self._ephemeral_undo:
            return
        self._ephemeral_board = self._ephemeral_undo.pop()
        if self._ephemeral_branch:
            self._ephemeral_branch.pop()
        self.review_board.set_position(self._ephemeral_board)
        self.ephemeral_undo_button.setEnabled(bool(self._ephemeral_undo))
        self.ephemeral_reset_button.setEnabled(bool(self._ephemeral_branch))
        self.ephemeral_status.setText(
            f"Временная ветка: {len(self._ephemeral_branch)} полуходов · исходный PGN не меняется.")

    def reset_ephemeral_branch(self) -> None:
        if not self._ephemeral_study or not self.review:
            return
        self._ephemeral_board = self.review["positions"][self.selected_ply].copy(stack=True)
        self._ephemeral_branch.clear()
        self._ephemeral_undo.clear()
        self.review_board.set_position(self._ephemeral_board)
        self.ephemeral_undo_button.setEnabled(False)
        self.ephemeral_reset_button.setEnabled(False)
        self.ephemeral_status.setText(
            f"Ветка сброшена к позиции после {self.selected_ply} полуходов; PGN не изменён.")

    def _phase_metrics_text(self, owner_color: str) -> str:
        if not self.evaluation_allowed:
            return self._blocked_analysis_text()
        if not self.review:
            return "Фазы: н/д"
        phase_by_ply = self._phase_by_ply()
        if not phase_by_ply or not any(value in {"opening", "middlegame", "endgame"}
                                       for value in phase_by_ply.values()):
            return "Фазы: классификация отсутствует · точность н/д"
        moves = self.review["moves"]
        phases = (("opening", "Дебют"), ("middlegame", "Миттельшпиль"),
                  ("endgame", "Эндшпиль"))
        rows = []
        analysis = self.review.get("analysis") or {}
        complete_classification = (analysis.get("status") == "complete"
                                   and analysis.get("source") == "stockfish")
        any_verified_strong = (complete_classification and
                               any(move.severity == "strong" for move in moves))
        for phase, label in phases:
            phase_moves = [move for move in moves if phase_by_ply.get(move.ply) == phase]
            if not phase_moves:
                continue
            sides = []
            for color, side_name in ((owner_color, "Я"),
                                     ("black" if owner_color == "white" else "white", "Соперник")):
                own = [move for move in phase_moves if move.mover == color]
                assessed = [move for move in own if move.cp_loss is not None
                            and move.severity != "unknown"]
                if assessed:
                    inaccuracies = sum(move.severity == "inaccuracy" for move in assessed)
                    mistakes = sum(move.severity == "mistake" for move in assessed)
                    blunders = sum(move.severity == "blunder" for move in assessed)
                    strong = (str(sum(move.severity == "strong" for move in assessed))
                              if any_verified_strong else "н/д")
                    counts = (f"неточн. {inaccuracies} · ошибок {mistakes} · "
                              f"зевков {blunders} · сильных {strong}")
                else:
                    counts = "неточн. н/д · ошибок н/д · зевков н/д · сильных н/д"
                sides.append(f"{side_name}: {counts} · охват {len(assessed)}/{len(own)}")
            rows.append(f"{label} · точность н/д\n" + "\n".join(sides))
        return "\n".join(rows) if rows else "Фазы: нет оценённых ходов · точность н/д"

    def _phase_by_ply(self) -> dict[int, str | None]:
        """Prefer measured move tags; use persisted classifier phase when present."""
        analysis = (self.review or {}).get("analysis") or {}
        phases = {int(row["ply"]): row.get("phase")
                  for row in analysis.get("move_metrics", [])}
        phases.update({ply: phase for ply, phase in (self.review or {}).get("phase_by_ply", {}).items()
                       if phase != "unknown"})
        for row in analysis.get("classifications", []):
            phase = row.get("phase")
            ply = int(row.get("ply", -1))
            if phase in {"opening", "middlegame", "endgame"} and phases.get(ply) in {None, "unknown"}:
                phases[ply] = phase
        return phases

    def _navigate_wheel(self, delta: int) -> None:
        if self.review and not self.training_mode:
            self.select_ply(self.selected_ply + (1 if delta > 0 else -1))

    def _graph_mode_changed(self, *_args) -> None:
        if hasattr(self, "graph"):
            delta = self.graph_mode.currentData() == "delta"
            self.graph.set_delta_mode(delta)
            self.settings.setValue("review/graph_mode", self.graph_mode.currentData())

    def _error_markers_changed(self, checked: bool) -> None:
        self.settings.setValue("review/error_markers", bool(checked))
        if hasattr(self, "graph"):
            self.graph.set_markers_visible(checked)

    def _refresh_selected_ply_events(self):
        self.event_list.clear()
        if not self.review or not self.evaluation_allowed:
            self.event_detail.setText(self.training_block_reason or
                                      "Оценки и события скрыты до проверки безопасности.")
            return
        analysis = self.review.get("analysis") or {}
        events = [row for row in analysis.get("events", [])
                  if int(row.get("ply", -1)) == self.selected_ply]
        alternatives = [row for row in analysis.get("alternatives", [])
                        if int(row.get("ply", -1)) == self.selected_ply]
        for row in events:
            event_name = {
                "blunder": "Зевок", "mistake": "Ошибка", "inaccuracy": "Неточность",
                "missed_win": "Упущенная возможность", "best_move": "Сохранённый лучший ход",
            }.get(str(row.get("event_type")), "Событие партии")
            confidence = {"high": "высокая уверенность", "medium": "средняя уверенность",
                          "low": "низкая уверенность", "unknown": "не подтверждено"}.get(
                              str(row.get("confidence_state")), "не подтверждено")
            self.event_list.addItem(f"{event_name} · {confidence}")
        assessment = next((row for row in analysis.get("assessments", [])
                           if int(row.get("ply", -1)) == self.selected_ply), None)
        pv = (assessment or {}).get("pv") or (assessment or {}).get("pv_uci") or ""
        pv_text = " ".join(pv) if isinstance(pv, (list, tuple)) else str(pv)
        details = [f"Позиция после {self.selected_ply} полуходов"]
        if pv_text:
            try:
                position = self.review["positions"][self.selected_ply].copy(stack=False)
                san_moves = []
                for raw in (pv if isinstance(pv, (list, tuple)) else pv_text.split()):
                    move = chess.Move.from_uci(str(raw))
                    if move not in position.legal_moves:
                        break
                    san_moves.append(position.san(move))
                    position.push(move)
                if san_moves:
                    details.append("Сохранённое продолжение: " + " ".join(san_moves))
            except (ValueError, IndexError, chess.InvalidMoveError):
                pass
        if alternatives:
            position = self.review["positions"][self.selected_ply].copy(stack=False)
            labels = []
            for row in alternatives:
                raw = row.get("uci") or row.get("move")
                try:
                    candidate = chess.Move.from_uci(str(raw)) if raw else None
                    label = position.san(candidate) if candidate in position.legal_moves else None
                except (ValueError, chess.InvalidMoveError):
                    label = None
                labels.append(str(label or row.get("san") or "вариант"))
            details.append("Сохранённые варианты: " + "; ".join(labels))
        if not events and not alternatives and not pv_text:
            details.append("Для этой позиции сохранённых событий или PV нет.")
        self.event_detail.setText("\n".join(details))

    def start_training(self):
        if not self.training_allowed or not self.evaluation_allowed or not self.review:
            self.training_status.setText(self.training_block_reason or "Упражнение сейчас недоступно.")
            return
        self.training_moves = eligible_error_moves(self.review)
        if not self.training_moves:
            self.training_status.setText("Нет пригодных собственных ошибок с проверенным улучшением.")
            return
        self.training_mode = True
        self.training_checked = self.training_improved = 0
        self.training_skipped = self.training_unverified = 0
        self._current_training_improved = False
        for button in (self.check_button, self.hint_button, self.skip_button,
                       self.return_review_button):
            button.setVisible(True)
        self.training_button.setVisible(False)
        for button in (self.previous_error_button, self.next_error_button,
                       self.analysis_button, self.open_lichess_button,
                       self.training_safety_button):
            button.hide()
        self.tabs.hide()
        self.left_metrics.hide()
        self.review_inspector.hide()
        self.selected_move_summary.hide()
        self.advantage_bar.hide()
        self.advantage_label.hide()
        self.moves_list.hide()
        self.advanced_group.hide()
        self.training_prompt.show()
        self.board_layout.setAlignment(self.review_board, Qt.AlignmentFlag.AlignHCenter)
        self.workspace_splitter.setSizes([0, 1, 0])
        self._fit_review_board()
        self.graph.hide()
        self.graph_title.setText("Режим упражнения · оценки скрыты")
        self.graph_legend.hide()
        self.training_index = 0
        self._load_training_error()

    def _load_training_error(self):
        move = self.training_moves[self.training_index]
        self.training_position = self.review["positions"][move.ply - 1].copy(stack=True)
        self.training_candidate = None
        self.training_proven = verified_improved_moves(self.review, move)
        self.review_board.set_position(self.training_position)
        self.review_board.set_hint_arrows(())
        self.review_board.set_flipped(self.review["owner_color"] == "black")
        self.review_board.set_interaction_enabled(True)
        self.training_status.setText(
            f"Твой ход — найди продолжение лучше сыгранного · ошибка {self.training_index + 1} из {len(self.training_moves)}")
        self.training_status.setVisible(True)
        side = "белых" if move.mover == "white" else "чёрных"
        self.training_prompt.setText(
            f"Найди улучшение после {move.san} · ход {side}\n"
            f"Позиция до ошибки · задача {self.training_index + 1} из {len(self.training_moves)}")
        self.check_button.setEnabled(False)
        self.check_button.setVisible(True)
        self.hint_button.setVisible(True)
        self.skip_button.setVisible(True)
        self.repeat_button.setVisible(False)
        self.continue_button.setVisible(False)
        self.move_detail.setText("Сделай один легальный ход на доске, затем нажми «Проверить».")

    def _training_move_intent(self, move: chess.Move):
        if not self.training_mode or self.training_position is None:
            return
        if move not in self.training_position.legal_moves:
            return
        self.training_candidate = move
        self.training_position.push(move)
        self.review_board.set_position(self.training_position)
        self.review_board.set_interaction_enabled(False)
        self.check_button.setEnabled(True)
        self.training_status.setText("Ход готов к проверке.")

    def check_training_move(self):
        if (not self.training_mode or not self.training_allowed or not self.evaluation_allowed
                or self.training_candidate is None):
            return
        self.training_checked += 1
        evidence = self.training_proven.get(self.training_candidate.uci())
        if evidence:
            played = self.training_moves[self.training_index]
            actual_cp = played.white_cp
            candidate_cp = evidence["score_white"]
            actual_text = (f"оценка сыгранного хода {actual_cp} cp" if actual_cp is not None
                           else "оценка сыгранного хода н/д")
            candidate_text = (f"сохранённый вариант {candidate_cp} cp" if candidate_cp is not None
                              else "сохранённый вариант без числовой оценки")
            before_position = self.training_position.copy(stack=True)
            before_position.pop()
            san = before_position.san(self.training_candidate)
            self._current_training_improved = True
            self.training_improved += 1
            self.training_status.setText(
                f"Найден улучшенный ход {san}; в партии было {played.san}. {actual_text}, "
                f"{candidate_text}, разница +{evidence['improvement_cp']} cp. "
                "Источник: сохранённый Stockfish run.")
            self.training_position.pop()
            self.training_position.push(self.training_candidate)
            self.check_button.setVisible(False)
            self.hint_button.setVisible(False)
            self.skip_button.setVisible(False)
            self.repeat_button.setVisible(True)
            self.continue_button.setVisible(True)
            self.review_board.set_interaction_enabled(False)
            self.move_detail.setText("Подтверждено сохранённой оценкой той же версии партии; PGN не изменён.")
        else:
            self.training_unverified += 1
            self.training_position.pop()
            self.review_board.set_position(self.training_position)
            self.training_candidate = None
            self.review_board.set_interaction_enabled(True)
            self.check_button.setEnabled(False)
            self.training_status.setText(
                "Не удалось достоверно проверить этот вариант по сохранённым оценкам. Он не помечен как неверный.")

    def show_hint(self):
        if (not self.training_mode or not self.training_allowed or not self.evaluation_allowed
                or not self.training_proven):
            return
        move = self.training_moves[self.training_index]
        choices = sorted(self.training_proven)
        try:
            candidate = chess.Move.from_uci(choices[0])
            if candidate not in self.training_position.legal_moves:
                return
            self.review_board.set_hint_arrows([candidate])
            self.training_status.setText(
                "Подсказка на доске: стрелка показывает начало одного подтверждённого улучшения. "
                "Можно запросить её повторно.")
        except (ValueError, TypeError, chess.InvalidMoveError):
            self.training_status.setText("Для этой ошибки нет доступной легальной подсказки.")

    def next_training_error(self, *, skipped: bool = False):
        if not self.training_mode:
            return
        if skipped:
            self.training_skipped += 1
        self.training_index += 1
        if self.training_index >= len(self.training_moves):
            summary = (f"Упражнение завершено · проверено {self.training_checked}, "
                       f"улучшено {self.training_improved}, пропущено {self.training_skipped}, "
                       f"не удалось проверить {self.training_unverified}. PGN не менялся.")
            self.stop_training(summary)
            return
        self._current_training_improved = False
        self._load_training_error()

    def _repeat_training_error(self):
        if self.training_mode:
            self._load_training_error()

    def stop_training(self, summary: str | None = None):
        if not self.training_mode:
            return
        self.training_mode = False
        self.review_board.set_hint_arrows(())
        self.training_candidate = None
        self.review_board.set_interaction_enabled(False)
        self.graph_title.setText("Оценка по ходу партии")
        self.graph_legend.show()
        self.graph.show()
        self.tabs.show()
        self.training_button.setVisible(True)
        self.training_prompt.hide()
        self.board_layout.setAlignment(
            self.review_board, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.left_metrics.show()
        self.review_inspector.show()
        self.selected_move_summary.show()
        self.advantage_bar.show()
        self.advantage_label.show()
        self.moves_list.show()
        self.advanced_group.show()
        self._review_splitter_user_sized = False
        self.workspace_splitter.setSizes([240, 360, 400])
        self._fit_review_board()
        self.training_button.setEnabled(self.training_allowed and bool(eligible_error_moves(self.review)))
        eligible = eligible_error_moves(self.review) if self.evaluation_allowed else []
        self.previous_error_button.setVisible(bool(eligible))
        self.next_error_button.setVisible(bool(eligible))
        game = self.review.get("game", {}) if self.review else {}
        finished = game.get("status") not in {"created", "started", "started_soon", "playing"}
        self.analysis_button.setVisible(bool(finished))
        self.training_safety_button.setVisible(bool(finished and not self.training_allowed))
        for button in (self.check_button, self.hint_button, self.skip_button,
                       self.repeat_button,
                       self.continue_button, self.return_review_button):
            button.setVisible(False)
        self.training_status.setText(summary or "Упражнение завершено. Исходная партия и PGN не менялись.")
        self.training_status.setVisible(bool(summary))
        self.select_ply(self.selected_ply)
