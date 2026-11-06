"""Compact home dashboard; all values and actions are supplied by the shell."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QToolTip, QVBoxLayout, QWidget,
)


class PuzzleRatingSparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(46)
        self.setMouseTracking(True)
        self.setAccessibleName("История рейтинга задач Lichess")
        self.hide()

    def set_points(self, points):
        self.points = points
        self.setVisible(bool(points))
        self.update()

    def _vertices(self):
        if not self.points:
            return []
        days = [date.fromisoformat(point["day"]).toordinal() for point in self.points]
        ratings = [point["rating"] for point in self.points]
        low, high = min(ratings), max(ratings)
        width, height = max(1, self.width() - 12), max(1, self.height() - 12)
        return [QPointF(6 + width * (day - days[0]) / max(1, days[-1] - days[0]),
                        6 + height * (1 - (rating - low + 5) / (high - low + 10)))
                for day, rating in zip(days, ratings)]

    def paintEvent(self, _event):
        points = self._vertices()
        if not points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(points[-1].x(), self.height())
        area.lineTo(points[0].x(), self.height())
        area.closeSubpath()
        painter.fillPath(area, QColor(92, 226, 130, 40))
        painter.setPen(QPen(QColor("#79E599"), 2))
        painter.drawPath(line)
        painter.setBrush(QColor("#79E599"))
        painter.drawEllipse(points[-1], 3, 3)

    def mouseMoveEvent(self, event):
        vertices = self._vertices()
        if not vertices:
            return
        index = min(range(len(vertices)), key=lambda i: abs(vertices[i].x() - event.position().x()))
        point = self.points[index]
        activity = point.get("attempts")
        activity_label = (f"решено {activity[1]} · не решено {activity[0] - activity[1]}"
                          if activity else "нет сохранённых попыток за этот день")
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"{point['day']} UTC · рейтинг Lichess {point['rating']}\n{activity_label}", self)


class HomePage(QWidget):
    def __init__(self, repository, settings, username, actions, filters_changed, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(9)
        self.home_status = QLabel("Практика, сохранённые партии и проверенная статистика.")
        self.home_status.setObjectName("secondary")
        self.home_status.setWordWrap(False)
        self.home_status.setMaximumHeight(24)
        layout.addWidget(self.home_status)

        # Account scope is shared with the authenticated profile. Keep the old
        # controls as hidden compatibility adapters; Home is not a filter page.
        self.dashboard_account = QComboBox(self)
        self.dashboard_account.setAccessibleName("Аккаунт сводки")
        self.dashboard_account.addItem("Все локальные партии", None)
        self.dashboard_account.currentIndexChanged.connect(filters_changed)
        self.dashboard_clock = QComboBox(self)
        self.dashboard_clock.setAccessibleName("Устаревший фильтр контроля времени")
        self.dashboard_clock.addItem("Любой контроль", "")
        self.dashboard_clock.hide()
        self.dashboard_account.hide()
        self._preferred_account = str(settings.value("workspace/selected_account", "", type=str))
        if username:
            self._preferred_account = f"lichess:{username.strip().lower()}"
        self._preferred_clock = ""  # the historical key remains stored but is no longer applied on Home

        self.dashboard_metrics = {}
        metrics_grid = QGridLayout()
        self.dashboard_metrics_grid = metrics_grid
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setHorizontalSpacing(16)
        metrics_grid.setVerticalSpacing(0)
        self._dashboard_metric_values = {}
        self._dashboard_metric_details = {}
        for key, label in (
                ("games", "Завершённые партии"),
                ("analysis", "Партии с оценками ходов"),
                ("accuracy", "Точность"),
                ("errors", "Ошибки на 100 оценённых ходов")):
            card = QFrame(self)
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 13, 16, 13)
            card_layout.setSpacing(2)
            caption = QLabel(label)
            caption.setStyleSheet("font-size: 17px; font-weight: 600;")
            caption.setWordWrap(False)
            value = QLabel("—")
            value.setStyleSheet("font-size: 38px; font-weight: 700;")
            detail = QLabel("Локальные данные")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            card_layout.addWidget(detail)
            self._dashboard_metric_values[key] = value
            self._dashboard_metric_details[key] = detail
            self.dashboard_metrics[key] = card
        # Error-rate detail belongs in Progress; retain the existing read-model
        # field for compatibility without giving it KPI prominence on Home.
        self.dashboard_metrics["errors"].hide()
        layout.addLayout(metrics_grid)

        top_row = QHBoxLayout()
        self.home_primary_actions = top_row
        top_row.setSpacing(9)
        self.home_progress_card = QFrame(self)
        self.home_progress_card.setObjectName("metricCard")
        self.home_progress_card.setMinimumHeight(140)
        progress_layout = QVBoxLayout(self.home_progress_card)
        progress_layout.setContentsMargins(11, 8, 11, 8)
        progress_layout.setSpacing(4)
        progress_title = QLabel("Рейтинг задач")
        progress_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        progress_layout.addWidget(progress_title)
        self.puzzle_rating = QLabel("—")
        self.puzzle_rating.setStyleSheet("font-size: 32px; font-weight: 700;")
        progress_layout.addWidget(self.puzzle_rating)
        self.puzzle_sparkline = PuzzleRatingSparkline(self.home_progress_card)
        progress_layout.addWidget(self.puzzle_sparkline)
        self.progress_trend = QLabel("Нет сохранённых точек тренда.")
        self.progress_trend.setObjectName("secondary")
        self.progress_trend.setWordWrap(True)
        self.progress_trend.setMaximumHeight(50)
        progress_layout.addWidget(self.progress_trend, 1)
        self.home_all_progress_button = QPushButton("Весь прогресс")
        self.home_all_progress_button.clicked.connect(actions["show_statistics"])
        progress_layout.addWidget(self.home_all_progress_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.dashboard_metrics["games"].layout().addWidget(
            self._dashboard_metric_values["analysis"])
        self._dashboard_metric_values["analysis"].setStyleSheet("font-size: 14px;")
        self.dashboard_metrics["games"].layout().addWidget(
            self._dashboard_metric_details["analysis"])
        self._dashboard_metric_values["analysis"].hide()
        self.dashboard_metrics["analysis"].hide()
        self.layout_metrics()

        self.home_play_card = QFrame(self)
        self.home_play_card.setObjectName("metricCard")
        self.home_play_card.setMaximumHeight(160)
        activity_layout = QHBoxLayout(self.home_play_card)
        activity_layout.setContentsMargins(11, 8, 11, 8)
        activity_layout.setSpacing(4)
        activity_title = QLabel("Следующее занятие")
        activity_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        activity_layout.addWidget(activity_title)
        self.activity_status = QLabel("Пока нет сохранённых задач для повторения.")
        self.activity_status.setObjectName("secondary")
        self.activity_status.setWordWrap(True)
        self.activity_status.setMaximumHeight(50)
        activity_layout.addWidget(self.activity_status, 1)
        self.home_activity_button = QPushButton("Открыть обучение")
        self.home_activity_button.setObjectName("primaryAction")
        self.home_activity_button.clicked.connect(actions["open_puzzle_history_local"])
        activity_layout.addWidget(self.home_activity_button)
        top_row.addWidget(self.home_play_card, 1)

        recent_card = QFrame(self)
        self.home_recent_card = recent_card
        recent_card.setObjectName("recentGames")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(11, 8, 11, 8)
        recent_title_row = QHBoxLayout()
        recent_title = QLabel("Последние партии")
        recent_title.setStyleSheet("font-size: 28px; font-weight: 700;")
        recent_title_row.addWidget(recent_title)
        recent_title_row.addStretch(1)
        self.home_games_button = QPushButton("Все партии")
        self.home_games_button.clicked.connect(actions["show_saved_games"])
        recent_title_row.addWidget(self.home_games_button)
        recent_layout.addLayout(recent_title_row)
        self.recent_safety_notice = QLabel("Оценки доступны после проверки безопасности Lichess.")
        self.recent_safety_notice.setObjectName("muted")
        self.recent_safety_notice.setMaximumHeight(20)
        recent_layout.addWidget(self.recent_safety_notice)
        recent_rows = QWidget()
        self._recent_games_layout = QVBoxLayout(recent_rows)
        self._recent_games_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_games_layout.setSpacing(12)
        self.recent_scroll = QScrollArea()
        self.recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_scroll.setWidget(recent_rows)
        recent_layout.addWidget(self.recent_scroll, 1)
        layout.addWidget(recent_card, 1)
        layout.addLayout(top_row)

        # Preserve shell callbacks and old tests as hidden compatibility hooks.
        self.home_friend_button = QPushButton("Играть с другом по ссылке")
        self.home_friend_button.clicked.connect(actions["open_friend_game"])
        self.home_username_button = QPushButton("Вызвать игрока Lichess")
        self.home_username_button.clicked.connect(actions["challenge_username"])
        self.active_games_button = QPushButton("Активные партии Lichess")
        self.active_games_button.clicked.connect(actions["show_active_games"])
        self.home_archive_status_button = QPushButton("Хранилище и статус архива")
        self.home_archive_status_button.clicked.connect(actions["show_archive_status"])
        self.home_archive_button = QPushButton("Полная синхронизация архива Lichess")
        self.home_archive_button.clicked.connect(actions["start_archive_sync"])
        self.home_stats_button = QPushButton("Статистика партий")
        self.home_stats_button.clicked.connect(actions["show_statistics"])
        self.home_batch_analysis_button = QPushButton("Массовый анализ Stockfish")
        self.home_batch_analysis_button.clicked.connect(actions["show_batch_analysis"])
        self.home_puzzles_button = QPushButton("Моя история задач Lichess")
        self.home_puzzles_button.clicked.connect(actions["open_puzzle_history_local"])
        self.home_settings_button = QPushButton("Настройки")
        self.home_settings_button.clicked.connect(actions["open_settings"])
        self.home_ai_button = QPushButton("Играть против Lichess AI")
        self.home_ai_button.clicked.connect(actions["play_ai"])
        self.home_local_button = QPushButton("Свободная доска")
        self.home_local_button.clicked.connect(actions["start_local_game"])
        self.resume_game_button = QPushButton("Вернуться в текущую партию")
        self.resume_game_button.clicked.connect(actions["resume_current_game"])
        self.home_review_button = QPushButton("Выбрать партию")
        self.home_review_button.clicked.connect(actions["show_saved_games"])
        self.resume_review_button = QPushButton("Продолжить последний разбор")
        self.resume_review_button.clicked.connect(actions["resume_saved_review"])
        self.home_more_toggle = QPushButton("Другие разделы")
        self.home_more_content = QWidget()
        self.home_more = QWidget()
        self._legacy_action_host = QWidget(self)
        self._legacy_action_host.hide()
        for widget in (self.home_friend_button, self.home_username_button,
                       self.active_games_button, self.home_archive_status_button,
                       self.home_archive_button, self.home_stats_button,
                       self.home_batch_analysis_button, self.home_puzzles_button,
                       self.home_settings_button, self.home_ai_button, self.home_local_button,
                       self.resume_game_button, self.home_review_button,
                       self.resume_review_button, self.home_more_toggle,
                       self.home_more_content, self.home_more):
            widget.setParent(self._legacy_action_host)
            widget.hide()

    def set_selector_options(self, options: dict) -> None:
        self.dashboard_account.blockSignals(True)
        self.dashboard_account.clear()
        self.dashboard_account.addItem("Все локальные партии", None)
        for item in options.get("accounts", []):
            self.dashboard_account.addItem(str(item["username"]), str(item["account_id"]))
        self.select_profile(self._preferred_account, notify=False)
        self.dashboard_account.blockSignals(False)

    def select_profile(self, profile: str | None, *, notify: bool = True) -> None:
        preferred = str(profile or "")
        self._preferred_account = preferred
        index = self.dashboard_account.findData(preferred)
        if index < 0 and preferred.startswith("lichess:"):
            username = preferred.partition(":")[2].casefold()
            for candidate in range(self.dashboard_account.count()):
                if self.dashboard_account.itemText(candidate).casefold() == username:
                    index = candidate
                    break
            if index < 0:
                # Keep a newly authenticated account scoped while selector
                # options are being refreshed. Never fall back to All accounts.
                self.dashboard_account.addItem(username, preferred)
                index = self.dashboard_account.count() - 1
        if index < 0:
            index = 0
        previous = self.dashboard_account.blockSignals(True)
        self.dashboard_account.setCurrentIndex(index)
        self.dashboard_account.blockSignals(previous)
        if notify:
            self.dashboard_account.currentIndexChanged.emit(index)

    def layout_metrics(self, width: int | None = None) -> None:
        grid = self.dashboard_metrics_grid
        cards = [self.dashboard_metrics["games"], self.dashboard_metrics["accuracy"],
                 self.home_progress_card]
        if not cards:
            return
        available = int(width if width is not None else self.width())
        columns = 3 if available >= 980 else 2 if available >= 620 else 1
        if getattr(self, "_dashboard_metric_columns", None) == columns:
            return
        self._dashboard_metric_columns = columns
        while grid.count():
            grid.takeAt(0)
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)
