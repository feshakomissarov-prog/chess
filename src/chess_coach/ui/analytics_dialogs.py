"""Statistics and graph dialogs separated from the root window shell."""

from __future__ import annotations

import chess
import json
import threading
from datetime import datetime, timedelta, timezone
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)
from ..lichess.analysis import CLASSIFIER_VERSION
from ..lichess.repository import GameRepository
from ..lichess.statistics import StatisticsService
from .dialog_support import _clock_filter_sort_key, _game_date_label, _result_label
from .display import format_clock_control, opening_tooltip, russian_opening_name, source_short


class EvaluationGraph(QWidget):
    """Small evaluation-over-ply chart; score is always from White's point of view."""
    selected_ply = Signal(int)

    def __init__(self, assessments: list[dict], parent=None,
                 phase_boundaries: dict | None = None) -> None:
        super().__init__(parent)
        self.assessments = assessments
        self.phase_boundaries = phase_boundaries
        self.setMinimumHeight(210)
        self.setMinimumWidth(420)

    def _points(self):
        values = []
        for item in self.assessments:
            score = item.get("white_cp")
            if score is None and item.get("mate_white") is not None:
                score = 1000 if item["mate_white"] > 0 else -1000 if item["mate_white"] < 0 else 0
            if score is not None:
                values.append((int(item["ply"]), max(-1000, min(1000, int(score)))))
        return values

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(32, 12, -12, -26)
        points = self._points()
        max_ply = max(1, max((ply for ply, _ in points), default=0))
        boundaries = self.phase_boundaries or {}
        middle = boundaries.get("middle_start_ply")
        end = boundaries.get("end_start_ply")
        regions = []
        if middle is not None:
            regions.append((0, min(max_ply, int(middle)), "Дебют", QColor(225, 239, 228, 115)))
            if end is not None and int(end) >= int(middle):
                regions.append((int(middle), min(max_ply, int(end)), "Миттельшпиль",
                                QColor(237, 231, 214, 105)))
                regions.append((int(end), max_ply, "Эндшпиль", QColor(225, 232, 243, 115)))
            else:
                regions.append((int(middle), max_ply, "Фаза неизвестна",
                                QColor(235, 235, 235, 125)))
        elif end is not None:
            regions.append((0, min(max_ply, int(end)), "Фаза неизвестна",
                            QColor(235, 235, 235, 125)))
            regions.append((int(end), max_ply, "Эндшпиль", QColor(225, 232, 243, 115)))
        else:
            regions.append((0, max_ply, "Фаза неизвестна", QColor(235, 235, 235, 125)))
        for start, finish, label, color in regions:
            left = rect.left() + int(rect.width() * start / max_ply)
            right = rect.left() + int(rect.width() * finish / max_ply)
            if right > left:
                painter.fillRect(left, rect.top(), right - left, rect.height(), color)
                painter.setPen(QPen(QColor("#777777"), 1))
                painter.drawText(left + 3, rect.top() + 16, label)
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        if not points:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Нет оценок для графика")
            return
        coords = []
        for ply, score in points:
            x = rect.left() + int((rect.width() * ply) / max_ply)
            y = rect.center().y() - int((rect.height() * score) / 2000)
            coords.append((x, y))
        painter.setPen(QPen(QColor("#315f9b"), 2))
        for first, second in zip(coords, coords[1:]):
            painter.drawLine(first[0], first[1], second[0], second[1])
        painter.setBrush(QColor("#315f9b"))
        for x, y in coords:
            painter.drawEllipse(x - 3, y - 3, 6, 6)
        for boundary in (middle, end):
            if boundary is not None and 0 <= int(boundary) <= max_ply:
                x = rect.left() + int(rect.width() * int(boundary) / max_ply)
                painter.setPen(QPen(QColor("#777777"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(x, rect.top(), x, rect.bottom())
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawText(2, rect.top() + 8, "+10 pawns")
        painter.drawText(2, rect.bottom(), "−10")
        painter.drawText(rect.left(), self.height() - 4, "ply 0")
        painter.drawText(rect.right() - 35, self.height() - 4, f"ply {max_ply}")

    def mousePressEvent(self, event) -> None:
        points = self._points()
        if points:
            rect = self.rect().adjusted(32, 12, -12, -26)
            max_ply = max(1, max(ply for ply, _ in points))
            ply = round((event.position().x() - rect.left()) * max_ply / max(1, rect.width()))
            self.selected_ply.emit(max(0, min(max_ply, ply)))


class RatingTrendGraph(QWidget):
    game_selected = Signal(str)

    def __init__(self, points: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.points = points
        self.setMinimumHeight(150)
        self.setMinimumWidth(360)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect().adjusted(42, 12, -12, -28)
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawRect(rect)
        if not self.points:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Нет рейтинговых точек")
            return
        ratings = [int(item["rating"]) for item in self.points]
        minimum, maximum = min(ratings), max(ratings)
        spread = max(100, maximum - minimum)
        low, high = minimum - spread * .1, maximum + spread * .1
        coords = []
        for index, rating in enumerate(ratings):
            x = rect.left() + int(rect.width() * index / max(1, len(ratings) - 1))
            y = rect.bottom() - int(rect.height() * (rating - low) / max(1, high - low))
            coords.append((x, y))
        painter.setPen(QPen(QColor("#315f9b"), 2))
        for first, second in zip(coords, coords[1:]):
            painter.drawLine(*first, *second)
        painter.setBrush(QColor("#315f9b"))
        for x, y in coords:
            painter.drawEllipse(x - 3, y - 3, 6, 6)
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawText(2, rect.top() + 8, str(maximum))
        painter.drawText(2, rect.bottom(), str(minimum))

    def mousePressEvent(self, event) -> None:
        if not self.points:
            return
        rect = self.rect().adjusted(42, 12, -12, -28)
        index = round((event.position().x() - rect.left()) *
                      (len(self.points) - 1) / max(1, rect.width()))
        index = max(0, min(len(self.points) - 1, index))
        self.game_selected.emit(str(self.points[index]["game_id"]))


class StatisticsGameDialog(QDialog):
    position_requested = Signal(str, int)

    def __init__(self, repository: GameRepository, game: dict, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.game = game
        self.setWindowTitle("Партия · статистика и анализ")
        self.resize(900, 760)
        layout = QVBoxLayout(self)
        color = game.get("account_color") or game.get("color") or "нет данных"
        opponent = game.get("account_opponent") or game.get("opponent") or "не указан"
        result = _result_label(chess.WHITE if color == "white" else chess.BLACK,
                               game.get("winner"), game.get("status", ""))
        line = repository.current_line(game["game_id"])
        analysis = repository.latest_analysis_detail(
            game["game_id"], int(line["line_revision"]),
            account_id=game.get("account_id"), user_color=game.get("account_color") or game.get("color")) if line else None
        revision = int(line["line_revision"]) if line else int(game.get("line_revision", 1))
        opening = repository.opening_match(game["game_id"], revision)
        phases = repository.phase_boundaries(game["game_id"], revision)
        source = (analysis.get("source") if analysis else "нет локальной оценки")
        owner_accuracy = (analysis.get("official_game_accuracy") if analysis else
                          game.get("lichess_accuracy_white") if color == "white" else
                          game.get("lichess_accuracy_black"))
        lichess_origin = (game.get("source") in {"lichess", "lichess_archive"}
                          or game.get("source_game_id"))
        accuracy_text = (f"Официальная Lichess accuracy игрока ({color}): "
                         f"{owner_accuracy if owner_accuracy is not None else 'нет данных'}"
                         if lichess_origin else "Официальная Lichess accuracy: нет данных от Lichess")
        opening_text = (f"Справочник: {russian_opening_name(opening['eco'], opening['name'])} · "
                        f"совпадение до ply {opening['matched_ply']}"
                        if opening else "Справочник дебютов: совпадения пока нет")
        if opening and opening.get("lichess_conflict"):
            opening_text += (f" · название Lichess отличается: "
                             f"{russian_opening_name(game.get('opening_eco'), game.get('opening_name'))}")
        phase_text = (f"Фазы: {phases['source']} / {phases['method_version']} · "
                      f"middlegame={phases['middle_start_ply']}, endgame={phases['end_start_ply']}"
                      if phases else "Границы фаз неизвестны")
        header = QLabel(
            f"{_game_date_label(game)} · "
            f"{game.get('white_player') or 'Белые'} — {game.get('black_player') or 'Чёрные'}\n"
            f"Ваш цвет: {color}; соперник: {opponent}; результат: {result}\n"
            f"Источник оценки: {source}; дебют: "
            f"{russian_opening_name(game.get('opening_eco'), game.get('opening_name'))}\n"
            f"{opening_text}\n{accuracy_text}\n{phase_text}")
        header.setWordWrap(True)
        source_opening = opening_tooltip(opening.get("name") if opening else game.get("opening_name"))
        if opening and opening.get("lichess_conflict"):
            source_opening += "\nНазвание Lichess: " + str(game.get("opening_name") or "не указано")
        header.setToolTip(source_opening)
        layout.addWidget(header)
        details = QGridLayout()
        self.moves = QListWidget()
        self.moves.setMaximumHeight(170)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(130)
        self.errors = QListWidget()
        self.errors.setMaximumHeight(170)
        self.events = QListWidget()
        self.events.setMaximumHeight(145)
        self.graph_host = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_host)
        if analysis and analysis.get("assessments"):
            coverage = analysis.get("coverage") or {}
            total_positions = int(coverage.get("total_positions") or (len(line["moves"].split()) + 1))
            scored_positions = int(coverage.get("scored_positions") or 0)
            decision = analysis.get("decision_coverage") or {}
            own_moves = int(decision.get("own_moves") or 0)
            comparable_own = int(decision.get("comparable_own_moves") or 0)
            decision_text = (f"Сравнимость собственных ходов: {comparable_own}/{own_moves}"
                             + ("; первый ход не оценён: нет оценки позиции до хода"
                                if decision.get("first_own_move_missing_baseline") else ""))
            accuracy_state = analysis.get("local_accuracy_status") or {}
            accuracy_reason = accuracy_state.get(
                "reason", "Локальная accuracy не рассчитывается; CPL не является процентом точности.")
            review_coverage = analysis.get("review_coverage") or {}
            review_text = (f"Selective review: {review_coverage.get('reviewed_moves', 0)} "
                           f"оценённых критических ходов из {review_coverage.get('selected_moves', 0)} "
                           f"выбранных (не заменяет основной охват)" if review_coverage else
                           "Selective review: ещё не запускался")
            self.graph = EvaluationGraph(analysis["assessments"], self, phases)
            self.graph.selected_ply.connect(self._request_position)
            self.graph_layout.addWidget(self.graph)
            self.graph_layout.addWidget(QLabel(
                f"Оценено позиций: {scored_positions} / {total_positions}; "
                f"источник: {source}; статус: {analysis.get('status', 'unknown')}; "
                f"алгоритм ошибок: {CLASSIFIER_VERSION}\n{decision_text}\n{accuracy_reason}\n{review_text}"))
            metrics = analysis.get("move_metrics", [])
            for item in metrics:
                label = str(item.get("severity", "unknown"))
                if label in {"mistake", "blunder", "inaccuracy"}:
                    row = QListWidgetItem(
                        f"Ход {item['ply']} · {label} · {item.get('phase') or 'фаза неизвестна'} · "
                        f"{item.get('cp_loss')} CP · {item.get('source')}")
                    row.setData(Qt.ItemDataRole.UserRole, int(item["ply"]))
                    self.errors.addItem(row)
            severity_counts = {label: sum(1 for item in metrics
                                          if item.get("mover") == color and item.get("severity") == label)
                               for label in ("inaccuracy", "mistake", "blunder")}
            own_phase_counts = {}
            for item in metrics:
                if item.get("mover") == color:
                    phase = item.get("phase") or "unknown"
                    own_phase_counts.setdefault(phase, {"inaccuracy": 0, "mistake": 0, "blunder": 0})
                    if item.get("severity") in own_phase_counts[phase]:
                        own_phase_counts[phase][item["severity"]] += 1
            phase_summary = " · ".join(
                f"{phase}: {values['inaccuracy']}/{values['mistake']}/{values['blunder']}"
                for phase, values in sorted(own_phase_counts.items())) or "нет классифицированных ходов"
            self.graph_layout.addWidget(QLabel(
                f"Свои неточности/ошибки/зевки: {severity_counts['inaccuracy']}/"
                f"{severity_counts['mistake']}/{severity_counts['blunder']} "
                f"(сопоставимых оценённых ходов: {sum(1 for item in metrics if item.get('mover') == color and item.get('cp_loss') is not None)})\n"
                f"По фазам (неточности/ошибки/зевки): {phase_summary}\n"
                "Локальная точность: недоступна, formula_id не утверждён."))
            for event in analysis.get("events", []):
                confidence = event.get("confidence_state", "unknown")
                try:
                    evidence = json.loads(event.get("evidence_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    evidence = {}
                label = (f"Ход {event.get('ply')} · {event.get('event_type')} · "
                         f"{confidence} · {event.get('source')}")
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, int(event.get("ply") or 0))
                item.setToolTip(json.dumps(evidence, ensure_ascii=False, indent=2))
                self.events.addItem(item)
        else:
            self.graph_layout.addWidget(QLabel(
                "Нет сохранённой оценки. Анализ запускается отдельно из библиотеки "
                "только для завершённой партии."))
        if line:
            board = chess.Board() if line["initial_fen"] == "startpos" else chess.Board(line["initial_fen"])
            for ply, uci in enumerate(line["moves"].split(), start=1):
                move = chess.Move.from_uci(uci)
                san = board.san(move)
                board.push(move)
                item = QListWidgetItem(f"{(ply + 1) // 2}{'.' if ply % 2 else '...'} {san}")
                item.setData(Qt.ItemDataRole.UserRole, ply)
                self.moves.addItem(item)
        for note in repository.list_notes(game["game_id"],
                                          line_revision=int(line["line_revision"]) if line else None):
            ply = note.get("ply")
            self.notes.append(f"Ход {ply if ply is not None else '—'}: {note['body']}")
        details.addWidget(QLabel("Позиции / ходы"), 0, 0)
        details.addWidget(self.moves, 1, 0)
        details.addWidget(QLabel("Ключевые ошибки"), 0, 1)
        details.addWidget(self.errors, 1, 1)
        details.addWidget(QLabel("Заметки"), 2, 0)
        details.addWidget(self.notes, 3, 0)
        details.addWidget(self.graph_host, 2, 1, 2, 1)
        details.addWidget(QLabel("События анализа (двойной щелчок открывает позицию до хода; подсказка содержит доказательства)"), 4, 0, 1, 2)
        details.addWidget(self.events, 5, 0, 1, 2)
        layout.addLayout(details, 1)
        pgn = QTextEdit()
        pgn.setReadOnly(True)
        pgn.setPlainText(repository.export_pgn(game["game_id"]) or "PGN недоступен")
        pgn.setMaximumHeight(150)
        layout.addWidget(QLabel("PGN"))
        layout.addWidget(pgn)
        actions = QHBoxLayout()
        self.open_position = QPushButton("Открыть выбранную позицию")
        self.open_position.setEnabled(bool(line and line["moves"]))
        self.open_position.clicked.connect(self._open_selected_move)
        actions.addWidget(self.open_position)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.moves.itemDoubleClicked.connect(self._open_selected_move)
        self.errors.itemDoubleClicked.connect(self._open_error_position)
        self.events.itemDoubleClicked.connect(self._open_event_position)

    def _open_selected_move(self, *_args) -> None:
        item = self.moves.currentItem()
        if item is not None:
            self._request_position(int(item.data(Qt.ItemDataRole.UserRole)))

    def _open_error_position(self, item) -> None:
        self._request_position(max(0, int(item.data(Qt.ItemDataRole.UserRole)) - 1))

    def _open_event_position(self, item) -> None:
        # Event ply identifies the played move; show the position before that decision.
        self._request_position(max(0, int(item.data(Qt.ItemDataRole.UserRole)) - 1))

    def _request_position(self, ply: int) -> None:
        self.accept()
        self.position_requested.emit(self.game["game_id"], int(ply))


class StatisticsDialog(QDialog):
    loaded = Signal(int, object, object, object)
    query_finished = Signal(object)
    worker_started = Signal(object)
    worker_finished = Signal(object)
    opening_loaded = Signal(object)
    opening_finished = Signal(object)
    position_requested = Signal(str, int)
    game_requested = Signal(str)
    batch_requested = Signal()
    safety_requested = Signal()

    def __init__(self, repository: GameRepository, parent=None,
                 saved_filters: dict | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.service = StatisticsService(repository)
        self.setWindowTitle("Статистика и анализ")
        self.setMinimumSize(480, 360)
        self.resize(1040, 820)
        self._generation = 0
        self._query_thread: threading.Thread | None = None
        self._opening_thread: threading.Thread | None = None
        self._pending_refresh = False
        self._close_when_idle = False
        self._safety_display_state: bool | None = None
        self._rows: list[dict] = []
        root = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)
        self.analysis_mask = QWidget(self)
        mask_layout = QVBoxLayout(self.analysis_mask)
        self.analysis_mask_label = QLabel(
            "Аналитика временно скрыта до свежей проверки состояния Lichess.")
        self.analysis_mask_label.setWordWrap(True)
        mask_layout.addWidget(self.analysis_mask_label)
        self.analysis_safety_button = QPushButton("Проверить безопасность и открыть данные")
        self.analysis_safety_button.clicked.connect(self.safety_requested.emit)
        mask_layout.addWidget(self.analysis_safety_button, 0, Qt.AlignmentFlag.AlignLeft)
        mask_layout.addStretch(1)
        self.analysis_mask.hide()
        root.addWidget(self.analysis_mask, 1)
        overview_page = QWidget(self)
        overview_layout = QVBoxLayout(overview_page)
        games_page = QWidget(self)
        games_layout = QVBoxLayout(games_page)
        openings_page = QWidget(self)
        openings_layout = QVBoxLayout(openings_page)
        critical_page = QWidget(self)
        critical_layout = QVBoxLayout(critical_page)
        self.tabs.addTab(self._scroll_tab(overview_page), "Обзор")
        self.tabs.addTab(self._scroll_tab(games_page), "Партии")
        self.tabs.addTab(self._scroll_tab(openings_page), "Дебюты/фазы")
        self.tabs.addTab(self._scroll_tab(critical_page), "Критические моменты")
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        footer = QHBoxLayout()
        self.batch_button = QPushButton("Массовый анализ Stockfish…")
        self.batch_button.setToolTip(
            "Явно запускает анализ выбранных завершённых партий после проверки безопасности.")
        self.batch_button.clicked.connect(self.batch_requested.emit)
        footer.addWidget(self.batch_button)
        footer.addStretch(1)
        footer.addWidget(close_button)
        root.addLayout(footer)
        filters = QGridLayout()
        self.account = QComboBox()
        self.account.addItem("Все локальные партии (смешанный срез)", None)
        options = self.service.filter_options()
        for account in options["accounts"]:
            self.account.addItem(account["username"], account["account_id"])
        if options["accounts"]:
            self.account.setCurrentIndex(1)
        self.period = QComboBox()
        for title, days in (("Всё время", 0), ("30 дней", 30), ("90 дней", 90), ("1 год", 365)):
            self.period.addItem(title, days)
        self.period.setCurrentIndex(self.period.findData(90))
        self.clock = QComboBox()
        self.clock.addItem("Любой контроль", "")
        seen_clock_labels: set[str] = set()
        for value in sorted(set(options["clocks"]), key=_clock_filter_sort_key):
            label = format_clock_control(value)
            if label in seen_clock_labels:
                label = f"{label} ({value})"
            seen_clock_labels.add(label)
            self.clock.addItem(label, value)
        self.clock.setEditable(True)
        self.clock.lineEdit().setPlaceholderText("Любой контроль")
        self.side = QComboBox()
        self._fill_combo(self.side, (("Любой цвет", ""), ("Белые", "white"), ("Чёрные", "black")))
        self.result_filter = QComboBox()
        self._fill_combo(self.result_filter, (("Любой результат", ""), ("Победа", "win"),
                                               ("Ничья", "draw"), ("Поражение", "loss"),
                                               ("Неизвестно / прервана", "unknown")))
        self.game_source = QComboBox()
        self._fill_combo(self.game_source, (("Любой источник партии", ""),
                                             ("Lichess archive", "lichess_archive"),
                                             ("Локальная", "local")))
        self.opponent = QLineEdit()
        self.opponent.setPlaceholderText("Соперник")
        self.opening = QLineEdit()
        self.opening.setPlaceholderText("Дебют / ECO")
        self.analyzed = QComboBox()
        self._fill_combo(self.analyzed, (("Анализ: любой", ""), ("С анализом", "yes"),
                                          ("Без анализа", "no")))
        self.analysis_source = QComboBox()
        self._fill_combo(self.analysis_source, (("Любой источник оценки", ""),
                                                 ("Stockfish", "stockfish"),
                                                 ("Lichess", "lichess-pgn")))
        fields = (("Аккаунт", self.account), ("Период", self.period),
                  ("Контроль", self.clock), ("Цвет", self.side),
                  ("Результат", self.result_filter), ("Источник партии", self.game_source),
                  ("Соперник", self.opponent), ("Дебют / ECO", self.opening),
                  ("Анализ", self.analyzed), ("Источник оценки", self.analysis_source))
        for index, (title, widget) in enumerate(fields):
            row, column = divmod(index, 2)
            cell = QVBoxLayout()
            cell.addWidget(QLabel(title))
            cell.addWidget(widget)
            filters.addLayout(cell, row, column)
        basic_filters = QGridLayout()
        for index in range(4):
            item = filters.takeAt(0)
            basic_filters.addLayout(item.layout(), index // 2, index % 2)
        advanced = QGroupBox("Расширенные фильтры")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QGridLayout(advanced)
        advanced_content = QWidget(advanced)
        advanced_content_layout = QGridLayout(advanced_content)
        for index in range(6):
            item = filters.takeAt(0)
            advanced_content_layout.addLayout(item.layout(), index // 2,
                                              index % 2)
        advanced_layout.addWidget(advanced_content, 0, 0)
        advanced_content.setVisible(False)
        advanced.toggled.connect(advanced_content.setVisible)
        self.advanced_filters_group = advanced
        self.advanced_filters_content = advanced_content
        self.restore_filter_state(saved_filters or {})
        root.insertLayout(0, basic_filters)
        root.insertWidget(1, advanced)
        all_time = QPushButton("Всё время")
        all_time.clicked.connect(lambda: self.period.setCurrentIndex(self.period.findData(0)))
        overview_layout.addWidget(all_time)
        self.mixed_warning = QLabel(
            "Все локальные данные: цвета/стороны из разных аккаунтов могут смешиваться.")
        self.mixed_warning.setWordWrap(True)
        overview_layout.addWidget(self.mixed_warning)
        overview_note = QLabel("Что показывает: результаты и личный профиль. Источник — локальная SQLite; официальная точность берётся только из данных Lichess.")
        self.overview_description = overview_note
        overview_note.setWordWrap(True)
        overview_note.setObjectName("secondary")
        overview_layout.addWidget(overview_note)
        self.summary_label = QLabel("Загружаем статистику…")
        self.summary_label.setWordWrap(True)
        overview_layout.addWidget(self.summary_label)
        opening_actions = QHBoxLayout()
        self.opening_refresh_button = QPushButton("Обновить локальную классификацию 25 партий")
        self.opening_refresh_button.setToolTip(
            "Явный локальный пакет: только 25 новых партий, без сети и движка.")
        self.opening_refresh_button.clicked.connect(self._start_opening_refresh)
        opening_actions.addWidget(self.opening_refresh_button)
        self.opening_summary = QLabel("Локальные opening/phase агрегаты доступны по кнопке.")
        self.opening_summary.setWordWrap(True)
        opening_actions.addWidget(self.opening_summary, 1)
        openings_layout.addLayout(opening_actions)
        openings_note = QLabel("Дебюты и фазы строятся из сохранённых партий и локальной классификации; без данных показатели остаются пустыми.")
        self.openings_description = openings_note
        openings_note.setWordWrap(True)
        openings_note.setObjectName("secondary")
        openings_layout.addWidget(openings_note)
        self.opening_table = QListWidget()
        self.opening_table.itemDoubleClicked.connect(self._drill_down_opening)
        openings_layout.addWidget(self.opening_table, 1)
        self.critical_summary = QLabel("Выберите аккаунт для персональных срезов."
                                       " Критические позиции появятся после локального анализа.")
        self.critical_summary.setWordWrap(True)
        critical_layout.addWidget(self.critical_summary)
        self.critical_select_account = QPushButton("Выбрать аккаунт")
        self.critical_select_account.setEnabled(len(options["accounts"]) > 0)
        self.critical_select_account.clicked.connect(self._select_personal_analytics_account)
        critical_layout.addWidget(self.critical_select_account, 0, Qt.AlignmentFlag.AlignLeft)
        self.critical_description = QLabel("Показываются только подтверждённые события и позиции из выбранной выборки.")
        self.critical_description.setObjectName("secondary")
        self.critical_description.setWordWrap(True)
        critical_layout.addWidget(self.critical_description)
        self.trend = RatingTrendGraph([])
        self.trend.game_selected.connect(self._select_game_id)
        overview_layout.addWidget(self.trend)
        self.games = QListWidget()
        self.games_description = QLabel("Партии соответствуют активным фильтрам. Двойной щелчок открывает карточку и переход к позиции.")
        self.games_description.setObjectName("secondary")
        self.games_description.setWordWrap(True)
        games_layout.addWidget(self.games_description)
        self.games_empty = QLabel("В этой выборке партий нет. Измените фильтры или выберите другой аккаунт.")
        self.games_empty.setObjectName("secondary")
        self.games_empty.setWordWrap(True)
        self.games_empty.setVisible(False)
        games_layout.addWidget(self.games_empty)
        games_layout.addWidget(self.games, 1)
        self.page_label = QLabel()
        self.details_button = QPushButton("Открыть карточку партии")
        self.details_button.setEnabled(False)
        self.reset_button = QPushButton("Сбросить фильтры")
        buttons = QHBoxLayout()
        buttons.addWidget(self.page_label, 1)
        self.previous_page = QPushButton("←")
        self.next_page = QPushButton("→")
        buttons.addWidget(self.previous_page)
        buttons.addWidget(self.next_page)
        buttons.addWidget(self.details_button)
        buttons.addWidget(self.reset_button)
        games_layout.addLayout(buttons)
        self._page = 0
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._refresh_now)
        self.loaded.connect(self._apply_result)
        self.query_finished.connect(self._query_finished)
        self.opening_loaded.connect(self._opening_result)
        self.opening_finished.connect(self._opening_query_finished)
        for widget in (self.account, self.period, self.clock, self.side, self.result_filter,
                       self.game_source, self.analyzed, self.analysis_source):
            widget.currentIndexChanged.connect(self._schedule_refresh)
        self.clock.lineEdit().textChanged.connect(self._schedule_refresh)
        self.opponent.textChanged.connect(self._schedule_refresh)
        self.opening.textChanged.connect(self._schedule_refresh)
        self.games.currentRowChanged.connect(self._selection_changed)
        self.games.itemDoubleClicked.connect(lambda _item: self._open_selected_detail())
        self.details_button.clicked.connect(self._open_selected_detail)
        self.reset_button.clicked.connect(self.reset_filters)
        self.previous_page.clicked.connect(lambda: self._set_page(max(0, self._page - 1)))
        self.next_page.clicked.connect(lambda: self._set_page(self._page + 1))
        self._schedule_refresh()

    def set_safety_display_allowed(self, allowed: bool, reason: str = "") -> None:
        """Hide every cached statistics tab until the shared safety lease is current."""
        permitted = bool(allowed)
        changed = self._safety_display_state is None or self._safety_display_state != permitted
        self._safety_display_state = permitted
        self.tabs.setVisible(permitted)
        self.analysis_mask.setVisible(not permitted)
        self.analysis_safety_button.setEnabled(not permitted)
        if reason:
            self.analysis_mask_label.setText(str(reason))
        if permitted and changed:
            self._schedule_refresh()

    @staticmethod
    def _scroll_tab(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def restore_filter_state(self, state: dict) -> None:
        """Restore only small filter values; never restore query/result payloads."""
        combos = {
            "account_id": self.account, "period_days": self.period, "side": self.side,
            "result": self.result_filter, "game_source": self.game_source,
            "analyzed": self.analyzed, "analysis_source": self.analysis_source,
        }
        for key, combo in combos.items():
            if key not in state:
                continue
            index = combo.findData(state[key])
            if index >= 0:
                combo.setCurrentIndex(index)
        if state.get("clock"):
            index = self.clock.findData(str(state["clock"]))
            if index >= 0:
                self.clock.setCurrentIndex(index)
            else:
                self.clock.setCurrentText(format_clock_control(state["clock"]))
        self.opponent.setText(str(state.get("opponent") or ""))
        self.opening.setText(str(state.get("opening") or ""))
        self.advanced_filters_group.setChecked(bool(state.get("advanced", False)))
        self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1,
                                            int(state.get("tab", 0) or 0))))

    def persisted_filter_state(self) -> dict:
        return {
            "account_id": self.account.currentData(), "period_days": self.period.currentData(),
            "clock": self._clock_filter_value() or "", "side": self.side.currentData(),
            "result": self.result_filter.currentData(), "game_source": self.game_source.currentData(),
            "opponent": self.opponent.text().strip(), "opening": self.opening.text().strip(),
            "analyzed": self.analyzed.currentData(), "analysis_source": self.analysis_source.currentData(),
            "advanced": self.advanced_filters_group.isChecked(), "tab": self.tabs.currentIndex(),
        }

    @staticmethod
    def _fill_combo(combo: QComboBox, items: tuple[tuple[str, str], ...]) -> None:
        for label, value in items:
            combo.addItem(label, value)

    def _select_personal_analytics_account(self) -> None:
        if self.account.count() > 1 and self.account.currentData() is None:
            self.account.setCurrentIndex(1)
        self.tabs.setCurrentIndex(0)
        self.account.setFocus()

    def _filters(self) -> dict:
        days = int(self.period.currentData() or 0)
        now = datetime.now(timezone.utc)
        return {
            "account_id": self.account.currentData(),
            "since": (now - timedelta(days=days)).isoformat() if days else None,
            "until": (now + timedelta(milliseconds=1)).isoformat() if days else None,
            "opponent": self.opponent.text().strip() or None,
            "clock": self._clock_filter_value(),
            "game_source": self.game_source.currentData() or None,
            "analysis_source": self.analysis_source.currentData() or None,
            "side": self.side.currentData() or None,
            "opening": self.opening.text().strip() or None,
            "result": self.result_filter.currentData() or None,
            "analyzed": self.analyzed.currentData() or None,
        }

    def _clock_filter_value(self) -> str | None:
        if self.clock.currentIndex() >= 0:
            current = self.clock.currentText().strip()
            item_text = self.clock.itemText(self.clock.currentIndex())
            if current == item_text:
                return self.clock.currentData() or None
        raw = self.clock.currentText().strip()
        return raw or None

    def _schedule_refresh(self, *_args) -> None:
        if self._close_when_idle:
            return
        self._page = 0
        self._refresh_timer.start()

    def _set_page(self, value: int) -> None:
        self._page = value
        self._refresh_timer.start(0)

    def _refresh_now(self) -> None:
        if self._close_when_idle:
            return
        if self._query_thread is not None and self._query_thread.is_alive():
            self._pending_refresh = True
            return
        self._generation += 1
        generation, filters, page = self._generation, self._filters(), self._page
        self.summary_label.setText("Обновляем статистику…")

        def work() -> None:
            try:
                summary = self.service.summary(**filters)
                parties = self.service.game_page(**filters, page=page, page_size=100)
                if filters["account_id"]:
                    summary["personal_profile"] = self.service.personal_profile(
                        account_id=filters["account_id"], clock=filters["clock"],
                        side=filters["side"], analysis_source=filters["analysis_source"])
                self.loaded.emit(generation, summary, parties, None)
            except Exception as exc:
                self.loaded.emit(generation, None, None, str(exc))
            finally:
                self.query_finished.emit(threading.current_thread())

        thread = threading.Thread(target=work, name="chess-coach-statistics", daemon=True)
        self._query_thread = thread
        self.worker_started.emit(thread)
        thread.start()

    def _start_opening_refresh(self) -> None:
        if self._opening_thread is not None and self._opening_thread.is_alive():
            return
        filters = self._filters()
        self.opening_refresh_button.setEnabled(False)
        self.opening_summary.setText("Сопоставляем локальный справочник для 25 последних партий…")

        def work() -> None:
            try:
                from ..lichess.opening_matcher import OpeningMatcher
                matched = OpeningMatcher().match_recent(self.repository, limit=25)
                phases = self.repository.infer_missing_phase_boundaries(limit=25)
                groups = StatisticsService(self.repository).opening_statistics(
                    account_id=filters["account_id"], analysis_source=filters["analysis_source"],
                    since=filters["since"], until=filters["until"], min_games=1)
                payload = {"matched": matched, "phases": phases, "groups": groups}
            except Exception as exc:
                payload = {"error": str(exc)}
            self.opening_loaded.emit(payload)
            self.opening_finished.emit(threading.current_thread())

        thread = threading.Thread(target=work, name="chess-coach-opening-index", daemon=True)
        self._opening_thread = thread
        self.worker_started.emit(thread)
        thread.start()

    def _opening_result(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("error"):
            self.opening_summary.setText(f"Не удалось обновить opening/phase facts: {payload['error']}")
            return
        groups = payload.get("groups") or []
        lines = [f"Дебютов/цветовых групп: {len(groups)} · новых совпадений: {payload['matched']} · "
                 f"границ фаз: {payload['phases']}"]
        lines.extend(f"{row['eco']} {row['opening']} · {row['my_color']} · "
                     f"партий {row['games']} · оценено своих ходов {row['evaluated_own_moves']} · "
                     f"ошибок/100 {row['own_errors_per_100_evaluated'] if row['own_errors_per_100_evaluated'] is not None else 'н/д'} · "
                     f"{row['analysis_source']}"
                     for row in groups[:8])
        self.opening_summary.setText("\n".join(lines))
        self.opening_table.clear()
        for row in groups:
            label = (f"{row['eco']} · {row['opening']} · {row['my_color']} · "
                     f"{row['games']} партий · оценено ходов {row['evaluated_own_moves']} · "
                     f"ошибок/100: {row['own_errors_per_100_evaluated'] if row['own_errors_per_100_evaluated'] is not None else 'н/д'}")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row.get("opening") or "")
            self.opening_table.addItem(item)
        self._schedule_refresh()

    def _drill_down_opening(self, item: QListWidgetItem) -> None:
        opening = item.data(Qt.ItemDataRole.UserRole)
        if opening:
            self.opening.setText(str(opening))
            self.tabs.setCurrentIndex(1)

    def _opening_query_finished(self, thread: threading.Thread) -> None:
        thread.join(timeout=0.05)
        if self._opening_thread is thread:
            self._opening_thread = None
        self.opening_refresh_button.setEnabled(True)
        self.worker_finished.emit(thread)
        if self._close_when_idle and self._query_thread is None:
            super().accept()

    def _apply_result(self, generation: int, summary: object, parties: object, error: object) -> None:
        if error:
            self.summary_label.setText(f"Не удалось построить статистику: {error}")
        elif generation == self._generation and isinstance(summary, dict) and isinstance(parties, dict):
            self._rows = parties["items"]
            self.games.clear()
            self.games_empty.setVisible(not self._rows)
            for game in self._rows:
                me_color = game.get("account_color") or game.get("color")
                outcome = _result_label(chess.WHITE if me_color == "white" else chess.BLACK,
                                        game.get("winner"), game.get("status", ""))
                player = game.get("account_player_name") or "Вы"
                opponent = game.get("account_opponent") or game.get("opponent") or "соперник"
                eco = game.get("matched_opening_eco") or game.get("opening_eco") or "ECO —"
                opening_original = game.get("matched_opening_name") or game.get("opening_name")
                opening_name = russian_opening_name(eco, opening_original)
                assessed = game.get("own_assessed_moves")
                total_own = game.get("total_own_moves")
                scored = game.get("scored_positions")
                total_positions = game.get("total_positions")
                coverage = (f"Оценено позиций: {scored if scored is not None else '—'}/"
                            f"{total_positions if total_positions is not None else '—'} · "
                            f"моих ходов: {assessed if assessed is not None else '—'}/"
                            f"{total_own if total_own is not None else '—'}"
                            if game.get("analysis_status") else "Оценки пока нет")
                if assessed is None:
                    error_counts = "Ошибки по моим ходам: нет данных"
                elif assessed == 0:
                    error_counts = (f"Нет оценённых моих ходов (0/{total_own if total_own is not None else '—'}).")
                else:
                    counts = (game.get("own_inaccuracies"), game.get("own_mistakes"),
                              game.get("own_blunders"))
                    error_counts = (f"Неточности {counts[0] if counts[0] is not None else '—'} · "
                                    f"ошибки {counts[1] if counts[1] is not None else '—'} · "
                                    f"зевки {counts[2] if counts[2] is not None else '—'} · "
                                    f"оценено ходов {assessed}")
                accuracy = game.get("owner_lichess_accuracy")
                accuracy_label = (f"Точность Lichess {accuracy:.1f}%" if accuracy is not None else
                                  "Точность Lichess —")
                conflict = " · конфликт названия" if game.get("opening_conflict") else ""
                short_source, source_detail = source_short(game.get("selected_analysis_source"))
                item = QListWidgetItem(
                    f"{_game_date_label(game)} · {outcome} · "
                    f"{player} — {opponent} · {opening_name}{conflict}\n"
                    f"{format_clock_control(game.get('clock'))} · {accuracy_label} · "
                    f"{error_counts} · {coverage} · источник {short_source}")
                item.setToolTip(f"Дебют в источнике: {opening_original or 'не указан'}\n{source_detail}")
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                             f"Партия. {outcome}. {player} против {opponent}. {opening_name}. "
                             f"Источник оценки: {source_detail}.")
                item.setData(Qt.ItemDataRole.UserRole, game["game_id"])
                self.games.addItem(item)
            total = summary["games"]
            coverage = summary["move_coverage_percent"]
            coverage_text = ("нет данных" if coverage is None else
                             f"сравнимые собственные ходы {summary['evaluated_moves']}/"
                             f"{summary['total_own_moves']} ({coverage}%) · оценено позиций: "
                             f"{summary['scored_positions']}")
            errors = summary["classifications_by_phase"]
            classified = {kind: sum(side_counts.get(kind, 0)
                                    for phase in errors.values()
                                    for side_counts in phase.values())
                          for kind in ("inaccuracy", "mistake", "blunder")}
            classified_text = (
                f"Мои неточности / ошибки / зевки: {classified['inaccuracy']} / "
                f"{classified['mistake']} / {classified['blunder']}"
                if int(summary.get("own_assessed_moves") or 0) > 0 else
                "Классификация собственных ходов: нет данных")
            accuracy = summary["lichess_accuracy"]
            scope = ("Смешанные локальные аккаунты; результаты по цвету в профиле партии."
                     if not self.account.currentData() else
                     f"Аккаунт: {self.account.currentText()}")
            self.mixed_warning.setText(scope)
            self.summary_label.setText(
                f"Партии: {total} · победы {summary['wins']} · ничьи {summary['draws']} · "
                f"поражения {summary['losses']}\n"
                f"Анализ есть у {summary['analysis_games']}/{total} партий · {coverage_text}\n"
                f"{classified_text} · ошибки на 100 оценённых моих ходов: "
                f"{summary['own_error_rate_per_100'] if summary['own_error_rate_per_100'] is not None else 'нет данных'}\n"
                f"Официальная точность Lichess: белые {accuracy['white'] if accuracy['white'] is not None else 'нет данных'}, "
                f"чёрные {accuracy['black'] if accuracy['black'] is not None else 'нет данных'}. "
                "Н/д означает отсутствие подходящего источника или знаменателя, а не нулевой результат.")
            profile = summary.get("personal_profile")
            if isinstance(profile, dict):
                sections = []
                for color, segment in profile["color_segments"].items():
                    stats = segment["current"]
                    official = segment["current_official_accuracy"]
                    official_text = (f"{official['accuracy']}% ({official['n']}/{official['games']} игр, Lichess)"
                                     if official["accuracy"] is not None else
                                     f"нет официальной accuracy ({official['n']}/{official['games']} игр)")
                    quality = (f"{stats['own_error_rate_per_100']} ошибок/100 оценённых ходов"
                               if stats["own_error_rate_per_100"] is not None else "ошибки/100: нет знаменателя")
                    average_errors = (f"среднее ошибок на анализированную игру: "
                                      f"{stats['avg_mistakes_per_analyzed_game']} ошибок + "
                                      f"{stats['avg_blunders_per_analyzed_game']} зевков "
                                      f"(N={stats['analyzed_games_with_classifications']})")
                    confidence = (f"предварительно: {segment['provisional_reason']}"
                                  if segment["provisional"] else "выборка >=30 полных партий")
                    sections.append(f"{('Белые' if color == 'white' else 'Чёрные')}, "
                                    f"{segment['current_window']}: игр {stats['games']}, "
                                    f"полных анализов {segment['complete_games']}, {confidence}; "
                                    f"официальная accuracy {official_text}; {quality}; {average_errors}")
                    diagnostics = segment["opening_phase_diagnostics"]
                    phase_lines = [f"{item['phase']}: {item['errors']} ошибок / "
                                   f"{item['evaluated_moves']} оценённых собственных ходов"
                                   for item in diagnostics["phases"][:3] if item["evaluated_moves"]]
                    opening_lines = [f"{item['eco']} {item['opening']}: {item['errors']} ошибок / "
                                     f"{item['evaluated_moves']} ходов в {item['games']} партиях"
                                     + ("; возможная повторяющаяся проблема требует проверки"
                                        if item["hypothesis_eligible"] else "; выборки недостаточно для гипотезы")
                                     for item in diagnostics["openings"][:3] if item["evaluated_moves"]]
                    focus_lines = [f"{item['game_id']} ply {item['ply']} ({item['phase']}): "
                                   f"{item['label']} {item['eval_loss_cp']} cp"
                                   for item in diagnostics["current_focus"][:3]]
                    sections.append("Срезы по фазам: " + ("; ".join(phase_lines) if phase_lines else "нет данных")
                                    + "\nСрезы по дебютам: "
                                    + ("; ".join(opening_lines) if opening_lines else "нет данных")
                                    + "\nДоказательные позиции для проверки: "
                                    + ("; ".join(focus_lines) if focus_lines else "нет ошибок в текущем срезе"))
                timeline = profile.get("historical_timeline") or []
                history_lines = [f"{item['period']} {('белыми' if item['my_color']=='white' else 'чёрными')}: "
                                 f"{item['games']} игр, W/D/L {item['wins']}/{item['draws']}/{item['losses']}, "
                                 f"accuracy {item['official_accuracy'] if item['official_accuracy'] is not None else 'н/д'} "
                                 f"(n={item['accuracy_n']})"
                                 for item in timeline[-6:]]
                comparison = profile["period_comparison_90d_vs_previous_90d"]
                comparison_text = "Сравнение 90-дневных периодов: " + "; ".join(
                    ("белые" if color == "white" else "чёрные") + " — " + value["explanation"]
                    for color, value in comparison.items())
                self.summary_label.setText(self.summary_label.text() + "\n\nТЕКУЩИЙ ПРОФИЛЬ "
                    f"(аккаунт; контроль: {profile['clock_scope']})\n" + "\n".join(sections) + "\n"
                    + ("Профиль предварительный при смешанных контролях. Выберите конкретный контроль времени.\n"
                       + "Контроли: " + "; ".join(f"{item['clock']}: {item['games']} игр, "
                           f"покрытие {item['coverage_percent'] if item['coverage_percent'] is not None else 'н/д'}%"
                           for item in profile.get("time_control_buckets", [])) + "\n"
                       if profile.get("time_control_buckets") else "")
                    + comparison_text + "\nИСТОРИЯ ПО МЕСЯЦАМ\n"
                    + ("\n".join(history_lines) if history_lines else "Исторических партий нет."))
            elif not self.account.currentData():
                self.summary_label.setText(self.summary_label.text() +
                    "\n\nПерсональный профиль: выберите конкретный аккаунт; смешанные аккаунты "
                    "не используются для вывода о слабых сторонах.")
            profile_text, marker, profile_details = self.summary_label.text().partition(
                "\n\nТЕКУЩИЙ ПРОФИЛЬ ")
            self.summary_label.setText(profile_text)
            self.critical_summary.setText(
                "ТЕКУЩИЙ ПРОФИЛЬ " + profile_details if marker else
                ("Выберите конкретный аккаунт для персональных срезов. Смешанные аккаунты "
                 "не превращаются в диагноз." if not self.account.currentData() else
                 "Для этого аккаунта пока недостаточно данных профиля."))
            if isinstance(profile, dict):
                official = []
                for color, segment in profile["color_segments"].items():
                    accuracy = segment["current_official_accuracy"]
                    label = "белые" if color == "white" else "чёрные"
                    value = (f"{accuracy['accuracy']}% ({accuracy['n']}/{accuracy['games']} игр)"
                             if accuracy["accuracy"] is not None else
                             f"нет данных ({accuracy['n']}/{accuracy['games']} игр)")
                    official.append(f"{label}: {value}")
                if official:
                    self.summary_label.setText(
                        self.summary_label.text() + "\nОфициальная точность Lichess — "
                        + "; ".join(official))
            self.trend.points = summary["rating_trend"]
            self.trend.update()
            self.page_label.setText(f"Строки {self._page * 100 + 1}–"
                                    f"{self._page * 100 + len(self._rows)} из {parties['total']}")
            self.previous_page.setEnabled(self._page > 0)
            self.next_page.setEnabled(self._page + 1 < parties["pages"])
            self._selection_changed(self.games.currentRow())
    def _query_finished(self, thread: threading.Thread) -> None:
        thread.join(timeout=0.05)
        if self._query_thread is thread:
            self._query_thread = None
        self.worker_finished.emit(thread)
        if self._pending_refresh:
            self._pending_refresh = False
            self._refresh_timer.start(0)
        elif self._close_when_idle and self._opening_thread is None:
            super().accept()

    def _selection_changed(self, row: int) -> None:
        self.details_button.setEnabled(0 <= row < len(self._rows))

    def _select_game_id(self, game_id: str) -> None:
        for index, game in enumerate(self._rows):
            if game["game_id"] == game_id:
                self.games.setCurrentRow(index)
                self._open_selected_detail()
                return

    def _open_selected_detail(self) -> None:
        index = self.games.currentRow()
        if not 0 <= index < len(self._rows):
            return
        self.game_requested.emit(str(self._rows[index]["game_id"]))

    def reset_filters(self) -> None:
        for combo in (self.account, self.clock, self.side, self.result_filter,
                      self.game_source, self.analyzed, self.analysis_source):
            combo.setCurrentIndex(0)
        self.period.setCurrentIndex(self.period.findData(90))
        self.opponent.clear()
        self.opening.clear()

    def closeEvent(self, event) -> None:
        if ((self._query_thread is not None and self._query_thread.is_alive())
                or (self._opening_thread is not None and self._opening_thread.is_alive())):
            self._close_when_idle = True
            self.summary_label.setText("Завершаем текущую выборку; окно закроется после сохранения результата.")
            event.ignore()
            return
        super().closeEvent(event)

    def accept(self) -> None:
        if ((self._query_thread is not None and self._query_thread.is_alive())
                or (self._opening_thread is not None and self._opening_thread.is_alive())):
            self._close_when_idle = True
            self.summary_label.setText("Завершаем текущую выборку перед закрытием экрана.")
            return
        super().accept()
