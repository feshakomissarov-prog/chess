"""Unified completed-game review workspace and ephemeral mistake exercise."""

from __future__ import annotations

import chess
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..board_view import BoardView
from ..lichess.game_review import (
    eligible_error_moves, load_game_review, verified_improved_moves,
)
from .display import (
    evaluation_label, format_clock_control, format_game_date, opening_tooltip, phase_label,
    russian_opening_name, severity_label, source_short,
)
from .move_table import MoveTable
from .theme import TOKENS


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
        self.coverage_text = ""
        self.move_san: dict[int, str] = {}
        self.setMinimumHeight(170)
        self.setMaximumHeight(220)
        self.setMinimumWidth(320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Интерактивный график оценки по ходам")
        self.setAccessibleDescription(
            "Горизонтальная ось — номер хода. Вертикальная — оценка позиции в пешках за белых, "
            "от −2 до +2. Стрелки влево и вправо перемещают выбор между оценёнными позициями.")

    def _values(self):
        if not self.safety_allowed:
            return []
        output = []
        for row in self.assessments:
            score = row.get("white_cp")
            mate = row.get("mate_white")
            if score is None and mate is not None:
                # Place mate markers at the corresponding edge; retain mate as a
                # distinct label instead of fabricating a centipawn score.
                score = 1000 if int(mate) > 0 else -1000 if int(mate) < 0 else 0
            if score is not None:
                output.append((int(row["ply"]), max(-200, min(200, int(score))),
                               int(mate) if mate is not None else None))
        return output

    @staticmethod
    def _continuous_segments(values):
        ordered = sorted(values, key=lambda row: int(row[0]))
        return [(first, second) for first, second in zip(ordered, ordered[1:])
                if int(second[0]) == int(first[0]) + 1]

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(42, 12, -16, -30)
        painter.fillRect(rect, QColor(TOKENS.surface))
        painter.setPen(QPen(QColor(TOKENS.border), 1))
        painter.drawRect(rect)
        values = self._values()
        max_ply = max(1, self.full_ply_count,
                      max((ply for ply, _score, _mate in values), default=0))
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        painter.drawText(2, rect.top() + 5, "+2.0")
        painter.drawText(10, rect.center().y() + 5, "0")
        painter.drawText(2, rect.bottom(), "−2.0")
        if not values:
            message = (self.safety_block_reason if not self.safety_allowed
                       else "Нет сохранённых оценок")
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        else:
            coords = []
            for ply, score, mate in values:
                x = rect.left() + round(rect.width() * ply / max_ply)
                y = rect.center().y() - round(rect.height() * score / 400)
                coords.append((x, y, ply, mate))
            painter.setPen(QPen(QColor(TOKENS.accent), 2))
            coordinate_by_ply = {row[2]: row for row in coords}
            for first, second in self._continuous_segments(values):
                a, b = coordinate_by_ply[first[0]], coordinate_by_ply[second[0]]
                painter.drawLine(a[0], a[1], b[0], b[1])
            for x, y, ply, mate in coords:
                marker = self.markers.get(ply, {})
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
                else:
                    painter.drawEllipse(x - 3, y - 3, 6, 6)
                if mate is not None:
                    painter.drawText(x + 5, y - 5, f"Мат {mate}" if mate > 0 else f"−Мат {abs(mate)}")
        selected_x = rect.left() + round(rect.width() * self.current_ply / max_ply)
        painter.setPen(QPen(QColor(TOKENS.text), 1, Qt.PenStyle.DashLine))
        painter.drawLine(selected_x, rect.top(), selected_x, rect.bottom())
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawText(rect.left(), self.height() - 5, "Ход 0 · оценка за белых")
        painter.drawText(rect.right() - 48, self.height() - 5, f"Ход {max_ply}")

    def select_ply(self, ply: int):
        self.current_ply = max(0, int(ply))
        self.update()

    def set_safety_gate(self, allowed: bool, reason: str = "") -> None:
        self.safety_allowed = bool(allowed)
        self.safety_block_reason = reason or "Оценки скрыты правилами Fair Play"
        self.update()

    def mousePressEvent(self, event):
        values = self._values()
        rect = self.rect().adjusted(42, 12, -16, -30)
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
        rect = self.rect().adjusted(42, 12, -16, -30)
        max_ply = max(1, self.full_ply_count,
                      max(ply for ply, _score, _mate in values))
        requested = round((event.position().x() - rect.left()) * max_ply /
                          max(1, rect.width()))
        ply, score, mate = min(values, key=lambda row: (abs(row[0] - requested), row[0]))
        assessment = next((row for row in self.assessments if int(row["ply"]) == ply), {})
        marker = self.markers.get(ply, {})
        coverage = self.coverage_text or f"{len(values)}/{self.full_ply_count + 1} сохранённых позиций"
        value = f"Мат {mate}" if mate is not None else evaluation_label(score)
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

    def __init__(self, repository, *, training_allowed: bool = False,
                 training_block_reason: str = "", parent=None):
        super().__init__(parent)
        self.repository = repository
        self.training_allowed = training_allowed
        self.evaluation_allowed = training_allowed
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
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        self.title = QLabel("Разбор партии")
        self.title.setObjectName("pageTitle")
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
        root.addWidget(self.subtitle)

        split = QSplitter(Qt.Orientation.Horizontal)
        board_column = QWidget()
        board_layout = QVBoxLayout(board_column)
        self.white_name = QLabel("Белые")
        self.white_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.review_board = BoardView(chess.Board())
        self.review_board.setAccessibleName("Доска просмотренной партии")
        self.review_board.setFixedSize(480, 480)
        self.review_board.set_interaction_enabled(False)
        self.review_board.set_premove_allowed(False)
        self.review_board.set_annotations_enabled(False)
        self.review_board.move_intent.connect(self._training_move_intent)
        self.black_name = QLabel("Чёрные")
        self.black_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        board_layout.addWidget(self.white_name)
        board_layout.addWidget(self.review_board, 0, Qt.AlignmentFlag.AlignCenter)
        board_layout.addWidget(self.black_name)
        split.addWidget(board_column)

        inspector = QWidget()
        inspector.setMinimumWidth(240)
        inspector_layout = QVBoxLayout(inspector)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Панель сведений о партии")
        moves_page = QWidget()
        moves_layout = QVBoxLayout(moves_page)
        self.side_metrics_summary = QLabel()
        self.side_metrics_summary.setWordWrap(True)
        moves_layout.addWidget(self.side_metrics_summary)
        self.selected_move_summary = QLabel("Выберите ход, чтобы увидеть его оценку.")
        self.selected_move_summary.setWordWrap(True)
        moves_layout.addWidget(self.selected_move_summary)
        self.moves_list = MoveTable()
        self.moves_list.setAccessibleName("Список ходов партии")
        self.moves_list.ply_selected.connect(self.select_ply)
        moves_layout.addWidget(self.moves_list, 1)
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
        inspector_layout.addWidget(self.tabs, 1)
        split.addWidget(inspector)
        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 4)
        self.workspace_splitter = split
        root.addWidget(split, 1)

        self.graph_title = QLabel("Оценка по ходу партии")
        self.graph_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(self.graph_title)
        self.graph_legend = QLabel("● хороший ход   ◆ неточность   ■ ошибка   ▲ зевок   · неизвестно — источник не классифицировал ход")
        self.graph_legend.setObjectName("secondary")
        root.addWidget(self.graph_legend)
        self.graph = ReviewGraph([], [])
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
        for button in (self.previous_error_button, self.next_error_button,
                       self.analysis_button, self.training_button, self.training_safety_button,
                       self.check_button, self.hint_button,
                       self.skip_button, self.continue_button, self.return_review_button):
            actions.addWidget(button)
            button.setVisible(False)
        actions.addWidget(self.repeat_button)
        self.repeat_button.setVisible(False)
        self.training_button.setVisible(True)
        self.training_safety_button.setVisible(False)
        self.training_status.setObjectName("secondary")
        actions.addWidget(self.training_status, 1)
        root.addLayout(actions)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.check_button.setEnabled(False)
        self.continue_button.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        splitter = getattr(self, "workspace_splitter", None)
        if splitter is not None:
            splitter.setOrientation(
                Qt.Orientation.Vertical if self.width() < 1120 else Qt.Orientation.Horizontal)

    def load_game(self, game: dict, *, training_allowed: bool | None = None,
                  training_block_reason: str | None = None) -> None:
        self.review = load_game_review(self.repository, game)
        if training_allowed is not None:
            self.training_allowed = training_allowed
            self.evaluation_allowed = training_allowed
        if training_block_reason is not None:
            self.training_block_reason = training_block_reason
        self.selected_ply = 0
        analysis = self.review["analysis"] or {}
        game_finished = game.get("status") not in {
            "created", "started", "started_soon", "playing"}
        has_engine_analysis = (analysis.get("source") == "stockfish" and
                               analysis.get("status") == "complete")
        self.analysis_button.setVisible(game_finished)
        self.analysis_button.setText("Повторить анализ Stockfish" if has_engine_analysis
                                     else "Проанализировать Stockfish")
        white = game.get("white_player") or "Белые"
        black = game.get("black_player") or "Чёрные"
        self.title.setText(f"{white} — {black}")
        source = analysis.get("source") or ""
        game_details = (f"{format_game_date(game.get('started_at'), precision=game.get('game_time_precision'))} · "
                        f"{format_clock_control(game.get('clock'))} · "
                        f"{russian_opening_name(game.get('eco'), game.get('opening_name'))}")
        self.subtitle.setText(game_details)
        self.subtitle.setToolTip(opening_tooltip(game.get("opening_name")))
        self.white_name.setText(f"♔  {white}")
        self.black_name.setText(f"♚  {black}")
        color = self.review["owner_color"]
        self.review_board.set_flipped(color == "black")
        self.review_board.set_position(self.review["positions"][0])
        assessment_by_ply = {int(item["ply"]): item for item in analysis.get("assessments", [])}
        self.moves_list.set_rows(
            self.review["moves"],
            scores=assessment_by_ply if self.evaluation_allowed else {},
            enabled=self.evaluation_allowed,
            show_severity=self.evaluation_allowed)
        metrics = self.review["per_side"]
        accuracy = self.review["official_accuracy"]
        accuracy_text = (f"Точность Lichess: белые {accuracy['white'] if accuracy['white'] is not None else '—'}, "
                         f"чёрные {accuracy['black'] if accuracy['black'] is not None else 'н/д'}"
                         if accuracy["source"] else "Официальная точность Lichess: нет источника")
        def side_counts(color: str) -> str:
            side = metrics[color]
            color_label = "Белые" if color == "white" else "Чёрные"
            if not side["assessed"]:
                return f"{color_label}: нет данных · охват 0/{side['total']} оценённых ходов"
            suffix = "" if side["assessed"] == side["total"] else " · только по оценённым ходам"
            return (f"{color_label}: хорошие {side['good']}, неточности {side['inaccuracy']}, "
                    f"ошибки {side['mistake']}, зевки {side['blunder']} · "
                    f"охват {side['assessed']}/{side['total']} оценённых ходов{suffix}")
        if self.evaluation_allowed:
            metrics_text = (
            f"Источник оценки: {source_short(source)[1]}\n"
            f"Оценено позиций: {self.review['coverage']['scored']}/{self.review['coverage']['total']}\n"
            f"{side_counts('white')}\n{side_counts('black')}\n"
            f"{accuracy_text}")
        else:
            metrics_text = (self.training_block_reason or
                            "Оценки, варианты и подсказки скрыты до проверки Fair Play.")
        self.metrics_label.setText(metrics_text)
        self.side_metrics_summary.setText(
            f"Я: {side_counts(color)}\nСоперник: "
            f"{side_counts('black' if color == 'white' else 'white')}\n{accuracy_text}"
            if self.evaluation_allowed else
            (self.training_block_reason or "Оценки скрыты до проверки безопасности."))
        self.side_metrics_summary.setToolTip(source_short(source)[1])
        self.selected_move_summary.setToolTip(source_short(source)[1])
        self.notes_list.clear()
        for note in self.review["notes"]:
            self.notes_list.addItem(f"ply {note.get('ply', 0)} · {note.get('body', '')}")
        self.tabs.setTabVisible(self.notes_tab_index, bool(self.review["notes"]))
        self.graph.assessments = analysis.get("assessments", []) if self.evaluation_allowed else []
        self.graph.full_ply_count = len(self.review["moves"])
        self.graph.analysis_source = str(analysis.get("source") or "")
        self.graph.analysis_status = str(analysis.get("status") or "")
        self.graph.move_san = {int(move.ply): str(move.san)
                               for move in self.review["moves"]}
        coverage = self.review.get("coverage", {})
        self.graph.coverage_text = f"{coverage.get('scored', 0)}/{coverage.get('total', 0)} позиций"
        self.graph.markers = ({move.ply: {"severity": move.severity, "mover": move.mover}
                               for move in self.review["moves"]}
                              if self.evaluation_allowed else {})
        self.graph.set_safety_gate(self.evaluation_allowed, self.training_block_reason)
        self.graph.select_ply(0)
        self._refresh_selected_ply_events()
        eligible = eligible_error_moves(self.review)
        self.previous_error_button.setVisible(self.evaluation_allowed and bool(eligible))
        self.next_error_button.setVisible(self.evaluation_allowed and bool(eligible))
        self.training_button.setText(
            f"Разобрать мои ошибки · {len(eligible)}" if self.evaluation_allowed
            else "Разобрать мои ошибки")
        reason = self.training_block_reason
        has_errors = bool(eligible)
        has_saved_analysis = bool(analysis and (analysis.get("assessments") or
                                                analysis.get("events") or
                                                analysis.get("alternatives")))
        allowed = self.training_allowed and has_errors
        self.training_button.setEnabled(allowed)
        self.training_button.setToolTip(reason if not self.training_allowed else
            "Нет завершённых собственных ошибок с проверенным улучшенным ходом." if not eligible else "")
        self.training_safety_button.setVisible(
            has_saved_analysis and not self.training_allowed)
        self.training_safety_button.setEnabled(
            has_saved_analysis and not self.training_allowed)
        self.training_safety_button.setToolTip(reason or "Проверить текущие активные партии Lichess.")
        self.training_status.setText("" if allowed else reason or (
            "Нет ошибок, для которых сохранённые оценки позволяют проверить улучшение."))

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

    def set_training_safety_result(self, allowed: bool, reason: str = ""):
        self.training_allowed = bool(allowed)
        self.evaluation_allowed = bool(allowed)
        self.training_block_reason = reason
        if not allowed:
            self.training_proven = {}
            self.training_candidate = None
        if not allowed and self.training_mode:
            self.stop_training(reason or "Упражнение остановлено: безопасность не подтверждена.")
        if self.review:
            game = self.review["game"]
            self.load_game(game, training_allowed=allowed,
                           training_block_reason=reason)
        self.training_safety_button.setText("Проверить безопасность")

    def set_safety_pending(self, reason: str = "Проверяем активные партии; оценки скрыты."):
        self.training_allowed = False
        self.evaluation_allowed = False
        self.training_block_reason = reason
        if self.training_mode:
            self.stop_training(reason)
        if self.review:
            self.graph.set_safety_gate(False, reason)
            self.graph.assessments = []
            self.graph.markers = {}
            self.review_board.set_hint_arrows(())
            self.selected_move_summary.setText(reason)
            self.moves_list.set_rows(self.review["moves"], scores={}, enabled=False,
                                     show_severity=False)
            self.event_list.clear()
            self.event_detail.setText(reason)
            self.metrics_label.setText(reason)
            self.move_detail.setText(reason)
        self.training_button.setEnabled(False)
        self.training_safety_button.setEnabled(True)
        self.training_safety_button.setText("Проверяем…")
        self.training_status.setText(reason)

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
        self.review_board.set_position(self.review["positions"][ply])
        self.review_board.set_hint_arrows(())
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
            delta_label = "изменение не рассчитано"
            if before_cp is not None and after_cp is not None:
                delta_label = f"изменение {int(after_cp) - int(before_cp):+d} сантипешек для белых"
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
            self.selected_move_summary.setText(
                f"Ход {move.san} · {severity_label(move.severity)}\n"
                f"До: {before_label} · после: {after_label} · {delta_label}\n{best_label}"
                + (f"\nЛучший ход из выбранной позиции: {position_best_label}"
                   if position_best_label else ""))
            self.selected_move_summary.setToolTip(
                f"{source_short(analysis.get('source'))[1]} · глубина {move.depth or '—'}")
            move_metric = next((item for item in analysis.get("move_metrics", [])
                                if int(item.get("ply", -1)) == ply), {})
            self.move_detail.setText(
                f"{move.san} · {severity_label(move.severity)} · "
                f"потеря {move.cp_loss if move.cp_loss is not None else 'н/д'} сантипешек · "
                f"{phase_label(move_metric.get('phase'))} · {source_short(move.source)[1]} · "
                f"глубина {move.depth or 'н/д'}")
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
        self.tabs.hide()
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
        self.training_button.setEnabled(self.training_allowed and bool(eligible_error_moves(self.review)))
        for button in (self.check_button, self.hint_button, self.skip_button,
                       self.repeat_button,
                       self.continue_button, self.return_review_button):
            button.setVisible(False)
        self.training_status.setText(summary or "Упражнение завершено. Исходная партия и PGN не менялись.")
        self.select_ply(self.selected_ply)
