"""Home/dashboard page widget. Data is supplied by the application shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)
from ..dashboard import dashboard_options
from ..display import format_clock_control


class HomePage(QScrollArea):
    def __init__(self, repository, settings, username, actions, filters_changed, parent=None):
        super().__init__(parent)
        options = dashboard_options(repository)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Ваша шахматная практика")
        title.setObjectName("pageTitle")
        title.setVisible(False)
        layout.addWidget(title)
        self.home_status = QLabel(
            "Начните локальную партию или подключите Lichess. Сводка и последние партии — ниже.")
        self.home_status.setWordWrap(True)
        self.home_status.setObjectName("secondary")
        self.home_status.setVisible(False)
        layout.addWidget(self.home_status)
        scope_row = QHBoxLayout()
        self.dashboard_account = QComboBox()
        self.dashboard_account.setAccessibleName("Аккаунт сводки")
        self.dashboard_account.addItem("Все локальные партии", None)
        self.dashboard_clock = QComboBox()
        self.dashboard_clock.setAccessibleName("Контроль времени сводки")
        self.dashboard_clock.addItem("Любой контроль", "")
        for account in options["accounts"]:
            self.dashboard_account.addItem(account["username"], account["account_id"])
        for clock in options["clocks"]:
            self.dashboard_clock.addItem(format_clock_control(clock), clock)
        preferred_account = str(settings.value("workspace/selected_account", "", type=str))
        if username:
            preferred_account = f"lichess:{username.strip().lower()}"
        preferred_index = self.dashboard_account.findData(preferred_account)
        if preferred_index >= 0:
            self.dashboard_account.setCurrentIndex(preferred_index)
        preferred_clock = str(settings.value("workspace/dashboard_clock", "", type=str))
        clock_index = self.dashboard_clock.findData(preferred_clock)
        if clock_index >= 0:
            self.dashboard_clock.setCurrentIndex(clock_index)
        scope_row.addWidget(QLabel("Аккаунт"))
        scope_row.addWidget(self.dashboard_account, 1)
        scope_row.addWidget(QLabel("Контроль"))
        scope_row.addWidget(self.dashboard_clock, 1)
        layout.addLayout(scope_row)
        self.dashboard_account.currentIndexChanged.connect(filters_changed)
        self.dashboard_clock.currentIndexChanged.connect(filters_changed)
        self.dashboard_metrics = {}
        metrics_grid = QGridLayout()
        self.dashboard_metrics_grid = metrics_grid
        metrics_grid.setHorizontalSpacing(12)
        metrics_grid.setVerticalSpacing(12)
        self._dashboard_metric_values = {}
        self._dashboard_metric_details = {}
        for index, (key, label) in enumerate((
                ("games", "Завершённые партии"), ("accuracy", "Точность Lichess"),
                ("analysis", "Анализ моих ходов"),
                ("errors", "Ошибки на 100 оценённых ходов"))):
            card = QFrame()
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(4)
            caption = QLabel(label)
            caption.setObjectName("secondary")
            caption.setWordWrap(True)
            caption.setMaximumHeight(34)
            value = QLabel("—")
            value.setStyleSheet("font-size: 22px; font-weight: 700;")
            detail = QLabel("Локальные данные")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            card_layout.addWidget(detail)
            self._dashboard_metric_values[key] = value
            self._dashboard_metric_details[key] = detail
            self.dashboard_metrics[key] = card
        self.layout_metrics()
        layout.addLayout(metrics_grid)
        recent_card = QFrame()
        self.home_recent_card = recent_card
        recent_card.setObjectName("metricCard")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(12, 10, 12, 10)
        recent_title = QLabel("Последние партии")
        recent_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        recent_layout.addWidget(recent_title)
        self._recent_games_layout = QVBoxLayout()
        self._recent_games_layout.setSpacing(8)
        recent_layout.addLayout(self._recent_games_layout, 1)
        primary_actions = QHBoxLayout()
        self.home_primary_actions = primary_actions
        play_card = QFrame()
        self.home_play_card = play_card
        play_card.setObjectName("metricCard")
        play_card.setMaximumHeight(230)
        play_layout = QVBoxLayout(play_card)
        play_layout.setContentsMargins(12, 10, 12, 10)
        play_layout.setSpacing(7)
        play_title = QLabel("Играть")
        play_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        play_layout.addWidget(play_title)
        play_hint = QLabel("Локальная доска или нерейтинговая партия Lichess AI")
        play_hint.setObjectName("secondary")
        play_hint.setWordWrap(True)
        play_layout.addWidget(play_hint)
        play_buttons = QVBoxLayout()
        self.home_ai_button = QPushButton("Играть против Lichess AI")
        self.home_ai_button.setObjectName("primaryAction")
        self.home_ai_button.clicked.connect(actions["play_ai"])
        self.home_local_button = QPushButton("Свободная доска")
        self.home_local_button.clicked.connect(actions["start_local_game"])
        self.resume_game_button = QPushButton("Вернуться в текущую партию")
        self.resume_game_button.clicked.connect(actions["resume_current_game"])
        self.resume_game_button.setVisible(False)
        play_buttons.addWidget(self.home_ai_button)
        play_buttons.addWidget(self.home_local_button)
        play_buttons.addWidget(self.resume_game_button)
        play_layout.addLayout(play_buttons)
        primary_actions.addWidget(play_card, 0, Qt.AlignmentFlag.AlignTop)
        primary_actions.addWidget(recent_card, 3)
        review_buttons = QHBoxLayout()
        self.home_review_button = QPushButton("Выбрать партию")
        self.home_review_button.clicked.connect(actions["show_saved_games"])
        review_buttons.addWidget(self.home_review_button)
        self.resume_review_button = QPushButton("Продолжить последний разбор")
        self.resume_review_button.setObjectName("primaryAction")
        self.resume_review_button.setAccessibleName("Продолжить разбор последней партии")
        self.resume_review_button.clicked.connect(actions["resume_saved_review"])
        self.resume_review_button.setVisible(False)
        review_buttons.addWidget(self.resume_review_button)
        play_layout.addLayout(review_buttons)
        layout.addLayout(primary_actions)

        self.home_friend_button = QPushButton("Играть с другом по ссылке")
        self.home_friend_button.clicked.connect(actions["open_friend_game"])
        self.home_username_button = QPushButton("Вызвать игрока Lichess")
        self.home_username_button.clicked.connect(actions["challenge_username"])
        self.active_games_button = QPushButton("Активные партии Lichess")
        self.active_games_button.clicked.connect(actions["show_active_games"])
        self.active_games_button.setEnabled(False)
        self.home_games_button = QPushButton("Завершённые партии")
        self.home_games_button.clicked.connect(actions["show_saved_games"])
        self.home_archive_status_button = QPushButton("Хранилище и статус архива")
        self.home_archive_status_button.clicked.connect(actions["show_archive_status"])
        self.home_pilot_import_button = QPushButton("Пробно импортировать 5–10 последних партий")
        self.home_pilot_import_button.clicked.connect(actions["start_archive_pilot"])
        self.home_pilot_import_button.setEnabled(False)
        self.home_archive_button = QPushButton("Полная синхронизация архива Lichess")
        self.home_archive_button.clicked.connect(actions["start_archive_sync"])
        self.home_archive_button.setEnabled(False)
        self.home_stats_button = QPushButton("Статистика партий")
        self.home_stats_button.clicked.connect(actions["show_statistics"])
        self.home_batch_analysis_button = QPushButton("Массовый анализ Stockfish")
        self.home_batch_analysis_button.clicked.connect(actions["show_batch_analysis"])
        self.home_batch_analysis_button.setEnabled(True)
        self.home_puzzles_button = QPushButton("Моя история задач Lichess")
        self.home_puzzles_button.clicked.connect(actions["open_puzzle_history_local"])
        self.home_puzzles_button.setEnabled(True)
        self.home_puzzles_pilot_button = QPushButton("Пробно загрузить 5–10 попыток задач")
        self.home_puzzles_pilot_button.clicked.connect(actions["start_puzzle_pilot"])
        self.home_puzzles_pilot_button.setEnabled(False)
        self.home_settings_button = QPushButton("Настройки")
        self.home_settings_button.clicked.connect(actions["open_settings"])
        self.home_more_toggle = QPushButton("Другие разделы и синхронизация  ▸")
        self.home_more_toggle.setCheckable(True)
        self.home_more_toggle.setAccessibleName("Показать другие разделы и синхронизацию")
        layout.addWidget(self.home_more_toggle)
        self.home_more = QGroupBox("Дополнительные разделы и синхронизация")
        more_layout = QVBoxLayout(self.home_more)
        for button in (self.home_friend_button, self.home_username_button,
                       self.active_games_button,
                       self.home_games_button, self.home_stats_button,
                       self.home_batch_analysis_button, self.home_puzzles_button,
                       self.home_settings_button, self.home_archive_status_button,
                       self.home_pilot_import_button, self.home_archive_button):
            more_layout.addWidget(button)
        more_layout_widget = QWidget()
        more_content = QVBoxLayout(more_layout_widget)
        more_content.setContentsMargins(0, 0, 0, 0)
        for index in range(more_layout.count()):
            item = more_layout.takeAt(0)
            if item.widget():
                more_content.addWidget(item.widget())
        self.home_more_content = more_layout_widget
        more_layout_widget.setVisible(False)
        more_layout.addWidget(more_layout_widget)
        self.home_more.setVisible(False)
        self.home_more_toggle.toggled.connect(self.home_more.setVisible)
        self.home_more_toggle.toggled.connect(more_layout_widget.setVisible)
        self.home_more_toggle.toggled.connect(lambda checked: self.home_more_toggle.setText(
            "Другие разделы и синхронизация  ▾" if checked else
            "Другие разделы и синхронизация  ▸"))
        layout.addWidget(self.home_more)
        layout.addStretch(1)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidget(page)

    def layout_metrics(self, width: int | None = None) -> None:
        grid = self.dashboard_metrics_grid
        cards = list(self.dashboard_metrics.values())
        if not cards:
            return
        columns = 2 if int(width if width is not None else self.width()) < 900 else 4
        if getattr(self, "_dashboard_metric_columns", None) == columns:
            return
        self._dashboard_metric_columns = columns
        while grid.count():
            grid.takeAt(0)
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)
