"""Lichess-mechanics puzzle practice in the Chess Coach desktop shell."""

from __future__ import annotations

import json
import time
import uuid

import chess
from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget,
)

from ...board_view import BoardView
from ...lichess.puzzle_training import (
    PuzzleOutcome, PuzzlePlayablePosition, PuzzleStatus, PuzzleTrainingState,
    practice_rating_delta,
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


def puzzle_result_label(row: dict) -> str:
    """Render historical outcomes in Russian without losing clean/error distinctions."""
    raw = str(row.get("result") or "").strip().casefold()
    source = str(row.get("source") or "").casefold()
    solved = bool(row.get("solved", row.get("win", False)))
    mistakes = max(0, int(row.get("mistakes") or 0))
    if source == "lichess" and "win" in row:
        return "Решено" if bool(row.get("win")) else "Ошибка"
    if raw in {"success", "решено"}:
        return "Решено с ошибками" if solved and mistakes else "Решено"
    if raw in {"mistake", "ошибка"}:
        return "Решено с ошибками" if solved and mistakes else "Ошибка"
    if raw in {"hinted", "с подсказкой"}:
        return "С подсказкой"
    if raw in {"revealed", "решение показано"}:
        return "Решение показано"
    if raw in {"skipped", "пропущено"}:
        return "Пропущено"
    return raw[:1].upper() + raw[1:] if raw else ("Решено" if solved else "Ошибка")


class PuzzleTrainingPage(QWidget):
    """Interactive board and controls. Persistence and network stay in the window."""

    new_puzzle_requested = Signal(str, str)
    authorize_requested = Signal(str, str)
    local_puzzle_requested = Signal(str, str)
    attempt_saved = Signal(object)
    replay_requested = Signal(str, str, str)
    history_requested = Signal()
    home_requested = Signal()
    account_changed = Signal(str)
    session_hidden = Signal()
    prefetch_requested = Signal(str, str, str, str)

    def __init__(self, *, parent=None, demo: bool = False) -> None:
        super().__init__(parent)
        self.state = PuzzleTrainingState()
        self.account_id = "local"
        self._started_at = 0.0
        self._puzzle_payload: dict = {}
        self._active_source = "local"
        self._session_id = ""
        self._session_account_id = "local"
        self._session_puzzle_id = ""
        self._pending_attempt_saves: dict[str, dict] = {}
        self._retry_attempt_queue: list[str] = []
        self._retry_in_progress_id: str | None = None
        self._local_seen_by_scope: dict[tuple[str, str, str], set[str]] = {}
        self._is_replay = False
        self._wrong_flash = False
        self._wrong_timer = QTimer(self)
        self._wrong_timer.setSingleShot(True)
        self._wrong_timer.timeout.connect(self._restore_after_wrong_move)
        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.timeout.connect(self._apply_opponent_reply)
        self._auto_next_timer = QTimer(self)
        self._auto_next_timer.setSingleShot(True)
        self._auto_next_timer.timeout.connect(self._auto_advance)
        self._prepared_online_puzzle: dict | None = None
        self._session_results: list[dict] = []
        self._session_result_index_by_id: dict[str, int] = {}
        self._session_streak = 0
        self._best_streak = 0
        self._session_clean = 0
        self._session_failed = 0
        self._session_hinted = 0
        self._session_outcome_ids: set[str] = set()
        self._finished_session_ids: set[str] = set()
        self._input_feedback_ms: list[float] = []
        self._input_feedback_kinds: list[str] = []
        self._pending_paint_started: float | None = None
        self._pending_paint_kind: str | None = None
        self._live_get_ms: float | None = None
        self._db_commit_ms: float | None = None
        self._prepared_next_ms: float | None = None
        self._next_requested_at: float | None = None
        self._rating_eligible = False
        self._current_cc_rating = 1500
        self._terminal_verdict = ""
        self._rating_preview: tuple[int, int] | None = None
        self._allow_submit = False
        self._local_count = 0
        self._local_stats = {"attempts": 0, "solved": 0, "success_percent": None}
        self._theme_stats = {"attempts": 0, "solved": 0, "success_percent": None}
        self._official_rating: int | None = None
        self._rating_date: str | None = None
        self.setObjectName("puzzleTrainingPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 10)
        root.setSpacing(6)

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
        self.source.setVisible(False)
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
        self.auto_next = QCheckBox("Автопереход после чистого решения")
        self.auto_next.setObjectName("puzzleAutoNext")
        self.auto_next.setAccessibleName("Автопереход после чистого решения")
        self.auto_next.setChecked(True)
        header_controls.addWidget(self.auto_next)
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
        self.authorize = QPushButton("Подключить задачи Lichess")
        self.authorize.setVisible(False)
        self.authorize.setToolTip("Отдельное разрешение Lichess только для чтения задач")
        self.filter_row.addWidget(self.authorize)
        root.addLayout(self.filter_row)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.theme_list = QListWidget()
        self.theme_list.setObjectName("puzzleThemeList")
        self.theme_list.setMinimumWidth(220)
        self.theme_list.setMaximumWidth(252)
        self.theme_list.setWordWrap(True)
        self.theme_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for category, *items in THEMES:
            head = QListWidgetItem(category)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setToolTip(category)
            head.setSizeHint(QSize(0, 30))
            self.theme_list.addItem(head)
            for theme_id, label in items:
                item = QListWidgetItem(f"  {label}")
                item.setData(Qt.ItemDataRole.UserRole, theme_id)
                item.setToolTip(label)
                item.setSizeHint(QSize(0, 36))
                self.theme_list.addItem(item)
        body.addWidget(self.theme_list)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(4, 0, 4, 0)
        self.turn_label = QLabel("Задача не загружена · ход стороны решающего")
        self.turn_label.setObjectName("secondary")
        self.turn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.turn_label)
        self.status = QLabel("Подключите задачи Lichess, чтобы начать.")
        self.status.setObjectName("puzzleVerdict")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumHeight(32)
        self.status.setAccessibleName("Состояние решения задачи")
        center_layout.addWidget(self.status)
        self.board = BoardView(chess.Board(), center)
        self.board.setObjectName("puzzleBoard")
        self.board.setMinimumSize(160, 160)
        self.board.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board.set_annotations_enabled(True)
        self.board.set_premove_allowed(False)
        self.board.set_show_legal_moves(True)
        self.board.setAccessibleName("Шахматная доска задачи")
        self.board.move_intent.connect(self._submit_move)
        self.board.wheel_navigate.connect(self._navigate_review)
        self.board.paint_completed.connect(self._first_feedback_paint_completed)
        center_layout.addWidget(self.board, 1)
        self.solution_line = QLabel("")
        self.solution_line.setObjectName("puzzleSolutionLine")
        self.solution_line.setWordWrap(True)
        self.solution_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.solution_line.setAccessibleName("Правильная линия решения")
        self.solution_line.hide()
        center_layout.addWidget(self.solution_line)
        controls = QGridLayout()
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(2)
        self._controls_layout = controls
        self._controls_compact = False
        self.hint = QPushButton("Подсказка")
        self.reveal = QPushButton("Показать решение")
        self.repeat = QPushButton("Повторить")
        self.next = QPushButton("Следующая задача")
        self.retry_save = QPushButton("Повторить сохранение")
        self.retry_save.setObjectName("retryPuzzleSave")
        self.retry_save.hide()
        self.retry_save.clicked.connect(self._retry_attempt_save)
        self.next.setObjectName("primaryAction")
        for button in (self.hint, self.reveal, self.repeat, self.retry_save, self.next):
            button.setMinimumHeight(38)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        center_layout.addLayout(controls)
        self._sync_control_layout()
        review_row = QHBoxLayout()
        self.review_back = QPushButton("←")
        self.review_back.setAccessibleName("Предыдущий известный ход")
        self.review_position = QLabel("Ходы задачи появятся здесь")
        self.review_position.setObjectName("secondary")
        self.review_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.review_forward = QPushButton("→")
        self.review_forward.setAccessibleName("Следующий уже сыгранный ход")
        self.return_to_solution = QPushButton("Вернуться к решению")
        self.return_to_solution.hide()
        review_row.addWidget(self.review_back)
        review_row.addWidget(self.review_position, 1)
        review_row.addWidget(self.review_forward)
        review_row.addWidget(self.return_to_solution)
        center_layout.addLayout(review_row)
        self.review_back.clicked.connect(lambda: self._navigate_review(-1))
        self.review_forward.clicked.connect(lambda: self._navigate_review(1))
        self.return_to_solution.clicked.connect(self._return_to_solution)
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
        self.practice_rating = QLabel("Рейтинг тренировки CC: 1500 (стартовый)")
        self.practice_rating.setObjectName("puzzlePracticeRating")
        self.practice_rating.setWordWrap(True)
        right_layout.addWidget(self.practice_rating)
        self.session_summary = QLabel("Серия: 0 · Лучшая: 0 · Решено чисто: 0 · Ошибок: 0")
        self.session_summary.setObjectName("puzzleSessionSummary")
        self.session_summary.setWordWrap(True)
        right_layout.addWidget(self.session_summary)
        self.results_tape = QLabel("Последние результаты: —")
        self.results_tape.setObjectName("puzzleResultsTape")
        self.results_tape.setWordWrap(True)
        self.results_tape.setAccessibleName("Последние результаты тренировки")
        right_layout.addWidget(self.results_tape)
        self.persistence_status = QLabel("")
        self.persistence_status.setObjectName("puzzlePersistenceStatus")
        self.persistence_status.setWordWrap(True)
        right_layout.addWidget(self.persistence_status)
        self.network_status = QLabel("")
        self.network_status.setObjectName("puzzleNetworkStatus")
        self.network_status.setWordWrap(True)
        right_layout.addWidget(self.network_status)
        history_heading = QLabel("Предыдущие задачи")
        history_heading.setStyleSheet("font-weight: 700; font-size: 16px")
        right_layout.addWidget(history_heading)
        self.history = QListWidget()
        self.history.setObjectName("puzzleRecentHistory")
        self.history.setMaximumHeight(260)
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

    def selected_difficulty(self) -> str:
        if self.more_difficulty.isChecked():
            return str(self.advanced_difficulty.currentData() or
                       self.difficulty.currentData() or "normal")
        return str(self.difficulty.currentData() or "normal")

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
        self._auto_next_timer.stop()
        self._wrong_timer.stop()
        self._reply_timer.stop()
        self.board.set_interaction_enabled(False)
        self._next_requested_at = time.perf_counter()
        self.network_status.setText("Загружаем следующую онлайн-задачу…")
        difficulty = str(self.difficulty.currentData() or "normal")
        if self.more_difficulty.isChecked():
            difficulty = str(self.advanced_difficulty.currentData() or difficulty)
        prepared = self.take_prepared_puzzle(self.account_id, self.selected_theme(), difficulty)
        if prepared is not None:
            self._apply_prepared_puzzle(prepared)
        else:
            self.new_puzzle_requested.emit(self.selected_theme(), difficulty)

    def set_prepared_puzzle(self, account_id: str, theme: str, difficulty: str,
                            puzzle: PuzzlePlayablePosition, payload: dict,
                            details: dict | None = None) -> None:
        self._prepared_online_puzzle = {
            "account_id": str(account_id), "theme": str(theme),
            "difficulty": str(difficulty), "puzzle": puzzle,
            "payload": dict(payload), "details": dict(details or {}),
        }
        self._live_get_ms = (details or {}).get("live_get_ms")
        if self.state.status in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED,
                                 PuzzleStatus.SKIPPED, PuzzleStatus.UNAVAILABLE}:
            self.network_status.setText("Следующая задача готова")

    def take_prepared_puzzle(self, account_id: str, theme: str,
                             difficulty: str) -> dict | None:
        prepared = self._prepared_online_puzzle
        self._prepared_online_puzzle = None
        if (prepared is None or prepared["account_id"] != str(account_id)
                or prepared["theme"] != str(theme)
                or prepared["difficulty"] != str(difficulty)):
            return None
        return prepared

    def _apply_prepared_puzzle(self, prepared: dict) -> None:
        details = prepared.get("details") or {}
        if self._next_requested_at is not None:
            self._prepared_next_ms = (time.perf_counter() - self._next_requested_at) * 1000
        self._live_get_ms = details.get("live_get_ms")
        self.account_id = prepared["account_id"]
        self.network_status.clear()
        self.load_puzzle(prepared["puzzle"], prepared["payload"], source="lichess",
                         rating_eligible=bool(details.get("rating_eligible", True)))
        self.set_recent_history(
            details.get("recent", []), local_count=details.get("local_count", self._local_count),
            official_rating=details.get("official_rating"),
            rating_date=details.get("rating_date"),
            local_stats=details.get("local_stats"), theme_stats=details.get("theme_stats"),
            practice_rating=details.get("practice_rating"))

    def load_puzzle(self, puzzle: PuzzlePlayablePosition, payload: dict | None = None,
                    *, source: str = "local",
                    rating_eligible: bool | None = None) -> None:
        self._wrong_timer.stop()
        self._reply_timer.stop()
        self._auto_next_timer.stop()
        self._wrong_flash = False
        self._is_replay = source == "replay" or source not in {"lichess", "demo"}
        self._rating_eligible = (source == "lichess" if rating_eligible is None
                                 else bool(rating_eligible))
        self.state.load(puzzle)
        self._puzzle_payload = dict(payload or {})
        self._active_source = source
        self._session_id = uuid.uuid4().hex
        self._session_account_id = str(self.account_id or "local")
        self._session_puzzle_id = puzzle.puzzle_id
        self._started_at = time.monotonic()
        self._terminal_verdict = ""
        self._rating_preview = None
        self._allow_submit = not self._is_replay
        self.board.set_position(self.state.board)
        self.board.set_flipped(not self.state.board.turn)
        self.board.clear_annotations(record_action=False)
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.board.set_feedback_squares(())
        self.board.set_interaction_enabled(not self._is_replay)
        self.return_to_solution.hide()
        self.review_back.setEnabled(self.state.review_count > 1)
        self.review_forward.setEnabled(False)
        self.review_position.setText("Начало задачи · колесо — просмотр сыгранных ходов")
        self.turn_label.setText(("Повтор · без рейтинга · " if self._is_replay else "Ваш ход · ") +
                                ("белые" if self.state.board.turn else "чёрные"))
        self.solution_line.clear()
        self.solution_line.hide()
        theme_text = ", ".join(THEME_LABELS.get(theme, theme) for theme in puzzle.themes) or "без темы"
        origin = ("Повтор задачи · без рейтинга" if self._is_replay else
                  "Новая задача Lichess" if source == "lichess" else "Демо · только для тестов")
        self.details.setText(f"ID: {puzzle.puzzle_id}\nИсточник: {origin}\n"
                             f"Тема: {theme_text}\nРейтинг задачи: {puzzle.rating or 'нет данных'}")
        self._show_verdict("Найдите лучший ход. ПКМ — ваши стрелки и отметки.", "neutral")
        self.persistence_status.clear()
        self.network_status.clear()
        self._set_controls(True, finished=self._is_replay)
        self.fetch.setText("Следующая задача")
        self._sync_hint_button()
        if source == "lichess":
            difficulty = self.selected_difficulty()
            self.prefetch_requested.emit(self.account_id, self.selected_theme(),
                                         difficulty, self._session_id)

    def set_loading(self, text: str = "Загружаю задачу…") -> None:
        self._allow_submit = False
        self.board.set_interaction_enabled(False)
        if self._terminal_verdict:
            self.network_status.setText("Загружаем следующую онлайн-задачу…")
        else:
            self._show_verdict(text, "neutral")
        self.fetch.setEnabled(False)

    def set_message(self, text: str) -> None:
        self._allow_submit = False
        self.board.set_interaction_enabled(False)
        if self._terminal_verdict:
            self.network_status.setText(str(text))
        else:
            self._show_verdict(text, "error")
        self.fetch.setEnabled(True)
        self._set_controls(False)

    def set_authorization_needed(self, needed: bool) -> None:
        self.authorize.setVisible(bool(needed))

    def _show_verdict(self, text: str, kind: str) -> None:
        colors = {"neutral": "#c8cde0", "success": "#69cf91",
                  "error": "#f0787d", "reply": "#bba8ff"}
        self.status.setText(str(text))
        self.status.setStyleSheet(
            f"font-weight: 700; color: {colors.get(kind, colors['neutral'])}; "
            "padding: 5px 8px; border-radius: 6px;"
        )
        self.status.setAccessibleName(f"Состояние решения: {text}")

    def _restore_after_wrong_move(self) -> None:
        session_id = getattr(self, "_wrong_session_id", None)
        if session_id != self._session_id or self.state.puzzle is None:
            return
        self._wrong_flash = False
        self.board.set_position(self.state.board)
        self.board.set_feedback_squares(())
        self.board.set_interaction_enabled(not self._is_replay)
        self.turn_label.setText("Ваш ход · " + ("белые" if self.state.board.turn else "чёрные"))
        self._show_verdict("✕ Неверный ход. Позиция восстановлена — попробуйте ещё.", "error")

    def _apply_opponent_reply(self) -> None:
        session_id = getattr(self, "_reply_session_id", None)
        if session_id != self._session_id or self.state.status != PuzzleStatus.OPPONENT_REPLY:
            return
        reply = chess.Move.from_uci(self.state._pending_reply)
        result = self.state.apply_opponent_reply()
        if result == "unavailable":
            self._allow_submit = False
            self.board.set_interaction_enabled(False)
            self._show_verdict("Ответ соперника недоступен в этой задаче.", "error")
            return
        if not self.board.animate_move(reply):
            self.board.set_position(self.state.board)
        self.board.set_feedback_squares(())
        self.board.clear_annotations(record_action=False)
        self.board.set_interaction_enabled(not self._is_replay)
        self.turn_label.setText("Ваш ход · " + ("белые" if self.state.board.turn else "чёрные"))
        if result == "success":
            self._finish(PuzzleOutcome.SUCCESS)
        elif result == "reply":
            self._show_verdict("Ответ соперника сыгран · ваш ход.", "neutral")

    def _navigate_review(self, direction: int) -> None:
        if not self.state.puzzle or not self.state._review_snapshots:
            return
        if self._wrong_flash or self.state.status == PuzzleStatus.OPPONENT_REPLY:
            self._show_verdict("Дождитесь завершения текущего хода.", "neutral")
            return
        self._auto_next_timer.stop()
        position = self.state.navigate_review(int(direction))
        self.board.set_position(position)
        self.board.set_interaction_enabled(False)
        self.return_to_solution.show()
        self.review_position.setText(
            f"Просмотр {self.state.review_cursor + 1}/{self.state.review_count} · "
            f"{self.state.review_label}")
        self.review_back.setEnabled(self.state.review_cursor > 0)
        self.review_forward.setEnabled(self.state.review_cursor < self.state.review_count - 1)
        self._show_verdict(
            "Просмотр уже сыгранной части · будущие ходы скрыты · вернитесь к решению.",
            "neutral")

    def _return_to_solution(self) -> None:
        if not self.state.puzzle:
            return
        position = self.state.return_to_solution()
        self.board.set_position(position)
        can_move = self._allow_submit and self.state.status in {
            PuzzleStatus.READY, PuzzleStatus.MISTAKE_RETRY,
        }
        self.board.set_interaction_enabled(can_move and not self._is_replay)
        self.return_to_solution.hide()
        self.review_position.setText("Текущая позиция решения")
        self.review_back.setEnabled(self.state.review_count > 1)
        self.review_forward.setEnabled(False)
        if self.state.status == PuzzleStatus.SUCCESS:
            self._show_verdict(self._terminal_verdict or "✓ Задача решена.", "success")
        else:
            self._show_verdict("Ваш ход · продолжайте решение.", "neutral")

    def _sync_hint_button(self) -> None:
        self.hint.setText(f"Подсказка ({min(self.state.hint_level, 2)}/2)")

    def reset_for_account_switch(self) -> None:
        self._wrong_timer.stop()
        self._reply_timer.stop()
        self._auto_next_timer.stop()
        self._prepared_online_puzzle = None
        self._allow_submit = False
        self._wrong_flash = False
        self.state = PuzzleTrainingState()
        self.board.set_interaction_enabled(False)
        self.board.set_position(chess.Board())
        self.board.set_feedback_squares(())
        self.board.clear_annotations(record_action=False)
        self._show_verdict("Профиль изменён · загружаем задачи выбранного профиля.", "neutral")
        self.network_status.clear()
        self.turn_label.setText("Задача не загружена")
        self.details.setText("Источник: —\nТема: —\nРейтинг задачи: —")
        self.return_to_solution.hide()

    def _submit_move(self, move: chess.Move) -> None:
        if not self._allow_submit:
            if self.state.status in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED,
                                     PuzzleStatus.SKIPPED}:
                self._show_verdict("Эта задача уже завершена. Выберите «Следующая задача» или «Повторить».",
                                   "neutral")
            return
        if self._wrong_flash or self.state.is_reviewing:
            return
        started = time.perf_counter()
        result = self.state.submit(move)
        if result == "illegal":
            self._show_verdict("Так ходить нельзя по правилам шахмат.", "error")
            return
        if result == "mistake":
            trial = self.state.board.copy(stack=True)
            trial.push(move)
            self._wrong_flash = True
            self._wrong_session_id = self._session_id
            self.board.set_interaction_enabled(False)
            self.board.set_position(trial)
            self.board.set_feedback_squares((move.from_square, move.to_square))
            self.turn_label.setText("Ваш ход · ошибка")
            self._show_verdict("✕ Неверный ход. Сейчас позиция вернётся.", "error")
            self._wrong_timer.start(420)
            self._record_input_feedback(started, "wrong")
            if self.state.mistakes == 1:
                self._record_first_failure()
            return
        if result == "unavailable":
            self._allow_submit = False
            self.board.set_interaction_enabled(False)
            self._show_verdict("Задача недоступна: решение содержит нелегальный ответ.", "error")
            return
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.board.clear_annotations(record_action=False)
        self.board.set_feedback_squares((move.from_square, move.to_square), "#52c97b")
        if not self.board.animate_move(move):
            self.board.set_position(self.state.board)
        self._sync_hint_button()
        self._record_input_feedback(started, "correct")
        if result == "success":
            self._finish(PuzzleOutcome.SUCCESS)
        else:
            self.turn_label.setText("Ход соперника")
            self._show_verdict("✓ Верно! Соперник отвечает…", "success")
            self.board.set_interaction_enabled(False)
            self._reply_session_id = self._session_id
            self._reply_timer.start(460)

    def _record_input_feedback(self, started: float, kind: str) -> None:
        """Start a local input-to-first-board-paint measurement."""
        self._pending_paint_started = started
        self._pending_paint_kind = kind
        self.board.update()

    def _first_feedback_paint_completed(self) -> None:
        if self._pending_paint_started is None:
            return
        self._input_feedback_ms.append(
            (time.perf_counter() - self._pending_paint_started) * 1000)
        self._input_feedback_kinds.append(str(self._pending_paint_kind or "move"))
        self._pending_paint_started = None
        self._pending_paint_kind = None

    def _hint(self) -> None:
        if self.state.is_reviewing:
            self._show_verdict("Сначала вернитесь к текущей позиции решения.", "neutral")
            return
        move = self.state.hint()
        if move is None:
            self._show_verdict("Максимум подсказок использован.", "neutral")
            return
        self._sync_hint_button()
        self._rating_eligible = self._rating_eligible and self._active_source == "lichess"
        self.board.set_hint_squares((move.from_square,))
        if self.state.hint_level >= 2:
            self.board.set_hint_arrows((move,))
        self._show_verdict(
            "Подсказка 1/2: выберите фигуру на отмеченном поле." if self.state.hint_level == 1
            else "Подсказка 2/2: направление хода показано стрелкой.", "neutral")

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
        self.board.set_position(self.state.board)
        self.board.set_interaction_enabled(False)
        self._finish(PuzzleOutcome.REVEALED)

    def _repeat(self) -> None:
        if not self.state.puzzle:
            return
        if self.state.status not in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED,
                                     PuzzleStatus.SKIPPED, PuzzleStatus.UNAVAILABLE}:
            answer = QMessageBox.question(
                self, "Повторить задачу", "Текущая попытка не завершена. Повторить с начала?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.state.skip()
            self._finish(PuzzleOutcome.SKIPPED)
        source = self._active_source
        self.load_puzzle(self.state.puzzle, self._puzzle_payload,
                         source="replay" if source != "demo" else "demo")

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
        if not self._session_id or self._session_id in self._finished_session_ids:
            return
        self._finished_session_ids.add(self._session_id)
        self._allow_submit = False
        self._wrong_timer.stop()
        self._reply_timer.stop()
        self.board.set_hint_arrows(())
        self.board.set_hint_squares(())
        self.board.set_feedback_squares(())
        self.board.clear_annotations(record_action=False)
        self.board.set_interaction_enabled(False)
        if outcome == PuzzleOutcome.SUCCESS:
            clean = self.state.mistakes == 0 and self.state.hints_used == 0
            verdict = ("✓ Задача решена! Следующая задача начнётся автоматически."
                       if clean else
                       "Задача решена с ошибкой." if self.state.mistakes else
                       "Задача решена с подсказкой.")
            self._terminal_verdict = verdict
            self._show_verdict(verdict, "success" if clean else "neutral")
            if self.state.mistakes == 0:
                self._show_solution_line()
        elif outcome == PuzzleOutcome.REVEALED:
            verdict = "Решение показано · можно просмотреть линию."
            self._terminal_verdict = verdict
            self._show_verdict(verdict, "neutral")
        else:
            verdict = "Задача пропущена · следующая задача доступна."
            self._terminal_verdict = verdict
            self._show_verdict(verdict, "error")
        self.turn_label.setText("Задача завершена")
        puzzle_rating = self.state.puzzle.rating if self.state.puzzle else None
        if self._rating_eligible and puzzle_rating is not None:
            clean = (outcome == PuzzleOutcome.SUCCESS and self.state.mistakes == 0
                     and self.state.hints_used == 0)
            delta = practice_rating_delta(self._current_cc_rating, puzzle_rating,
                                          1.0 if clean else 0.0)
            self._rating_preview = (self._current_cc_rating, delta)
            self.practice_rating.setText(
                f"Рейтинг тренировки CC: {self._current_cc_rating} → "
                f"{self._current_cc_rating + delta} ({delta:+d}) · ожидает сохранения")
        self._record_session_result(outcome)
        payload = json.dumps(self._puzzle_payload, ensure_ascii=False, separators=(",", ":"))
        result = ("revealed" if outcome == PuzzleOutcome.REVEALED else
                  "skipped" if outcome == PuzzleOutcome.SKIPPED else
                  "hinted" if self.state.hints_used else
                  "mistake" if self.state.mistakes else "success")
        attempt = {
            "attempt_id": uuid.uuid4().hex,
            "session_id": self._session_id,
            "account_id": self._session_account_id,
            "puzzle_id": self._session_puzzle_id,
            "result": result, "mistakes": self.state.mistakes,
            "solved": outcome == PuzzleOutcome.SUCCESS,
            "hints": self.state.hints_used, "duration_ms": int((time.monotonic() - self._started_at) * 1000),
            "puzzle_json": payload, "source": self._active_source,
            "themes": list(self.state.puzzle.themes), "selected_theme": self.selected_theme(),
            "first_failure": bool(self.state.first_failure),
            "rating_eligible": bool(self._rating_eligible and not self._is_replay),
        }
        self._pending_attempt_saves[attempt["attempt_id"]] = attempt
        self._refresh_retry_save_control()
        self._sync_control_layout()
        self.persistence_status.setText("● Сохраняется…")
        self.attempt_saved.emit(attempt)
        self._set_controls(True, finished=True)
        if (outcome == PuzzleOutcome.SUCCESS and self.state.mistakes == 0
                and self.state.hints_used == 0 and self.auto_next.isChecked()
                and self._active_source == "lichess" and not self._is_replay):
            self._auto_next_session_id = self._session_id
            self._auto_next_timer.start(1250)
        else:
            self.next.setText("Следующая задача →")
        self._update_session_display()

    def _record_session_result(self, outcome: PuzzleOutcome) -> None:
        if not self._session_id:
            return
        clean = (outcome == PuzzleOutcome.SUCCESS and self.state.mistakes == 0
                 and self.state.hints_used == 0 and not self._is_replay)
        if outcome == PuzzleOutcome.REVEALED or outcome == PuzzleOutcome.SKIPPED or self._is_replay:
            mark, label = "–", "без рейтинга"
        elif outcome == PuzzleOutcome.SUCCESS and self.state.hints_used and not self.state.mistakes:
            mark, label = "•", "с подсказкой"
        elif clean:
            mark, label = "✓", "чисто"
        else:
            mark, label = "✕", "ошибка/подсказка"
        existing = next((row for row in self._session_results
                         if row["session_id"] == self._session_id), None)
        if existing is not None:
            existing.update(mark=mark, label=label, pending=True)
        else:
            if clean:
                self._session_streak += 1
                self._best_streak = max(self._best_streak, self._session_streak)
                self._session_clean += 1
            elif mark in {"✕", "•"}:
                self._session_streak = 0
                if mark == "✕" and not self.state.first_failure:
                    self._session_failed += 1
                elif mark == "•":
                    self._session_hinted += 1
            else:
                self._session_streak = 0
            row = {"mark": mark, "label": label, "puzzle_id": self._session_puzzle_id,
                   "session_id": self._session_id, "pending": True}
            self._session_results.append(row)
            self._session_results = self._session_results[-20:]
            self._session_outcome_ids.add(self._session_id)
        self._update_session_display()

    def _record_first_failure(self) -> None:
        if self._session_id in self._session_outcome_ids:
            return
        self._session_streak = 0
        self._session_failed += 1
        self._session_results.append({
            "mark": "✕", "label": "первая ошибка", "puzzle_id": self._session_puzzle_id,
            "session_id": self._session_id, "pending": True,
        })
        self._session_results = self._session_results[-20:]
        self._session_outcome_ids.add(self._session_id)
        self._update_session_display()

    def _update_session_display(self) -> None:
        self.session_summary.setText(
            f"Серия: {self._session_streak} · Лучшая: {self._best_streak} · "
            f"Решено чисто: {self._session_clean} · Ошибок: {self._session_failed} · "
            f"С подсказкой: {self._session_hinted}")
        self.results_tape.setText(
            "Последние результаты: " + (" ".join(item["mark"] for item in self._session_results)
                                          if self._session_results else "—"))
        self.results_tape.setToolTip(" · ".join(
            f"{item['puzzle_id']}: {item['label']}" for item in self._session_results))

    def _retry_attempt_save(self) -> None:
        if self._retry_in_progress_id:
            return
        attempt_id = next((value for value in self._retry_attempt_queue
                           if value in self._pending_attempt_saves), None)
        attempt = self._pending_attempt_saves.get(attempt_id or "")
        if attempt is None or attempt_id is None:
            self._refresh_retry_save_control()
            return
        self._retry_attempt_id = attempt_id
        self._retry_in_progress_id = attempt_id
        self._refresh_retry_save_control()
        if self._session_id == attempt["session_id"]:
            self.persistence_status.setText("● Повторяем сохранение…")
        self.attempt_saved.emit(dict(attempt))

    def _refresh_retry_save_control(self) -> None:
        self._retry_attempt_queue = list(dict.fromkeys(
            value for value in self._retry_attempt_queue
            if value in self._pending_attempt_saves))
        if self._retry_in_progress_id:
            self.retry_save.setText("Повторяем сохранение…")
            self.retry_save.setEnabled(False)
            self.retry_save.setVisible(True)
            self._sync_control_layout()
            return
        self._retry_attempt_id = next(iter(self._retry_attempt_queue), None)
        count = len(self._retry_attempt_queue)
        self.retry_save.setText(
            "Повторить сохранение" if count <= 1 else f"Повторить сохранение ({count})")
        self.retry_save.setEnabled(count > 0)
        self.retry_save.setVisible(count > 0)
        self._sync_control_layout()

    def _attempt_save_completed(self, attempt_id: str, success: bool,
                                data: dict | None = None, error: str | None = None) -> None:
        attempt = self._pending_attempt_saves.get(str(attempt_id))
        if attempt is None:
            return
        if not success:
            retry_id = str(attempt_id)
            if retry_id not in self._retry_attempt_queue:
                self._retry_attempt_queue.append(retry_id)
            if self._retry_in_progress_id == retry_id:
                self._retry_in_progress_id = None
            if self._session_id == attempt["session_id"]:
                self.persistence_status.setText(
                    f"Не удалось сохранить · {error or 'ошибка записи'} · повторите попытку")
            self._refresh_retry_save_control()
            return

        self._pending_attempt_saves.pop(str(attempt_id), None)
        self._retry_attempt_queue = [value for value in self._retry_attempt_queue
                                     if value != str(attempt_id)]
        if self._retry_in_progress_id == str(attempt_id):
            self._retry_in_progress_id = None
        if getattr(self, "_retry_attempt_id", None) == str(attempt_id):
            self._retry_attempt_id = None
        self._db_commit_ms = (data or {}).get("db_commit_ms")
        for result_row in self._session_results:
            if result_row["session_id"] == str(attempt.get("session_id")):
                result_row["pending"] = False
        self._refresh_retry_save_control()
        if self._session_id != attempt["session_id"] or self.account_id != attempt["account_id"]:
            return
        self.persistence_status.setText("✓ Сохранено в SQLite")
        payload = data or {}
        self.set_recent_history(
            payload.get("recent", []), local_count=payload.get("local_count", self._local_count),
            local_stats=payload.get("local_stats"), theme_stats=payload.get("theme_stats"),
            practice_rating=payload.get("practice_rating"))
        self._update_session_display()

    def attach_pending_attempts(self, attempts: list[dict]) -> None:
        for attempt in attempts:
            attempt_id = str(attempt.get("attempt_id") or "")
            if attempt_id:
                self._pending_attempt_saves[attempt_id] = dict(attempt)
                if attempt_id not in self._retry_attempt_queue:
                    self._retry_attempt_queue.append(attempt_id)
        if attempts:
            self.persistence_status.setText("Результат ожидает сохранения · можно повторить")
        self._refresh_retry_save_control()

    def local_seen_ids(self, scope: tuple[str, str, str]) -> set[str]:
        return self._local_seen_by_scope.setdefault(scope, set())

    def mark_local_candidate_seen(self, scope: tuple[str, str, str], puzzle_id: str) -> None:
        self.local_seen_ids(scope).add(str(puzzle_id))

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
                           theme_stats: dict | None = None,
                           practice_rating: dict | None = None) -> None:
        self._local_count = max(0, int(local_count))
        if local_stats is not None:
            self._local_stats = dict(local_stats)
        if theme_stats is not None:
            self._theme_stats = dict(theme_stats)
        if official_rating is not None:
            self._official_rating = int(official_rating)
        if rating_date:
            self._rating_date = str(rating_date)
        if practice_rating is not None:
            value = max(0, int(practice_rating.get("rating", 1500)))
            delta = int(practice_rating.get("delta", 0))
            rated_count = int(practice_rating.get("rated_attempts", 0))
            self._current_cc_rating = value
            self.practice_rating.setText(
                f"Рейтинг тренировки CC: {value}"
                + (" (стартовый)" if rated_count == 0 else "")
                + (f" · {delta:+d} за задачу" if delta else ""))
        self.history.clear()
        for row in rows[:8]:
            source = "Chess Coach" if row.get("source") == "local" else "Lichess · sync"
            outcome = puzzle_result_label(row)
            themes = row.get("themes") or []
            theme = THEME_LABELS.get(themes[0], themes[0]) if themes else "без темы"
            created = row.get("created_at_ms") or row.get("attempt_date_ms")
            date_text = time.strftime("%d.%m", time.localtime(created / 1000)) if created else "—"
            item = QListWidgetItem(f"{date_text} · {theme}\n{outcome} · {source}")
            item.setSizeHint(QSize(0, 50))
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(
                f"{row.get('puzzle_id', '—')} · {date_text} · {theme} · {outcome} · {source}")
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

    def _auto_advance(self) -> None:
        if (self._auto_next_session_id != self._session_id
                or self.state.status != PuzzleStatus.SUCCESS
                or self.state.mistakes or self.state.hints_used or self._is_replay):
            return
        self._next()

    def _open_history_item(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) or {}
        puzzle_id = str(row.get("puzzle_id", ""))
        if puzzle_id:
            self.replay_requested.emit(self.account_id, puzzle_id,
                                       str(row.get("source") or ""))

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
        center_width = self.board.parentWidget().width() if self.board.parentWidget() else self.width()
        compact_controls = self._needs_compact_controls(center_width) or self.height() < 700
        if compact:
            self._training_root_layout.setContentsMargins(8, 2, 8, 4)
            self._training_root_layout.setSpacing(4)
            center_layout = self.board.parentWidget().layout()
            if center_layout is not None:
                center_layout.setSpacing(0)
            for button in (self.hint, self.reveal, self.repeat, self.retry_save, self.next):
                button.setMinimumHeight(34)
        else:
            self._training_root_layout.setContentsMargins(10, 6, 10, 10)
            self._training_root_layout.setSpacing(6)
            center_layout = self.board.parentWidget().layout()
            if center_layout is not None:
                center_layout.setSpacing(6)
            for button in (self.hint, self.reveal, self.repeat, self.retry_save, self.next):
                button.setMinimumHeight(38)
        if compact_controls != self._controls_compact:
            self._controls_compact = compact_controls
            self._sync_control_layout()

    def _needs_compact_controls(self, center_width: int | None = None) -> bool:
        if center_width is None:
            parent = self.board.parentWidget()
            center_width = parent.width() if parent is not None else self.width()
        visible_buttons = [
            button for button in (self.hint, self.reveal, self.repeat,
                                  self.retry_save, self.next)
            if not button.isHidden()
        ]
        spacing = max(0, self._controls_layout.horizontalSpacing())
        # The normal grid gives every visible action one equal-width column.
        # Fit the longest label in every column instead of comparing the sum
        # of unequal natural widths with the available space.
        required_width = (max((button.sizeHint().width() for button in visible_buttons),
                              default=0) * len(visible_buttons))
        required_width += spacing * max(0, len(visible_buttons) - 1)
        return center_width < required_width

    def _sync_control_layout(self) -> None:
        compact_controls = (self._needs_compact_controls()
                            or self.height() < 700)
        self._controls_compact = compact_controls
        layout = self._controls_layout
        while layout.count():
            layout.takeAt(0)
        if not self._controls_compact:
            for column, button in enumerate(
                    (self.hint, self.reveal, self.repeat, self.retry_save, self.next)):
                layout.addWidget(button, 0, column)
            return
        if self.retry_save.isVisible():
            for column, button in enumerate((self.hint, self.reveal, self.repeat)):
                layout.addWidget(button, 0, column)
            layout.addWidget(self.retry_save, 1, 0, 1, 2)
            layout.addWidget(self.next, 1, 2)
        else:
            layout.addWidget(self.hint, 0, 0)
            layout.addWidget(self.reveal, 0, 1, 1, 2)
            layout.addWidget(self.repeat, 1, 0)
            layout.addWidget(self.next, 1, 1, 1, 2)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._auto_next_timer.stop()
        self._wrong_timer.stop()
        self._reply_timer.stop()
        super().hideEvent(event)
        self.session_hidden.emit()
