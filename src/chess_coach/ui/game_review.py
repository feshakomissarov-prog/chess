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
from .theme import TOKENS


class ReviewGraph(QWidget):
    selected_ply = Signal(int)

    def __init__(self, assessments: list[dict], metrics: list[dict], parent=None):
        super().__init__(parent)
        self.assessments = assessments
        self.markers = {int(row["ply"]): row for row in metrics}
        self.current_ply = 0
        self.setMinimumHeight(170)
        self.setMaximumHeight(220)
        self.setMinimumWidth(320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Интерактивный график оценки по ходам")

    def _values(self):
        output = []
        for row in self.assessments:
            score = row.get("white_cp")
            mate = row.get("mate_white")
            if score is None and mate is not None:
                # Place mate markers at the corresponding edge; retain mate as a
                # distinct label instead of fabricating a centipawn score.
                score = 1000 if int(mate) > 0 else -1000 if int(mate) < 0 else 0
            if score is not None:
                output.append((int(row["ply"]), max(-1000, min(1000, int(score))),
                               int(mate) if mate is not None else None))
        return output

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(42, 12, -16, -30)
        painter.fillRect(rect, QColor(TOKENS.surface))
        painter.setPen(QPen(QColor(TOKENS.border), 1))
        painter.drawRect(rect)
        values = self._values()
        max_ply = max(1, max((ply for ply, _score, _mate in values), default=0))
        painter.setPen(QPen(QColor(TOKENS.secondary), 1))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        if not values:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Нет сохранённых оценок")
        else:
            coords = []
            for ply, score, mate in values:
                x = rect.left() + round(rect.width() * ply / max_ply)
                y = rect.center().y() - round(rect.height() * score / 2000)
                coords.append((x, y, ply, mate))
            painter.setPen(QPen(QColor(TOKENS.accent), 2))
            for first, second in zip(coords, coords[1:]):
                painter.drawLine(first[0], first[1], second[0], second[1])
            for x, y, ply, mate in coords:
                marker = self.markers.get(ply, {})
                severity = marker.get("severity")
                if severity in {"mistake", "blunder"}:
                    color = QColor(TOKENS.danger)
                elif severity == "inaccuracy":
                    color = QColor(TOKENS.warning)
                elif severity == "good":
                    color = QColor(TOKENS.success)
                else:
                    color = QColor(TOKENS.info)
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
        painter.drawText(rect.left(), self.height() - 5, "ply 0 · оценки со стороны белых")
        painter.drawText(rect.right() - 48, self.height() - 5, f"ply {max_ply}")

    def select_ply(self, ply: int):
        self.current_ply = max(0, int(ply))
        self.update()

    def mousePressEvent(self, event):
        values = self._values()
        if not values:
            return
        rect = self.rect().adjusted(42, 12, -16, -30)
        max_ply = max(1, max(ply for ply, _score, _mate in values))
        ply = round((event.position().x() - rect.left()) * max_ply / max(1, rect.width()))
        self.selected_ply.emit(max(0, min(max_ply, ply)))

    def keyPressEvent(self, event):
        values = self._values()
        if not values:
            super().keyPressEvent(event)
            return
        max_ply = max(ply for ply, _score, _mate in values)
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

    def __init__(self, repository, *, training_allowed: bool = False,
                 training_block_reason: str = "", parent=None):
        super().__init__(parent)
        self.repository = repository
        self.training_allowed = training_allowed
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
        self.moves_list = QListWidget()
        self.moves_list.setAccessibleName("Список ходов партии")
        self.moves_list.currentItemChanged.connect(self._move_selected)
        self.tabs.addTab(self.moves_list, "Ходы")
        facts = QWidget()
        self.metrics_layout = QVBoxLayout(facts)
        self.metrics_label = QLabel()
        self.metrics_label.setWordWrap(True)
        self.metrics_layout.addWidget(self.metrics_label)
        self.move_detail = QLabel("Выберите позицию на графике или ход в списке.")
        self.move_detail.setWordWrap(True)
        self.metrics_layout.addWidget(self.move_detail)
        self.tabs.addTab(facts, "Показатели")
        self.notes_list = QListWidget()
        self.tabs.addTab(self.notes_list, "Заметки")
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
        for button in (self.training_button, self.training_safety_button, self.check_button, self.hint_button,
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
        if training_block_reason is not None:
            self.training_block_reason = training_block_reason
        self.selected_ply = 0
        analysis = self.review["analysis"] or {}
        white = game.get("white_player") or "Белые"
        black = game.get("black_player") or "Чёрные"
        self.title.setText(f"{white} — {black}")
        source = analysis.get("source") or "локальная оценка не сохранена"
        self.subtitle.setText(
            f"{game.get('started_at') or 'Дата н/д'} · {game.get('clock') or 'Контроль н/д'} · "
            f"{game.get('status') or 'результат н/д'} · анализ: {source} · "
            f"охват позиций {self.review['coverage']['scored']}/{self.review['coverage']['total']}")
        self.white_name.setText(f"♔  {white}")
        self.black_name.setText(f"♚  {black}")
        color = self.review["owner_color"]
        self.review_board.set_flipped(color == "black")
        self.review_board.set_position(self.review["positions"][0])
        self.moves_list.clear()
        start = QListWidgetItem("Начальная позиция")
        start.setData(Qt.ItemDataRole.UserRole, 0)
        self.moves_list.addItem(start)
        for move in self.review["moves"]:
            prefix = f"{(move.ply + 1) // 2}." if move.mover == "white" else ""
            row = QListWidgetItem(f"{prefix}{move.san}   ·   {move.severity}")
            row.setData(Qt.ItemDataRole.UserRole, move.ply)
            self.moves_list.addItem(row)
        self.moves_list.setCurrentRow(0)
        metrics = self.review["per_side"]
        accuracy = self.review["official_accuracy"]
        accuracy_text = (f"Официальная точность Lichess: белые {accuracy['white'] if accuracy['white'] is not None else 'н/д'}, "
                         f"чёрные {accuracy['black'] if accuracy['black'] is not None else 'н/д'}"
                         if accuracy["source"] else "Официальная точность Lichess: нет источника")
        def side_counts(color: str) -> str:
            side = metrics[color]
            if not side["assessed"]:
                return f"{color}: н/д · охват 0/{side['total']} сравнимых ходов"
            suffix = "" if side["assessed"] == side["total"] else " · только по оценённым ходам"
            return (f"{color}: хорошие {side['good']}, неточности {side['inaccuracy']}, "
                    f"ошибки {side['mistake']}, зевки {side['blunder']} · "
                    f"охват {side['assessed']}/{side['total']} сравнимых ходов{suffix}")
        self.metrics_label.setText(
            f"Источник локальных классов: {source} · статус {analysis.get('status') or 'нет данных'}\n"
            f"Оценено позиций: {self.review['coverage']['scored']}/{self.review['coverage']['total']}\n"
            f"{side_counts('white')}\n{side_counts('black')}\n"
            f"{accuracy_text}")
        self.notes_list.clear()
        for note in self.review["notes"]:
            self.notes_list.addItem(f"ply {note.get('ply', 0)} · {note.get('body', '')}")
        self.graph.assessments = analysis.get("assessments", [])
        self.graph.markers = {move.ply: {"severity": move.severity, "mover": move.mover}
                              for move in self.review["moves"]}
        self.graph.select_ply(0)
        eligible = eligible_error_moves(self.review)
        self.training_button.setText(f"Разобрать мои ошибки · {len(eligible)}")
        reason = self.training_block_reason
        has_errors = bool(eligible)
        allowed = self.training_allowed and has_errors
        self.training_button.setEnabled(allowed)
        self.training_button.setToolTip(reason if not self.training_allowed else
            "Нет завершённых собственных ошибок с проверенным улучшенным ходом." if not eligible else "")
        self.training_safety_button.setVisible(has_errors and not self.training_allowed
                                               and not reason.startswith("Заблокировано:"))
        self.training_safety_button.setEnabled(has_errors and not self.training_allowed
                                               and not reason.startswith("Заблокировано:"))
        self.training_safety_button.setToolTip(reason or "Проверить текущие активные партии Lichess.")
        self.training_status.setText("" if allowed else reason or (
            "Нет ошибок, для которых сохранённые оценки позволяют проверить улучшение."))

    def _request_safety(self):
        if self.review:
            self.training_safety_button.setEnabled(False)
            self.training_safety_button.setText("Проверяем…")
            self.training_status.setText("Проверяем активные партии; подсказки пока скрыты.")
            self.safety_requested.emit(str(self.review["game"]["game_id"]))

    def set_training_safety_result(self, allowed: bool, reason: str = ""):
        self.training_allowed = bool(allowed)
        self.training_block_reason = reason
        if self.review:
            self.load_game(self.review["game"], training_allowed=allowed,
                           training_block_reason=reason)
        self.training_safety_button.setText("Проверить безопасность")

    def _export(self):
        if self.review:
            self.export_requested.emit(str(self.review["game"]["game_id"]))

    def _move_selected(self, current, _previous):
        if current is not None and not self.training_mode:
            self.select_ply(int(current.data(Qt.ItemDataRole.UserRole)))

    def select_ply(self, ply: int):
        if not self.review or self.training_mode:
            return
        ply = max(0, min(len(self.review["moves"]), int(ply)))
        self.selected_ply = ply
        self.review_board.set_position(self.review["positions"][ply])
        self.graph.select_ply(ply)
        self.moves_list.blockSignals(True)
        self.moves_list.setCurrentRow(ply)
        self.moves_list.blockSignals(False)
        if ply:
            move = self.review["moves"][ply - 1]
            self.move_detail.setText(
                f"{move.san} · {move.mover} · {move.severity} · "
                f"потеря {move.cp_loss if move.cp_loss is not None else 'н/д'} CP · "
                f"источник {move.source} · depth {move.depth or 'н/д'} / nodes {move.nodes or 'н/д'}")
        else:
            self.move_detail.setText("Начальная позиция партии.")
        self.position_selected.emit(str(self.review["game"]["game_id"]), ply)

    def start_training(self):
        if not self.training_allowed or not self.review:
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
        if not self.training_mode or self.training_candidate is None:
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
        if not self.training_mode or not self.training_proven:
            return
        move = self.training_moves[self.training_index]
        choices = sorted(self.training_proven)
        self.training_status.setText(
            f"Подсказка по запросу: подтверждённый ход {choices[0]} для ошибки на ply {move.ply}.")

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
