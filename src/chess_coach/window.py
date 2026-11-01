"""Main M0 window and settings dialog."""

from __future__ import annotations

import chess
import json
import time
import threading
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import uuid
import weakref
import PySide6
import shiboken6
from PySide6.QtCore import QProcess, QSettings, Qt, QStandardPaths, QTimer, Signal, qVersion
from PySide6.QtGui import QKeySequence, QShortcut, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QFileDialog,
    QGridLayout,
    QLineEdit,
    QPushButton,
    QProgressDialog,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .board_view import BoardView
from .controller import GameController
from .lichess.auth import CredentialStorageError, WindowsCredentialStore
from .lichess.analysis import AnalysisOptions, CLASSIFIER_VERSION, CompletedGameAnalyzer
from .lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from .lichess.repository import GameRepository
from .lichess.service import LichessService
from .lichess.session import LichessGameSession, classify_lichess_opponent
from .lichess.statistics import StatisticsService


def _puzzle_attempt_summary(attempts: list[dict]) -> str:
    """Describe only the displayed local attempts; repeated puzzle IDs count per try."""
    if not attempts:
        return "Попыток в этой выборке нет."
    wins = sum(int(bool(item["win"])) for item in attempts)
    ratings = [int(item["puzzle_rating"]) for item in attempts
               if item.get("puzzle_rating") is not None]
    themes: dict[str, list[int]] = {}
    for item in attempts:
        try:
            values = json.loads(item.get("themes_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []
        for theme in values if isinstance(values, list) else []:
            aggregate = themes.setdefault(str(theme), [0, 0])
            aggregate[0] += 1
            aggregate[1] += int(bool(item["win"]))
    theme_text = ", ".join(
        f"{theme}: {values[1]}/{values[0]} ({round(100 * values[1] / values[0])}%)"
        for theme, values in sorted(themes.items())) or "нет данных"
    average = round(sum(ratings) / len(ratings)) if ratings else "н/д"
    rate = round(100 * wins / len(attempts), 1)
    return (f"В показанной выборке: {len(attempts)} попыток · решено {wins} ({rate}%) · "
            f"средний рейтинг задачи {average}. Темы (успешно/попытки): {theme_text}. "
            "Повторные попытки одной задачи учитываются отдельно.")


def _game_date_label(game: dict) -> str:
    started = game.get("started_at")
    if not started:
        return "дата неизвестна"
    if game.get("game_time_precision") == "date_only":
        return f"{str(started)[:10]} (точность до дня)"
    return str(started)


def _batch_safety_from_games(games: object) -> bool | None:
    """Classify one complete /api/account/playing snapshot; uncertainty blocks UCI."""
    if not isinstance(games, list) or len(games) >= 50:
        return None
    return not any(classify_lichess_opponent(game) != "official_ai" for game in games)


class OnlineGameDialog(QDialog):
    def __init__(self, title: str, include_level: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QVBoxLayout(self)
        self.color = QComboBox()
        self.color.addItem("Случайный", "random")
        self.color.addItem("Белыми", "white")
        self.color.addItem("Чёрными", "black")
        form.addWidget(QLabel("Ваш цвет"))
        form.addWidget(self.color)
        self.clock = QComboBox()
        self.clock.addItem("Без часов", (None, 0))
        self.clock.addItem("3 + 2", (180, 2))
        self.clock.addItem("10 + 0", (600, 0))
        self.clock.addItem("15 + 10", (900, 10))
        form.addWidget(QLabel("Контроль времени"))
        form.addWidget(self.clock)
        self.rated = QCheckBox("Рейтинговая партия (изменит рейтинг)")
        if not include_level:
            form.addWidget(self.rated)
        self.level = QComboBox()
        for level in range(1, 9):
            self.level.addItem(f"Lichess AI · уровень {level}", level)
        if include_level:
            form.addWidget(self.level)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addWidget(buttons)

    def selection(self):
        return self.color.currentData(), *self.clock.currentData(), self.level.currentData(), self.rated.isChecked()


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
        opening_text = (f"Справочник: {opening['eco']} {opening['name']} · совпадение до ply {opening['matched_ply']}"
                        if opening else "Справочник дебютов: совпадения пока нет")
        if opening and opening.get("lichess_conflict"):
            opening_text += (f" · конфликт с Lichess: {game.get('opening_eco') or 'ECO —'} "
                             f"{game.get('opening_name') or ''}")
        phase_text = (f"Фазы: {phases['source']} / {phases['method_version']} · "
                      f"middlegame={phases['middle_start_ply']}, endgame={phases['end_start_ply']}"
                      if phases else "Границы фаз неизвестны")
        header = QLabel(
            f"{_game_date_label(game)} · "
            f"{game.get('white_player') or 'Белые'} — {game.get('black_player') or 'Чёрные'}\n"
            f"Ваш цвет: {color}; соперник: {opponent}; результат: {result}\n"
            f"Источник оценки: {source}; дебют: {game.get('opening_eco') or ''} "
            f"{game.get('opening_name') or 'не указан'}\n{opening_text}\n{accuracy_text}\n{phase_text}")
        header.setWordWrap(True)
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
            review_coverage = analysis.get("review_coverage") or {}
            review_text = (f"Selective review: {review_coverage.get('reviewed_moves', 0)} "
                           f"оценённых критических ходов из {review_coverage.get('selected_moves', 0)} "
                           f"выбранных (не заменяет полный fast охват)" if review_coverage else
                           "Selective review: ещё не запускался")
            self.graph = EvaluationGraph(analysis["assessments"], self, phases)
            self.graph.selected_ply.connect(self._request_position)
            self.graph_layout.addWidget(self.graph)
            self.graph_layout.addWidget(QLabel(
                f"Оценено позиций: {scored_positions} / {total_positions}; "
                f"источник: {source}; статус: {analysis.get('status', 'unknown')}; "
                f"алгоритм ошибок: {CLASSIFIER_VERSION}\n{review_text}"))
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

    def __init__(self, repository: GameRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.service = StatisticsService(repository)
        self.setWindowTitle("Статистика и анализ")
        self.resize(980, 820)
        self._generation = 0
        self._query_thread: threading.Thread | None = None
        self._opening_thread: threading.Thread | None = None
        self._pending_refresh = False
        self._close_when_idle = False
        self._rows: list[dict] = []
        root = QVBoxLayout(self)
        filters = QGridLayout()
        self.account = QComboBox()
        self.account.addItem("Все локальные партии (смешанный срез)", None)
        options = self.service.filter_options()
        for account in options["accounts"]:
            self.account.addItem(account["username"], account["account_id"])
        self.period = QComboBox()
        for title, days in (("Всё время", 0), ("30 дней", 30), ("90 дней", 90), ("1 год", 365)):
            self.period.addItem(title, days)
        self.period.setCurrentIndex(self.period.findData(90))
        self.clock = QComboBox()
        self.clock.addItem("Любой контроль", "")
        for value in options["clocks"]:
            self.clock.addItem(value, value)
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
                                                 ("Lichess PGN", "lichess-pgn")))
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
        root.addLayout(filters)
        self.mixed_warning = QLabel(
            "Все локальные данные: цвета/стороны из разных аккаунтов могут смешиваться.")
        self.mixed_warning.setWordWrap(True)
        root.addWidget(self.mixed_warning)
        self.summary_label = QLabel("Загружаем статистику…")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        opening_actions = QHBoxLayout()
        self.opening_refresh_button = QPushButton("Сопоставить дебюты и фазы для 25 партий")
        self.opening_refresh_button.setToolTip(
            "Явный локальный пакет: только 25 новых партий, без сети и движка.")
        self.opening_refresh_button.clicked.connect(self._start_opening_refresh)
        opening_actions.addWidget(self.opening_refresh_button)
        self.opening_summary = QLabel("Локальные opening/phase агрегаты доступны по кнопке.")
        self.opening_summary.setWordWrap(True)
        opening_actions.addWidget(self.opening_summary, 1)
        root.addLayout(opening_actions)
        self.trend = RatingTrendGraph([])
        self.trend.game_selected.connect(self._select_game_id)
        root.addWidget(self.trend)
        self.games = QListWidget()
        root.addWidget(self.games, 1)
        self.page_label = QLabel()
        self.details_button = QPushButton("Открыть карточку партии")
        self.details_button.setEnabled(False)
        self.reset_button = QPushButton("Сбросить фильтры")
        close_button = QPushButton("Закрыть")
        buttons = QHBoxLayout()
        buttons.addWidget(self.page_label, 1)
        self.previous_page = QPushButton("←")
        self.next_page = QPushButton("→")
        buttons.addWidget(self.previous_page)
        buttons.addWidget(self.next_page)
        buttons.addWidget(self.details_button)
        buttons.addWidget(self.reset_button)
        buttons.addWidget(close_button)
        root.addLayout(buttons)
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
        close_button.clicked.connect(self.accept)
        self._schedule_refresh()

    @staticmethod
    def _fill_combo(combo: QComboBox, items: tuple[tuple[str, str], ...]) -> None:
        for label, value in items:
            combo.addItem(label, value)

    def _filters(self) -> dict:
        days = int(self.period.currentData() or 0)
        now = datetime.now(timezone.utc)
        return {
            "account_id": self.account.currentData(),
            "since": (now - timedelta(days=days)).isoformat() if days else None,
            "until": (now + timedelta(milliseconds=1)).isoformat() if days else None,
            "opponent": self.opponent.text().strip() or None,
            "clock": (None if self.clock.currentData() == "" else
                      self.clock.currentText().strip() or None),
            "game_source": self.game_source.currentData() or None,
            "analysis_source": self.analysis_source.currentData() or None,
            "side": self.side.currentData() or None,
            "opening": self.opening.text().strip() or None,
            "result": self.result_filter.currentData() or None,
            "analyzed": self.analyzed.currentData() or None,
        }

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
                from .lichess.opening_matcher import OpeningMatcher
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
        self._schedule_refresh()

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
            for game in self._rows:
                me_color = game.get("account_color") or game.get("color")
                outcome = _result_label(chess.WHITE if me_color == "white" else chess.BLACK,
                                        game.get("winner"), game.get("status", ""))
                player = game.get("account_player_name") or "Вы"
                opponent = game.get("account_opponent") or game.get("opponent") or "соперник"
                eco = game.get("matched_opening_eco") or game.get("opening_eco") or "ECO —"
                opening_name = game.get("matched_opening_name") or game.get("opening_name") or "дебют неизвестен"
                coverage = (f"{game.get('scored_positions') or 0}/{game.get('total_positions') or 0} поз. "
                            f"· сравнимые свои ходы {game.get('own_assessed_moves') or 0}/"
                            f"{game.get('total_own_moves') or 0} "
                            f"({game.get('analysis_status')})"
                            if game.get("analysis_status") else "анализ не запускался")
                error_counts = (f"н/о/з {game.get('own_inaccuracies') or 0}/"
                                f"{game.get('own_mistakes') or 0}/{game.get('own_blunders') or 0}")
                accuracy = game.get("owner_lichess_accuracy")
                accuracy_label = f"Lichess {accuracy:.1f}%" if accuracy is not None else "точность н/д"
                conflict = " · конфликт названия" if game.get("opening_conflict") else ""
                item = QListWidgetItem(
                    f"{_game_date_label(game)} · {outcome} · "
                    f"{player} vs {opponent} · {eco} {opening_name}{conflict}\n"
                    f"{accuracy_label} · {error_counts} · {coverage} · "
                    f"{game.get('selected_analysis_source') or 'источник оценки отсутствует'}")
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
            accuracy = summary["lichess_accuracy"]
            scope = ("Смешанные локальные аккаунты; результаты по цвету в профиле партии."
                     if not self.account.currentData() else
                     f"Аккаунт: {self.account.currentText()}")
            self.mixed_warning.setText(scope)
            self.summary_label.setText(
                f"Партии: {total} · победы/ничьи/поражения: {summary['wins']}/"
                f"{summary['draws']}/{summary['losses']} · неизвестно/прерваны: {summary['unknown_or_aborted']}\n"
                f"С локальной оценкой: {summary['analysis_games']}/{total} · покрытие ходов: {coverage_text}\n"
                f"Ошибки / неточности / зевки по версии {CLASSIFIER_VERSION}: "
                f"{classified['mistake']} / {classified['inaccuracy']} / {classified['blunder']} · "
                f"свои ошибки на 100 оценённых ходов: "
                f"{summary['own_error_rate_per_100'] if summary['own_error_rate_per_100'] is not None else 'нет данных'}\n"
                f"Средняя потеря CP: {summary['avg_cpl_cp'] if summary['avg_cpl_cp'] is not None else 'нет данных'} · "
                f"колебания ≥200 CP: {summary['swings_200cp']} · "
                f"Lichess accuracy (официально): белые {accuracy['white'] if accuracy['white'] is not None else 'нет данных'}, "
                f"чёрные {accuracy['black'] if accuracy['black'] is not None else 'нет данных'}")
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
        detail = StatisticsGameDialog(self.repository, self._rows[index], self)
        detail.position_requested.connect(self.position_requested.emit)
        detail.exec()

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


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.color = QComboBox()
        self.color.addItem("Белыми", chess.WHITE)
        self.color.addItem("Чёрными", chess.BLACK)
        color = settings.value("player_color", chess.WHITE, type=int)
        self.color.setCurrentIndex(self.color.findData(color))
        layout.addWidget(QLabel("Моя сторона для теста предходов"))
        layout.addWidget(self.color)
        self.flipped = QCheckBox("Перевернуть доску")
        self.flipped.setChecked(settings.value("board_flipped", False, type=bool))
        layout.addWidget(self.flipped)
        self.legal = QCheckBox("Показывать доступные ходы")
        self.legal.setChecked(settings.value("show_legal_moves", True, type=bool))
        layout.addWidget(self.legal)
        self.last = QCheckBox("Подсвечивать последний ход")
        self.last.setChecked(settings.value("show_last_move", True, type=bool))
        layout.addWidget(self.last)
        self.annotations = QCheckBox("Включить ручные стрелки и выделения")
        self.annotations.setChecked(settings.value("annotations_enabled", True, type=bool))
        layout.addWidget(self.annotations)
        self.allow_premoves = QCheckBox("Разрешить предходы")
        self.allow_premoves.setChecked(settings.value("allow_premoves", True, type=bool))
        layout.addWidget(self.allow_premoves)
        layout.addWidget(QLabel("Локальный Stockfish (не загружается приложением)"))
        engine_row = QHBoxLayout()
        self.engine_path = QLineEdit(settings.value("stockfish_path", "", type=str))
        self.engine_path.setPlaceholderText("Выберите установленный UCI executable")
        engine_row.addWidget(self.engine_path, 1)
        browse_engine = QPushButton("Обзор…")
        browse_engine.clicked.connect(self._browse_engine)
        engine_row.addWidget(browse_engine)
        layout.addLayout(engine_row)
        self.engine_test = QPushButton("Проверить движок (до 5 секунд)")
        self.engine_test.clicked.connect(self._test_engine)
        layout.addWidget(self.engine_test)
        self.engine_test_status = QLabel("Проверка запускается только по этой кнопке.")
        self.engine_test_status.setWordWrap(True)
        layout.addWidget(self.engine_test_status)
        license_note = QLabel(
            'Stockfish распространяется по GPL v3. Используйте '
            '<a href="https://stockfishchess.org/download/">официальный выпуск</a> '
            'и соблюдайте его лицензию; приложение не скачивает бинарник.')
        license_note.setOpenExternalLinks(True)
        license_note.setWordWrap(True)
        layout.addWidget(license_note)
        self.analysis_preset = QComboBox()
        self.analysis_preset.addItem("Быстрый · 25 000 узлов", "quick")
        self.analysis_preset.addItem("Обычный · 100 000 узлов", "normal")
        self.analysis_preset.addItem("Подробный · 500 000 узлов", "detailed")
        preset = settings.value("analysis_preset", "normal", type=str)
        self.analysis_preset.setCurrentIndex(max(0, self.analysis_preset.findData(preset)))
        self.analysis_preset.currentIndexChanged.connect(self._apply_analysis_preset)
        layout.addWidget(QLabel("Профиль анализа"))
        layout.addWidget(self.analysis_preset)
        self.analysis_limit = QComboBox()
        self.analysis_limit.addItem("Узлы", "nodes")
        self.analysis_limit.addItem("Глубина", "depth")
        self.analysis_limit.addItem("Секунды на позицию", "time")
        limit_kind = settings.value("analysis_limit_kind", "nodes", type=str)
        self.analysis_limit.setCurrentIndex(max(0, self.analysis_limit.findData(limit_kind)))
        layout.addWidget(QLabel("Ограничение на позицию"))
        layout.addWidget(self.analysis_limit)
        self.analysis_limit_value = QSpinBox()
        self.analysis_limit_value.setRange(1, 1_000_000)
        self.analysis_limit_value.setValue(settings.value("analysis_limit_value", 100_000, type=int))
        layout.addWidget(self.analysis_limit_value)
        self.analysis_limit.currentIndexChanged.connect(self._update_limit_range)
        self._update_limit_range()
        self.engine_threads = QSpinBox()
        self.engine_threads.setRange(1, 8)
        self.engine_threads.setValue(settings.value("stockfish_threads", 1, type=int))
        layout.addWidget(QLabel("Потоки Stockfish (1–8)"))
        layout.addWidget(self.engine_threads)
        self.engine_hash = QSpinBox()
        self.engine_hash.setRange(16, 512)
        self.engine_hash.setSuffix(" МБ")
        self.engine_hash.setValue(settings.value("stockfish_hash_mb", 128, type=int))
        layout.addWidget(QLabel("Память Hash (16–512 МБ)"))
        layout.addWidget(self.engine_hash)
        self._engine_process: QProcess | None = None
        self._engine_probe_timer: QTimer | None = None
        self._engine_probe_shutdown_timer: QTimer | None = None
        self._engine_probe_buffer = bytearray()
        self._engine_probe_finishing = False
        self._engine_probe_finish_message: str | None = None
        self._engine_probe_owner = None
        self.finished.connect(lambda _result: self._stop_engine_probe())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


    def save(self, settings: QSettings) -> None:
        settings.setValue("player_color", self.color.currentData())
        settings.setValue("board_flipped", self.flipped.isChecked())
        settings.setValue("show_legal_moves", self.legal.isChecked())
        settings.setValue("show_last_move", self.last.isChecked())
        settings.setValue("annotations_enabled", self.annotations.isChecked())
        settings.setValue("allow_premoves", self.allow_premoves.isChecked())
        settings.setValue("stockfish_path", self.engine_path.text().strip())
        settings.setValue("analysis_preset", self.analysis_preset.currentData())
        settings.setValue("analysis_limit_kind", self.analysis_limit.currentData())
        settings.setValue("analysis_limit_value", self.analysis_limit_value.value())
        settings.setValue("stockfish_threads", self.engine_threads.value())
        settings.setValue("stockfish_hash_mb", self.engine_hash.value())
        settings.sync()

    def _browse_engine(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите установленный Stockfish UCI executable", self.engine_path.text(),
            "Программы (*.exe);;Все файлы (*)")
        if path:
            self.engine_path.setText(path)

    def _update_limit_range(self) -> None:
        kind = self.analysis_limit.currentData()
        ranges = {"nodes": (1_000, 100_000_000, 100_000),
                  "depth": (1, 100, 16), "time": (1, 3_600, 5)}
        minimum, maximum, default = ranges.get(kind, ranges["nodes"])
        old_value = self.analysis_limit_value.value()
        self.analysis_limit_value.setRange(minimum, maximum)
        self.analysis_limit_value.setValue(old_value if minimum <= old_value <= maximum else default)

    def _apply_analysis_preset(self) -> None:
        if self.analysis_limit.currentData() != "nodes":
            return
        values = {"quick": 25_000, "normal": 100_000, "detailed": 500_000}
        self.analysis_limit_value.setValue(
            values.get(self.analysis_preset.currentData(), self.analysis_limit_value.value()))

    def analysis_options(self) -> AnalysisOptions:
        kind = self.analysis_limit.currentData()
        value = self.analysis_limit_value.value()
        if kind == "time":
            value *= 1000
        return AnalysisOptions(limit_kind=kind, limit_value=value,
                              threads=self.engine_threads.value(),
                              hash_mb=self.engine_hash.value())

    def _test_engine(self) -> None:
        path = self.engine_path.text().strip()
        if not path:
            self.engine_test_status.setText("Сначала выберите локальный executable.")
            return
        if self._engine_process is not None:
            return
        process_parent = self.parent() or QApplication.instance()
        self._engine_probe_owner = process_parent
        process = QProcess(process_parent)
        self._engine_process = process
        self._engine_probe_finishing = False
        self._engine_probe_finish_message = None
        self._engine_probe_buffer.clear()
        self.engine_test.setEnabled(False)
        self.engine_test_status.setText("Запущена ограниченная UCI-проверка…")
        if hasattr(process_parent, "_track_engine_probe"):
            process_parent._track_engine_probe(process)
        dialog_ref = weakref.ref(self)
        owner_ref = weakref.ref(process_parent)
        def dispatch(method: str, *args) -> None:
            dialog = dialog_ref()
            if dialog is not None and shiboken6.isValid(dialog):
                getattr(dialog, method)(*args)
            elif method == "_engine_probe_finished":
                current = args[0]
                if current.state() == QProcess.ProcessState.NotRunning:
                    owner = owner_ref()
                    if (owner is not None and shiboken6.isValid(owner)
                            and hasattr(owner, "_untrack_engine_probe")):
                        owner._untrack_engine_probe(current)
                    current.deleteLater()
        process.started.connect(lambda current=process: dispatch("_engine_probe_started", current))
        process.readyReadStandardOutput.connect(lambda current=process: dispatch("_read_engine_probe", current))
        process.errorOccurred.connect(lambda error, current=process:
                                      dispatch("_engine_probe_error", current, error))
        process.finished.connect(lambda code, status, current=process:
                                  dispatch("_engine_probe_finished", current, code, status))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda current=process: dispatch("_engine_probe_timeout", current))
        self._engine_probe_timer = timer
        timer.start(5_000)
        process.start(path, [])

    def _engine_probe_started(self, process: QProcess) -> None:
        if process is self._engine_process and not self._engine_probe_finishing:
            process.write(b"uci\n")

    def _read_engine_probe(self, process: QProcess) -> None:
        if process is not self._engine_process or self._engine_probe_finishing:
            return
        self._engine_probe_buffer.extend(bytes(process.readAllStandardOutput()))
        output = self._engine_probe_buffer.decode("utf-8", errors="replace")
        if "uciok" in output:
            name = next((line.removeprefix("id name ") for line in output.splitlines()
                         if line.startswith("id name ")), "UCI engine")
            self._finish_engine_probe(f"UCI отвечает: {name}", graceful=True)

    def _engine_probe_error(self, process: QProcess, _error) -> None:
        if (process is self._engine_process and not self._engine_probe_finishing
                and process.state() == QProcess.ProcessState.NotRunning):
            self._finish_engine_probe(f"Не удалось запустить движок: {process.errorString()}")

    def _engine_probe_finished(self, process: QProcess, _code, _status) -> None:
        if process is not self._engine_process:
            process.deleteLater()
            return
        if not self._engine_probe_finishing and "uciok" not in self._engine_probe_buffer.decode(
                "utf-8", errors="replace"):
            self._engine_probe_finish_message = "Процесс завершился до ответа uciok."
        self._finalize_engine_probe(process)

    def _engine_probe_timeout(self, process: QProcess | None = None) -> None:
        process = process or self._engine_process
        if process is None:
            return
        if process is self._engine_process and not self._engine_probe_finishing:
            self._finish_engine_probe("UCI не ответил за 5 секунд; останавливаем процесс.")

    def _finish_engine_probe(self, message: str | None = None, *, graceful: bool = False) -> None:
        process = self._engine_process
        if process is None or self._engine_probe_finishing:
            return
        self._engine_probe_finishing = True
        if message is not None:
            self._engine_probe_finish_message = message
        if self._engine_probe_timer is not None:
            self._engine_probe_timer.stop()
            self._engine_probe_timer.deleteLater()
            self._engine_probe_timer = None
        if process.state() == QProcess.ProcessState.NotRunning:
            self._finalize_engine_probe(process)
            return
        if graceful:
            process.write(b"quit\n")
        else:
            process.kill()
        # The bounded kill timer must outlive SettingsDialog if it is closed while
        # the child is still shutting down.
        owner = self._engine_probe_owner or QApplication.instance()
        timer = QTimer(owner)
        timer.setSingleShot(True)
        dialog_ref = weakref.ref(self)
        def kill_or_delegate(current=process) -> None:
            dialog = dialog_ref()
            if dialog is not None and shiboken6.isValid(dialog):
                dialog._kill_engine_probe(current)
            elif current.state() != QProcess.ProcessState.NotRunning:
                current.kill()
        timer.timeout.connect(kill_or_delegate)
        self._engine_probe_shutdown_timer = timer
        timer.start(1_000 if graceful else 2_000)

    def _kill_engine_probe(self, process: QProcess) -> None:
        if process is not self._engine_process:
            return
        if process.state() == QProcess.ProcessState.NotRunning:
            self._finalize_engine_probe(process)
            return
        process.kill()
        # Keep the QProcess alive until its finished signal confirms OS exit.
        if self._engine_probe_shutdown_timer is not None:
            self._engine_probe_shutdown_timer.start(2_000)

    def _finalize_engine_probe(self, process: QProcess) -> None:
        if process is not self._engine_process:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            return
        for timer_name in ("_engine_probe_timer", "_engine_probe_shutdown_timer"):
            timer = getattr(self, timer_name)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, timer_name, None)
        self._engine_process = None
        owner = self._engine_probe_owner
        if owner is not None and hasattr(owner, "_untrack_engine_probe"):
            owner._untrack_engine_probe(process)
        self._engine_probe_owner = None
        self._engine_probe_finishing = False
        if self._engine_probe_finish_message:
            self.engine_test_status.setText(self._engine_probe_finish_message)
        self._engine_probe_finish_message = None
        process.deleteLater()
        self.engine_test.setEnabled(True)

    def _stop_engine_probe(self) -> None:
        self._finish_engine_probe("Проверка движка остановлена.")


class BatchAnalysisDialog(QDialog):
    """Explicit controls for the bounded recent-first fast-analysis queue."""

    run_requested = Signal(int, int, int, bool, bool, bool)
    pause_requested = Signal()
    cancel_requested = Signal()
    worker_progress = Signal(str)
    worker_queue_status = Signal(str)
    worker_finished = Signal(bool, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Массовый анализ завершённых партий")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Анализ запускается только по кнопке и только для завершённых стандартных партий. "
            "Перед каждой партией Lichess проверяется заново; неизвестное состояние останавливает очередь."))
        form = QGridLayout()
        self.limit = QSpinBox(); self.limit.setRange(1, 10_000); self.limit.setValue(1000)
        self.nodes = QSpinBox(); self.nodes.setRange(1000, 30_000); self.nodes.setSingleStep(1000); self.nodes.setValue(20_000)
        self.hash_mb = QSpinBox(); self.hash_mb.setRange(16, 128); self.hash_mb.setSingleStep(16); self.hash_mb.setValue(64)
        self.retry_failed = QCheckBox("Повторить ранее завершившиеся ошибкой/отменённые задания")
        self.selective_review = QCheckBox("После быстрого прохода выборочно перепроверить критические позиции (до 12 на игру / 100 всего)")
        form.addWidget(QLabel("Максимум партий для добавления"), 0, 0); form.addWidget(self.limit, 0, 1)
        form.addWidget(QLabel("Узлов на позицию"), 1, 0); form.addWidget(self.nodes, 1, 1)
        form.addWidget(QLabel("Hash (МБ)"), 2, 0); form.addWidget(self.hash_mb, 2, 1)
        layout.addLayout(form)
        layout.addWidget(self.retry_failed)
        layout.addWidget(self.selective_review)
        self.queue_label = QLabel("Очередь ещё не загружена")
        self.queue_label.setWordWrap(True)
        layout.addWidget(self.queue_label)
        self.progress_label = QLabel("Движок не запущен")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Добавить недостающие и запустить")
        self.start_button.clicked.connect(lambda: self.run_requested.emit(
            self.limit.value(), self.nodes.value(), self.hash_mb.value(), False,
            self.retry_failed.isChecked(), self.selective_review.isChecked()))
        self.resume_button = QPushButton("Продолжить очередь")
        # A persistent queue may have been paused by a previous application run.
        self.resume_button.setEnabled(True)
        self.resume_button.clicked.connect(lambda: self.run_requested.emit(
            self.limit.value(), self.nodes.value(), self.hash_mb.value(), True, False,
            self.selective_review.isChecked()))
        self.pause_button = QPushButton("Пауза")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.cancel_button = QPushButton("Отменить очередь")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        controls.addWidget(self.start_button); controls.addWidget(self.resume_button)
        controls.addWidget(self.pause_button); controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.worker_progress.connect(self.progress_label.setText)
        self.worker_queue_status.connect(self.queue_label.setText)
        self.worker_finished.connect(self._worker_finished)

    def _worker_finished(self, paused: bool, message: str) -> None:
        self.set_running(False)
        self.resume_button.setEnabled(paused)
        self.progress_label.setText(message)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.resume_button.setEnabled(not running)
        self.limit.setEnabled(not running)
        self.nodes.setEnabled(not running)
        self.hash_mb.setEnabled(not running)
        self.retry_failed.setEnabled(not running)
        self.selective_review.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.cancel_button.setEnabled(running)

    def closeEvent(self, event) -> None:
        if self.pause_button.isEnabled() or self.cancel_button.isEnabled():
            self.progress_label.setText("Сначала поставьте очередь на паузу или отмените её.")
            event.ignore()
            return
        super().closeEvent(event)


class ActiveGamesDialog(QDialog):
    refresh_requested = Signal()
    open_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Активные партии Lichess")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.status = QLabel("Нажмите «Обновить», чтобы запросить список у Lichess.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.games = QListWidget()
        self.games.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.games)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        buttons.addWidget(self.refresh_button)
        self.open_button = QPushButton("Открыть выбранную партию")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.games.currentItemChanged.connect(
            lambda current, _previous: self.open_button.setEnabled(current is not None)
        )

    def set_loading(self) -> None:
        self.status.setText("Запрашиваем активные партии…")
        self.refresh_button.setEnabled(False)

    def set_error(self, message: str) -> None:
        self.status.setText(message)
        self.refresh_button.setEnabled(True)

    def set_games(self, games: list[dict]) -> None:
        self.games.clear()
        for game in games:
            opponent = game.get("opponent") or {}
            opponent_name = opponent.get("username") or opponent.get("id") or "Соперник"
            color = {"white": "белыми", "black": "чёрными"}.get(
                game.get("color"), "цвет не указан"
            )
            speed = str(game.get("speed") or "контроль не указан")
            kind = "заочная" if speed.casefold() == "correspondence" else "реальное время"
            is_my_turn = game.get("isMyTurn")
            turn = ("ваш ход" if is_my_turn else "ход соперника"
                    if isinstance(is_my_turn, bool) else "чей ход не указан")
            seconds = game.get("secondsLeft")
            remaining = f" · осталось {int(seconds)} с" if isinstance(seconds, (int, float)) else ""
            item = QListWidgetItem(
                f"{opponent_name} · вы {color} · {kind} ({speed}) · {turn}{remaining}"
            )
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games.addItem(item)
        self.status.setText(
            f"Активных партий: {len(games)}." if games else "Lichess не вернул активных партий."
        )
        self.refresh_button.setEnabled(True)
        self.open_button.setEnabled(self.games.currentItem() is not None)

    def open_selected(self) -> None:
        item = self.games.currentItem()
        if item is not None:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()


class MainWindow(QMainWindow):
    analysis_progress = Signal(str, int, int)
    analysis_finished = Signal(str, object)

    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        asynchronous_storage: bool = False,
        offline_mode: bool = False,
        manual_connect_mode: bool = False,
    ) -> None:
        super().__init__()
        if offline_mode and manual_connect_mode:
            raise ValueError("--offline и --manual-connect нельзя включить одновременно")
        self.offline_mode = offline_mode
        self.manual_connect_mode = manual_connect_mode
        self._offline_data_dir: tempfile.TemporaryDirectory | None = None
        if offline_mode:
            # Smoke runs must not read or write the owner's settings or game DB.
            self._offline_data_dir = tempfile.TemporaryDirectory(prefix="chess-coach-offline-")
            data_root = Path(self._offline_data_dir.name)
            self.settings = QSettings(
                str(data_root / "settings.ini"), QSettings.Format.IniFormat
            )
        else:
            self.settings = settings if settings is not None else QSettings("ChessCoach", "ChessCoach")
            data_root = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            ))
            data_root.mkdir(parents=True, exist_ok=True)
        color = self.settings.value("player_color", chess.WHITE, type=int)
        self.controller = GameController(chess.Color(color))
        self.game_repository = GameRepository(data_root / "games.sqlite3", async_writes=asynchronous_storage)
        self._online_history_game_id: str | None = None
        self._online_history_moves: tuple[chess.Move, ...] = ()
        self._online_history_position: chess.Board | None = None
        if offline_mode or manual_connect_mode:
            # Offline is isolated; manual-connect uses the normal profile but
            # must not touch credentials or create workers before user intent.
            self.lichess = None
        else:
            self.lichess = self._create_lichess_service()
        self.online_sessions: dict[str, LichessGameSession] = {}
        self.online_session: LichessGameSession | None = None
        self._local_board_mode = "free"
        self.online_view_ply: int | None = None
        self._screen_mode = "home"
        self._awaiting_game_start = False
        self._selected_live_game_id: str | None = None
        self._review_session: LichessGameSession | None = None
        self._lichess_connected = False
        self._archive_progress_dialog: QProgressDialog | None = None
        self._puzzle_progress_dialog: QProgressDialog | None = None
        self._close_retry_scheduled = False
        self._analysis_stop: threading.Event | None = None
        self._analysis_thread: threading.Thread | None = None
        self._batch_thread: threading.Thread | None = None
        self._batch_stop: threading.Event | None = None
        self._batch_pause: threading.Event | None = None
        self._batch_dialog: BatchAnalysisDialog | None = None
        self._batch_engine_key: str | None = None
        self._batch_config: tuple[str, int, int] | None = None
        self._statistics_workers: set[threading.Thread] = set()
        self._analysis_dialog: QProgressDialog | None = None
        self._analysis_safety_request_id: str | None = None
        self._analysis_safety_context: tuple | None = None
        self._analysis_safety_phase: str | None = None
        self._analysis_session_generation = 0
        self._engine_probe_processes: set[QProcess] = set()
        self._shutdown_requested = False
        self._shutdown_read_only = False
        self._analysis_shutdown_deadline: float | None = None
        self.analysis_progress.connect(self._analysis_progress)
        self.analysis_finished.connect(self._analysis_finished)
        self.setWindowTitle("Chess Coach")
        self.setMinimumSize(1040, 680)
        self.resize(1360, 860)
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #f4f1ea; color: #292a2b; }"
            "QPushButton { background: #ffffff; border: 1px solid #d9d5cb;"
            " border-radius: 6px; padding: 7px 11px; }"
            "QPushButton:hover { background: #ebe6db; }"
            "QPushButton:disabled { color: #9b9a96; }"
            "QFrame#card { background: #ffffff; border: 1px solid #e5e1d8;"
            " border-radius: 8px; }"
            "QListWidget { background: #ffffff; border: 1px solid #e5e1d8;"
            " border-radius: 6px; padding: 5px; }"
        )
        self._build_ui()
        self.show_home()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(250)
        self._auto_sync_timer = QTimer(self)
        self._auto_sync_timer.setInterval(10 * 60 * 1000)
        self._auto_sync_timer.timeout.connect(self._run_background_sync)
        self._auto_sync_timer.start()
        self._close_retry_timer = QTimer(self)
        self._close_retry_timer.setSingleShot(True)
        self._close_retry_timer.setInterval(200)
        self._close_retry_timer.timeout.connect(self._retry_close_after_workers)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.cancel_premove)
        if self.lichess:
            self._attach_lichess_service(self.lichess)
            self.lichess.restore()
        elif manual_connect_mode:
            self.connection.setText("●  Ручной режим · запросов нет")
            self.connect_button.setText("Подключиться вручную")
            self.connect_button.setToolTip(
                "Профиль и локальные партии доступны. OAuth, восстановление соединения и запросы "
                "к Lichess начнутся только после нажатия этой кнопки."
            )
        elif offline_mode:
            self._disable_online_controls()
        self.refresh()

    def _create_lichess_service(self):
        try:
            return LichessService(token_store=WindowsCredentialStore(),
                                  repository=self.game_repository, parent=self)
        except CredentialStorageError:
            return None

    def _attach_lichess_service(self, service) -> None:
        service.status_changed.connect(self._lichess_status)
        service.authenticated.connect(self._lichess_authenticated)
        service.error.connect(self._lichess_error)
        service.move_failed.connect(self._lichess_move_failed)
        service.takeback_action_completed.connect(self._lichess_takeback_completed)
        service.event_received.connect(self._lichess_event)
        service.game_event_received.connect(self._lichess_game_event)
        service.challenge_ready.connect(self._challenge_ready)
        service.challenge_status_changed.connect(self._challenge_status)
        service.invitation_cancelled.connect(self._invitation_cancelled)
        service.active_games_received.connect(self._active_games_received)
        service.active_games_error.connect(self._active_games_error)
        service.analysis_safety_games_received.connect(self._analysis_safety_games_received)
        service.analysis_safety_error.connect(self._analysis_safety_error)
        service.archive_progress.connect(self._archive_progress)
        service.archive_finished.connect(self._archive_finished)
        service.archive_pilot_progress.connect(self._archive_pilot_progress)
        service.archive_pilot_finished.connect(self._archive_pilot_finished)
        service.archive_game_refresh_finished.connect(self._archive_game_refresh_finished)
        service.puzzles_progress.connect(self._puzzles_progress)
        service.puzzles_scanned.connect(self._puzzles_scanned)
        service.background_sync_finished.connect(self._background_sync_finished)
        service.puzzles_finished.connect(self._puzzles_finished)
        service.puzzle_pilot_progress.connect(self._puzzle_pilot_progress)
        service.puzzle_pilot_finished.connect(self._puzzle_pilot_finished)

    def _disable_online_controls(self) -> None:
        self.connection.setText("●  Офлайн-режим · Lichess отключён")
        self.connect_button.setEnabled(False)
        self.connect_button.setToolTip("В офлайн-smoke авторизация и сетевые запросы отключены.")
        self.disconnect_button.setVisible(False)
        for button in (
            self.friend_link_button, self.username_button, self.ai_button,
            self.home_ai_button, self.home_friend_button, self.home_username_button,
            self.cancel_challenge_button, self.active_games_button, self.home_pilot_import_button,
            self.home_puzzles_pilot_button,
        ):
            button.setEnabled(False)
            button.setVisible(False)

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        brand = QLabel("♟  Chess Coach")
        brand.setStyleSheet("font-size: 22px; font-weight: 700;")
        header.addWidget(brand)
        header.addSpacing(24)
        for label in ("Играть", "Мои партии", "Анализ", "Тренировки"):
            button = QPushButton(label)
            if label == "Играть":
                button.clicked.connect(self.show_home)
            elif label == "Мои партии":
                button.clicked.connect(self.show_saved_games)
            else:
                button.setEnabled(False)
                button.setToolTip("Будет добавлено позже")
            header.addWidget(button)
        header.addStretch(1)
        self.connection = QLabel("●  Не подключено")
        self.connection.setStyleSheet("color: #77766f;")
        header.addWidget(self.connection)
        self.connect_button = QPushButton("Подключить Lichess")
        self.connect_button.clicked.connect(self.connect_lichess)
        header.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Отключить")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_lichess)
        header.addWidget(self.disconnect_button)
        self.settings_button = QPushButton("Настройки")
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        outer.addLayout(header)

        self.screen_title = QLabel("Главная")
        self.screen_title.setStyleSheet("font-size: 18px; font-weight: 700; padding: 2px 4px;")
        outer.addWidget(self.screen_title)
        self.screen_stack = QStackedWidget()
        self.home_page = self._build_home_page()
        self.screen_stack.addWidget(self.home_page)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        board_column = QWidget()
        board_layout = QVBoxLayout(board_column)
        board_layout.setContentsMargins(0, 0, 8, 0)
        board_layout.setSpacing(8)
        self.opponent_label = QLabel("Соперник  ·  локальная партия")
        self.opponent_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.opponent_label)
        self.board = BoardView(self.controller.board)
        self.board.setMinimumSize(440, 440)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self.board.move_intent.connect(self.on_move_intent)
        self.board.wheel_navigate.connect(self.navigate_by)
        self.board.premove_intent.connect(self.on_premove_intent)
        self.board.cancel_premove_requested.connect(self.cancel_premove)
        board_layout.addWidget(self.board, 1, Qt.AlignmentFlag.AlignCenter)
        self.you_label = QLabel("Вы  ·  офлайн")
        self.you_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.you_label)
        splitter.addWidget(board_column)

        side = QWidget()
        side.setMinimumWidth(340)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 0, 0, 0)
        side_layout.setSpacing(10)
        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        self.turn_label = QLabel()
        self.turn_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        status_layout.addWidget(self.turn_label)
        self.game_status = QLabel("Локальная тренировочная доска")
        self.game_status.setStyleSheet("color: #77766f;")
        status_layout.addWidget(self.game_status)
        side_layout.addWidget(status_card)
        self.local_mode_label = QLabel("Режим локальной доски")
        side_layout.addWidget(self.local_mode_label)
        self.local_mode_combo = QComboBox()
        self.local_mode_combo.addItem("Свободная доска (за обе стороны)", "free")
        self.local_mode_combo.addItem("Тренировка предходов (мой цвет)", "premove_training")
        self.local_mode_combo.currentIndexChanged.connect(self._local_board_mode_changed)
        side_layout.addWidget(self.local_mode_combo)
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
        self.clock_label = QLabel("Без часов")
        self.clock_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        side_layout.addWidget(self.clock_label)
        self.friend_link_button = QPushButton("С другом по ссылке")
        self.friend_link_button.clicked.connect(self.open_friend_game)
        self.username_button = QPushButton("Вызвать игрока Lichess")
        self.username_button.clicked.connect(self.challenge_username)
        self.ai_button = QPushButton("Играть против Lichess AI")
        self.ai_button.clicked.connect(self.play_ai)
        self.resign_button = QPushButton("Сдаться")
        self.resign_button.clicked.connect(self.resign_online)
        self.draw_button = QPushButton("Предложить ничью")
        self.draw_button.clicked.connect(self.handle_draw)
        self.decline_draw_button = QPushButton("Отклонить ничью")
        self.decline_draw_button.clicked.connect(lambda: self.lichess.draw(self.online_session.game_id, False) if self.online_session else None)
        self.decline_draw_button.setVisible(False)
        self.takeback_button = QPushButton("Предложить вернуть ход")
        self.takeback_button.setToolTip("Запросить возврат через Lichess и дождаться подтверждённой сервером позиции.")
        self.takeback_button.clicked.connect(self.request_takeback)
        self.accept_takeback_button = QPushButton("Принять возврат хода")
        self.accept_takeback_button.clicked.connect(lambda: self.answer_takeback(True))
        self.decline_takeback_button = QPushButton("Отклонить возврат хода")
        self.decline_takeback_button.clicked.connect(lambda: self.answer_takeback(False))
        self.accept_takeback_button.setVisible(False)
        self.decline_takeback_button.setVisible(False)
        self.claim_draw_button = QPushButton("Заявить ничью по правилу")
        self.claim_draw_button.clicked.connect(lambda: self.lichess.claim_draw(self.online_session.game_id) if self.online_session else None)
        self.leave_game_button = QPushButton("Закрыть просмотр партии")
        self.leave_game_button.clicked.connect(self.leave_online_game)
        for button in (self.friend_link_button, self.username_button, self.ai_button, self.resign_button, self.draw_button, self.decline_draw_button, self.takeback_button, self.accept_takeback_button, self.decline_takeback_button, self.claim_draw_button, self.leave_game_button):
            button.setEnabled(False)
            button.setVisible(False)
            side_layout.addWidget(button)
        self.undo_local_button = QPushButton("Отменить последний ход")
        self.undo_local_button.setToolTip("Отменяет последний подтверждённый ход локальной партии. Для Lichess используется «Вернуть ход».")
        self.undo_local_button.clicked.connect(self.undo_local_last_move)
        self.undo_local_button.setVisible(False)
        side_layout.addWidget(self.undo_local_button)
        self.games_button = QPushButton("Завершённые партии")
        self.games_button.clicked.connect(self.show_saved_games)
        side_layout.addWidget(self.games_button)
        self.cancel_challenge_button = QPushButton("Отменить ожидающий вызов")
        self.cancel_challenge_button.clicked.connect(self.cancel_username_challenge)
        self.cancel_challenge_button.setEnabled(False)
        self.cancel_challenge_button.setVisible(False)
        side_layout.addWidget(self.cancel_challenge_button)
        self.cancel_invitation_button = QPushButton("Отменить приглашение по ссылке")
        self.cancel_invitation_button.clicked.connect(self.cancel_friend_invitation)
        self.cancel_invitation_button.setEnabled(False)
        self.cancel_invitation_button.setVisible(False)
        side_layout.addWidget(self.cancel_invitation_button)

        moves_title = QLabel("ХОДЫ")
        moves_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #77766f;")
        side_layout.addWidget(moves_title)
        self.moves = QListWidget()
        self.moves.itemClicked.connect(self.on_move_selected)
        side_layout.addWidget(self.moves, 1)
        history_controls = QHBoxLayout()
        for label, action in (
            ("|<", lambda: self.navigate_to(0)),
            ("<", lambda: self.navigate_by(-1)),
            (">", lambda: self.navigate_by(1)),
            (">|", self.navigate_live),
        ):
            button = QPushButton(label)
            button.setToolTip(
                {"|<": "К началу", "<": "На ход назад", ">": "На ход вперёд", ">|": "К текущей позиции"}[label]
            )
            button.clicked.connect(action)
            history_controls.addWidget(button)
        side_layout.addLayout(history_controls)
        self.history_label = QLabel("Актуальная позиция")
        self.history_label.setStyleSheet("color: #77766f;")
        side_layout.addWidget(self.history_label)

        self.premove_title = QLabel("ПРОВЕРКА ПРЕДХОДОВ")
        self.premove_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #77766f;")
        side_layout.addWidget(self.premove_title)
        self.premove_label = QLabel("Один предход можно поставить, пока ходит соперник.")
        self.premove_label.setWordWrap(True)
        side_layout.addWidget(self.premove_label)
        self.cancel_premove_button = QPushButton("Отменить предход")
        self.cancel_premove_button.clicked.connect(self.cancel_premove)
        side_layout.addWidget(self.cancel_premove_button)
        self.opponent_move = QComboBox()
        side_layout.addWidget(self.opponent_move)
        self.apply_opponent_button = QPushButton("Сыграть тестовый ход соперника")
        self.apply_opponent_button.clicked.connect(self.apply_opponent_move)
        side_layout.addWidget(self.apply_opponent_button)

        actions = QHBoxLayout()
        self.new_game_button = QPushButton("Новая партия")
        self.new_game_button.clicked.connect(self.new_game)
        self.flip_button = QPushButton("Перевернуть")
        self.flip_button.clicked.connect(self.flip_board)
        self.clear_marks_button = QPushButton("Очистить пометки")
        self.clear_marks_button.clicked.connect(self.board.clear_annotations)
        actions.addWidget(self.new_game_button)
        actions.addWidget(self.flip_button)
        side_layout.addLayout(actions)
        side_layout.addWidget(self.clear_marks_button)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 13)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([850, 450])
        self.game_page = splitter
        self.screen_stack.addWidget(self.game_page)
        self.result_page = self._build_result_page()
        self.screen_stack.addWidget(self.result_page)
        self.screen_stack.setCurrentWidget(self.home_page)
        outer.addWidget(self.screen_stack, 1)
        self.setCentralWidget(root)

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Создать или открыть партию"))
        self.home_status = QLabel("Выберите режим игры. Анализ и тренировки появятся позже.")
        self.home_status.setWordWrap(True)
        layout.addWidget(self.home_status)
        self.home_ai_button = QPushButton("Играть против Lichess AI")
        self.home_ai_button.clicked.connect(self.play_ai)
        self.home_friend_button = QPushButton("Играть с другом по ссылке")
        self.home_friend_button.clicked.connect(self.open_friend_game)
        self.home_username_button = QPushButton("Вызвать игрока Lichess")
        self.home_username_button.clicked.connect(self.challenge_username)
        self.active_games_button = QPushButton("Активные партии Lichess")
        self.active_games_button.clicked.connect(self.show_active_games)
        self.active_games_button.setEnabled(False)
        self.home_local_button = QPushButton("Локальная доска")
        self.home_local_button.clicked.connect(self.start_local_game)
        self.home_games_button = QPushButton("Завершённые партии")
        self.home_games_button.clicked.connect(self.show_saved_games)
        self.home_archive_status_button = QPushButton("Хранилище и статус архива")
        self.home_archive_status_button.clicked.connect(self.show_archive_status)
        self.home_pilot_import_button = QPushButton("Пробно импортировать 5–10 последних партий")
        self.home_pilot_import_button.clicked.connect(self.start_archive_pilot)
        self.home_pilot_import_button.setEnabled(False)
        self.home_archive_button = QPushButton("Полная синхронизация архива Lichess")
        self.home_archive_button.clicked.connect(self.start_archive_sync)
        self.home_archive_button.setEnabled(False)
        self.home_stats_button = QPushButton("Статистика партий")
        self.home_stats_button.clicked.connect(self.show_statistics)
        self.home_batch_analysis_button = QPushButton("Массовый анализ Stockfish")
        self.home_batch_analysis_button.clicked.connect(self.show_batch_analysis)
        self.home_batch_analysis_button.setEnabled(False)
        self.home_puzzles_button = QPushButton("Моя история задач Lichess")
        self.home_puzzles_button.clicked.connect(self.start_puzzle_sync)
        self.home_puzzles_button.setEnabled(False)
        self.home_puzzles_pilot_button = QPushButton("Пробно загрузить 5–10 попыток задач")
        self.home_puzzles_pilot_button.clicked.connect(self.start_puzzle_pilot)
        self.home_puzzles_pilot_button.setEnabled(False)
        self.home_settings_button = QPushButton("Настройки")
        self.home_settings_button.clicked.connect(self.open_settings)
        self.resume_game_button = QPushButton("Вернуться в текущую партию")
        self.resume_game_button.clicked.connect(self.resume_current_game)
        for button in (self.home_ai_button, self.home_friend_button,
                       self.home_username_button, self.active_games_button,
                       self.home_local_button,
                       self.home_games_button, self.home_archive_status_button,
                       self.home_pilot_import_button,
                       self.home_archive_button, self.home_stats_button,
                       self.home_batch_analysis_button,
                       self.home_puzzles_button, self.home_puzzles_pilot_button,
                       self.home_settings_button,
                       self.resume_game_button):
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.result_summary = QLabel("Партия завершена")
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)
        self.result_saved_label = QLabel()
        layout.addWidget(self.result_saved_label)
        self.result_review_button = QPushButton("Просмотреть партию")
        self.result_review_button.clicked.connect(self.review_finished_game)
        self.result_export_button = QPushButton("Экспорт PGN")
        self.result_export_button.clicked.connect(self.export_current_pgn)
        self.result_new_button = QPushButton("Новая партия")
        self.result_new_button.clicked.connect(self.show_home)
        self.result_home_button = QPushButton("Главная")
        self.result_home_button.clicked.connect(self.show_home)
        for button in (self.result_review_button, self.result_export_button,
                       self.result_new_button, self.result_home_button):
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def refresh(self, message: str | None = None) -> None:
        if self.online_session is not None:
            self.undo_local_button.setVisible(False)
            self._refresh_online(message)
            return
        for button in (self.takeback_button, self.accept_takeback_button,
                       self.decline_takeback_button):
            button.setVisible(False)
        snapshot = self.controller.snapshot()
        local_game_visible = self.screen_stack.currentWidget() is self.game_page
        self.undo_local_button.setVisible(local_game_visible and self.controller.ply > 0)
        self.undo_local_button.setEnabled(local_game_visible and self.controller.ply > 0)
        self.board.set_position(snapshot.board)
        self.board.set_annotation_context("__local__", snapshot.board)
        self.board.set_interaction_enabled(snapshot.live)
        is_local_mode = self._screen_mode == "local" and local_game_visible
        is_training = is_local_mode and self._local_board_mode == "premove_training"
        self.local_mode_label.setVisible(is_local_mode)
        self.local_mode_combo.setVisible(is_local_mode)
        for widget in (self.premove_title, self.premove_label, self.cancel_premove_button,
                       self.opponent_move, self.apply_opponent_button):
            widget.setVisible(is_training)
        self._apply_preferences()
        if is_local_mode:
            self.board.set_premove_allowed(
                self.settings.value("allow_premoves", True, type=bool) and is_training
            )
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        viewed_ply = (
            self.controller.ply
            if self.controller.view_ply is None
            else self.controller.view_ply
        )
        self._sync_online_history(self.controller.board, "__local__", viewed_ply)
        if self.controller.view_ply == 0:
            self.history_label.setText("Начальная позиция")
        elif not snapshot.live:
            self.history_label.setText(f"Просмотр хода {snapshot.ply} из {self.controller.ply}")
        else:
            self.history_label.setText("Актуальная позиция")
        turn_name = "белых" if snapshot.board.turn == chess.WHITE else "чёрных"
        if is_local_mode and self._local_board_mode == "free":
            turn_text = f"Сейчас ход {turn_name}"
        else:
            turn_text = "Ход белых" if snapshot.board.turn else "Ход чёрных"
        self.turn_label.setText(turn_text + ("  ·  просмотр истории" if not snapshot.live else ""))
        opponent_turn = self.controller.board.turn != self.controller.player_color
        legal = (
            list(self.controller.board.legal_moves)
            if opponent_turn and self.controller.viewing_live_position
            else []
        )
        self.opponent_move.clear()
        for move in legal:
            san = self.controller.board.san(move)
            self.opponent_move.addItem(f"{san}  ({move.uci()})", move.uci())
        enabled = (is_training and opponent_turn and self.controller.viewing_live_position
                   and bool(legal))
        self.opponent_move.setEnabled(enabled)
        self.apply_opponent_button.setEnabled(enabled)
        if self.controller.premove is not None:
            self.premove_label.setText(
                f"Предход: {self.controller.premove.uci()}  ·  Esc отменяет"
            )
        elif is_training and opponent_turn:
            side_name = "белыми" if self.controller.player_color == chess.WHITE else "чёрными"
            self.premove_label.setText(f"Вы играете {side_name}. Выберите свою фигуру, затем клетку назначения.")
        elif is_training:
            self.premove_label.setText("Предход появится, когда будет ход соперника.")
        self.cancel_premove_button.setEnabled(self.controller.premove is not None)
        self.clear_marks_button.setEnabled(self.board._annotations_enabled)
        if message:
            self.game_status.setText(message)
        elif is_local_mode and self._local_board_mode == "free":
            self.game_status.setText("Свободная доска: делайте ходы за белых и чёрных.")
        elif is_training:
            self.game_status.setText("Локальная тренировка предходов.")
        else:
            self.game_status.setText("Локальная тренировочная доска")

    def show_home(self) -> None:
        self._screen_mode = "home"
        self.screen_title.setText("Главная")
        self.screen_stack.setCurrentWidget(self.home_page)
        active_id = self._selected_live_game_id
        active = self.online_sessions.get(active_id) if active_id else None
        if active and active.status in {"started", "playing"}:
            self.resume_game_button.setVisible(True)
            self.home_status.setText(f"Идёт партия против {active.opponent}. Её можно продолжить позже.")
        elif self.manual_connect_mode and not self.lichess:
            self.resume_game_button.setVisible(False)
            self.home_status.setText(
                "Ручной режим: до нажатия «Подключиться вручную» приложение не обращается к "
                "Lichess и не восстанавливает OAuth-сессию. Локальные партии доступны."
            )
        else:
            self.resume_game_button.setVisible(False)
            self.home_status.setText("Выберите режим игры. Анализ и тренировки появятся позже.")
        for button in (self.home_ai_button, self.home_friend_button, self.home_username_button):
            button.setEnabled(self._online_actions_allowed())
        self.active_games_button.setEnabled(self._online_actions_allowed())
        self.home_archive_button.setEnabled(self._online_actions_allowed())
        self.home_batch_analysis_button.setEnabled(self._online_actions_allowed())
        self.home_pilot_import_button.setEnabled(self._online_actions_allowed())
        self.home_puzzles_button.setEnabled(self._online_actions_allowed())
        self.home_puzzles_pilot_button.setEnabled(self._online_actions_allowed())

    def start_archive_sync(self) -> None:
        if not self._online_actions_allowed():
            return
        answer = QMessageBox.question(
            self, "Синхронизация архива",
            "Chess Coach запросит PGN партий вашего аккаунта и сохранит их локально. "
            "Первый запуск загрузит архив, следующие — только новые/изменившиеся записи. "
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Подготовка синхронизации…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("Архив Lichess")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_archive_sync)
        self._archive_progress_dialog = dialog
        dialog.show()
        self.lichess.sync_archive()

    def start_archive_pilot(self) -> None:
        if not self._online_actions_allowed():
            return
        choice, accepted = QInputDialog.getItem(
            self, "Пробный импорт архива", "Сколько последних завершённых партий импортировать?",
            ["5", "10"], 0, False)
        if not accepted:
            return
        limit = int(choice)
        answer = QMessageBox.question(
            self, "Подтвердите пробный импорт",
            f"Будут запрошены последние {limit} завершённых партий аккаунта Lichess "
            "и сохранены в существующую локальную SQLite. Пробный импорт не меняет "
            "курсор полной синхронизации. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Получено: 0 · новых: 0", "Отменить", 0, 0, self)
        dialog.setWindowTitle("Пробный импорт Lichess")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_archive_sync)
        self._archive_progress_dialog = dialog
        dialog.show()
        self.lichess.pilot_import_archive(max_games=limit)

    def _archive_pilot_progress(self, received: int, new: int, game_id: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.setLabelText(
                f"Получено: {received} · новых: {new}\nТекущая партия: {game_id}")

    def _archive_pilot_finished(self, received: int, new: int, message: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.close()
            self._archive_progress_dialog = None
        QMessageBox.information(
            self, "Пробный импорт Lichess",
            f"Записей получено: {received}\nНовых партий: {new}\n"
            f"Курсор полной синхронизации не изменён.\n\n{message}")

    def _archive_progress(self, count: int, game_id: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.setLabelText(
                f"Сохранено партий: {count}\nТекущая запись: {game_id}")

    def _archive_finished(self, count: int, message: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.close()
            self._archive_progress_dialog = None
        state = self._current_archive_sync_state()
        successful = state.get("last_success_at")
        last_success = self._format_sync_time(successful) if successful else "ещё не было"
        resumable = ("Да — продолжение доступно" if state.get("last_error")
                     or not state.get("backfill_complete") else "Нет")
        diagnostics = (f"Новых партий в этом запуске: {count}\n"
                       f"Последняя успешная синхронизация: {last_success}\n"
                       f"Можно возобновить: {resumable}")
        if state.get("last_error"):
            diagnostics += f"\nОшибка/прерывание: {state['last_error']}"
        QMessageBox.information(self, "Архив Lichess", f"{diagnostics}\n\n{message}")

    def _current_archive_sync_state(self) -> dict:
        if not self.lichess or not self.lichess.username:
            return {}
        account_id = f"lichess:{self.lichess.username.strip().lower()}"
        return self.game_repository.archive_sync_state(account_id)

    def show_archive_status(self) -> None:
        username = self.lichess.username if self.lichess and self.lichess.username else None
        account_id = f"lichess:{username.strip().lower()}" if username else None
        count = self.game_repository.search_games(
            completed_only=False, account_id=account_id, page_size=1)["total"]
        store_type = "Изолированная временная база --offline" if self.offline_mode else "Пользовательская SQLite"
        details = [
            f"Хранилище: {store_type}",
            f"Режим запуска: {'--offline' if self.offline_mode else '--manual-connect' if self.manual_connect_mode else 'обычный'}",
            f"Состояние Lichess: {'подключено' if self._lichess_connected else 'не подключено'}",
            f"Python: {platform.python_version()} · PySide6: {PySide6.__version__} · Qt: {qVersion()} · shiboken6: {shiboken6.__version__}",
            f"Путь к базе (показан по явному запросу): {self.game_repository.path}",
            f"Локальных партий: {count}",
            f"Lichess аккаунт: {username or 'не подключён'}",
        ]
        if account_id:
            state = self.game_repository.archive_sync_state(account_id)
            stage = "Полная выгрузка завершена" if state.get("backfill_complete") else "Полная выгрузка не завершена"
            details.append(f"Этап архива: {stage}")
            details.append(f"Последняя полная/инкрементальная синхронизация: "
                           f"{self._format_sync_time(state.get('last_success_at')) if state.get('last_success_at') else 'нет'}")
            details.append(f"Новых партий при последней синхронизации: {state.get('last_sync_new_count', 0)}")
            details.append(f"Можно продолжить: {'да' if state.get('last_error') or not state.get('backfill_complete') else 'нет'}")
            runs = self.game_repository.list_archive_pilot_runs(account_id, limit=1)
            if runs:
                pilot = runs[0]
                details.append(f"Последний пробный импорт: получено {pilot['received_count']}, "
                               f"новых {pilot['new_count']} · {pilot['status']}")
            if state.get("last_error"):
                details.append(f"Последняя ошибка/прерывание: {state['last_error']}")
        else:
            details.append("Подключите Lichess, чтобы увидеть статус архива по аккаунту.")
        QMessageBox.information(self, "Хранилище и статус архива", "\n".join(details))

    @staticmethod
    def _format_sync_time(value: str) -> str:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        except (AttributeError, ValueError):
            return str(value)

    def _archive_game_refresh_finished(self, game_id: str, message: str) -> None:
        if message.startswith("История этой партии обновлена"):
            self.home_status.setText(f"Партия {game_id}: {message}")
        else:
            self.home_status.setText(f"Не удалось обновить партию {game_id}: {message}")

    def show_statistics(self) -> None:
        dialog = StatisticsDialog(self.game_repository, self)
        self._statistics_dialog = dialog
        dialog.worker_started.connect(self._statistics_worker_started)
        dialog.worker_finished.connect(self._statistics_worker_finished)
        dialog.position_requested.connect(self._open_statistics_position)
        dialog.exec()
        self._statistics_dialog = None

    def show_batch_analysis(self) -> None:
        if not self._online_actions_allowed() or not self.lichess:
            QMessageBox.warning(self, "Массовый анализ",
                "Для fail-closed проверки fair-play сначала подключите Lichess. "
                "Без подтверждённого состояния аккаунта очередь не запускается.")
            return
        if self._batch_dialog is not None:
            self._batch_dialog.show(); self._batch_dialog.raise_(); self._batch_dialog.activateWindow()
            return
        dialog = BatchAnalysisDialog(self)
        self._batch_dialog = dialog
        dialog.run_requested.connect(lambda limit, nodes, hash_mb, resume, retry_failed, selective_review:
            self._start_batch_analysis(dialog, limit, nodes, hash_mb, resume,
                                       retry_failed, selective_review))
        dialog.pause_requested.connect(self._pause_batch_analysis)
        dialog.cancel_requested.connect(self._cancel_batch_analysis)
        dialog.finished.connect(lambda _result: setattr(self, "_batch_dialog", None))
        dialog.show()

    def _start_batch_analysis(self, dialog: BatchAnalysisDialog, limit: int, nodes: int,
                              hash_mb: int, resume: bool, retry_failed: bool = False,
                              selective_review: bool = False) -> None:
        if self._shutdown_requested or (self._batch_thread and self._batch_thread.is_alive()):
            return
        service = self.lichess
        username = str(getattr(service, "username", "") or "").strip()
        engine_path = str(self.settings.value("stockfish_path", "", type=str)).strip()
        if not service or not username or not engine_path:
            QMessageBox.warning(dialog, "Массовый анализ",
                "Подключите аккаунт Lichess и выберите существующий Stockfish executable "
                "в настройках. Бинарник автоматически не скачивается.")
            return
        try:
            options = FastPassOptions(nodes=nodes, hash_mb=hash_mb)
            runner = BatchAnalysisRunner(self.game_repository, engine_path, options=options)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(dialog, "Массовый анализ", str(exc))
            return
        config = (engine_path, runner.options.nodes, runner.options.hash_mb)
        self._batch_config = config
        self._batch_engine_key = runner.engine_key
        self._batch_stop = threading.Event()
        self._batch_pause = threading.Event()
        stop, pause = self._batch_stop, self._batch_pause
        generation = self._analysis_session_generation
        account_id = self.game_repository.ensure_archive_account(username)
        dialog.set_running(True)
        dialog.worker_progress.emit("Подготовка очереди; Stockfish ещё не запущен…")

        def work() -> None:
            try:
                if resume:
                    available = self.game_repository.analysis_job_counts(
                        engine_version=runner.engine_key, parameters=runner.parameters)
                    review_available = (self.game_repository.analysis_job_counts(
                        engine_version=runner.review_engine_key) if selective_review else {})
                    if not (any(available.get(status, 0)
                                for status in ("queued", "paused", "running"))
                            or any(review_available.get(status, 0)
                                   for status in ("queued", "paused", "running"))):
                        dialog.worker_finished.emit(
                            False, "Для выбранного движка и бюджета нет незавершённой очереди.")
                        return
                    resumed = self.game_repository.resume_paused_analysis_jobs(
                        engine_version=runner.engine_key, parameters=runner.parameters)
                    if selective_review:
                        resumed += self.game_repository.resume_paused_analysis_jobs(
                            engine_version=runner.review_engine_key)
                    enqueued = {"queued": 0, "already_present": 0}
                else:
                    enqueued = runner.enqueue_missing(account_id=account_id, limit=limit,
                                                      retry_failed=retry_failed)
                    resumed = 0
                unknown_preflight = threading.Event()
                initial_counts = self.game_repository.analysis_job_counts(
                    engine_version=runner.engine_key, parameters=runner.parameters)
                review_counts = self.game_repository.analysis_job_counts(
                    engine_version=runner.review_engine_key)
                dialog.worker_queue_status.emit(
                    f"Fast — добавлено: {enqueued['queued']} · очередь: {initial_counts.get('queued', 0)} · "
                    f"пауза: {initial_counts.get('paused', 0)} · готово: {initial_counts.get('complete', 0)} · "
                    f"ошибки: {initial_counts.get('failed', 0)}\n"
                    f"Selective review — очередь: {review_counts.get('queued', 0)} · "
                    f"пауза: {review_counts.get('paused', 0)} · готово: {review_counts.get('complete', 0)} · "
                    f"ошибки: {review_counts.get('failed', 0)}")

                def safety_check(_job: dict) -> bool | None:
                    # Called before every game. Any disconnect, identity change,
                    # malformed/truncated list, or API error fails closed.
                    if (stop.is_set() or pause.is_set() or not self._lichess_connected
                            or self.lichess is not service
                            or generation != self._analysis_session_generation
                            or str(getattr(service, "username", "") or "").strip().casefold()
                               != username.casefold()):
                        unknown_preflight.set()
                        return None
                    token = service.token
                    if not token:
                        unknown_preflight.set()
                        return None
                    try:
                        games = service.client.get_playing_games(token)
                    except Exception:
                        unknown_preflight.set()
                        raise
                    if (stop.is_set() or pause.is_set() or not self._lichess_connected
                            or self.lichess is not service
                            or generation != self._analysis_session_generation
                            or str(getattr(service, "username", "") or "").strip().casefold()
                               != username.casefold()
                            or service.token != token):
                        unknown_preflight.set()
                        return None
                    safety = _batch_safety_from_games(games)
                    if safety is None:
                        unknown_preflight.set()
                    return safety

                def progress(game_id: str, done: int, total: int) -> None:
                    counts = self.game_repository.analysis_job_counts(
                        engine_version=runner.engine_key, parameters=runner.parameters)
                    dialog.worker_queue_status.emit(
                        f"Очередь: {counts.get('queued', 0)} · пауза: {counts.get('paused', 0)} · "
                        f"выполнены: {counts.get('complete', 0)} · ошибки: {counts.get('failed', 0)} · "
                        f"неизвестное состояние: {int(unknown_preflight.is_set())}")
                    dialog.worker_progress.emit(
                        f"Партия {game_id}: позиции {done}/{total} · "
                        f"очередь: {counts.get('queued', 0)} · готово: {counts.get('complete', 0)} · "
                        f"ошибки: {counts.get('failed', 0)}")

                result = runner.run(stop_event=stop, pause_event=pause,
                                    safety_check=safety_check, progress=progress,
                                    include_selective_review=selective_review,
                                    account_id=account_id)
                counts = self.game_repository.analysis_job_counts(
                    engine_version=runner.engine_key, parameters=runner.parameters)
                review_counts = self.game_repository.analysis_job_counts(
                    engine_version=runner.review_engine_key)
                dialog.worker_queue_status.emit(
                    f"Fast — очередь: {counts.get('queued', 0)} · пауза: {counts.get('paused', 0)} · "
                    f"готово: {counts.get('complete', 0)} · ошибки: {counts.get('failed', 0)} · "
                    f"unknown: {int(unknown_preflight.is_set())}\n"
                    f"Selective review — очередь: {review_counts.get('queued', 0)} · "
                    f"пауза: {review_counts.get('paused', 0)} · готово: {review_counts.get('complete', 0)} · "
                    f"ошибки: {review_counts.get('failed', 0)}")
                dialog.worker_finished.emit(bool(result.get("paused")),
                    f"Добавлено: {enqueued['queued']} · уже стояло в очереди: {enqueued['already_present']} · "
                    f"возобновлено: {resumed}\nОбработано сейчас: {result.get('processed', 0)} · "
                    f"очередь: {counts.get('queued', 0)} · пауза: {counts.get('paused', 0)} · "
                    f"готово: {counts.get('complete', 0)} · ошибки: {counts.get('failed', 0)} · "
                    f"отменено: {counts.get('cancelled', 0)}\n"
                    f"Selective review: добавлено {result.get('review_queued', 0)}, "
                    f"очередь {review_counts.get('queued', 0)}, готово {review_counts.get('complete', 0)}\n"
                    f"{result.get('reason') or ''}")
            except Exception as exc:
                dialog.worker_finished.emit(False, f"Очередь не запущена или остановлена: {exc}")

        thread = threading.Thread(target=work, name="chess-coach-batch-analysis", daemon=True)
        self._batch_thread = thread
        thread.start()

    def _pause_batch_analysis(self) -> None:
        if self._batch_pause is not None:
            self._batch_pause.set()
            if self._batch_dialog is not None:
                self._batch_dialog.pause_button.setEnabled(False)
                self._batch_dialog.progress_label.setText("Пауза запрошена; завершаем текущую позицию…")

    def _cancel_batch_analysis(self) -> None:
        if self._batch_stop is not None:
            self._batch_stop.set()
            if self._batch_dialog is not None:
                self._batch_dialog.pause_button.setEnabled(False)
                self._batch_dialog.cancel_button.setEnabled(False)
                self._batch_dialog.progress_label.setText("Отмена запрошена; останавливаем движок…")

    def _statistics_worker_started(self, thread: threading.Thread) -> None:
        self._statistics_workers.add(thread)

    def _statistics_worker_finished(self, thread: threading.Thread) -> None:
        self._statistics_workers.discard(thread)
        if self._shutdown_requested and self._close_retry_scheduled:
            self._close_retry_timer.start(0)

    def _open_statistics_position(self, game_id: str, ply: int) -> None:
        dialog = getattr(self, "_statistics_dialog", None)
        if dialog is not None:
            dialog.accept()
            self._statistics_dialog = None
        self._open_analysis_position(game_id, ply)

    def start_puzzle_sync(self) -> None:
        if not self._online_actions_allowed():
            return
        answer = QMessageBox.question(
            self, "История шахматных задач",
            "Откроется Lichess OAuth для отдельного разрешения puzzle:read. "
            "Chess Coach сохранит только вашу личную историю задач в локальной SQLite. "
            "Текущий токен для партий не будет заменён. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Проверяем разрешение и обновляем историю задач…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("История задач Lichess")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_puzzle_sync)
        self._puzzle_progress_dialog = dialog
        self._puzzles_scanned_count = 0
        self._puzzles_new_count = 0
        dialog.show()
        self.lichess.connect_and_sync_puzzle_activity(self.game_repository)

    def start_puzzle_pilot(self) -> None:
        if not self._online_actions_allowed():
            return
        value, accepted = QInputDialog.getItem(
            self, "Пробная загрузка задач", "Сколько последних попыток загрузить?",
            ["5", "10"], 0, False)
        if not accepted:
            return
        limit = int(value)
        answer = QMessageBox.question(
            self, "Пробная загрузка задач Lichess",
            f"Chess Coach запросит отдельное разрешение Lichess puzzle:read и загрузит не более "
            f"{limit} последних попыток. Полный курсор синхронизации и токен для партий "
            "не изменятся. Запросы будут выполняться только после подтверждения. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Ожидание OAuth и загрузка ограниченной выборки…",
                                 "Отменить", 0, 0, self)
        dialog.setWindowTitle("Пробная история задач")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_puzzle_sync)
        self._puzzle_pilot_progress_dialog = dialog
        dialog.show()
        self.lichess.pilot_puzzle_activity(limit, self.game_repository)

    def _puzzle_pilot_progress(self, received: int, new_count: int) -> None:
        dialog = getattr(self, "_puzzle_pilot_progress_dialog", None)
        if dialog:
            dialog.setLabelText(f"Получено попыток: {received} · новых: {new_count}")

    def _puzzle_pilot_finished(self, received: int, new_count: int,
                               run_id: str, message: str) -> None:
        dialog = getattr(self, "_puzzle_pilot_progress_dialog", None)
        if dialog:
            dialog.close()
            self._puzzle_pilot_progress_dialog = None
        account_id = (f"lichess:{self.lichess.puzzle_username.strip().lower()}"
                      if self.lichess and self.lichess.puzzle_username else "")
        QMessageBox.information(
            self, "Пробная история задач",
            f"Получено попыток: {received}\nНовых: {new_count}\n"
            "Курсор полной синхронизации не изменён.\n" + message)
        if account_id and run_id:
            self.show_puzzle_history(account_id, pilot_run_id=run_id)

    def _puzzles_progress(self, count: int) -> None:
        self._puzzles_new_count = count
        self._update_puzzle_sync_progress()

    def _puzzles_scanned(self, count: int) -> None:
        self._puzzles_scanned_count = count
        self._update_puzzle_sync_progress()

    def _update_puzzle_sync_progress(self) -> None:
        if self._puzzle_progress_dialog:
            self._puzzle_progress_dialog.setLabelText(
                f"Проверено попыток: {getattr(self, '_puzzles_scanned_count', 0)}"
                f" · новых: {getattr(self, '_puzzles_new_count', 0)}")

    def _puzzles_finished(self, count: int, message: str) -> None:
        if self._puzzle_progress_dialog:
            self._puzzle_progress_dialog.close()
            self._puzzle_progress_dialog = None
        if self.lichess and (self.lichess.puzzle_username or self.lichess.username):
            puzzle_username = self.lichess.puzzle_username or self.lichess.username
            account_id = f"lichess:{puzzle_username.strip().lower()}"
            stats = self.game_repository.puzzle_statistics(account_id)
            sync_state = self.game_repository.puzzle_sync_state(account_id)
            rate = stats["success_percent"]
            successful = sync_state.get("last_success_at")
            last_success = self._format_sync_time(successful) if successful else "ещё не было"
            resumable = ("Да" if sync_state.get("last_error")
                         or not sync_state.get("backfill_complete")
                         or sync_state.get("incremental_before") else "Нет")
            result = (f"Всего попыток: {stats['attempts']}\n"
                      f"Новых попыток в этом запуске: {count}\n"
                      f"Решено: {stats['wins']} · Ошибок: {stats['failures']}\n"
                      f"Доля успеха: {rate if rate is not None else 'н/д'}%\n"
                      f"Средний рейтинг задач: {stats['average_puzzle_rating'] if stats['average_puzzle_rating'] is not None else 'н/д'}\n"
                      f"Последняя успешная синхронизация: {last_success}\n"
                      f"Можно возобновить: {resumable}"
                      +(f"\nОшибка/прерывание: {sync_state['last_error']}" if sync_state.get("last_error") else "")
                      +"\n"
                      f"{message}")
        else:
            result = f"Обработано: {count}. {message}"
        QMessageBox.information(self, "История задач Lichess", result)
        if self.lichess and (self.lichess.puzzle_username or self.lichess.username):
            puzzle_username = self.lichess.puzzle_username or self.lichess.username
            self.show_puzzle_history(f"lichess:{puzzle_username.strip().lower()}")

    def show_puzzle_history(self, account_id: str, *, pilot_run_id: str | None = None) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Моя история задач Lichess · локально")
        dialog.resize(850, 540)
        layout = QHBoxLayout(dialog)
        history = QListWidget()
        history_panel = QVBoxLayout()
        summary = QLabel()
        summary.setWordWrap(True)
        history_panel.addWidget(summary)
        history_panel.addWidget(history, 1)
        detail = QVBoxLayout()
        board = BoardView(chess.Board(), dialog)
        board.set_interaction_enabled(False)
        solution = QLabel("Выберите попытку для офлайн-просмотра.")
        solution.setWordWrap(True)
        detail.addWidget(board, 1)
        detail.addWidget(solution)
        history_widget = QWidget(dialog)
        history_widget.setLayout(history_panel)
        layout.addWidget(history_widget, 1)
        detail_widget = QWidget(dialog)
        detail_widget.setLayout(detail)
        layout.addWidget(detail_widget, 2)
        attempts = (self.game_repository.puzzle_pilot_history(
            account_id, run_id=pilot_run_id, limit=500) if pilot_run_id
            else self.game_repository.puzzle_history(account_id, limit=500))
        attempts = [attempt for attempt in attempts if attempt.get("attempt_uid")]
        summary.setText(_puzzle_attempt_summary(attempts))
        for attempt in attempts:
            date = datetime.fromtimestamp(attempt["attempt_date_ms"] / 1000).strftime("%Y-%m-%d %H:%M")
            themes = ", ".join(json.loads(attempt["themes_json"]))
            outcome = "Решено" if attempt["win"] else "Не решено"
            item = QListWidgetItem(f"{date} · {outcome} · {attempt['puzzle_rating'] or 'без рейтинга'} · {themes}")
            item.setData(Qt.ItemDataRole.UserRole, attempt)
            history.addItem(item)
        def select_attempt(row: int) -> None:
            if row < 0:
                return
            attempt = history.item(row).data(Qt.ItemDataRole.UserRole)
            try:
                position = chess.Board(attempt["fen"])
                board.set_position(position)
                moves = json.loads(attempt["solution_json"])
                san = []
                for uci in moves:
                    move = chess.Move.from_uci(uci)
                    san.append(position.san(move))
                    position.push(move)
                solution.setText(f"Позиция FEN: {attempt['fen']}\n"+
                                 "Решение (офлайн, без отправки результата): " + " ".join(san))
            except (ValueError, chess.InvalidMoveError):
                solution.setText("Не удалось разобрать позицию или решение этой задачи.")
        history.currentRowChanged.connect(select_attempt)
        if attempts:
            history.setCurrentRow(0)
        else:
            solution.setText("История задач пуста.")
        dialog.exec()

    def start_local_game(self) -> None:
        self.online_session = None
        self._review_session = None
        self.board.clear_annotations()
        self.controller.new_game()
        self._local_board_mode = "free"
        self.controller.set_premove_training(False)
        self.local_mode_combo.blockSignals(True)
        self.local_mode_combo.setCurrentIndex(self.local_mode_combo.findData("free"))
        self.local_mode_combo.blockSignals(False)
        self._screen_mode = "local"
        self.screen_title.setText("Локальная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Новая свободная партия. Можно ходить за обе стороны.")

    def _local_board_mode_changed(self, _index: int) -> None:
        if self._screen_mode != "local":
            return
        mode = self.local_mode_combo.currentData()
        if mode not in {"free", "premove_training"} or mode == self._local_board_mode:
            return
        self._local_board_mode = mode
        discarded = self.controller.set_premove_training(mode == "premove_training")
        self.board.set_premove(None, self.controller.player_color)
        if discarded:
            message = "Режим изменён; ожидавшийся предход снят, история ходов сохранена."
        elif mode == "free":
            message = "Свободная доска включена. История ходов сохранена."
        else:
            message = "Тренировка предходов включена. История ходов сохранена."
        self.refresh(message)

    def resume_current_game(self) -> None:
        game_id = self._selected_live_game_id
        session = self.online_sessions.get(game_id) if game_id else None
        if not session:
            return
        if session is not self.online_session:
            self.board.clear_annotations()
        self.online_session = session
        self._review_session = None
        self.online_view_ply = None
        self._screen_mode = "online"
        self.screen_title.setText("Активная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Возврат к текущей партии.")

    def _show_result(self, session: LichessGameSession, saved: bool, detail: str | None = None) -> None:
        self._screen_mode = "result"
        self.screen_title.setText("Партия завершена")
        outcome = _result_label(session.color, session.winner, session.status)
        self.result_summary.setText(
            f"{outcome}\nСоперник: {session.opponent}\nСтатус Lichess: {session.status}"
        )
        write_pending = self.game_repository.has_pending_write(session.game_id)
        stored = (None if write_pending or session.final_sync_pending
                  else self.game_repository.get_game(session.game_id))
        if session.final_sync_pending:
            saved = False
            saved_label = "Результат получен; ожидаем подтверждённую финальную историю от Lichess."
        elif write_pending:
            saved = False
            saved_label = "Финальная история подтверждена; сохраняем партию локально…"
            QTimer.singleShot(80, lambda game_id=session.game_id: self._poll_result_write(game_id))
        else:
            saved = bool(saved and stored and stored["moves"] == session.moves
                         and stored["status"] == session.status
                         and not stored.get("final_sync_pending", 0))
            saved_label = ("Партия сохранена локально." if saved else
                           f"Не удалось подтвердить сохранение локальной партии: {detail or self.game_repository.write_error(session.game_id) or 'ошибка записи'}")
        self.result_saved_label.setText(
            saved_label
        )
        self.result_saved_label.setStyleSheet("color: #39704a;" if saved else "color: #9b3434;")
        self.result_review_button.setEnabled(bool(session.moves))
        self.result_export_button.setEnabled(
            not session.final_sync_pending and not write_pending
            and bool(self.game_repository.export_pgn(session.game_id))
        )
        self.screen_stack.setCurrentWidget(self.result_page)

    def _poll_result_write(self, game_id: str) -> None:
        session = self.online_sessions.get(game_id)
        if (not session or session.final_sync_pending or self._screen_mode != "result"
                or game_id != self._selected_live_game_id):
            return
        if self.game_repository.has_pending_write(game_id):
            QTimer.singleShot(80, lambda: self._poll_result_write(game_id))
            return
        self._show_result(session, True)

    def review_finished_game(self) -> None:
        session = self.online_session
        if not session or session.status in {"started", "playing", "created"}:
            return
        self._review_session = session
        self._screen_mode = "review"
        self.screen_title.setText("Просмотр завершённой партии")
        self.online_view_ply = None
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Просмотр сохранённой партии (только чтение).")

    def export_current_pgn(self) -> None:
        session = self.online_session
        if not session:
            return
        pgn = self.game_repository.export_pgn(session.game_id)
        if not pgn:
            QMessageBox.information(self, "Экспорт PGN", "Для этой партии пока нет сохранённого PGN.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт PGN", f"{session.game_id}.pgn", "PGN (*.pgn)")
        if path:
            Path(path).write_text(pgn + "\n", encoding="utf-8")

    def _apply_preferences(self) -> None:
        self.board.set_flipped(self.settings.value("board_flipped", False, type=bool))
        self.board.set_show_legal_moves(self.settings.value("show_legal_moves", True, type=bool))
        self.board.set_show_last_move(self.settings.value("show_last_move", True, type=bool))
        self.board.set_annotations_enabled(
            self.settings.value("annotations_enabled", True, type=bool)
        )
        allow_premoves = self.settings.value("allow_premoves", True, type=bool)
        self.board.set_premove_allowed(allow_premoves)
        if not allow_premoves:
            self.controller.cancel_premove()
            if self.online_session:
                self.online_session.premove = None

    def on_move_intent(self, move: chess.Move) -> None:
        if self.online_session is not None:
            session = self.online_session
            if self.online_view_ply is not None or not session.begin_submission(move):
                self.refresh("Ход недоступен: проверьте актуальную позицию.")
                return
            self.lichess.send_move(session.game_id, move.uci(), session.pending_request_id)
            self.refresh(f"Отправляем ход {move.uci()}; ожидаем подтверждения Lichess.")
            return
        previous = tuple(self.controller.board.move_stack)
        if self.controller.apply_move_intent(move):
            if tuple(self.controller.board.move_stack) != previous:
                self.board.clear_annotations()
            self.refresh("Ход записан в локальную партию.")
        else:
            self.refresh("Ход не выполнен: позиция не изменилась.")

    def on_premove_intent(self, move: chess.Move) -> None:
        if self.online_session is not None:
            if self.online_session.queue_premove(move):
                self.board.set_premove(move, self.online_session.color)
                self.refresh("Предход записан и будет проверен после хода соперника.")
            else:
                self.refresh("Предход не принят в текущей позиции.")
            return
        if self.controller.queue_premove(move):
            self.refresh("Предход поставлен. Он будет проверен после хода соперника.")
        else:
            self.refresh("Предход не принят в текущем состоянии.")

    def cancel_premove(self) -> None:
        if self.online_session is not None:
            self.online_session.premove = None
            self.board.set_premove(None, self.online_session.color)
            self.refresh("Предход отменён.")
            return
        self.controller.cancel_premove()
        self.refresh("Предход отменён.")

    def undo_local_last_move(self) -> None:
        if self.online_session is not None:
            return
        if self.controller.undo_last_move():
            self.board.clear_annotations()
            self.refresh("Последний ход локальной партии отменён.")
        else:
            self.refresh("В локальной партии пока нет хода для отмены.")

    def apply_opponent_move(self) -> None:
        if self._local_board_mode != "premove_training" or not self.controller.premove_training:
            return
        raw = self.opponent_move.currentData()
        if not raw:
            return
        move = chess.Move.from_uci(raw)
        previous = tuple(self.controller.board.move_stack)
        _, message = self.controller.apply_test_opponent_move(move)
        if tuple(self.controller.board.move_stack) != previous:
            self.board.clear_annotations()
        self.refresh(message)

    def on_move_selected(self, item: QListWidgetItem) -> None:
        if self.online_session is not None:
            ply = int(item.data(Qt.ItemDataRole.UserRole))
            self.online_view_ply = None if ply >= len(self.online_session.board.move_stack) else ply
            self.refresh()
            return
        self.controller.go_to(int(item.data(Qt.ItemDataRole.UserRole)))
        self.refresh()

    def navigate_to(self, ply: int) -> None:
        if self.online_session is not None:
            self.online_view_ply = None if ply >= len(self.online_session.board.move_stack) else ply
            self.refresh()
            return
        self.controller.go_to(ply)
        self.refresh()

    def navigate_by(self, delta: int) -> None:
        if self.online_session is not None:
            maximum = len(self.online_session.board.move_stack)
            current = maximum if self.online_view_ply is None else self.online_view_ply
            self.online_view_ply = max(0, min(maximum, current + delta))
            if self.online_view_ply == maximum:
                self.online_view_ply = None
            self.refresh()
            return
        self.controller.step_view(delta)
        self.refresh()

    def navigate_live(self) -> None:
        if self.online_session is not None:
            self.online_view_ply = None
            self.refresh()
            return
        self.controller.go_live()
        self.refresh()

    def flip_board(self) -> None:
        flipped = not self.settings.value("board_flipped", False, type=bool)
        self.settings.setValue("board_flipped", flipped)
        self.settings.sync()
        self.board.set_flipped(flipped)

    def new_game(self) -> None:
        if self.online_session is not None:
            QMessageBox.information(
                self, "Онлайн-партия",
                "Чтобы вернуться к локальной доске, дождитесь завершения текущей онлайн-партии.",
            )
            return
        if self.controller.ply:
            answer = QMessageBox.question(
                self,
                "Новая партия",
                "Начать новую локальную партию? Текущая партия не будет сохранена.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.controller.new_game()
        self.board.clear_annotations()
        self.refresh("Новая локальная партия.")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.save(self.settings)
            new_color = chess.Color(int(self.settings.value("player_color", chess.WHITE)))
            self.controller.player_color = new_color
            if not self.settings.value("allow_premoves", True, type=bool):
                self.cancel_premove()
            self.refresh("Настройки сохранены.")

    def connect_lichess(self) -> None:
        if self.offline_mode:
            QMessageBox.information(self, "Lichess", "Сетевые функции отключены в изолированном офлайн-режиме.")
            return
        if not self.lichess:
            self.lichess = self._create_lichess_service()
            if self.lichess:
                self._attach_lichess_service(self.lichess)
        if not self.lichess:
            QMessageBox.warning(self, "Lichess", "Windows Credential Manager недоступен. Установите зависимости проекта.")
            return
        # A previous account's asynchronous safety response must not survive a
        # user-initiated reconnect, even before OAuth returns the new identity.
        self._invalidate_analysis_preflight()
        if self.manual_connect_mode:
            try:
                has_saved_token = bool(self.lichess.token)
            except CredentialStorageError as exc:
                QMessageBox.warning(self, "Lichess", str(exc))
                return
            if has_saved_token:
                self.connect_button.setEnabled(False)
                self.lichess.restore()
            else:
                self.lichess.connect_account()
            return
        if getattr(self, "_restore_retry_ready", False) and self.lichess.token:
            self._restore_retry_ready = False
            self.connect_button.setEnabled(False)
            self.lichess.restore()
            return
        self.lichess.connect_account()

    def disconnect_lichess(self) -> None:
        self._invalidate_analysis_preflight()
        self._lichess_connected = False
        self._pause_batch_analysis()
        if self.lichess:
            self.lichess.disconnect_account()

    def _invalidate_analysis_preflight(self) -> None:
        """Invalidate a pending safety reply when the authenticated session changes."""
        self._analysis_session_generation += 1
        self._pause_batch_analysis()
        if self._analysis_safety_phase == "preflight" and self._analysis_safety_request_id:
            self._cancel_analysis_action(self._analysis_safety_request_id)

    def cancel_username_challenge(self) -> None:
        if self.lichess:
            self.lichess.cancel_username_challenge()

    def show_active_games(self) -> None:
        if not self._online_actions_allowed():
            self.home_status.setText("Подключите Lichess, чтобы запросить активные партии.")
            return
        dialog = ActiveGamesDialog(self)
        self._active_games_dialog = dialog
        dialog.refresh_requested.connect(self._request_active_games)
        dialog.open_requested.connect(self.open_active_game)
        dialog.refresh_requested.emit()
        dialog.exec()
        self._active_games_dialog = None

    def _request_active_games(self) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if not dialog or not self.lichess:
            return
        dialog.set_loading()
        self.lichess.fetch_active_games()

    def _active_games_received(self, games: list[dict]) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_games(games)

    def _active_games_error(self, message: str) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_error(message)

    def open_active_game(self, game: dict) -> None:
        game_id = game.get("gameId")
        if not game_id or not self._online_actions_allowed():
            return
        session = self.online_sessions.get(str(game_id))
        if session is not None and session.status not in {"created", "started", "playing"}:
            QMessageBox.information(self, "Активная партия", "Lichess сообщает, что эта партия уже завершена.")
            return
        if session is None:
            session = LichessGameSession(str(game_id), self.game_repository)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            session.status = "started"
            session.clock_control = str(game.get("speed") or "-")
            self.online_sessions[session.game_id] = session
        else:
            # A queued premove belongs to an earlier visible session. Viewing a
            # game from the account list must never submit that stale intent.
            session.premove = None
        # Refresh the immutable type from the server's account-playing entry,
        # including when this session already exists locally.
        session.set_server_game_identity(game)
        if session is not self.online_session:
            self.board.clear_annotations()
        self.online_session = session
        self._selected_live_game_id = session.game_id
        self._review_session = None
        self.online_view_ply = None
        self._screen_mode = "online"
        self.screen_title.setText("Активная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Открываем выбранную активную партию; позицию подтвердит поток Lichess.")
        self.lichess.start_game(session.game_id)

    def _lichess_status(self, status: str) -> None:
        self.connection.setText("●  " + status)
        connected = bool(self._lichess_connected and not self.offline_mode)
        if status == "Не подключено":
            self._invalidate_analysis_preflight()
            self._lichess_connected = False
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(False)
            self.active_games_button.setEnabled(False)
        retry_status = status.startswith("Ограничение Lichess (429)")
        if status == "Проверяем подключение…":
            self.connect_button.setEnabled(False)
        elif retry_status:
            self.connect_button.setEnabled(False)
            self._restore_retry_ready = False
            import re
            match = re.search(r"через (\d+) с", status)
            delay = max(1, int(match.group(1))) if match else 60
            QTimer.singleShot(delay * 1000, self._enable_restore_retry)
            QTimer.singleShot(delay * 1000, self._refresh_online_controls)
        elif status in {"Не подключено", "Ошибка подключения"}:
            self._restore_retry_ready = False
            self.connect_button.setText("Подключить Lichess")
            self.connect_button.setEnabled(True)
        elif status.startswith("Подключено"):
            self._restore_retry_ready = False
            self.connect_button.setText("Подключить Lichess")
            self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(connected)
        online_allowed = connected and self._lichess_rate_limit_remaining() == 0
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setEnabled(online_allowed)
        for button in (self.home_ai_button, self.home_friend_button, self.home_username_button):
            button.setEnabled(online_allowed)
        self.active_games_button.setEnabled(online_allowed)
        self.home_archive_button.setEnabled(online_allowed)
        self.home_batch_analysis_button.setEnabled(online_allowed)
        self.home_pilot_import_button.setEnabled(online_allowed)
        self.home_puzzles_button.setEnabled(online_allowed)
        self.home_puzzles_pilot_button.setEnabled(online_allowed)
        if status.startswith("Ограничение Lichess"):
            self.home_status.setText(status + ". Автоматические запросы приостановлены.")

    def _enable_restore_retry(self) -> None:
        if (self.lichess and self.lichess.token and not self._lichess_connected
                and not self.offline_mode):
            self._restore_retry_ready = True
            self.connect_button.setText("Повторить подключение")
            self.connect_button.setEnabled(True)

    def _lichess_rate_limit_remaining(self) -> int:
        if not self.lichess:
            return 0
        return max(0, int(getattr(self.lichess.client, "rate_limit_remaining", 0)))

    def _online_actions_allowed(self) -> bool:
        return bool(self.lichess and not self.offline_mode and self._lichess_connected
                    and self._lichess_rate_limit_remaining() == 0)

    def _refresh_online_controls(self) -> None:
        if (not self.lichess or self.offline_mode or not self._lichess_connected):
            return
        remaining = self._lichess_rate_limit_remaining()
        if remaining > 0:
            QTimer.singleShot(remaining * 1000, self._refresh_online_controls)
            return
        for button in (self.friend_link_button, self.username_button, self.ai_button,
                       self.home_ai_button, self.home_friend_button, self.home_username_button,
                       self.active_games_button, self.home_archive_button,
                       self.home_batch_analysis_button,
                       self.home_pilot_import_button, self.home_puzzles_button):
            button.setEnabled(True)

    def _lichess_authenticated(self, username: str) -> None:
        self._invalidate_analysis_preflight()
        self._lichess_connected = True
        self.connection.setText(f"●  Подключено: {username}")
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self._refresh_online_controls()
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(self.online_session is None)
        self._resume_pending_final_syncs()
        # Reuse the existing OAuth tokens; never start authorization from a timer.
        QTimer.singleShot(3000, self._run_background_sync)

    def _run_background_sync(self) -> None:
        if (self._shutdown_requested or self.offline_mode or not self._lichess_connected
                or self.lichess is None or not self.lichess.username):
            return
        if any(s.status in {"started", "playing"} and not s.is_ai_opponent
               for s in self.online_sessions.values()):
            return  # Prioritize live play over background network traffic.
        self.lichess.sync_archive(background=True)
        self.lichess.sync_puzzle_activity_background(self.game_repository)

    def _background_sync_finished(self, kind: str, message: str) -> None:
        if not self._shutdown_requested:
            self.connection.setToolTip(f"Последнее фоновое обновление {kind}: {message}")

    def _resume_pending_final_syncs(self) -> None:
        """Resume persisted terminal games without taking over the visible screen."""
        for row in self.game_repository.list_pending_final_sync():
            game_id = row["game_id"]
            session = self.online_sessions.get(game_id)
            if session is None:
                session = LichessGameSession(game_id, self.game_repository)
                session.initial_fen = row["initial_fen"]
                session.moves = row["moves"]
                session.color = chess.WHITE if row["color"] == "white" else chess.BLACK
                session.opponent = row["opponent"]
                session.rated = bool(row["rated"])
                session.clock_control = row["clock"]
                session.status = row["status"]
                session.winner = row["winner"]
                session.white_player = row["white_player"]
                session.black_player = row["black_player"]
                session.started_at = row["started_at"]
                session.final_sync_pending = True
                session._rebuild_board()
                self.online_sessions[game_id] = session
            self.lichess.mark_game_finished(game_id)
            self.lichess.start_game(game_id)

    def _lichess_error(self, message: str) -> None:
        self._awaiting_game_start = False
        self.cancel_challenge_button.setEnabled(False)
        self.cancel_challenge_button.setVisible(False)
        if getattr(self, "_open_challenge_id", None):
            self.cancel_invitation_button.setEnabled(True)
        self.game_status.setText(message)
        QMessageBox.warning(self, "Lichess", message)
        self.refresh(message)

    def _lichess_move_failed(self, game_id: str, uci: str, request_id: str, status: int) -> None:
        session = self.online_sessions.get(game_id)
        definitive = status in {400, 401, 403, 404, 429}
        if not session or session.pending_uci != uci or not session.fail_submission(
                request_id, definitive=definitive):
            return
        message = {
            400: "Lichess отклонил ход (400). Проверьте позицию и попробуйте снова.",
            401: "Lichess отклонил ход (401). Переподключите аккаунт.",
            403: "Lichess запретил ход (403). Проверьте состояние партии.",
            404: "Партия больше не найдена.",
            429: "Lichess временно ограничил запросы (429). Попробуйте позже.",
        }.get(status, "Не удалось отправить ход; результат запроса неизвестен.")
        if status not in {400, 401, 403, 404, 429}:
            self.game_status.setText(f"Ход {uci}: {message}")
            return
        self.refresh(message)

    def request_takeback(self) -> None:
        self._start_takeback_action("offer")

    def answer_takeback(self, accept: bool) -> None:
        self._start_takeback_action("accept" if accept else "decline")

    def _start_takeback_action(self, action: str) -> None:
        session = self.online_session
        if not session or not self.lichess:
            return
        request_id = session.begin_takeback_action(action)
        if not request_id:
            self.refresh("Возврат хода сейчас недоступен: дождитесь синхронизации партии.")
            return
        messages = {
            "offer": ("Запрашиваем возврат хода у Lichess AI…" if session.is_ai_opponent
                      else "Отправляем предложение вернуть ход…"),
            "accept": "Принимаем предложение; ждём подтверждения позиции Lichess…",
            "decline": "Отправляем отказ от возврата хода…",
        }
        self.refresh(messages[action])
        self.lichess.takeback(session.game_id, action, request_id)

    def _lichess_takeback_completed(self, game_id: str, request_id: str, status: int) -> None:
        session = self.online_sessions.get(game_id)
        if not session:
            return
        action = session.takeback_action_pending
        if not session.finish_takeback_action(request_id, status):
            return
        if status == 0:
            messages = {
                "offer": ("Запрос отправлен; ожидаем подтверждённую позицию Lichess AI…"
                          if session.is_ai_opponent else
                          "Предложение возврата отправлено; ждём ответа соперника."),
                "accept": "Предложение принято; ждём обновления позиции от Lichess…",
                "decline": "Отказ отправлен; ждём подтверждения от Lichess…",
            }
            message = messages.get(action, "Запрос возврата отправлен; ждём Lichess.")
        else:
            message = {
                400: "Lichess отклонил запрос возврата хода (400). Позиция не изменена.",
                403: "Lichess запретил возврат хода (403). Позиция не изменена.",
                404: "Партия не найдена; позиция не изменена.",
                429: "Lichess временно ограничил запросы (429). Позиция не изменена; дождитесь окончания паузы перед новым действием.",
            }.get(status, "Не удалось отправить запрос возврата хода. Позиция не изменена.")
        self.refresh(message)

    def open_friend_game(self) -> None:
        if not self._online_actions_allowed():
            return
        dialog = OnlineGameDialog("Партия с другом по ссылке", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText("Создаём вызов и присоединяем вас…")
        self.home_status.setText("Создаём вызов и присоединяем вас…")
        self.lichess.open_friend_game(color, limit, increment, rated)

    def challenge_username(self) -> None:
        if not self._online_actions_allowed():
            return
        username, ok = QInputDialog.getText(self, "Вызов игрока", "Имя пользователя Lichess:")
        if not ok or not username.strip():
            return
        dialog = OnlineGameDialog("Контроль времени", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText(f"Ожидаем ответ от {username.strip()}…")
        self.home_status.setText(f"Ожидаем ответ от {username.strip()}…")
        self.lichess.challenge_username(username.strip(), color, limit, increment, rated)

    def play_ai(self) -> None:
        if not self._online_actions_allowed():
            return
        dialog = OnlineGameDialog("Игра против Lichess AI", include_level=True, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, level, _ = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText("Создаём партию против Lichess AI…")
        self.home_status.setText("Создаём партию против Lichess AI…")
        self.lichess.create_ai_game(level, color, limit, increment)

    def _challenge_ready(self, result: dict) -> None:
        url = result.get("friendUrl")
        if not url:
            self.game_status.setText("Вызов отправлен; ждём ответ Lichess…")
            return
        self._open_challenge_id = result.get("id")
        active_game = self.online_session and self.online_session.status in {"started", "playing"}
        self.cancel_invitation_button.setEnabled(bool(self._open_challenge_id) and not active_game)
        self.cancel_invitation_button.setVisible(bool(self._open_challenge_id) and not active_game)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(False)
        dialog = QDialog(self)
        dialog.setWindowTitle("Приглашение готово")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Вы подключились к партии. Отправьте другу ссылку:"))
        link = QLineEdit(url)
        link.setReadOnly(True)
        layout.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = QPushButton("Скопировать ссылку")
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(url))
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        self.game_status.setText("Ожидаем, пока друг подключится через браузер…")

    def _challenge_status(self, message: str, can_cancel: bool) -> None:
        if not can_cancel and any(word in message.casefold() for word in ("отклонил", "отменён", "отменено", "завершено")):
            self._awaiting_game_start = False
        self.game_status.setText(message)
        self.cancel_challenge_button.setEnabled(can_cancel)
        self.cancel_challenge_button.setVisible(can_cancel)

    def cancel_friend_invitation(self) -> None:
        if not getattr(self, "_open_challenge_id", None):
            return
        if QMessageBox.question(self, "Отменить приглашение", "Отменить приглашение по ссылке?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.lichess.cancel_open_invitation(self._open_challenge_id)
        self.cancel_invitation_button.setEnabled(False)
        self.game_status.setText("Отменяем приглашение…")

    def _invitation_cancelled(self, challenge_id: str) -> None:
        if challenge_id == getattr(self, "_open_challenge_id", None):
            self._awaiting_game_start = False
            self._open_challenge_id = None
            self.cancel_invitation_button.setVisible(False)
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(self._lichess_connected and self.online_session is None)
            self.game_status.setText("Приглашение отменено.")

    def _lichess_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "gameStart":
            self._open_challenge_id = None
            self.cancel_invitation_button.setEnabled(False)
            self.cancel_invitation_button.setVisible(False)
            game = event.get("game", {})
            game_id = game.get("gameId")
            if not game_id:
                return
            previous = self.online_sessions.get(game_id)
            session = previous or LichessGameSession(game_id, self.game_repository)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            session.status = "started"
            session.set_server_game_identity(game)
            self.online_sessions[game_id] = session
            if (not session.is_ai_opponent
                    and getattr(self, "_analysis_stop", None) is not None):
                self._analysis_stop.set()
            if not session.is_ai_opponent and self._batch_pause is not None:
                self._pause_batch_analysis()
            if self._awaiting_game_start and previous is None:
                self._awaiting_game_start = False
                self.board.clear_annotations()
                self._selected_live_game_id = game_id
                self.online_session = session
                self._review_session = None
                self.online_view_ply = None
                self._screen_mode = "online"
                self.screen_title.setText("Активная партия")
                self.screen_stack.setCurrentWidget(self.game_page)
                self.refresh(f"Партия началась против {session.opponent}.")
        elif kind == "gameFinish":
            game_id = event.get("game", {}).get("gameId")
            if game_id in self.online_sessions:
                game = event.get("game", {})
                game_status = game.get("status", {})
                status = game_status.get("name", "unknownFinish") if isinstance(game_status, dict) else str(game_status)
                session = self.online_sessions[game_id]
                error = None
                if session.status in {"started", "playing", "created"}:
                    try:
                        session.finish(status, game.get("winner"))
                    except Exception as exc:
                        error = str(exc)
                        session.status = status
                        session.winner = game.get("winner")
                if session.final_sync_pending:
                    self.lichess.mark_game_finished(game_id)
                    self.lichess.start_game(game_id)
                if (game_id == self._selected_live_game_id
                        and self.online_session is session
                        and self._screen_mode == "online"):
                    self._show_result(session, error is None, error)
                elif game_id == self._selected_live_game_id and self._screen_mode == "home":
                    self.show_home()
                    if error:
                        self.home_status.setText(
                            f"Партия завершена, локальная запись не подтверждена: {error}"
                        )
        elif kind == "challenge":
            if self._screen_mode == "home":
                self.home_status.setText("Получено или обновлено приглашение Lichess.")

    def _lichess_game_event(self, game_id: str, event: dict) -> None:
        session = self.online_sessions.get(game_id)
        if session is None:
            session = LichessGameSession(game_id, self.game_repository)
            self.online_sessions[game_id] = session
        # Finish is terminal. A buffered or reordered active state must never
        # reopen the session or overwrite the saved result.
        if (session.status not in {"created", "started", "playing"}
                and not session.final_sync_pending
                and event.get("type") in {"gameState", "gameFull"}):
            return
        if event.get("type") == "gameFull":
            # gameFull/gameState are position snapshots. Preserve opponent
            # classification established by gameStart/account-playing; player
            # decoration in later snapshots is not sufficient to redefine it.
            username = (self.lichess.username or "").casefold()
            white = event.get("white", {})
            black = event.get("black", {})
            if username and white.get("name", "").casefold() == username:
                session.configure_players(white, black, "white")
            elif username and black.get("name", "").casefold() == username:
                session.configure_players(white, black, "black")
            elif event.get("color") in {"white", "black"}:
                session.configure_players(white, black, event["color"])
        if event.get("type") == "opponentGone":
            if (game_id == self._selected_live_game_id and self.online_session is session
                    and self._screen_mode == "online"):
                self.refresh("Соперник временно отключился; ждём переподключения.")
            return
        previous_moves = tuple(session.moves.split())
        previous_view_ply = self.online_view_ply if (
            game_id == self._selected_live_game_id and self.online_session is session
        ) else None
        try:
            session.apply_event(event)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError) as exc:
            self._lichess_error(f"Не удалось синхронизировать позицию Lichess: {exc}")
            return
        except Exception as exc:
            if (game_id == self._selected_live_game_id and self.online_session is session
                    and self._screen_mode == "online"):
                if session.status not in {"started", "playing", "created"}:
                    self._show_result(session, False, str(exc))
                else:
                    self.refresh(f"Позиция обновлена с Lichess, но локальная запись не удалась: {exc}")
            return
        if (session.status in {"started", "playing"} and not session.is_ai_opponent
                and getattr(self, "_analysis_stop", None) is not None):
            self._analysis_stop.set()
        had_premove = session.premove is not None
        current_moves = tuple(session.moves.split())
        takeback_confirmed = (len(current_moves) < len(previous_moves)
                              and previous_moves[:len(current_moves)] == current_moves)
        is_selected_live = (
            game_id == self._selected_live_game_id
            and self.online_session is session
            and self._screen_mode == "online"
        )
        if is_selected_live and current_moves != previous_moves:
            # Only the selected game's authoritative line can clear current
            # marks. A duplicate/clock event or background game must not.
            self.board.clear_annotations()
        if is_selected_live:
            if previous_view_ply is not None:
                self.online_view_ply = min(previous_view_ply, len(session.board.move_stack))
                if self.online_view_ply == len(session.board.move_stack):
                    self.online_view_ply = None
            if session.status not in {"started", "playing", "created"}:
                self._show_result(session, True)
            else:
                message = "Возврат хода подтверждён Lichess." if takeback_confirmed else None
                if previous_view_ply is not None and takeback_confirmed:
                    message = "Возврат подтверждён; просмотр ограничен новой историей."
                self.refresh(message)
        elif (game_id == self._selected_live_game_id and self.online_session is session
              and self._screen_mode == "result" and not session.final_sync_pending):
            # The terminal result screen stays open while its saved history is reconciled.
            self._show_result(session, True)
        elif (game_id == self._selected_live_game_id and self._screen_mode == "home"
              and session.status not in {"started", "playing", "created"}):
            self.show_home()
        premove = session.take_ready_premove() if is_selected_live else None
        if premove and session.begin_submission(premove):
            self.lichess.send_move(game_id, premove.uci(), session.pending_request_id)
            if is_selected_live:
                self.refresh(f"Предход {premove.uci()} отправлен; ожидаем подтверждения Lichess.")
        elif had_premove and session.premove is None:
            if is_selected_live:
                self.refresh("Предход не стал легальным и снят.")
        if session.status not in {"created", "started", "playing"}:
            QTimer.singleShot(3000, self._run_background_sync)

    def _refresh_online(self, message: str | None = None) -> None:
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
        self.premove_title.setVisible(True)
        session = self.online_session
        board = session.board
        render_board = session.render_board
        ply = len(board.move_stack) if self.online_view_ply is None else min(self.online_view_ply, len(board.move_stack))
        display_source = render_board if self.online_view_ply is None else board
        live = self.online_view_ply is None or ply == len(board.move_stack)
        if live:
            # render_board is disposable; BoardView copies it when installing.
            shown = display_source
        else:
            shown = board.root()
            for move in board.move_stack[:ply]:
                shown.push(move)
        self.board.set_position(shown)
        self.board.set_annotation_context(session.game_id, shown)
        active = (session.status in {"started", "playing"}
                  and self._screen_mode != "review"
                  and not session.awaiting_authoritative_snapshot)
        self.board.set_interaction_enabled(live and active and not session.input_locked)
        self._apply_preferences()
        self.board.set_premove(session.premove, session.color)
        self.opponent_label.setText(f"Соперник  ·  {session.opponent}")
        self.you_label.setText("Вы  ·  " + ("белыми" if session.color == chess.WHITE else "чёрными"))
        turn = ("Синхронизация позиции…" if session.awaiting_authoritative_snapshot
                else "Ваш ход" if session.is_my_turn else "Ход соперника")
        self.turn_label.setText(turn + (" · история" if not live else ""))
        if message:
            self.game_status.setText(message)
        elif session.awaiting_authoritative_snapshot:
            self.game_status.setText(
                "Ожидаем первичную подтверждённую позицию Lichess; действия партии заблокированы."
            )
        elif session.pending_uci:
            self.game_status.setText(f"Ход {session.pending_uci} отправлен; ожидаем подтверждения Lichess…")
        elif session.takeback_requested_by_opponent:
            self.game_status.setText("Соперник предлагает вернуть ход.")
        elif session.takeback_accept_pending:
            self.game_status.setText("Возврат принят; ожидаем подтверждения позиции Lichess…")
        elif session.takeback_decline_pending:
            self.game_status.setText("Отказ отправлен; ожидаем обновления состояния партии…")
        elif session.takeback_requested_by_me:
            self.game_status.setText(
                "Запрос возврата отправлен; ждём обновления позиции Lichess AI."
                if session.is_ai_opponent else
                "Предложение возврата отправлено; ждём ответа соперника."
            )
        else:
            self.game_status.setText(f"Lichess · {session.status}")
        self._sync_online_history(board, session.game_id, ply)
        self._update_clock()
        self.history_label.setText("Актуальная позиция" if live else f"Просмотр хода {ply} из {len(board.move_stack)}")
        self.resign_button.setEnabled(active)
        self.resign_button.setVisible(active)
        self.draw_button.setEnabled(active)
        self.draw_button.setVisible(active)
        self.draw_button.setText("Принять ничью" if session.draw_offered_by_opponent else "Предложить ничью")
        self.decline_draw_button.setVisible(session.draw_offered_by_opponent)
        self.decline_draw_button.setEnabled(session.draw_offered_by_opponent)
        self.takeback_button.setVisible(active)
        self.takeback_button.setText(
            "Ожидаем ответа на возврат…" if session.takeback_requested_by_me
            else "Вернуть ход" if session.is_ai_opponent else "Предложить вернуть ход"
        )
        self.takeback_button.setToolTip(
            "Запросить серверный возврат у Lichess AI. После ответа доска изменится только по подтверждённому состоянию."
            if session.is_ai_opponent else
            "Предложить сопернику возврат хода через Lichess. Это не отмена ожидающего предхода."
        )
        self.undo_local_button.setVisible(False)
        self.takeback_button.setEnabled(
            active and bool(session.moves) and not session.input_locked
        )
        show_takeback = (session.takeback_requested_by_opponent
                         and not session.awaiting_authoritative_snapshot)
        self.accept_takeback_button.setVisible(show_takeback)
        self.decline_takeback_button.setVisible(show_takeback)
        response_enabled = (show_takeback and not session.pending_uci
                            and not session.takeback_action_pending
                            and not session.takeback_accept_pending
                            and not session.takeback_decline_pending)
        self.accept_takeback_button.setEnabled(response_enabled)
        self.decline_takeback_button.setEnabled(response_enabled)
        self.claim_draw_button.setEnabled(active and session.board.can_claim_draw())
        self.claim_draw_button.setVisible(active and session.board.can_claim_draw())
        self.leave_game_button.setEnabled(session.status not in {"started", "playing", "created"})
        self.leave_game_button.setVisible(session.status not in {"started", "playing", "created"})
        self.leave_game_button.setText("Главная")
        self.new_game_button.setEnabled(False)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(False)
        self.cancel_premove_button.setEnabled(session.premove is not None)
        self.cancel_premove_button.setVisible(session.premove is not None)
        self.premove_label.setVisible(session.premove is not None)
        if session.premove:
            self.premove_label.setText(f"Предход: {session.premove.uci()} · ожидание подтверждения хода соперника.")
        for widget in (self.opponent_move, self.apply_opponent_button):
            widget.setVisible(False)

    def _sync_online_history(self, board: chess.Board, game_id: str, selected_ply: int) -> None:
        """Append SAN rows for new plies; rebuild only on a new/divergent line."""
        moves = tuple(board.move_stack)
        previous = self._online_history_moves
        incremental = (
            self._online_history_game_id == game_id
            and len(moves) >= len(previous)
            and moves[:len(previous)] == previous
            and self._online_history_position is not None
        )
        unchanged = incremental and moves == previous
        scroll = self.moves.verticalScrollBar().value()
        self.moves.blockSignals(True)
        try:
            if not incremental:
                self.moves.clear()
                position = board.root()
                start_ply = 0
            elif not unchanged:
                position = self._online_history_position.copy(stack=True)
                start_ply = len(previous)
            else:
                position = None
                start_ply = len(moves)
            if position is not None:
                for index in range(start_ply, len(moves)):
                    move = moves[index]
                    ply = index + 1
                    prefix = f"{(ply + 1) // 2}. " if ply % 2 else f"{(ply + 1) // 2}... "
                    item = QListWidgetItem(prefix + position.san(move))
                    item.setData(Qt.ItemDataRole.UserRole, ply)
                    self.moves.addItem(item)
                    position.push(move)
            desired_ply = min(selected_ply, len(moves))
            current = self.moves.currentItem()
            if current is None or current.data(Qt.ItemDataRole.UserRole) != desired_ply:
                selected = next((self.moves.item(i) for i in range(self.moves.count())
                                 if self.moves.item(i).data(Qt.ItemDataRole.UserRole) == desired_ply), None)
                if selected:
                    self.moves.setCurrentItem(selected)
                elif desired_ply == 0:
                    self.moves.setCurrentRow(-1)
            self._online_history_game_id = game_id
            self._online_history_moves = moves
            if not unchanged:
                self._online_history_position = board.copy(stack=True)
        finally:
            self.moves.blockSignals(False)
        self.moves.verticalScrollBar().setValue(scroll)

    def _update_clock(self) -> None:
        session = self.online_session
        if session and self._screen_mode == "online":
            write_error = self.game_repository.write_error(session.game_id)
            if write_error:
                self.game_status.setText(f"Ошибка сохранения локальной партии: {write_error}")
        if not session or session.white_ms is None or session.black_ms is None:
            self.clock_label.setText(
                f"Контроль {session.clock_control}" if session and session.clock_control != "-" else "Без часов"
            )
            return
        elapsed = (max(0, int((time.monotonic() - session._last_clock_at) * 1000))
                   if session.status in {"started", "playing"} else 0)
        white = max(0, session.white_ms - (elapsed if session.board.turn == chess.WHITE else 0))
        black = max(0, session.black_ms - (elapsed if session.board.turn == chess.BLACK else 0))
        self.clock_label.setText(f"Белые {white // 60000:02}:{white // 1000 % 60:02}   Чёрные {black // 60000:02}:{black // 1000 % 60:02}")

    def resign_online(self) -> None:
        session = self.online_session
        if not session:
            return
        action = "Прервать партию" if not session.board.move_stack else "Сдаться"
        if QMessageBox.question(self, "Завершить партию", f"{action} в текущей партии?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        if session.board.move_stack:
            self.lichess.resign(session.game_id)
        else:
            self.lichess.abort(session.game_id)

    def handle_draw(self) -> None:
        session = self.online_session
        if not session:
            return
        if session.draw_offered_by_opponent:
            if QMessageBox.question(self, "Ничья", "Принять предложение ничьей?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
                self.lichess.draw(session.game_id, True)
        else:
            self.lichess.draw(session.game_id, True)

    def show_saved_games(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Библиотека партий")
        dialog.resize(680, 520)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Поиск по сопернику, игроку, ID или статусу")
        layout.addWidget(search)
        games_list = QListWidget()
        layout.addWidget(games_list, 1)
        page_row = QHBoxLayout()
        previous = QPushButton("← Назад")
        page_label = QLabel()
        following = QPushButton("Вперёд →")
        page_row.addWidget(previous)
        page_row.addWidget(page_label, 1)
        page_row.addWidget(following)
        layout.addLayout(page_row)
        note_row = QHBoxLayout()
        note = QLineEdit()
        note.setPlaceholderText("Заметка к начальной позиции выбранной партии")
        save_note = QPushButton("Сохранить заметку")
        note_row.addWidget(note, 1)
        note_row.addWidget(save_note)
        layout.addLayout(note_row)
        actions = QHBoxLayout()
        open_game = QPushButton("Открыть для просмотра")
        export_game = QPushButton("Экспорт PGN")
        analyze_game = QPushButton("Анализ Stockfish")
        refresh_game = QPushButton("Обновить эту партию из Lichess")
        refresh_game.setEnabled(bool(self._lichess_connected and not self.offline_mode))
        close = QPushButton("Закрыть")
        actions.addWidget(open_game)
        actions.addWidget(export_game)
        actions.addWidget(analyze_game)
        actions.addWidget(refresh_game)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        state = {"page": 0, "items": []}

        def selected_game():
            index = games_list.currentRow()
            return state["items"][index] if 0 <= index < len(state["items"]) else None

        def load_page():
            account_id = (f"lichess:{self.lichess.username.strip().lower()}"
                          if self.lichess and self.lichess.username else None)
            result = self.game_repository.search_games(
                search.text(), page=state["page"], page_size=50, completed_only=True,
                account_id=account_id)
            state["items"] = result["items"]
            games_list.clear()
            for game in result["items"]:
                view_state = (f" · просмотр/анализ недоступны ({game.get('variant') or 'variant'})"
                              if game.get("import_error") or game.get("variant", "standard") != "standard"
                              else "")
                opponent_label = game.get("account_opponent") or game["opponent"]
                games_list.addItem(f"{game['white_player']} – {game['black_player']}  ·  "
                                   f"для вас: {opponent_label}  ·  "
                                   f"{game['status']}  ·  {game['updated_at'][:10]}{view_state}")
            page_label.setText(f"Страница {state['page'] + 1} из {max(1, result['pages'])}"
                               f"  ·  {result['total']} партий")
            previous.setEnabled(state["page"] > 0)
            following.setEnabled(state["page"] + 1 < result["pages"])
        def open_selected():
            game = selected_game()
            if game:
                if self._open_saved_game(game):
                    dialog.accept()
                else:
                    QMessageBox.information(dialog, "Партия сохранена как PGN",
                        self._raw_only_game_message(game))
        def export_selected():
            game = selected_game()
            if not game:
                return
            pgn = self.game_repository.export_pgn(game["game_id"])
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Экспорт PGN", f"{game['game_id']}.pgn", "PGN (*.pgn)")
            if path and pgn is not None:
                Path(path).write_text(pgn.rstrip() + "\n", encoding="utf-8")
        def save_selected_note():
            game = selected_game()
            if not game:
                return
            try:
                current = self.game_repository.current_line(game["game_id"])
                self.game_repository.save_note(
                    game["game_id"], note.text(),
                    line_revision=current["line_revision"] if current else None,
                    ply=0 if current else None)
                note.clear()
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(dialog, "Заметка", str(exc))
        def analyze_selected():
            game = selected_game()
            if not game:
                return
            if not self._is_standard_board_game(game):
                QMessageBox.information(dialog, "Stockfish",
                    "Для этой записи доска не поддерживается. Оригинальный PGN можно экспортировать.")
                return
            if game["status"] in {"created", "started", "started_soon", "playing"}:
                QMessageBox.information(dialog, "Stockfish",
                                        "Движок доступен только после завершения партии.")
                return
            engine_path = self.settings.value("stockfish_path", "", type=str).strip()
            if not engine_path:
                QMessageBox.information(
                    dialog, "Stockfish не настроен",
                    "Сначала укажите локальный executable и проверьте его в Настройках. "
                    "Приложение не скачивает движок автоматически.")
                return
            dialog.accept()
            self._start_game_analysis(game["game_id"], engine_path)
        def refresh_selected():
            game = selected_game()
            if not game or not self.lichess or not self._lichess_connected:
                return
            if game.get("source") != "lichess_archive":
                QMessageBox.information(dialog, "Обновить партию",
                    "Обновление доступно для партий, импортированных из текущего аккаунта Lichess.")
                return
            self.lichess.refresh_archive_game(game["game_id"])
        search.textChanged.connect(lambda _text: (state.update(page=0), load_page()))
        previous.clicked.connect(lambda: (state.update(page=max(0, state["page"] - 1)), load_page()))
        following.clicked.connect(lambda: (state.update(page=state["page"] + 1), load_page()))
        games_list.itemDoubleClicked.connect(lambda _item: open_selected())
        open_game.clicked.connect(open_selected)
        export_game.clicked.connect(export_selected)
        save_note.clicked.connect(save_selected_note)
        analyze_game.clicked.connect(analyze_selected)
        refresh_game.clicked.connect(refresh_selected)
        close.clicked.connect(dialog.reject)
        load_page()
        dialog.exec()

    def _start_game_analysis(self, game_id: str, engine_path: str) -> None:
        active_thread = self._analysis_thread
        if self._shutdown_requested:
            return
        if active_thread is not None and active_thread.is_alive():
            self.home_status.setText("Анализ уже выполняется. Дождитесь его завершения или отмените его.")
            return
        if self._analysis_safety_request_id is not None:
            self.home_status.setText("Проверяется активность Lichess для анализа.")
            return
        options = self._analysis_options_from_settings()
        if not (self.lichess and self._lichess_connected):
            answer = QMessageBox.question(
                self, "Проверка перед анализом",
                "Подключённый аккаунт Lichess не может быть проверен. Подтвердите, что сейчас "
                "не участвуете в онлайн-партии с человеком, прежде чем запускать Stockfish.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._launch_game_analysis(game_id, engine_path, options)
            return

        request_id = uuid.uuid4().hex
        account_id = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        if not account_id:
            self.home_status.setText("Не удалось определить подключённый аккаунт; анализ не запущен.")
            return
        self._analysis_safety_request_id = request_id
        self._analysis_safety_phase = "preflight"
        dialog = QProgressDialog("Проверяем активные партии Lichess…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("Послепартийный анализ")
        dialog.setMinimumDuration(0)
        dialog.show()
        self._analysis_dialog = dialog
        stop = threading.Event()
        self._analysis_stop = stop
        self._analysis_safety_context = (
            request_id, game_id, engine_path, options,
            self._analysis_session_generation, account_id, self.lichess,
        )
        dialog.canceled.connect(lambda request=request_id: self._cancel_analysis_action(request))
        self.lichess.check_active_games_for_analysis(request_id)

    def _cancel_analysis_action(self, request_id: str) -> None:
        """Cancel either the server preflight or the analysis using one dialog."""
        if request_id != self._analysis_safety_request_id:
            return
        if self._analysis_stop is not None:
            self._analysis_stop.set()
        if self._analysis_safety_phase == "preflight":
            self._analysis_safety_request_id = None
            self._analysis_safety_context = None
            self._analysis_safety_phase = None
            self._analysis_stop = None
            if self._analysis_dialog is not None:
                self._analysis_dialog.close()
                self._analysis_dialog = None
            return
        if self._analysis_safety_phase == "running" and self._analysis_dialog is not None:
            self._analysis_dialog.setLabelText("Останавливаем анализ…")
            button = self._analysis_dialog.findChild(QPushButton)
            if button is not None:
                button.setEnabled(False)

    def _analysis_safety_reply_is_current(self, request_id: str) -> bool:
        if (request_id != self._analysis_safety_request_id
                or self._analysis_safety_phase != "preflight"):
            return False
        context = self._analysis_safety_context
        if not context or len(context) < 7:
            return False
        _request, _game_id, _engine_path, _options, generation, account_id, service = context
        current_account = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        if (not self._lichess_connected or self.lichess is not service
                or generation != self._analysis_session_generation
                or not account_id or current_account != account_id):
            self._cancel_analysis_action(request_id)
            return False
        return True

    @staticmethod
    def _active_game_blocks_engine(game: dict) -> bool:
        return classify_lichess_opponent(game) != "official_ai"

    def _analysis_safety_games_received(self, request_id: str, games: list[dict]) -> None:
        if not self._analysis_safety_reply_is_current(request_id):
            return
        if not isinstance(games, list) or len(games) >= 50:
            self._analysis_safety_error(
                request_id, "Снимок активных партий некорректный или может быть усечён."
            )
            return
        context = self._analysis_safety_context
        stop = self._analysis_stop
        if not context or stop is None:
            return
        if stop.is_set():
            self._cancel_analysis_action(request_id)
            return
        active_human_ids = {session.game_id for session in self.online_sessions.values()
                            if session.status in {"started", "playing"} and not session.is_ai_opponent}
        if any(self._active_game_blocks_engine(game) for game in games) or active_human_ids:
            stop.set()
            if self._analysis_dialog:
                self._analysis_dialog.close()
                self._analysis_dialog = None
            self._analysis_stop = None
            self._analysis_safety_request_id = None
            self._analysis_safety_context = None
            self._analysis_safety_phase = None
            QMessageBox.warning(self, "Stockfish заблокирован",
                "У аккаунта есть активная партия с человеком. Анализ Stockfish не запущен.")
            return
        _request, game_id, engine_path, options, _generation, _account_id, _service = context
        self._analysis_safety_context = None
        self._analysis_safety_phase = "running"
        if self._analysis_dialog is not None:
            self._analysis_dialog.setLabelText("Анализ партии Stockfish…")
        self._launch_game_analysis(game_id, engine_path, options,
                                   stop=stop, dialog=self._analysis_dialog,
                                   request_id=request_id)

    def _analysis_safety_error(self, request_id: str, message: str) -> None:
        if not self._analysis_safety_reply_is_current(request_id):
            return
        self._analysis_safety_request_id = None
        self._analysis_safety_context = None
        self._analysis_safety_phase = None
        if self._analysis_stop is not None:
            self._analysis_stop.set()
            self._analysis_stop = None
        if self._analysis_dialog:
            self._analysis_dialog.close()
            self._analysis_dialog = None
        if not self._shutdown_requested:
            QMessageBox.warning(self, "Проверка Lichess не выполнена",
                "Не удалось подтвердить отсутствие активной партии. Stockfish не запущен.\n\n" + message)

    def _launch_game_analysis(self, game_id: str, engine_path: str, options: AnalysisOptions,
                              *, stop: threading.Event | None = None,
                              dialog: QProgressDialog | None = None,
                              request_id: str | None = None) -> None:
        stop = stop or threading.Event()
        if dialog is None:
            dialog = QProgressDialog("Запуск Stockfish…", "Отменить", 0, 0, self)
            dialog.setWindowTitle("Послепартийный анализ")
            dialog.setMinimumDuration(0)
            dialog.show()
            self._analysis_dialog = dialog
            request_id = request_id or uuid.uuid4().hex
            self._analysis_safety_request_id = request_id
            self._analysis_safety_phase = "running"
            dialog.canceled.connect(lambda request=request_id: self._cancel_analysis_action(request))
        self._analysis_stop = stop
        active_human_ids = {session.game_id for session in self.online_sessions.values()
                            if session.status in {"started", "playing"} and not session.is_ai_opponent}
        def work():
            try:
                result = CompletedGameAnalyzer(self.game_repository).analyze(
                    game_id, engine_path, options,
                    active_human_game_ids=active_human_ids,
                    progress=lambda done, total: self.analysis_progress.emit(game_id, done, total),
                    stop_event=stop)
                self.analysis_finished.emit(game_id, result)
            except Exception as exc:
                self.analysis_finished.emit(game_id, {"error": str(exc)})
        thread = threading.Thread(target=work, name="chess-coach-analysis", daemon=True)
        self._analysis_thread = thread
        thread.start()

    def _analysis_options_from_settings(self) -> AnalysisOptions:
        kind = self.settings.value("analysis_limit_kind", "nodes", type=str)
        value = self.settings.value("analysis_limit_value", 100_000, type=int)
        if kind not in {"nodes", "depth", "time"}:
            kind = "nodes"
        if kind == "nodes":
            value = max(1_000, min(value, 100_000_000))
        elif kind == "time":
            value *= 1000
        return AnalysisOptions(
            limit_kind=kind, limit_value=value,
            threads=self.settings.value("stockfish_threads", 1, type=int),
            hash_mb=self.settings.value("stockfish_hash_mb", 128, type=int),
        ).normalized()

    def _track_engine_probe(self, process: QProcess) -> None:
        self._engine_probe_processes.add(process)

    def _untrack_engine_probe(self, process: QProcess) -> None:
        self._engine_probe_processes.discard(process)

    def _analysis_progress(self, game_id: str, done: int, total: int) -> None:
        if self._shutdown_requested:
            return
        if getattr(self, "_analysis_dialog", None):
            self._analysis_dialog.setLabelText(f"Анализ позиции {done} из {total}")
            self._analysis_dialog.setRange(0, total)
            self._analysis_dialog.setValue(done)

    def _analysis_finished(self, game_id: str, result: object) -> None:
        self._analysis_stop = None
        self._analysis_safety_request_id = None
        self._analysis_safety_context = None
        self._analysis_safety_phase = None
        if getattr(self, "_analysis_dialog", None):
            self._analysis_dialog.close()
            self._analysis_dialog = None
        if self._shutdown_requested:
            return
        if isinstance(result, dict) and result.get("error"):
            QMessageBox.warning(self, "Stockfish", result["error"])
        else:
            count = len(result.get("assessments", [])) if isinstance(result, dict) else 0
            if count:
                dialog = QDialog(self)
                dialog.setWindowTitle("Оценка партии · белые сверху положительны")
                layout = QVBoxLayout(dialog)
                layout.addWidget(QLabel(f"Оценено позиций: {count}. Источник: "
                                        f"{result.get('source', 'Stockfish')}. Клик по графику открывает позицию."))
                graph = EvaluationGraph(result["assessments"], dialog)
                graph.selected_ply.connect(lambda ply: self._open_analysis_position(game_id, ply))
                layout.addWidget(graph)
                close = QPushButton("Закрыть")
                close.clicked.connect(dialog.accept)
                layout.addWidget(close)
                dialog.resize(720, 320)
                dialog.exec()
            else:
                QMessageBox.information(self, "Stockfish", "Оценок для этой партии нет.")

    def _open_analysis_position(self, game_id: str, ply: int) -> None:
        game = self.game_repository.get_game(game_id)
        if not game:
            return
        if not self._open_saved_game(game):
            self.home_status.setText(self._raw_only_game_message(game))
            return
        self.navigate_to(ply)

    @staticmethod
    def _is_standard_board_game(game: dict) -> bool:
        return (game.get("variant", "standard") == "standard"
                and not game.get("import_error")
                and bool(game.get("initial_fen")))

    @staticmethod
    def _raw_only_game_message(game: dict) -> str:
        variant = (game.get("variant") or "неизвестный").capitalize()
        reason = game.get("import_error") or "для этого варианта нет стандартной шахматной доски"
        return (f"Запись сохранена как исходный PGN. Вариант: {variant}. "
                f"Просмотр доски и анализ недоступны: {reason}. Экспорт PGN доступен.")

    def _open_saved_game(self, game: dict) -> bool:
        if not self._is_standard_board_game(game):
            self.home_status.setText(self._raw_only_game_message(game))
            return False
        self.board.clear_annotations()
        session = LichessGameSession(game["game_id"], self.game_repository)
        session.initial_fen = game["initial_fen"]
        session.moves = game["moves"]
        session.color = chess.WHITE if game["color"] == "white" else chess.BLACK
        session.opponent = game["opponent"]
        session.status = game["status"]
        session.winner = game["winner"]
        session.clock_control = game["clock"]
        session._rebuild_board()
        self.online_session = session
        self._review_session = session
        self.online_view_ply = None
        self._screen_mode = "review"
        self.screen_title.setText("Просмотр сохранённой партии")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Открыта локально сохранённая партия (только просмотр).")
        return True

    def leave_online_game(self) -> None:
        if self._screen_mode == "review":
            self.online_session = None
            self._review_session = None
            self.online_view_ply = None
            self.show_home()
            return
        if not self.online_session:
            return
        session = self.online_session
        if session.status in {"started", "playing"}:
            self.show_home()
            return
        self.show_home()

    def closeEvent(self, event) -> None:
        self._shutdown_requested = True
        if self._analysis_safety_request_id is not None:
            self._cancel_analysis_action(self._analysis_safety_request_id)
        analysis_thread = self._analysis_thread
        analysis_running = bool(analysis_thread and analysis_thread.is_alive())
        batch_thread = self._batch_thread
        batch_running = bool(batch_thread and batch_thread.is_alive())
        if analysis_running or batch_running:
            if self._analysis_shutdown_deadline is None:
                self._analysis_shutdown_deadline = time.monotonic() + 10.0
            if analysis_running and self._analysis_stop is not None:
                self._analysis_stop.set()
            if analysis_running and self._analysis_dialog is not None:
                self._analysis_dialog.setLabelText("Останавливаем Stockfish перед закрытием приложения…")
            if batch_running and self._batch_stop is not None:
                self._batch_stop.set()
            if batch_running and self._batch_dialog is not None:
                self._batch_dialog.progress_label.setText(
                    "Останавливаем массовый анализ перед закрытием приложения…")
        else:
            self._analysis_shutdown_deadline = None

        workers_stopped = True
        if self.lichess:
            close_method = self.lichess.close
            try:
                close_result = close_method(timeout=0.15)
            except TypeError:
                close_result = close_method()
            workers_stopped = close_result is not False
        analysis_stopped = not (analysis_thread and analysis_thread.is_alive())
        batch_stopped = not (batch_thread and batch_thread.is_alive())
        statistics_stopped = not any(thread.is_alive() for thread in self._statistics_workers)
        # Do not destroy the QProcess parent until every child's finished handler
        # has observed and released that child, even if state() already says stopped.
        engine_probes_stopped = not self._engine_probe_processes
        if (not workers_stopped or not analysis_stopped or not batch_stopped or not statistics_stopped
                or not engine_probes_stopped or self.game_repository.has_pending_writes()):
            event.ignore()
            self.home_status.setText(
                "Завершаем фоновые операции и дочерние процессы, сохраняем подтверждённые данные. "
                "Окно закроется после безопасной остановки.")
            if (analysis_running and self._analysis_shutdown_deadline is not None
                    and time.monotonic() >= self._analysis_shutdown_deadline):
                self._analysis_shutdown_deadline = None
                self._close_retry_scheduled = False
                self._close_retry_timer.stop()
                self.setEnabled(True)
                self._shutdown_read_only = True
                self.setWindowTitle("Chess Coach — ожидание завершения Stockfish")
                self.home_status.setText(
                    "Stockfish не остановился за 10 секунд. Приложение ожидает завершения анализа; "
                    "действия отключены. После завершения нажмите X, чтобы закрыть окно.")
                # The service has already entered its close path. Keep the shell
                # available for the window's close control, but make the app's
                # controls clearly unavailable until the user retries closing.
                if self.centralWidget() is not None:
                    self.centralWidget().setEnabled(False)
                return
            if (batch_running and self._analysis_shutdown_deadline is not None
                    and time.monotonic() >= self._analysis_shutdown_deadline):
                self._analysis_shutdown_deadline = None
                self._close_retry_scheduled = False
                self._close_retry_timer.stop()
                self.setEnabled(True)
                self._shutdown_read_only = True
                self.setWindowTitle("Chess Coach — ожидание остановки массового анализа")
                self.home_status.setText(
                    "Массовый Stockfish не остановился за 10 секунд. Приложение ожидает "
                    "завершения процесса; после остановки снова нажмите X.")
                if self.centralWidget() is not None:
                    self.centralWidget().setEnabled(False)
                return
            if not self._close_retry_scheduled:
                self._close_retry_scheduled = True
                self.setEnabled(False)
                self._close_retry_timer.start()
            return
        self.game_repository.shutdown()
        if self._offline_data_dir is not None:
            self._offline_data_dir.cleanup()
            self._offline_data_dir = None
        event.accept()
        super().closeEvent(event)

    def _retry_close_after_workers(self) -> None:
        self._close_retry_scheduled = False
        self.close()


def _result_label(color: chess.Color, winner: str | None, status: str) -> str:
    if winner not in {"white", "black"}:
        if status in {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}:
            return "Ничья"
        return "Партия прервана"
    won = (winner == "white") == color
    return "Победа" if won else "Поражение"
