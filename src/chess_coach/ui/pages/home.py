"""Compact home dashboard; all values and actions are supplied by the shell."""

from __future__ import annotations

from datetime import date
import math

import chess

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)
from ..icons import icon_pixmap
from ...board_view import BoardView


class HomeEvaluationGraph(QWidget):
    """Compact, source-backed score graph in white's point of view."""

    AXIS_LIMIT_CP = 600

    def __init__(self, points=None, total_plies: int = 0,
                 phases: dict | None = None, parent=None):
        super().__init__(parent)
        self.points = []
        self.empty_text = "Нет сохранённых оценок"
        self.total_plies = max(1, int(total_plies or 1))
        self.phases = phases if isinstance(phases, dict) else {}
        self._hit_targets = []
        self.setMinimumSize(230, 108)
        self.setMouseTracking(True)
        self.setAccessibleName("График преимущества по ходам")
        self.setAccessibleDescription(
            "Вертикальная шкала в пешках от белой перспективы: плюс — преимущество белых, "
            "минус — преимущество чёрных. Пропуски оценок не соединяются.")
        self.set_points(points or [], total_plies=total_plies, phases=phases)

    def set_points(self, points, *, total_plies: int | None = None,
                   phases: dict | None = None) -> None:
        self.points = sorted((dict(row) for row in (points or [])
                              if type(row.get("ply")) is int), key=lambda row: row["ply"])
        if total_plies is not None:
            self.total_plies = max(1, int(total_plies or 1))
        if phases is not None:
            self.phases = phases if isinstance(phases, dict) else {}
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(38, 8, -8, -24)

    @staticmethod
    def _white_perspective_label(cp: int | None, mate: int | None) -> str:
        if mate is not None:
            side = "белые" if mate > 0 else "чёрные" if mate < 0 else "мат"
            return f"Мат: {side} через {abs(mate)}"
        if cp is None:
            return "Оценки позиции нет"
        return f"Оценка за белых: {cp / 100:+.2f} пешки"

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        self._hit_targets = []
        painter.setPen(QPen(QColor("#354058"), 1))
        painter.drawRect(plot)
        limit = self.AXIS_LIMIT_CP
        for tick in (limit, 400, 200, 0, -200, -400, -limit):
            y = plot.center().y() - round(plot.height() * tick / (2 * limit))
            painter.setPen(QPen(QColor("#64708A" if tick == 0 else "#354058"),
                                1 if tick == 0 else 1))
            painter.drawLine(plot.left(), y, plot.right(), y)
            label = f"{tick / 100:+g}" if tick else "0"
            painter.setPen(QColor("#AEB8D0"))
            painter.drawText(0, y + 4, label)

        middle = self.phases.get("middle_start_ply")
        end = self.phases.get("end_start_ply")
        stage_labels = []
        phase_marks = []
        if (type(middle) is int and 0 < middle < self.total_plies
                and type(end) is int and middle < end < self.total_plies):
            phase_marks = [middle, end]
            stage_labels = [
                (middle // 2, "Дебют"),
                ((middle + end) // 2, "Миттельшпиль"),
                ((end + self.total_plies) // 2, "Эндшпиль"),
            ]
        else:
            # Unknown phase boundaries are marked as neutral progress, not as
            # invented chess stages.
            stage_labels = [(self.total_plies // 6, "Начало"),
                            (self.total_plies // 2, "Середина"),
                            (self.total_plies * 5 // 6, "Конец")]
        painter.setPen(QPen(QColor("#59637A"), 1, Qt.PenStyle.DashLine))
        for ply in phase_marks:
            if ply <= 0 or ply >= self.total_plies:
                continue
            x = plot.left() + round(plot.width() * ply / self.total_plies)
            painter.drawLine(x, plot.top(), x, plot.bottom())
        painter.setPen(QColor("#AEB8D0"))
        for ply, label in stage_labels:
            x = plot.left() + round(plot.width() * ply / self.total_plies)
            label_width = 92 if label == "Миттельшпиль" else 62
            left = max(0, min(self.width() - label_width, x - label_width // 2))
            painter.drawText(left, self.height() - 4, label)

        scored = []
        for row in self.points:
            cp = row.get("white_cp")
            mate = row.get("mate_white")
            if cp is None and mate is None:
                continue
            ply = max(0, min(self.total_plies, int(row["ply"])))
            x = plot.left() + round(plot.width() * ply / self.total_plies)
            clipped = (mate is None and abs(int(cp)) > limit)
            if mate is not None:
                y = plot.top() + 7 if int(mate) > 0 else plot.bottom() - 7
            else:
                score = max(-limit, min(limit, int(cp)))
                y = plot.center().y() - round(plot.height() * score / (2 * limit))
            scored.append((ply, x, y, None if mate is not None else int(cp),
                           None if mate is None else int(mate), clipped))

        painter.save()
        painter.setClipRect(plot.adjusted(1, 1, -1, -1))
        previous = None
        for point in scored:
            ply, x, y, cp, mate, clipped = point
            if (previous is not None and cp is not None and previous[3] is not None
                    and ply == previous[0] + 1):
                color = QColor("#38D887" if (cp + previous[3]) >= 0 else "#ED7483")
                shade = QColor(color)
                shade.setAlpha(42)
                baseline = plot.center().y()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(shade)
                painter.drawPolygon(QPolygonF([
                    QPointF(previous[1], baseline), QPointF(previous[1], previous[2]),
                    QPointF(x, y), QPointF(x, baseline)]))
                painter.setPen(QPen(color, 2))
                painter.drawLine(previous[1], previous[2], x, y)
            if mate is not None:
                color = QColor("#38D887" if mate > 0 else "#ED7483")
                painter.setPen(QPen(color, 2))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, y), 3, 3)
                mate_label = "M+" if mate > 0 else "M−" if mate < 0 else "M0"
                painter.drawText(max(plot.left() + 2, min(plot.right() - 24, x + 3)),
                                 max(plot.top() + 10, min(plot.bottom() - 2, y - 3)),
                                 mate_label)
            elif clipped:
                color = QColor("#38D887" if cp > 0 else "#ED7483")
                triangle_y = plot.top() + 2 if cp > 0 else plot.bottom() - 2
                triangle = QPolygonF([QPointF(x, triangle_y - 4),
                                      QPointF(x - 4, triangle_y + 3),
                                      QPointF(x + 4, triangle_y + 3)])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawPolygon(triangle)
            self._hit_targets.append((x, y, self._white_perspective_label(cp, mate)))
            previous = point
        painter.restore()
        if not scored:
            painter.setPen(QColor("#AEB8D0"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.empty_text)

    def mouseMoveEvent(self, event):
        if not self._hit_targets:
            return
        x, y = event.position().x(), event.position().y()
        point = min(self._hit_targets, key=lambda item: (item[0] - x) ** 2 + (item[1] - y) ** 2)
        if (point[0] - x) ** 2 + (point[1] - y) ** 2 <= 100:
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"Ход {point[0]} · {point[2]}", self)


class PuzzlePreviewBoard(BoardView):
    """Read-only mini board whose click opens the exact saved exercise."""

    open_requested = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: N802 - Qt API override
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PuzzleRatingSparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(112)
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
        axis_low, axis_high, _step = self._rating_axis(ratings)
        plot = self.rect().adjusted(38, 8, -8, -24)
        width, height = max(1, plot.width()), max(1, plot.height())
        return [QPointF(plot.left() + width * (day - days[0]) / max(1, days[-1] - days[0]),
                        plot.bottom() - height * (rating - axis_low) / (axis_high - axis_low))
                for day, rating in zip(days, ratings)]

    @staticmethod
    def _rating_axis(ratings: list[int]) -> tuple[int, int, int]:
        low, high = min(ratings), max(ratings)
        span = max(100, high - low)
        step = max(50, math.ceil(span / 4 / 50) * 50)
        axis_low = (low // step) * step - step
        axis_high = ((high + step - 1) // step) * step + step
        return axis_low, axis_high, step

    def paintEvent(self, _event):
        points = self._vertices()
        if not points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self.rect().adjusted(38, 8, -8, -24)
        ratings = [int(point["rating"]) for point in self.points]
        axis_low, axis_high, step = self._rating_axis(ratings)
        painter.setPen(QPen(QColor("#354058"), 1))
        painter.drawRect(plot)
        for rating in range(axis_low, axis_high + 1, step):
            fraction = (rating - axis_low) / (axis_high - axis_low)
            y = plot.bottom() - round(plot.height() * fraction)
            painter.setPen(QPen(QColor("#354058"), 1))
            painter.drawLine(plot.left(), y, plot.right(), y)
            painter.setPen(QColor("#AEB8D0"))
            painter.drawText(0, y + 4, str(rating))
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(points[-1].x(), plot.bottom())
        area.lineTo(points[0].x(), plot.bottom())
        area.closeSubpath()
        painter.fillPath(area, QColor(92, 226, 130, 40))
        painter.setPen(QPen(QColor("#79E599"), 2))
        painter.drawPath(line)
        painter.setBrush(QColor("#79E599"))
        painter.drawEllipse(points[-1], 3, 3)
        dates = [date.fromisoformat(point["day"]) for point in self.points]
        span_days = max(1, (dates[-1] - dates[0]).days)
        painter.setPen(QColor("#AEB8D0"))
        last_is_today = dates[-1] == date.today()
        for fraction in (0, 1 / 3, 2 / 3, 1):
            elapsed = round(span_days * fraction)
            current = dates[0] + (dates[-1] - dates[0]) * fraction
            label = ("сегодня" if fraction == 1 and last_is_today else
                     current.strftime("%d.%m") if fraction == 1 else
                     f"−{span_days - elapsed} дн.")
            x = plot.left() + round(plot.width() * elapsed / span_days)
            painter.drawText(max(0, min(self.width() - 48, x - 24)), self.height() - 4, label)

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
        self.home_status.hide()
        layout.addWidget(self.home_status)
        # The status row is hidden on Home; retain the baseline rhythm gap.
        layout.addSpacing(2)

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
        # Establish the saved account scope before the first Home query. The
        # selector options may still be loading; select_profile inserts a
        # scoped placeholder instead of briefly querying "all accounts".
        self.select_profile(self._preferred_account, notify=False)
        self._preferred_clock = ""  # the historical key remains stored but is no longer applied on Home

        self.dashboard_metrics = {}
        metrics_grid = QGridLayout()
        self.dashboard_metrics_grid = metrics_grid
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setHorizontalSpacing(10)
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
            caption.setStyleSheet("font-size: 16px; font-weight: 600;")
            caption.setWordWrap(True)
            caption_row = QHBoxLayout()
            if key in {"games", "accuracy"}:
                icon = QLabel()
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                icon.setFixedSize(42, 42)
                icon.setPixmap(icon_pixmap("pawn" if key == "games" else "target",
                                           25, "#D8D0FF", icon.devicePixelRatioF()))
                icon.setStyleSheet("background: #39365E; color: #C8BBFF;"
                                   "border-radius: 10px; font-size: 25px;")
                caption_row.addWidget(icon)
            caption_row.addWidget(caption, 1)
            value = QLabel("—")
            value.setStyleSheet("font-size: 54px; font-weight: 700;")
            detail = QLabel("Локальные данные")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            detail.setStyleSheet("font-size: 12px;")
            card_layout.addLayout(caption_row)
            card_layout.addWidget(value)
            card_layout.addWidget(detail)
            self._dashboard_metric_values[key] = value
            self._dashboard_metric_details[key] = detail
            self.dashboard_metrics[key] = card
        # Error-rate detail belongs in Progress; retain the existing read-model
        # field for compatibility without giving it KPI prominence on Home.
        self.dashboard_metrics["errors"].hide()
        for key in ("games", "accuracy"):
            # Leave enough vertical room for the wrapped, source-qualified
            # summary even when Windows font metrics round a line upward.
            self.dashboard_metrics[key].setMinimumHeight(186)
            self.dashboard_metrics[key].setMaximumHeight(198)

        top_row = QHBoxLayout()
        self.home_primary_actions = top_row
        top_row.setSpacing(9)
        self.home_progress_card = QFrame(self)
        self.home_progress_card.setObjectName("metricCard")
        # Title, rating, trend graph, wrapped trend label and their margins
        # need roughly 200 logical px; a smaller minimum makes the graph and
        # trend overlap at compact Windows heights.
        self.home_progress_card.setMinimumHeight(262)
        self.home_progress_card.setMaximumHeight(310)
        progress_layout = QVBoxLayout(self.home_progress_card)
        progress_layout.setContentsMargins(11, 8, 11, 4)
        progress_layout.setSpacing(2)
        progress_title = QLabel("Рейтинг задач")
        progress_title.setStyleSheet("font-size: 19px; font-weight: 600;")
        progress_title.setWordWrap(True)
        self.home_all_progress_button = QPushButton("Весь прогресс")
        self.home_all_progress_button.setObjectName("primaryAction")
        self.home_all_progress_button.setMaximumHeight(32)
        self.home_all_progress_button.clicked.connect(actions["show_statistics"])
        progress_heading = QHBoxLayout()
        progress_heading.addWidget(progress_title, 1)
        progress_heading.addWidget(self.home_all_progress_button)
        progress_layout.addLayout(progress_heading)
        self.puzzle_rating = QLabel("—")
        self.puzzle_rating.setStyleSheet("font-size: 48px; font-weight: 700;")
        progress_layout.addWidget(self.puzzle_rating)
        self.puzzle_sparkline = PuzzleRatingSparkline(self.home_progress_card)
        self.puzzle_sparkline.setObjectName("homePuzzleRatingGraph")
        self.puzzle_sparkline.setMinimumHeight(112)
        self.puzzle_sparkline.setMaximumHeight(132)
        self.puzzle_sparkline.setMinimumWidth(0)
        self.puzzle_sparkline.setSizePolicy(QSizePolicy.Policy.Ignored,
                                            QSizePolicy.Policy.Preferred)
        progress_layout.addWidget(self.puzzle_sparkline)
        self.progress_trend = QLabel("Нет сохранённых точек тренда.")
        self.progress_trend.setObjectName("secondary")
        self.progress_trend.setWordWrap(True)
        self.progress_trend.setMinimumHeight(22)
        self.progress_trend.setSizePolicy(QSizePolicy.Policy.Preferred,
                                          QSizePolicy.Policy.Minimum)
        progress_layout.addWidget(self.progress_trend)
        self.dashboard_metrics["games"].layout().addWidget(
            self._dashboard_metric_values["analysis"])
        self._dashboard_metric_values["analysis"].setStyleSheet("font-size: 14px;")
        self.dashboard_metrics["games"].layout().addWidget(
            self._dashboard_metric_details["analysis"])
        self._dashboard_metric_values["analysis"].hide()
        self._dashboard_metric_details["analysis"].hide()
        self.dashboard_metrics["analysis"].hide()
        self.layout_metrics()

        self.home_puzzle_card = QFrame(self)
        self.home_play_card = self.home_puzzle_card  # compatibility alias for shell hooks
        self.home_puzzle_card.setObjectName("metricCard")
        self.home_puzzle_card.setMinimumHeight(250)
        self.home_puzzle_card.setMaximumHeight(292)
        puzzle_layout = QVBoxLayout(self.home_puzzle_card)
        puzzle_layout.setContentsMargins(11, 8, 11, 8)
        puzzle_layout.setSpacing(6)
        puzzle_heading = QHBoxLayout()
        puzzle_heading.setSpacing(8)
        puzzle_icon = QLabel()
        puzzle_icon.setFixedSize(34, 34)
        puzzle_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        puzzle_icon.setPixmap(icon_pixmap("target", 21, "#D8D0FF",
                                          puzzle_icon.devicePixelRatio()))
        puzzle_icon.setStyleSheet("background:#39365E;border-radius:9px;")
        puzzle_heading.addWidget(puzzle_icon)
        activity_title = QLabel("Следующая задача")
        activity_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        puzzle_heading.addWidget(activity_title, 1)
        puzzle_layout.addLayout(puzzle_heading)
        puzzle_content = QHBoxLayout()
        puzzle_content.setSpacing(12)
        self.puzzle_preview_board = PuzzlePreviewBoard(chess.Board(), self.home_puzzle_card)
        self.puzzle_preview_board.setObjectName("homePuzzleBoard")
        self.puzzle_preview_board.setMinimumSize(112, 112)
        self.puzzle_preview_board.setMaximumSize(210, 210)
        self.puzzle_preview_board.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                QSizePolicy.Policy.Preferred)
        self.puzzle_preview_board.set_interaction_enabled(False)
        self.puzzle_preview_board.set_show_legal_moves(False)
        self.puzzle_preview_board.set_show_last_move(False)
        self.puzzle_preview_board.setAccessibleName("Открыть сохранённую задачу")
        self.puzzle_preview_board.hide()
        puzzle_content.addWidget(self.puzzle_preview_board, 0, Qt.AlignmentFlag.AlignVCenter)
        puzzle_details = QVBoxLayout()
        puzzle_details.setSpacing(7)
        self.activity_status = QLabel("Пока нет доступной задачи.")
        self.activity_status.setObjectName("secondary")
        self.activity_status.setWordWrap(True)
        self.activity_status.setMinimumHeight(42)
        puzzle_details.addWidget(self.activity_status)
        self.puzzle_side_to_move = QLabel("")
        self.puzzle_side_to_move.setObjectName("secondary")
        self.puzzle_side_to_move.setWordWrap(True)
        puzzle_details.addWidget(self.puzzle_side_to_move)
        self.puzzle_meta = QLabel("")
        self.puzzle_meta.setObjectName("muted")
        self.puzzle_meta.setWordWrap(True)
        puzzle_details.addWidget(self.puzzle_meta)
        puzzle_details.addStretch(1)
        self.home_activity_button = QPushButton("Открыть обучение")
        self.home_activity_button.setObjectName("primaryAction")
        self.home_activity_button.setMinimumHeight(40)
        self.home_activity_button.clicked.connect(self._open_puzzle_preview)
        puzzle_details.addWidget(self.home_activity_button)
        puzzle_content.addLayout(puzzle_details, 1)
        puzzle_layout.addLayout(puzzle_content, 1)
        self._puzzle_open_action = actions.get("open_saved_puzzle")
        self._puzzle_history_action = actions["open_puzzle_history_local"]
        self._puzzle_preview = None
        self.puzzle_preview_board.open_requested.connect(self._open_puzzle_preview)
        top_row.addWidget(self.home_puzzle_card, 1)

        recent_card = QFrame(self)
        self.home_recent_card = recent_card
        recent_card.setObjectName("recentGames")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(11, 8, 11, 8)
        recent_layout.setSpacing(8)
        recent_title_row = QHBoxLayout()
        recent_icon = QLabel()
        recent_icon.setFixedSize(30, 30)
        recent_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_icon.setPixmap(icon_pixmap("clock", 22, "#E5E1FF",
                                          recent_icon.devicePixelRatio()))
        recent_title_row.addWidget(recent_icon)
        recent_title = QLabel("Последние партии")
        recent_title.setStyleSheet("font-size: 24px; font-weight: 700;")
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
        self._recent_games_layout.setSpacing(6)
        self.recent_scroll = QScrollArea()
        self.recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_scroll.setWidget(recent_rows)
        recent_layout.addWidget(self.recent_scroll, 1)
        # Keep progress and the next-lesson action in the center zone and the
        # three compact recent-game cards visible together in the right zone.
        # This follows the approved Home layout and avoids a page-level scroll.
        overview = QWidget(self)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(8)
        overview_layout.addLayout(metrics_grid)
        overview_layout.addWidget(self.home_progress_card, 1)
        overview_layout.addLayout(top_row)
        overview_scroll = QScrollArea(self)
        self.home_overview_scroll = overview_scroll
        overview_scroll.setObjectName("homeOverviewScroll")
        overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        overview_scroll.setWidget(overview)
        zones = QHBoxLayout()
        zones.setContentsMargins(0, 0, 0, 0)
        zones.setSpacing(12)
        zones.addWidget(overview_scroll, 38)
        zones.addWidget(recent_card, 62)
        layout.addLayout(zones, 1)

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
        selected = self.dashboard_account.currentData()
        self.dashboard_account.blockSignals(True)
        self.dashboard_account.clear()
        self.dashboard_account.addItem("Все локальные партии", None)
        for item in options.get("accounts", []):
            self.dashboard_account.addItem(str(item["username"]), str(item["account_id"]))
        if selected is not None and self.dashboard_account.findData(selected) >= 0:
            self.select_profile(str(selected), notify=False)
        else:
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
        cards = [self.dashboard_metrics["games"], self.dashboard_metrics["accuracy"]]
        if not cards:
            return
        available = int(width if width is not None else self.width())
        columns = 2 if available >= 620 else 1
        if getattr(self, "_dashboard_metric_columns", None) == columns:
            return
        self._dashboard_metric_columns = columns
        while grid.count():
            grid.takeAt(0)
        for column in range(2):
            grid.setColumnStretch(
                column, int(column < columns))
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)

    def set_puzzle_preview(self, preview: dict | None, *, loading: bool = False) -> None:
        self._puzzle_preview = preview if isinstance(preview, dict) else None
        if self._puzzle_preview:
            try:
                board = chess.Board(str(preview["fen"]))
                if not board.is_valid() or board.turn != bool(preview["solver_is_white"]):
                    raise ValueError("invalid saved puzzle position")
            except (KeyError, TypeError, ValueError):
                self._puzzle_preview = None
        if self._puzzle_preview:
            board = chess.Board(str(self._puzzle_preview["fen"]))
            self.puzzle_preview_board.set_position(board)
            self.puzzle_preview_board.set_flipped(board.turn == chess.BLACK)
            self.puzzle_preview_board.setAccessibleDescription(
                f"Сохранённая задача {self._puzzle_preview['puzzle_id']}; "
                f"ход за {'белых' if board.turn == chess.WHITE else 'чёрных'}. Нажмите Enter.")
            self.puzzle_preview_board.setToolTip("Открыть и решить эту сохранённую задачу")
            self.puzzle_preview_board.show()
            self.activity_status.setText(f"Задача Lichess · {self._puzzle_preview['puzzle_id']}")
            self.puzzle_side_to_move.setText(
                "Найдите лучший ход за белых" if board.turn == chess.WHITE
                else "Найдите лучший ход за чёрных")
            rating = self._puzzle_preview.get("rating")
            themes = self._puzzle_preview.get("themes") or []
            meta = []
            if rating is not None:
                meta.append(f"Рейтинг задачи: {rating}")
            if themes:
                meta.append("Темы: " + ", ".join(str(theme) for theme in themes[:3]))
            self.puzzle_meta.setText(" · ".join(meta))
            self.home_activity_button.setText("Решать задачу")
            self.home_activity_button.setToolTip("Открыть и решить именно эту сохранённую задачу")
        else:
            self.puzzle_preview_board.hide()
            self.puzzle_side_to_move.clear()
            self.puzzle_meta.clear()
            self.activity_status.setText(
                "Загружаю сохранённые задачи…" if loading else
                "Пока нет доступной задачи. Можно открыть историю обучения.")
            self.home_activity_button.setText("Открыть обучение")
            self.home_activity_button.setToolTip("Открыть локальную историю задач")

    def _open_puzzle_preview(self) -> None:
        if self._puzzle_preview and self._puzzle_open_action:
            self._puzzle_open_action(str(self._puzzle_preview["account_id"]),
                                     str(self._puzzle_preview["puzzle_id"]))
            return
        self._puzzle_history_action()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.layout_metrics(event.size().width())
        if hasattr(self, "puzzle_preview_board"):
            content_width = max(0, self.home_puzzle_card.contentsRect().width() - 34)
            min_side = min(210, max(112, round(content_width * .40)))
            max_side = min(210, max(min_side, round(content_width * .43)))
            self.puzzle_preview_board.setMinimumSize(min_side, min_side)
            self.puzzle_preview_board.setMaximumSize(max_side, max_side)
