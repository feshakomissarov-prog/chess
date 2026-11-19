"""Lichess-mechanics puzzle practice in the Chess Coach desktop shell."""

from __future__ import annotations

import json
import time

import chess
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget,
)

from ...board_view import BoardView
from ...lichess.puzzle_training import (
    PuzzleOutcome, PuzzlePlayablePosition, PuzzleStatus, PuzzleTrainingState,
)


THEMES = (
    ("Общее", ("mix", "Смешанная")),
    ("Фазы", ("opening", "Дебют"), ("middlegame", "Миттельшпиль"),
     ("endgame", "Эндшпиль")),
    ("Тактика", ("pin", "Связка"), ("fork", "Вилка"),
     ("sacrifice", "Жертва"), ("skewer", "Сквозной удар"),
     ("discoveredAttack", "Открытое нападение"),
     ("defensiveMove", "Защитный ход"), ("hangingPiece", "Незащищённая фигура")),
    ("Мат", ("mate", "Мат"), ("mateIn1", "Мат в 1"),
     ("mateIn2", "Мат в 2"), ("mateIn3", "Мат в 3")),
    ("Эндшпили", ("rookEndgame", "Ладейный"), ("bishopEndgame", "Слоновый"),
     ("pawnEndgame", "Пешечный"), ("knightEndgame", "Коневой"),
     ("queenEndgame", "Ферзевый")),
    ("Длина", ("oneMove", "В один ход"), ("short", "Короткие"), ("long", "Длинные")),
    ("Превращение", ("promotion", "Превращение"), ("underPromotion", "Недопревращение")),
)
THEME_LABELS = {theme_id: label for _category, *items in THEMES
                for theme_id, label in items}


class PuzzleTrainingPage(QWidget):
    """Interactive board and controls. Persistence and network stay in the window."""

    new_puzzle_requested = Signal(str, str)
    authorize_requested = Signal(str, str)
    local_puzzle_requested = Signal(str, str)
    attempt_saved = Signal(object)
    history_requested = Signal()
    home_requested = Signal()
    account_changed = Signal(str)
    session_hidden = Signal()

    def __init__(self, *, parent=None, demo: bool = False) -> None:
        super().__init__(parent)
        self.state = PuzzleTrainingState()
        self.account_id = "local"
        self._started_at = 0.0
        self._puzzle_payload: dict = {}
        self._active_source = "local"
        self._allow_submit = False
        self._local_count = 0
        self._local_stats = {"attempts": 0, "solved": 0, "success_percent": None}
        self._theme_stats = {"attempts": 0, "solved": 0, "success_percent": None}
        self._official_rating: int | None = None
        self._rating_date: str | None = None
        self.setObjectName("puzzleTrainingPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Решение задач")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._training_root_layout = root
        self._training_header_layout = header
        self._training_header_reflowed = False
        self._training_header_controls = QWidget()
        header_controls = QHBoxLayout(self._training_header_controls)
        header_controls.setContentsMargins(0, 0, 0, 0)
        self.source = QComboBox()
        self.source.addItem("Новые Lichess", "new")
        self.source.addItem("Мои ошибки", "errors")
        self.source.addItem("Сохранённые", "saved")
        self.source.setAccessibleName("Источник задач")
        header_controls.addWidget(self.source)
        self.account_selector = QComboBox()
        self.account_selector.setAccessibleName("Профиль истории задач")
        self.account_selector.addItem("Локальный профиль", "local")
        self.account_selector.currentIndexChanged.connect(
            lambda index: self.account_changed.emit(str(self.account_selector.itemData(index) or "local")))
        header_controls.addWidget(self.account_selector)
        self.difficulty = QComboBox()
        self.difficulty.addItem("Легче", "easier")
        self.difficulty.addItem("Обычная", "normal")
        self.difficulty.addItem("Сложнее", "harder")
        self.difficulty.setCurrentIndex(1)
        self.difficulty.setAccessibleName("Сложность задач")
        header_controls.addWidget(self.difficulty)
        self.advanced_difficulty = QComboBox()
        self.advanced_difficulty.addItem("Самая лёгкая", "easiest")
        self.advanced_difficulty.addItem("Самая сложная", "hardest")
        self.advanced_difficulty.setVisible(False)
        header_controls.addWidget(self.advanced_difficulty)
        self.more_difficulty = QPushButton("Дополнительно")
        self.more_difficulty.setCheckable(True)
        self.more_difficulty.toggled.connect(self.advanced_difficulty.setVisible)
        header_controls.addWidget(self.more_difficulty)
        header.addWidget(self._training_header_controls)
        root.addLayout(header)

        self.filter_row = QHBoxLayout()
        self.theme_search = QLineEdit()
        self.theme_search.setPlaceholderText("Найти тему…")
        self.theme_search.setMaximumWidth(190)
        self.theme = QComboBox()
        self.theme.setEditable(False)
        self.theme.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.theme.addItem("Смешанная тренировка", "mix")
        for category, *items in THEMES[1:]:
            self.theme.insertSeparator(self.theme.count())
            self.theme.addItem(f"— {category} —", None)
            self.theme.model().item(self.theme.count() - 1).setEnabled(False)
            for theme_id, label in items:
                self.theme.addItem(label, theme_id)
        self.theme.setCurrentIndex(0)
        self.theme.setAccessibleName("Тема задачи")
        self.filter_row.addWidget(QLabel("Тема"))
        self.filter_row.addWidget(self.theme_search)
        self.filter_row.addWidget(self.theme, 1)
        self.fetch = QPushButton("Новая задача")
        self.fetch.setObjectName("primaryAction")
        self.fetch.setMinimumHeight(38)
        self.filter_row.addWidget(self.fetch)
        self.authorize = QPushButton("Разрешить puzzle:read")
        self.authorize.setVisible(False)
        self.authorize.setToolTip("Отдельное разрешение Lichess только для чтения задач")
        self.filter_row.addWidget(self.authorize)
        root.addLayout(self.filter_row)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.theme_list = QListWidget()
        self.theme_list.setObjectName("puzzleThemeList")
        self.theme_list.setMaximumWidth(180)
        for category, *items in THEMES:
            head = QListWidgetItem(category)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            self.theme_list.addItem(head)
            for theme_id, label in items:
                item = QListWidgetItem(f"  {label}")
                item.setData(Qt.ItemDataRole.UserRole, theme_id)
                self.theme_list.addItem(item)
        body.addWidget(self.theme_list)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(4, 0, 4, 0)
        self.turn_label = QLabel("Задача не загружена · ход стороны решающего")
        self.turn_label.setObjectName("secondary")
        self.turn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.turn_label)
        self.board = BoardView(chess.Board(), center)
        self.board.setObjectName("puzzleBoard")
        self.board.setMinimumSize(160, 160)
        self.board.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board.set_annotations_enabled(True)
        self.board.set_premove_allowed(False)
        self.board.set_show_legal_moves(True)
        self.board.setAccessibleName("Шахматная доска задачи")
        self.board.move_intent.connect(self._submit_move)
        center_layout.addWidget(self.board, 1)
        self.status = QLabel("Выберите тему и нажмите «Новая задача».")
        self.status.setObjectName("puzzleStatus")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.status)
        self.solution_line = QLabel("")
        self.solution_line.setObjectName("puzzleSolutionLine")
        self.solution_line.setWordWrap(True)
        self.solution_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.solution_line.setAccessibleName("Правильная линия решения")
        self.solution_line.hide()
        center_layout.addWidget(self.solution_line)
        controls = QHBoxLayout()
        self.hint = QPushButton("Подсказка")
        self.reveal = QPushButton("Показать решение")
        self.repeat = QPushButton("Повторить")
        self.next = QPushButton("Следующая задача")
        self.next.setObjectName("primaryAction")
        for button in (self.hint, self.reveal, self.repeat, self.next):
            button.setMinimumHeight(38)
            controls.addWidget(button)
        center_layout.addLayout(controls)
        self.hint.clicked.connect(self._hint)
        self.reveal.clicked.connect(self._reveal)
        self.repeat.clicked.connect(self._repeat)
        self.next.clicked.connect(self._next)
        body.addWidget(center)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(190)
        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        heading = QLabel("Текущая задача")
        heading.setStyleSheet("font-weight: 700; font-size: 17px")
        right_layout.addWidget(heading)
        self.details = QLabel("Источник: —\nТема: —\nРейтинг задачи: —")
        self.details.setWordWrap(True)
        self.details.setObjectName("puzzleDetails")
        right_layout.addWidget(self.details)
        self.metrics = QLabel("Рейтинг Lichess: нет данных\nЛокальные занятия: 0")
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("secondary")
        right_layout.addWidget(self.metrics)
        history_heading = QLabel("Предыдущие задачи")
        history_heading.setStyleSheet("font-weight: 700; font-size: 16px")
        right_layout.addWidget(history_heading)
        self.history = QListWidget()
        self.history.setObjectName("puzzleRecentHistory")
        self.history.setMaximumHeight(260)
        self.history.itemClicked.connect(self._open_history_item)
        right_layout.addWidget(self.history, 1)
        self.history_button = QPushButton("Вся история")
        self.history_button.clicked.connect(self.history_requested.emit)
        right_layout.addWidget(self.history_button)
        self.home_button = QPushButton("Главная")
        self.home_button.clicked.connect(self.home_requested.emit)
        right_layout.addWidget(self.home_button)
        right_scroll.setWidget(right)
        body.addWidget(right_scroll)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        root.addWidget(body, 1)

        self.source.currentIndexChanged.connect(self._source_changed)
        self.fetch.clicked.connect(self._next)
        self.authorize.clicked.connect(lambda: self.authorize_requested.emit(
            self.selected_theme(), str(self.difficulty.currentData() or "normal")))
        self.theme.activated.connect(self._theme_selected)
        self.theme_search.textChanged.connect(self._filter_themes)
        self.theme_list.itemClicked.connect(self._theme_list_clicked)
        self._set_controls(False)
        if demo:
            payload = {"game": {"id": "demo", "pgn": "1. e4 e5 2. Nf3"},
                       "puzzle": {"id": "Demo", "initialPly": 2,
                                  "solution": ["d7d5"], "themes": ["fork"],
                                  "rating": 1450, "plays": 42, "lastMove": "g1f3"}}
            from ...lichess.puzzle_training import normalize_api_puzzle
            self.load_puzzle(normalize_api_puzzle(payload), payload, source="demo")

    def selected_theme(self) -> str:
        value = self.theme.currentData()
        if value:
            return str(value)
        text = self.theme.currentText().strip()
        return next((theme_id for theme_id, label in THEME_LABELS.items()
                     if label.casefold() == text.casefold()), "mix")

    def set_accounts(self, rows: list[dict], selected_account: str) -> None:
        self.account_selector.blockSignals(True)
        self.account_selector.clear()
        self.account_selector.addItem("Локальный профиль", "local")
        for row in rows:
            account_id = str(row.get("account_id") or "")
            if account_id:
                self.account_selector.addItem(
                    f"{row.get('username') or account_id} · {row.get('attempts', 0)}", account_id)
        index = self.account_selector.findData(selected_account)
        if index < 0 and selected_account != "local":
            self.account_selector.addItem(selected_account, selected_account)
            index = self.account_selector.count() - 1
        self.account_selector.setCurrentIndex(max(0, index))
        self.account_selector.blockSignals(False)
        self.account_id = str(self.account_selector.currentData() or "local")

    def _theme_selected(self, _index: int) -> None:
        self._save_theme_selection()

    def _theme_list_clicked(self, item: QListWidgetItem) -> None:
        theme_id = item.data(Qt.ItemDataRole.UserRole)
        if theme_id:
            index = self.theme.findData(theme_id)
            if index >= 0:
                self.theme.setCurrentIndex(index)
                self._save_theme_selection()

    def _save_theme_selection(self) -> None:
        pass  # MainWindow persists the selected ID when a request starts.

    def _filter_themes(self, text: str) -> None:
        query = str(text).strip().casefold()
        for index in range(self.theme.count()):
            label = self.theme.itemText(index)
            theme_id = self.theme.itemData(index)
            if not theme_id:
                continue
            visible = not query or query in label.casefold() or query in str(theme_id).casefold()
            model = self.theme.model()
            model.item(index).setHidden(not visible)

    def _source_changed(self, _index: int) -> None:
        new_source = self.source.currentData() == "new"
        self.difficulty.setEnabled(new_source)
        self.more_difficulty.setEnabled(new_source)
        self._set_controls(self.state.puzzle is not None)

    def _next(self) -> None:
        if (self.state.puzzle is not None and self._allow_submit
                and self.state.status not in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED,
                                              PuzzleStatus.SKIPPED, PuzzleStatus.UNAVAILABLE}):
            answer = QMessageBox.question(
                self, "Пропустить задачу", "Текущая попытка будет сохранена как пропуск. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.state.skip()
            self._finish(PuzzleOutcome.SKIPPED)
        mode = str(self.source.currentData() or "new")
        if mode == "new":
            difficulty = str(self.difficulty.currentData() or "normal")
            if self.more_difficulty.isChecked():
                difficulty = str(self.advanced_difficulty.currentData() or difficulty)
            self.new_puzzle_requested.emit(self.selected_theme(), difficulty)
        else:
            self.local_puzzle_requested.emit(mode, self.selected_theme())

    def load_puzzle(self, puzzle: PuzzlePlayablePosition, payload: dict | None = None,
                    *, source: str = "local") -> None:
        self.state.load(puzzle)
        self._puzzle_payload = dict(payload or {})
        self._active_source = source
        self._started_at = time.monotonic()
        self._allow_submit = True
        self.board.set_position(self.state.board)
        self.board.set_flipped(not self.state.board.turn)
        self.board.clear_annotations(record_action=False)
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.turn_label.setText("Ход белых" if self.state.board.turn else "Ход чёрных")
        self.solution_line.clear()
        self.solution_line.hide()
        theme_text = ", ".join(THEME_LABELS.get(theme, theme) for theme in puzzle.themes) or "без темы"
        origin = "Новая задача Lichess" if source == "lichess" else "Сохранённая задача"
        self.details.setText(f"ID: {puzzle.puzzle_id}\nИсточник: {origin}\n"
                             f"Тема: {theme_text}\nРейтинг задачи: {puzzle.rating or 'нет данных'}")
        self.status.setText("Найдите лучший ход. ПКМ — свои стрелки и отметки.")
        self._set_controls(True)

    def set_loading(self, text: str = "Загружаю задачу…") -> None:
        self._allow_submit = False
        self.status.setText(text)
        self.fetch.setEnabled(False)

    def set_message(self, text: str) -> None:
        self._allow_submit = False
        self.status.setText(text)
        self.fetch.setEnabled(True)
        self._set_controls(False)

    def set_authorization_needed(self, needed: bool) -> None:
        self.authorize.setVisible(bool(needed))

    def _submit_move(self, move: chess.Move) -> None:
        if not self._allow_submit:
            return
        result = self.state.submit(move)
        if result == "illegal":
            self.status.setText("Этот ход невозможен по правилам шахмат.")
            return
        if result == "mistake":
            self.board.set_position(self.state.board)
            self.status.setText("Не лучший ход. Попробуйте ещё.")
            return
        if result == "unavailable":
            self._allow_submit = False
            self.status.setText("Задача недоступна: решение содержит нелегальный ответ.")
            return
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.board.clear_annotations(record_action=False)
        self.board.set_position(self.state.board)
        self.turn_label.setText("Ход белых" if self.state.board.turn else "Ход чёрных")
        if result == "success":
            self._finish(PuzzleOutcome.SUCCESS)
        else:
            self.status.setText("Ход верный · ответ соперника воспроизведён из решения.")

    def _hint(self) -> None:
        move = self.state.hint()
        if move is None:
            return
        self.board.set_hint_squares((move.from_square,))
        if self.state.hint_level >= 2:
            self.board.set_hint_arrows((move,))
        self.status.setText("Подсказка: начните с отмеченного поля." if self.state.hint_level == 1
                            else "Подсказка: нужный ход показан стрелкой.")

    def _reveal(self) -> None:
        if not self.state.puzzle:
            return
        answer = QMessageBox.question(self, "Показать решение",
                                      "Открыть всю правильную линию? Попытка будет отмечена как «решение показано».",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state.reveal()
        self._show_solution_line()
        self._finish(PuzzleOutcome.REVEALED)

    def _repeat(self) -> None:
        if self.state.puzzle:
            self.load_puzzle(self.state.puzzle, self._puzzle_payload, source=self._active_source)

    def _show_solution_line(self) -> None:
        if not self.state.puzzle:
            return
        board = self.state.puzzle.board()
        san: list[str] = []
        for raw_move in self.state.puzzle.expected_user_moves:
            move = chess.Move.from_uci(raw_move)
            san.append(f"{board.fullmove_number}{'.' if board.turn else '...'} {board.san(move)}")
            board.push(move)
        self.solution_line.setText("Правильная линия: " + "  ".join(san))
        self.solution_line.show()

    def _finish(self, outcome: PuzzleOutcome) -> None:
        self._allow_submit = False
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.board.clear_annotations(record_action=False)
        if outcome == PuzzleOutcome.SUCCESS:
            clean = self.state.mistakes == 0 and self.state.hints_used == 0
            self.status.setText("Задача решена чисто." if clean else "Задача решена с ошибкой или подсказкой.")
            self._show_solution_line()
        elif outcome == PuzzleOutcome.REVEALED:
            self.status.setText("Решение показано · результат не засчитан как решённый.")
        else:
            self.status.setText("Задача пропущена · результат сохранён локально.")
        payload = json.dumps(self._puzzle_payload, ensure_ascii=False, separators=(",", ":"))
        result = ("revealed" if outcome == PuzzleOutcome.REVEALED else
                  "skipped" if outcome == PuzzleOutcome.SKIPPED else
                  "hinted" if self.state.hints_used else
                  "mistake" if self.state.mistakes else "success")
        self.attempt_saved.emit({
            "account_id": self.account_id, "puzzle_id": self.state.puzzle.puzzle_id,
            "result": result, "mistakes": self.state.mistakes,
            "solved": outcome == PuzzleOutcome.SUCCESS,
            "hints": self.state.hints_used, "duration_ms": int((time.monotonic() - self._started_at) * 1000),
            "puzzle_json": payload, "source": self._active_source,
        })
        self._local_count += 1
        self._local_stats["attempts"] += 1
        self._local_stats["solved"] += int(outcome == PuzzleOutcome.SUCCESS)
        attempts = self._local_stats["attempts"]
        self._local_stats["success_percent"] = (
            round(self._local_stats["solved"] / attempts * 100, 1) if attempts else None)
        if self.state.puzzle.themes:
            self._theme_stats["attempts"] += 1
            self._theme_stats["solved"] += int(outcome == PuzzleOutcome.SUCCESS)
            self._theme_stats["success_percent"] = round(
                self._theme_stats["solved"] / self._theme_stats["attempts"] * 100, 1)
        recent = [{"puzzle_id": self.state.puzzle.puzzle_id, "result": result,
                   "created_at_ms": int(time.time() * 1000), "source": "local",
                   "themes": list(self.state.puzzle.themes)}]
        for index in range(self.history.count()):
            recent.append(self.history.item(index).data(Qt.ItemDataRole.UserRole) or {})
        self.set_recent_history(recent[:8], local_count=self._local_count,
                                local_stats=self._local_stats, theme_stats=self._theme_stats)
        self._set_controls(True, finished=True)

    def _set_controls(self, loaded: bool, *, finished: bool = False) -> None:
        self.hint.setEnabled(loaded and not finished)
        self.reveal.setEnabled(loaded and not finished)
        self.repeat.setEnabled(loaded)
        self.next.setEnabled(True)
        self.fetch.setEnabled(True)

    def set_recent_history(self, rows: list[dict], *, local_count: int = 0,
                           official_rating: int | None = None,
                           rating_date: str | None = None,
                           local_stats: dict | None = None,
                           theme_stats: dict | None = None) -> None:
        self._local_count = max(0, int(local_count))
        if local_stats is not None:
            self._local_stats = dict(local_stats)
        if theme_stats is not None:
            self._theme_stats = dict(theme_stats)
        if official_rating is not None:
            self._official_rating = int(official_rating)
        if rating_date:
            self._rating_date = str(rating_date)
        self.history.clear()
        for row in rows[:8]:
            source = "Chess Coach" if row.get("source") == "local" else "Lichess · sync"
            outcome = str(row.get("result") or ("Решено" if row.get("win") else "Ошибка"))
            themes = row.get("themes") or []
            theme = THEME_LABELS.get(themes[0], themes[0]) if themes else "без темы"
            created = row.get("created_at_ms") or row.get("attempt_date_ms")
            date_text = time.strftime("%d.%m", time.localtime(created / 1000)) if created else "—"
            item = QListWidgetItem(f"{date_text} · {theme} · {outcome} · {source}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(f"{row.get('puzzle_id', '—')} · {source} · {outcome}")
            self.history.addItem(item)
        rating_text = (f"{self._official_rating} · {self._rating_date or 'дата не указана'}"
                       if self._official_rating is not None else "нет данных")
        local_rate = self._local_stats.get("success_percent")
        local_solved = int(self._local_stats.get("solved", 0))
        local_attempts = int(self._local_stats.get("attempts", local_count))
        theme_rate = self._theme_stats.get("success_percent")
        theme_id = self.selected_theme()
        theme_label = THEME_LABELS.get(theme_id, "выбранная тема")
        self.metrics.setText(
            f"Рейтинг Lichess (синхронизация): {rating_text}\n"
            f"Локально решено: {local_solved}/{local_attempts}"
            f" ({local_rate if local_rate is not None else 'н/д'}%)\n"
            f"{theme_label}: {self._theme_stats.get('solved', 0)}/"
            f"{self._theme_stats.get('attempts', 0)}"
            f" ({theme_rate if theme_rate is not None else 'н/д'}%)")

    def _open_history_item(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) or {}
        puzzle_id = str(row.get("puzzle_id", ""))
        if puzzle_id:
            self.local_puzzle_requested.emit("replay", puzzle_id)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        compact = self.width() < 1050 or self.height() < 650
        reflow_header = self.width() < 900 or self.height() < 650
        if reflow_header != self._training_header_reflowed:
            if reflow_header:
                self._training_header_layout.removeWidget(self._training_header_controls)
                self._training_root_layout.insertWidget(
                    1, self._training_header_controls, 0, Qt.AlignmentFlag.AlignRight)
            else:
                self._training_root_layout.removeWidget(self._training_header_controls)
                self._training_header_layout.addWidget(self._training_header_controls)
            self._training_header_reflowed = reflow_header
        self.theme_list.setVisible(not compact)
        self.theme.setVisible(compact)
        self.theme_search.setVisible(not compact)
        self.history.setMaximumHeight(120 if self.height() < 650 else 260)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self.session_hidden.emit()
