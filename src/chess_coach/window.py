"""Main M0 window and settings dialog."""

from __future__ import annotations

import chess
import chess.pgn
import json
import io
import os
import time
import re
import threading
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
import weakref
import PySide6
import shiboken6
from PySide6.QtCore import QEvent, QProcess, QSettings, Qt, QTimer, Signal, qVersion, QUrl
from PySide6.QtGui import (QKeySequence, QShortcut, QColor, QPainter, QPen,
                           QDesktopServices, QGuiApplication, QFontMetrics)
from PySide6.QtWidgets import (
    QCheckBox,
    QButtonGroup,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
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
    QRadioButton,
    QTabWidget,
    QSpinBox,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .board_view import BoardView
from .controller import GameController
from .engine_manager import discover_stockfish, install_official_stockfish
from .uci import popen_uci_hidden
from .position_analysis import evaluate_position
from .lichess.auth import CredentialStorageError
from .lichess.analysis import AnalysisOptions, CLASSIFIER_VERSION, CompletedGameAnalyzer
from .lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from .lichess.repository import GameRepository, SCHEMA_VERSION
from .lichess.service import LichessService
from .bootstrap import ApplicationRuntime, create_runtime
from .application.app_state import ReviewResumeTarget, WorkspaceSnapshot, WorkspaceStateStore
from .application.game_sessions import GameSessionCoordinator
from .application.game_finalization import GameFinalizationCoordinator, FinalizationPhase
from .application.routes import WorkspaceNavigator, WorkspaceRoute
from .application.background_work import OperationLane
from .application.fair_play import playing_snapshot_allows_analysis
from .application.archive_sync import ArchiveSyncCoordinator
from .application.library import GameLibraryQueryCoordinator
from .application.dashboard import (DashboardQueryCoordinator, dashboard_options,
                                    dashboard_snapshot)
from .lichess.session import LichessGameSession, classify_lichess_opponent
from .lichess.game_review import load_game_review
from .lichess.statistics import StatisticsService
from .diagnostics import anonymized_report, log_event, logs_directory
from .ui.theme import TOKENS, stylesheet as desktop_stylesheet
from .ui.dashboard import game_card_text
from .ui.game_review import GameReviewWorkspace
from .ui.move_table import MoveTable
from .ui.sounds import (
    DEFAULT_SOUND_SELECTIONS,
    SOUND_CATALOG,
    ChessSoundPlayer,
    selected_sound_ids,
)
from .ui.workspace_state import reposition_visible_frame
from .ui.pages import PlayPage, ResultPage
from .ui.pages.home import HomePage
from .ui.pages.library import LibraryPage
from .ui.pages.puzzles import PuzzleHistoryPage
from .ui.pages.analysis import AnalysisLandingPage
from .ui.pages.background import BackgroundOperationsPage
from .ui.dialogs import (OnlineGameDialog, BatchAnalysisDialog, ActiveGamesDialog,
                        LichessConnectionDialog, SettingsDialog)
from .ui.analytics_dialogs import (EvaluationGraph, RatingTrendGraph,
                                   StatisticsGameDialog, StatisticsDialog)
from .ui.dialog_support import (_clock_filter_sort_key, _game_date_label, _result_label)
from .ui.display import (format_clock_control, format_game_date,
                         format_game_timeline,
                         format_remaining_clock, opening_tooltip, russian_opening_name,
                         source_short)


class EmbeddedStatisticsPage(StatisticsDialog):
    """Existing statistics read model embedded in the main workspace."""

    close_requested = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.metric_migration_status = QLabel("Метрики сохранённых анализов: состояние не загружено.")
        self.metric_migration_status.setWordWrap(True)
        self.metric_migration_status.setObjectName("secondary")
        self.metric_migration_status.setAccessibleName("Фоновое обновление метрик")
        self.layout().insertWidget(0, self.metric_migration_status)
        self._metric_migration_text = ""

    def set_metric_migration_status(self, text: str, *, safety_allowed: bool) -> None:
        self._metric_migration_text = str(text)
        self.metric_migration_status.setText(
            self._metric_migration_text if safety_allowed else
            "Статус обновления аналитики скрыт до безопасной проверки Lichess.")

    def accept(self) -> None:
        self.close_requested.emit()

    def reject(self) -> None:
        self.close_requested.emit()


class EmbeddedPuzzleHistoryPage(QWidget):
    """Compatibility host for the existing puzzle widget inside the shell."""

    close_requested = Signal()

    def accept(self) -> None:
        self.close_requested.emit()

    def reject(self) -> None:
        self.close_requested.emit()


class EmbeddedBatchAnalysisPage(BatchAnalysisDialog):
    """Existing durable queue controls embedded in the Analysis workspace."""

    close_requested = Signal()

    def accept(self) -> None:
        self.close_requested.emit()

    def reject(self) -> None:
        self.close_requested.emit()


class EphemeralReviewRepository:
    """Read-only in-memory adapter for external PGN/FEN; never touches SQLite."""

    def __init__(self, game_id: str, initial_fen: str, moves: str) -> None:
        self.game_id = str(game_id)
        self._line = {"game_id": self.game_id, "line_revision": 1,
                      "line_hash": "ephemeral", "initial_fen": initial_fen,
                      "moves": moves}

    def current_line(self, game_id: str) -> dict | None:
        return dict(self._line) if str(game_id) == self.game_id else None

    def latest_analysis_detail(self, *_args, **_kwargs) -> None:
        return None

    def lichess_analysis_backfill_state(self, *_args, **_kwargs) -> None:
        return None

    def list_notes(self, *_args, **_kwargs) -> list[dict]:
        return []


_MAX_EXTERNAL_STUDY_BYTES = 4 * 1024 * 1024


def _parse_external_study_text(value: str) -> dict:
    """Parse one standard PGN or a FEN away from the Qt thread."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("Пустой ввод.")
    if len(value.encode("utf-8")) > _MAX_EXTERNAL_STUDY_BYTES:
        raise ValueError("Файл/текст превышает безопасный лимит 4 МиБ.")

    try:
        fen_board = chess.Board(value)
    except ValueError:
        fen_board = None
    if fen_board is not None and fen_board.is_valid():
        return {"initial_fen": fen_board.fen(), "moves": [], "headers": {}}

    stream = io.StringIO(value)
    game = chess.pgn.read_game(stream)
    if game is None:
        raise ValueError("Введите одну корректную стандартную PGN-партию или FEN.")
    if game.errors:
        raise ValueError("PGN содержит некорректные ходы или заголовки.")
    if chess.pgn.read_game(stream) is not None:
        raise ValueError("Пока принимается одна партия за раз.")
    headers = dict(game.headers)
    if str(headers.get("Variant", "Standard")).casefold() not in {"standard", "chess"}:
        raise ValueError("Поддерживается только стандартный шахматный PGN.")
    board = game.board()
    if not isinstance(board, chess.Board) or not board.is_valid():
        raise ValueError("Начальная позиция PGN некорректна.")
    initial_fen = board.fen()
    moves: list[str] = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("PGN содержит нелегальный ход.")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise ValueError("PGN не содержит ходов.")
    return {
        "initial_fen": "startpos" if initial_fen == chess.STARTING_FEN else initial_fen,
        "moves": moves,
        "headers": headers,
    }


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


_PUZZLE_THEME_LABELS = {
    "mateIn1": "мат в 1", "mateIn2": "мат в 2", "mateIn3": "мат в 3",
    "fork": "вилка", "pin": "связка", "skewer": "рентген", "hangingPiece": "незащищённая фигура",
    "discoveredAttack": "вскрытое нападение", "defensiveMove": "защитный ход",
    "sacrifice": "жертва", "endgame": "эндшпиль", "opening": "дебют",
    "middlegame": "миттельшпиль", "promotion": "превращение пешки",
}


def _puzzle_themes_label(value: object) -> str:
    try:
        themes = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        themes = []
    if not isinstance(themes, list):
        return "нет данных"
    return ", ".join(_PUZZLE_THEME_LABELS.get(str(theme), str(theme)) for theme in themes) or "нет данных"


def _clock_increment_label(value_ms: object) -> str:
    """Format Lichess clock increment; game events carry milliseconds."""
    if type(value_ms) is not int or value_ms <= 0:
        return ""
    seconds = value_ms / 1000
    shown = (str(int(seconds)) if seconds.is_integer()
             else f"{seconds:.1f}".rstrip("0").rstrip("."))
    return f"+{shown} с после подтверждённого хода"






def _archive_row_presentation(game: dict) -> tuple[str, str]:
    statuses = {
        "mate": "Мат", "resign": "Сдался", "draw": "Ничья",
        "stalemate": "Пат", "timeout": "Время вышло", "outoftime": "Время вышло",
        "aborted": "Прервана", "noStart": "Не началась", "cheat": "Завершена сервером",
        "variantEnd": "Завершена", "unknownFinish": "Завершена", "finished": "Завершена",
    }
    status = statuses.get(str(game.get("status")), "Завершена")
    source = str(game.get("source") or "")
    source_label = ("Локальная" if source == "local" else
                    "Lichess AI" if game.get("opponent_type") == "official_ai" else "Lichess")
    date_label = format_game_timeline(game)
    opponent = game.get("account_opponent") or game.get("opponent") or "Соперник неизвестен"
    text = (f"{date_label} · {opponent} · {status} · {source_label}\n"
            f"{game.get('white_player') or 'Белые'} — {game.get('black_player') or 'Чёрные'}")
    if game.get("import_error") or game.get("variant", "standard") != "standard":
        text += f" · просмотр недоступен ({game.get('variant') or 'вариант'})"
    return text, source_label


class _DashboardGameCard(QFrame):
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def watch_children(self):
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.activated.emit()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


















class MainWindow(QMainWindow):
    analysis_progress = Signal(str, int, int)
    analysis_finished = Signal(str, object)
    engine_install_progress = Signal(str)
    engine_install_finished = Signal(object)
    position_eval_finished = Signal(int, object, object)
    position_eval_update = Signal(int, object)
    dashboard_finished = Signal(int, object, object, object)
    library_query_finished = Signal(int, object, object)
    library_target_finished = Signal(int, str, object, object)
    library_detail_finished = Signal(int, str, object, object)
    note_save_finished = Signal(str, str, object)
    finalization_probe_finished = Signal(str, str, object)
    archive_account_ready_finished = Signal(int, str, object, object)
    external_study_finished = Signal(int, object, object)
    background_status_finished = Signal(int, object)
    legacy_metrics_finished = Signal(int, object)
    puzzle_history_finished = Signal(str, object, object)
    statistics_filter_options_finished = Signal(int, object, object)

    @property
    def _selected_live_game_id(self) -> str | None:
        """Compatibility access to selection now owned by GameSessionCoordinator."""
        coordinator = getattr(self, "game_sessions", None)
        return coordinator.selected_game_id if coordinator is not None else None

    @_selected_live_game_id.setter
    def _selected_live_game_id(self, game_id: str | None) -> None:
        coordinator = getattr(self, "game_sessions", None)
        if coordinator is None:
            return
        if game_id is None:
            coordinator.clear_selection()
        else:
            coordinator.select(game_id)

    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        asynchronous_storage: bool = False,
        offline_mode: bool = False,
        manual_connect_mode: bool = False,
        runtime: ApplicationRuntime | None = None,
    ) -> None:
        super().__init__()
        if offline_mode and manual_connect_mode:
            raise ValueError("--offline и --manual-connect нельзя включить одновременно")
        if settings is not None and runtime is not None and settings is not runtime.settings:
            raise ValueError("MainWindow settings must be owned by the injected runtime")
        if runtime is not None and (offline_mode or manual_connect_mode) and (
                offline_mode != runtime.offline_mode
                or manual_connect_mode != runtime.manual_connect_mode):
            raise ValueError("MainWindow mode conflicts with the injected runtime")
        self._connection_dialog: LichessConnectionDialog | None = None
        self.runtime = runtime or create_runtime(
            settings=settings, offline_mode=offline_mode,
            manual_connect_mode=manual_connect_mode,
            asynchronous_storage=asynchronous_storage)
        self.stockfish_manager = self.runtime.stockfish_manager
        self.engine_analysis = self.runtime.engine_analysis
        self._offline_data_dir = self.runtime.offline_data_dir
        self.settings = self.runtime.settings
        self.workspace_state = WorkspaceStateStore(self.settings)
        self.offline_mode = self.runtime.offline_mode
        self.manual_connect_mode = self.runtime.manual_connect_mode
        # Cached engine-derived dashboard data is hidden until this process has
        # a fresh account-wide Lichess safety result. Isolated offline mode is
        # already protected from live play and network access.
        self._dashboard_analysis_safety_allowed = bool(self.offline_mode)
        self._dashboard_display_safety_request_id: str | None = None
        self._dashboard_display_safety_context: dict | None = None
        self._dashboard_safety_retry_timer = QTimer(self)
        self._dashboard_safety_retry_timer.setSingleShot(True)
        self._dashboard_safety_retry_timer.timeout.connect(self._retry_dashboard_display_safety)
        self._dashboard_safety_retry_due = False
        self._latest_dashboard_snapshot: dict | None = None
        selected_auth_method = self.settings.value("lichess/selected_auth_method", "oauth", type=str)
        if selected_auth_method not in {"oauth", "personal_token"}:
            selected_auth_method = "oauth"
            self.settings.setValue("lichess/selected_auth_method", selected_auth_method)
        color = self.settings.value("player_color", chess.WHITE, type=int)
        self.controller = GameController(chess.Color(color))
        self.game_repository = self.runtime.repository
        self._ephemeral_study = False
        self.archive_sync = ArchiveSyncCoordinator(self.game_repository)
        self.dashboard_queries = DashboardQueryCoordinator(self.game_repository)
        self._dashboard_options_loaded = False
        self._home_detail_game_id: str | None = None
        self._home_detail_payload: dict | None = None
        self._home_detail_lookup_pending = False
        self._library_detail_target_id: str | None = None
        self._ready_analytics_failure_status = ""
        self._library_filter_options_loaded = False
        self.library_queries = GameLibraryQueryCoordinator(self.game_repository)
        self._library_query_generation = 0
        self._library_query_handle = None
        self._library_detail_generation = 0
        self._library_detail_handle = None
        self.sound_player = self.runtime.create_sound_player(self)
        self._online_history_game_id: str | None = None
        self._online_history_moves: tuple[chess.Move, ...] = ()
        self._online_history_position: chess.Board | None = None
        self.lichess = self.runtime.create_lichess_service(self)
        self.game_sessions: GameSessionCoordinator[LichessGameSession] = GameSessionCoordinator()
        self.game_finalization = GameFinalizationCoordinator()
        self.workspace_navigator = WorkspaceNavigator()
        self._finalization_timers: dict[str, QTimer] = {}
        self.online_sessions = self.game_sessions.sessions
        self.online_session: LichessGameSession | None = None
        self._local_board_mode = "free"
        self._local_sound_game_id = uuid.uuid4().hex
        self._orientation_overrides: dict[str, bool] = {}
        self.online_view_ply: int | None = None
        self._screen_mode = "home"
        self._awaiting_game_start = False
        self._selected_live_game_id: str | None = None
        self._review_session: LichessGameSession | None = None
        self._lichess_connected = False
        self._lichess_account_authenticated = False
        self._lichess_profile_username = ""
        self._event_stream_recovery_pending = False
        self._lichess_session_selected = False
        self._lichess_stream_ready_generation: int | None = None
        self._active_games_request_id: str | None = None
        self._active_games_request_generation: int | None = None
        self._open_challenge_generation: int | None = None
        self._pending_game_actions: set[tuple[str, str]] = set()
        self._backfill_wakeup_identity: tuple[int, str] | None = None
        self._backfill_deferred_for_human_game = False
        self._backfill_wakeup_timer = QTimer(self)
        self._backfill_wakeup_timer.setSingleShot(True)
        self._backfill_wakeup_timer.timeout.connect(self._lichess_backfill_wakeup)
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
        self._statistics_options_generation = 0
        self._statistics_options_handle = None
        self._statistics_hub_connected = False
        self._dashboard_thread: threading.Thread | None = None
        self._position_eval_generation = 0
        self._position_eval_thread: threading.Thread | None = None
        self._position_eval_stop: threading.Event | None = None
        self._position_eval_handle: dict = {}
        self._position_eval_mode = False
        self._position_eval_queued = False
        self._position_eval_preflight_id: str | None = None
        self._position_eval_preflight_context: tuple | None = None
        self._position_eval_safety_lease: tuple | None = None
        self._position_eval_timer = QTimer(self)
        self._position_eval_timer.setSingleShot(True)
        self._position_eval_timer.setInterval(300)
        self._position_eval_timer.timeout.connect(self._start_position_evaluation)
        self._analysis_dialog: QProgressDialog | None = None
        self._analysis_safety_request_id: str | None = None
        self._analysis_safety_context: tuple | None = None
        self._analysis_safety_phase: str | None = None
        self._analysis_worker_context: tuple | None = None
        self._review_safety_request_id: str | None = None
        self._review_safety_context: tuple | None = None
        self._analysis_session_generation = 0
        self._engine_probe_processes: set[QProcess] = set()
        self._engine_install_stop: threading.Event | None = None
        self._engine_install_thread: threading.Thread | None = None
        self._engine_install_dialog: QProgressDialog | None = None
        self._shutdown_requested = False
        self._shutdown_read_only = False
        self._analysis_shutdown_deadline: float | None = None
        self._workspace_state_restoring = True
        self._active_route = self.workspace_state.last_route()
        self._board_focus_mode = self.settings.value("workspace/board_focus_mode", False, type=bool)
        self._workspace_save_timer = QTimer(self)
        self._workspace_save_timer.setSingleShot(True)
        self._workspace_save_timer.setInterval(500)
        self._workspace_save_timer.timeout.connect(self._persist_workspace_state)
        self._auto_sync_timer = QTimer(self)
        self._auto_sync_timer.setInterval(10 * 60 * 1000)
        self._auto_sync_timer.timeout.connect(self._run_background_sync)
        self._legacy_metrics_timer = QTimer(self)
        self._legacy_metrics_timer.setSingleShot(True)
        self._legacy_metrics_timer.setInterval(500)
        self._legacy_metrics_timer.timeout.connect(self._start_legacy_metrics_batch)
        self._legacy_metrics_generation = 0
        self._legacy_metrics_scope: str | None = None
        self._legacy_metrics_handle = None
        self._legacy_metrics_stop: threading.Event | None = None
        self._legacy_metrics_text = (
            "Локальные метрики сохранённых анализов обновляются фоновыми партиями; "
            "движок и сеть для этого не запускаются.")
        self._legacy_metrics_complete_revision: int | None = None
        self._puzzle_history_callbacks: dict[str, object] = {}
        self._note_save_requests: dict[str, tuple[str, str, object]] = {}
        self._finalization_probe_generation: dict[str, str] = {}
        self._finalization_probe_handles: dict[str, object] = {}
        self._confirmed_terminal_games: dict[str, dict] = {}
        self._archive_account_ready_generation: int | None = None
        self._account_background_started_generation: int | None = None
        self._archive_account_handle = None
        self._puzzle_accounts_generation = 0
        self._puzzle_accounts_handle = None
        self.analysis_progress.connect(self._analysis_progress)
        self.analysis_finished.connect(self._analysis_finished)
        self.engine_install_progress.connect(self._engine_install_progress_changed)
        self.engine_install_finished.connect(self._engine_install_finished)
        self.position_eval_finished.connect(self._position_eval_finished)
        self.position_eval_update.connect(self._position_eval_update)
        self.dashboard_finished.connect(self._dashboard_result_ready)
        self.library_query_finished.connect(self._library_query_result_ready)
        self.library_target_finished.connect(self._library_target_result_ready)
        self.library_detail_finished.connect(self._library_detail_result_ready)
        self.note_save_finished.connect(self._note_save_result_ready)
        self.finalization_probe_finished.connect(self._finalization_probe_result_ready)
        self.archive_account_ready_finished.connect(self._archive_account_ready_result)
        self.external_study_finished.connect(self._external_study_parse_finished)
        self.background_status_finished.connect(self._background_operations_result_ready)
        self.legacy_metrics_finished.connect(self._legacy_metrics_batch_finished)
        self.puzzle_history_finished.connect(self._puzzle_history_result_ready)
        self.statistics_filter_options_finished.connect(
            self._statistics_filter_options_ready)
        self._external_study_generation = 0
        self._background_status_generation = 0
        self._background_status_handle = None
        self.setWindowTitle("Chess Coach")
        self.setMinimumSize(500, 360)
        self.resize(1360, 860)
        self._restore_window_frame()
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
        self.focus_mode_button.setChecked(self._board_focus_mode)
        self._set_sidebar_collapsed(
            self.settings.value("workspace/sidebar_collapsed", self.width() < 1160, type=bool),
            explicit=self.settings.value("workspace/sidebar_explicit", False, type=bool))
        self._workspace_state_restoring = False
        self.show_home()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(250)
        self._close_retry_timer = QTimer(self)
        self._close_retry_timer.setSingleShot(True)
        self._close_retry_timer.setInterval(200)
        self._close_retry_timer.timeout.connect(self._retry_close_after_workers)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.cancel_premove)
        if self.lichess:
            self._attach_lichess_service(self.lichess)
            self.lichess.restore()
        elif self.manual_connect_mode:
            self._set_connection_header(
                "Ручной",
                "Подключение и сетевые запросы начнутся только после нажатия кнопки в приложении.")
            self.connect_button.setText("Подключиться вручную")
            self.connect_button.setToolTip(
                "Профиль и локальные партии доступны. Выбор OAuth/PAT и сетевые запросы "
                "к Lichess начнутся только после нажатия этой кнопки."
            )
        elif self.offline_mode:
            self._disable_online_controls()
        self.refresh()
        if self.offline_mode:
            # Let the shell and local-only views settle before starting the
            # low-priority read/derive/write worker.
            self._legacy_metrics_timer.start(1500)
        if not self.offline_mode:
            QTimer.singleShot(75, self._restore_workspace_after_startup)

    def _create_lichess_service(self):
        """Compatibility hook; new assembly is owned by ApplicationRuntime."""
        return self.runtime.create_lichess_service(self, user_requested=True)

    def _attach_lichess_service(self, service) -> None:
        service.session_status_changed.connect(self._lichess_session_status)
        if hasattr(service, "background_operation_status"):
            service.background_operation_status.connect(
                self._lichess_background_operation_status)
        service.session_authenticated.connect(self._lichess_session_authenticated)
        service.session_event_stream_ready.connect(self._lichess_event_stream_ready)
        service.session_error.connect(self._lichess_session_error)
        if hasattr(service, "session_move_failed"):
            service.session_move_failed.connect(self._lichess_session_move_failed)
        else:
            service.move_failed.connect(self._lichess_move_failed)
        if hasattr(service, "session_takeback_action_completed"):
            service.session_takeback_action_completed.connect(
                self._lichess_session_takeback_completed)
        else:
            service.takeback_action_completed.connect(self._lichess_takeback_completed)
        service.session_event_received.connect(self._lichess_session_event)
        service.session_game_event_received.connect(self._lichess_session_game_event)
        if hasattr(service, "session_action_started"):
            service.session_action_started.connect(self._lichess_session_action_started)
            service.session_action_completed.connect(self._lichess_session_action_completed)
        if hasattr(service, "session_challenge_ready"):
            service.session_challenge_ready.connect(self._lichess_session_challenge_ready)
            service.session_challenge_status_changed.connect(
                self._lichess_session_challenge_status)
        else:
            service.challenge_ready.connect(self._challenge_ready)
            service.challenge_status_changed.connect(self._challenge_status)
        if hasattr(service, "session_invitation_cancelled"):
            service.session_invitation_cancelled.connect(
                self._lichess_session_invitation_cancelled)
        else:
            service.invitation_cancelled.connect(self._invitation_cancelled)
        if hasattr(service, "session_active_games_received"):
            service.session_active_games_received.connect(self._lichess_session_active_games_received)
            service.session_active_games_error.connect(self._lichess_session_active_games_error)
        else:
            service.active_games_received.connect(self._active_games_received)
            service.active_games_error.connect(self._active_games_error)
        if hasattr(service, "session_operation_failed"):
            service.session_operation_failed.connect(self._lichess_session_operation_failed)
        service.analysis_safety_games_received.connect(self._analysis_safety_games_received)
        service.analysis_safety_error.connect(self._analysis_safety_error)
        service.archive_progress.connect(self._archive_progress)
        service.archive_finished.connect(self._archive_finished)
        service.archive_pilot_progress.connect(self._archive_pilot_progress)
        service.archive_pilot_finished.connect(self._archive_pilot_finished)
        service.archive_game_refresh_finished.connect(self._archive_game_refresh_finished)
        service.puzzles_progress.connect(self._puzzles_progress)
        service.puzzles_scanned.connect(self._puzzles_scanned)
        service.puzzles_started.connect(self._puzzles_started)
        service.background_sync_finished.connect(self._background_sync_finished)
        service.lichess_analysis_backfill_finished.connect(
            self._lichess_analysis_backfill_finished)
        service.lichess_analysis_backfill_progress.connect(
            self._lichess_analysis_backfill_progress)
        service.puzzles_finished.connect(self._puzzles_finished)
        service.puzzle_pilot_progress.connect(self._puzzle_pilot_progress)
        service.puzzle_pilot_finished.connect(self._puzzle_pilot_finished)

    def _current_lichess_generation(self, generation: int) -> bool:
        return bool(self.lichess and self.lichess.is_auth_generation_current(generation))

    def _current_account_sessions(self) -> tuple[LichessGameSession, ...]:
        if not self.lichess or not self._lichess_account_authenticated:
            return ()
        return self.game_sessions.for_owner(
            self.lichess.username, generation=self.lichess.auth_generation)

    def _sessions_for_safety(self) -> tuple[LichessGameSession, ...]:
        """Include unknown-owner live sessions as a fail-closed safety signal."""
        account = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        return tuple(
            session for session in self.online_sessions.values()
            if not getattr(session, "detached", False)
            and (getattr(session, "owner_username", None) is None
                 or getattr(session, "owner_username", None) == account)
        )

    def _detach_lichess_sessions(self) -> None:
        for session in self.online_sessions.values():
            session.detach()
        self.game_sessions.clear_selection()
        self._selected_live_game_id = None
        self.online_session = None
        self._review_session = None
        self.online_view_ply = None
        self._remote_active_games = []
        self._pending_game_actions.clear()
        self._active_games_request_id = None
        self._active_games_request_generation = None
        self._open_challenge_id = None
        self._open_challenge_generation = None
        self._awaiting_game_start = False
        if hasattr(self, "_play_mode_page"):
            self._play_mode_page.clear_invitation()
        if getattr(self, "_screen_mode", "") == "online":
            self.show_home()

    def _lichess_session_move_failed(self, generation: int, game_id: str, uci: str,
                                     request_id: str, status: int) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_move_failed(game_id, uci, request_id, status)

    def _lichess_session_takeback_completed(self, generation: int, game_id: str,
                                            request_id: str, status: int) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_takeback_completed(game_id, request_id, status)

    def _lichess_session_challenge_ready(self, generation: int, request_id: str,
                                         result: object) -> None:
        if self._current_lichess_generation(generation):
            self._open_challenge_generation = generation
            self._challenge_ready(result)

    def _lichess_session_challenge_status(self, generation: int, request_id: str,
                                          message: str, waiting: bool) -> None:
        if self._current_lichess_generation(generation):
            self._challenge_status(message, waiting)

    def _lichess_session_invitation_cancelled(self, generation: int, request_id: str,
                                               challenge_id: str) -> None:
        if self._current_lichess_generation(generation):
            self._invitation_cancelled(challenge_id)

    def _lichess_session_active_games_received(self, generation: int, request_id: str,
                                               games: object) -> None:
        if (self._current_lichess_generation(generation)
                and generation == self._active_games_request_generation
                and request_id == self._active_games_request_id):
            self._active_games_received(games, generation=generation, request_id=request_id)

    def _lichess_session_active_games_error(self, generation: int, request_id: str,
                                            message: str) -> None:
        if (self._current_lichess_generation(generation)
                and generation == self._active_games_request_generation
                and request_id == self._active_games_request_id):
            self._active_games_error(message)

    def _lichess_session_operation_failed(self, generation: int, request_id: str,
                                          command: str, game_id: str, status: int,
                                          message: str) -> None:
        if not self._current_lichess_generation(generation):
            return
        if command in {"username_challenge", "create_challenge"}:
            if command == "username_challenge":
                self._awaiting_game_start = False
                self.cancel_challenge_button.setEnabled(False)
                self.cancel_challenge_button.setVisible(False)
                if hasattr(self, "_play_mode_page"):
                    self._play_mode_page.set_request_pending(False)
            self._challenge_status(f"Не удалось выполнить запрос: {message}", False)
        elif command == "cancel_invitation":
            if getattr(self, "cancel_invitation_button", None) is not None:
                self.cancel_invitation_button.setEnabled(True)
            if hasattr(self, "_play_mode_page"):
                self._play_mode_page.cancel_invitation_button.setEnabled(True)
            self._notify_inline(f"Не удалось отменить приглашение: {message}", timeout_ms=7000)
        elif game_id:
            self._notify_inline(f"Действие Lichess не выполнено: {message}", timeout_ms=7000)

    def _lichess_session_action_started(self, generation: int, request_id: str,
                                        command: str, game_id: str) -> None:
        if not self._current_lichess_generation(generation):
            return
        self._pending_game_actions.add((str(game_id), str(command)))
        if self.online_session and self.online_session.game_id == game_id:
            self._refresh_online()

    def _lichess_session_action_completed(self, generation: int, request_id: str,
                                          command: str, game_id: str, status: int) -> None:
        if not self._current_lichess_generation(generation):
            return
        self._pending_game_actions.discard((str(game_id), str(command)))
        if self.online_session and self.online_session.game_id == game_id:
            self._refresh_online()

    def _lichess_session_status(self, generation: int, status: str) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_status(status)

    def _lichess_background_operation_status(self, generation: int, source: str,
                                             status_code: int, message: str) -> None:
        """Handle one endpoint's failure without changing account/stream identity."""
        if not self._current_lichess_generation(generation):
            return
        if source != "ready_analytics":
            return
        code = int(status_code)
        self._ready_analytics_failure_status = str(message)
        self._ready_analytics_failure_session = (
            int(generation), str(getattr(self.lichess, "username", "") or "").casefold())
        self.lichess_analysis_status.setText(f"Lichess · {message}")
        self.lichess_analysis_status.setToolTip(
            "Источник: очередь готовой аналитики. Учётная запись и игровой поток имеют отдельное состояние.")
        page = getattr(self, "_background_operations_page", None)
        if page is not None:
            self._refresh_background_operations_page()
        if code in {401, 403, 429}:
            # Any inability to re-confirm account/playing state revokes display
            # permission. It does not imply that /api/account or the game stream failed.
            self._revoke_dashboard_display_safety(
                "Аналитика скрыта · требуется новая проверка Lichess")
            self.safety_retry_button.setVisible(bool(
                self._lichess_connected and self._lichess_account_authenticated))
            self.sidebar_connection_label.setText(
                "Поток готов" if self._lichess_connected else "Восстановление…")
            if code == 429:
                self._schedule_dashboard_safety_retry(
                    max(60, self._lichess_rate_limit_remaining()), automatic=True)
        self._notify_inline(f"Фоновая аналитика Lichess: {message}", timeout_ms=7000)

    def _lichess_session_authenticated(self, generation: int, username: str) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_authenticated(username)
            if getattr(self, "_background_operations_page", None) is not None:
                self._refresh_background_operations_page()

    def _lichess_event_stream_ready(self, generation: int, username: str) -> None:
        if (not self._current_lichess_generation(generation) or self.offline_mode
                or not self._lichess_account_authenticated or not username
                or username.casefold() != str(self.lichess.username or "").casefold()):
            return
        self._lichess_stream_ready_generation = generation
        self._lichess_connected = True
        self._lichess_session_selected = True
        self._event_stream_recovery_pending = False
        self._set_connection_header(
            "Готов", "Аккаунт подтверждён; игровой поток Lichess доступен.")
        self.sidebar_connection_label.setText("Поток готов")
        self.disconnect_button.setVisible(True)
        self.connect_button.setVisible(False)
        self._refresh_online_controls()
        self._request_dashboard_display_safety()
        self._cancel_legacy_metrics_batch("account_switched")
        if self._archive_account_ready_generation == generation:
            self._start_connected_account_background_work(generation, username)

    def _archive_account_ready_result(self, generation: int, username: str,
                                      payload: object, error: object) -> None:
        # A SQLite worker can finish after closeEvent has begun. Its result is
        # no longer useful and must not start fresh dashboard/sync workers on
        # a coordinator that is already draining.
        if (self._shutdown_requested or not self._current_lichess_generation(generation) or not self.lichess
                or str(self.lichess.username or "").casefold() != str(username).casefold()):
            return
        if (error or not isinstance(payload, dict)
                or not isinstance(payload.get("account_id"), str)):
            self._notify_inline("Не удалось подготовить локальный профиль Lichess. "
                                "Игровой поток останется подключён, фоновые операции приостановлены.")
            self._set_connection_header("Профиль недоступен", "Ошибка локального профиля; сеть и партия не сбрасываются.")
            return
        account_id = payload["account_id"]
        self._archive_account_ready_generation = generation
        self._home_view.select_profile(account_id, notify=False)
        self._refresh_home_dashboard()
        self._pending_final_sync_resume_rows = payload.get("pending_final_syncs", [])
        self._resume_pending_final_syncs()
        if (self._lichess_connected and self._lichess_stream_ready_generation == generation):
            self._start_connected_account_background_work(generation, username)

    def _start_connected_account_background_work(self, generation: int, username: str) -> None:
        if (self._account_background_started_generation == generation
                or not self._current_lichess_generation(generation)
                or not self._lichess_connected):
            return
        self._account_background_started_generation = generation
        # Archive/puzzle history synchronization is separate from optional
        # ready-analysis backfill and keeps its incremental periodic schedule.
        auto_archive = self.settings.value("lichess/auto_archive_sync", True, type=bool)
        auto_puzzles = self.settings.value("lichess/auto_puzzle_history_sync", True, type=bool)
        if auto_archive or auto_puzzles:
            QTimer.singleShot(3000, self._run_background_sync)
            self._auto_sync_timer.start()
        else:
            self._auto_sync_timer.stop()
        if not self.settings.value("lichess/auto_ready_analytics", True, type=bool):
            self._cancel_lichess_backfill_wakeup()
            self.lichess_analysis_status.setText("Lichess · фоновая аналитика выключена")
            return
        if self.settings.value("lichess/ready_analytics_paused", False, type=bool):
            self.lichess_analysis_status.setText("Lichess · фоновая аналитика приостановлена")
            return
        if any(session.status in {"started", "playing"} and not session.is_ai_opponent
               for session in self._sessions_for_safety()):
            self._backfill_deferred_for_human_game = True
            self.lichess_analysis_status.setText(
                "Lichess · проверка отложена до завершения партии с человеком")
            return
        self._backfill_deferred_for_human_game = False
        self.lichess_analysis_status.setText("Lichess · готовим дозагрузку готовых данных…")
        self._schedule_lichess_backfill_wakeup(
            generation, username, (datetime.now(timezone.utc) + timedelta(milliseconds=1200)).isoformat(),
            debounce_if_due=False)

    def _start_lichess_analysis_backfill(self, generation: int, *, manual: bool = False,
                                         force_retry: bool = False) -> None:
        self._cancel_lichess_backfill_wakeup()
        if (self._shutdown_requested or self.offline_mode
                or not self._current_lichess_generation(generation)
                or self._lichess_stream_ready_generation != generation
                or not self._lichess_connected or not self.lichess
                or (not manual and not self.settings.value(
                    "lichess/auto_ready_analytics", True, type=bool))
                or self.settings.value("lichess/ready_analytics_paused", False, type=bool)
                or any(session.status in {"started", "playing"} and not session.is_ai_opponent
                       for session in self._sessions_for_safety())):
            return
        self.lichess.refresh_lichess_analysis_background(
            generation=generation, limit=10,
            daily_limit=self.settings.value("lichess/ready_analytics_daily_limit", 25, type=int),
            force_retry=force_retry)
        self.lichess_analysis_status.setText("Lichess · проверяем до 10 завершённых партий…")

    def _lichess_analysis_backfill_progress(self, generation: int, username: str,
                                           done: int, total: int, _game: dict) -> None:
        if not self._accept_backfill_status(generation, username):
            return
        self.lichess_analysis_status.setText(f"Lichess · фоновая аналитика: {done}/{total} партий")

    def _accept_backfill_status(self, generation: int, username: str) -> bool:
        return bool(self._current_lichess_generation(generation) and self.lichess
                    and str(self.lichess.username or "").casefold() == username.casefold())

    def _reset_backfill_status(self, text: str = "Lichess · нет текущей фоновой операции") -> None:
        self._cancel_lichess_backfill_wakeup()
        self._backfill_deferred_for_human_game = False
        self.lichess_analysis_status.setText(text)
        self.lichess_analysis_status.setToolTip("")

    def _cancel_lichess_backfill_wakeup(self) -> None:
        timer = getattr(self, "_backfill_wakeup_timer", None)
        if timer is not None:
            timer.stop()
        self._backfill_wakeup_identity = None

    def _schedule_lichess_backfill_wakeup(self, generation: int, username: str,
                                          next_eligible_at: str | None, *,
                                          debounce_if_due: bool = True) -> None:
        self._cancel_lichess_backfill_wakeup()
        if (not next_eligible_at or self._shutdown_requested or self.offline_mode
                or not self._current_lichess_generation(generation)
                or self._lichess_stream_ready_generation != generation
                or not self._lichess_connected
                or not self.settings.value("lichess/auto_ready_analytics", True, type=bool)
                or self.settings.value("lichess/ready_analytics_paused", False, type=bool)
                or any(session.status in {"started", "playing"} and not session.is_ai_opponent
                       for session in self._sessions_for_safety())):
            return
        try:
            due_at = datetime.fromisoformat(str(next_eligible_at).replace("Z", "+00:00"))
            if due_at.tzinfo is None:
                return
            delay_ms = int(max(0.0, (due_at - datetime.now(timezone.utc)).total_seconds()) * 1000)
        except (TypeError, ValueError, OverflowError):
            return
        # A still-due batch is resumed after a quiet period. Durable retry and
        # daily-limit timestamps retain their exact future deadline.
        if debounce_if_due and delay_ms == 0:
            delay_ms = 60_000
        self._backfill_wakeup_identity = (generation, username.casefold())
        self._backfill_wakeup_timer.start(min(delay_ms, 2_147_000_000))

    def _lichess_backfill_wakeup(self) -> None:
        identity, self._backfill_wakeup_identity = self._backfill_wakeup_identity, None
        if identity is None:
            return
        generation, expected_username = identity
        if (not self.lichess or str(self.lichess.username or "").casefold() != expected_username
                or not self._current_lichess_generation(generation)):
            return
        self._start_lichess_analysis_backfill(generation)

    def _lichess_analysis_backfill_finished(self, generation: int, username: str,
                                            checked: int, total: int, accuracy: int,
                                            evals: int, no_data: int, errors: int,
                                            state: str, next_eligible_at: str | None = None) -> None:
        if not self._accept_backfill_status(generation, username):
            return
        self.lichess_analysis_status.setText(
            f"Lichess · проверено {checked}/{total} · точность {accuracy} · оценки {evals} · "
            f"без анализа {no_data} · ошибки {errors}")
        self.lichess_analysis_status.setToolTip(
            f"Источник: документированный JSON export Lichess. {state}. "
            "Локальные оценки Stockfish хранятся отдельно.")
        if not self._shutdown_requested and self._screen_mode == "home":
            self.refresh()
        if "человеческая/неизвестная активная партия" in state:
            self._backfill_deferred_for_human_game = True
            self._cancel_lichess_backfill_wakeup()
            return
        if any(marker in state for marker in ("приостановлено", "отключена", "HTTP 401", "HTTP 403")):
            self._cancel_lichess_backfill_wakeup()
            return
        self._schedule_lichess_backfill_wakeup(
            generation, username, next_eligible_at, debounce_if_due=True)

    def show_background_analytics(self) -> None:
        page = getattr(self, "_background_operations_page", None)
        if page is None:
            page = BackgroundOperationsPage(self)
            page.pause_requested.connect(self._set_backfill_paused)
            page.manual_check_requested.connect(self._manual_lichess_analytics_check)
            page.refresh_requested.connect(self._refresh_background_operations_page)
            page.close_requested.connect(self.navigate_back)
            self._background_operations_page = page
            self.screen_stack.addWidget(page)
        self._refresh_background_operations_page()
        self.screen_title.setText("Фоновые операции")
        self.screen_stack.setCurrentWidget(page)

    def _refresh_background_operations_page(self) -> None:
        page = getattr(self, "_background_operations_page", None)
        if page is None:
            return
        username = str(getattr(self.lichess, "username", "") or "").strip()
        account_id = f"lichess:{username.casefold()}" if username else None
        available = bool(
            self.lichess and not self.offline_mode and self._lichess_connected
            and self._lichess_stream_ready_generation == getattr(
                self.lichess, "auth_generation", None))
        self._background_status_generation += 1
        generation = self._background_status_generation
        previous = self._background_status_handle
        if previous is not None:
            previous.cancel("status_refresh_superseded")
        stop_event = threading.Event()
        repository = self.game_repository
        metric_scope = (self.dashboard_account.currentData()
                        if hasattr(self, "dashboard_account") else None)
        operations = self.runtime.work_coordinator.snapshots()

        def worker() -> None:
            try:
                if stop_event.is_set():
                    return
                archive = repository.archive_sync_state(account_id) if account_id else {}
                puzzles = repository.puzzle_sync_state(account_id) if account_id else {}
                summary = (repository.lichess_analysis_backfill_summary(account_id)
                           if account_id else {})
                metric_status = repository.analysis_metrics_migration_status(
                    account_id=str(metric_scope) if metric_scope else None)
                paused = bool(account_id and
                              repository.lichess_analysis_backfill_paused(account_id))
                if stop_event.is_set():
                    return
                self.background_status_finished.emit(generation, {
                    "account_id": account_id, "username": username, "available": available,
                    "archive": archive, "puzzles": puzzles, "summary": summary,
                    "paused": paused, "operations": operations,
                    "metric_scope": metric_scope, "metric_status": metric_status,
                })
            except Exception as exc:
                if not stop_event.is_set():
                    self.background_status_finished.emit(generation, {
                        "account_id": account_id, "username": username, "available": available,
                        "failure_code": type(exc).__name__, "operations": operations,
                    })

        page.summary.setText("Читаю сохранённый статус фоновых операций…")
        self._background_status_handle = self._start_registered_work(
            worker, kind="background_status_read", lane=OperationLane.INTERACTIVE,
            thread_name="cc-background-status", stop_event=stop_event)

    def _background_operations_result_ready(self, generation: int, payload: object) -> None:
        if (generation != self._background_status_generation
                or not isinstance(payload, dict)
                or getattr(self, "_background_operations_page", None) is None):
            return
        username = str(getattr(self.lichess, "username", "") or "").strip()
        account_id = f"lichess:{username.casefold()}" if username else None
        if payload.get("account_id") != account_id:
            return
        summary = payload.get("summary") or {}
        statuses: list[str] = []
        safety_allowed = self._library_detail_safety_allowed()
        metric_status = payload.get("metric_status") or {}
        if safety_allowed and payload.get("metric_scope") == (
                self.dashboard_account.currentData()
                if hasattr(self, "dashboard_account") else None):
            metric_text = self._format_legacy_metrics_status(metric_status)
            self._legacy_metrics_text = metric_text
        else:
            metric_text = "Статус обновления аналитики скрыт до безопасной проверки Lichess."
        if payload.get("failure_code"):
            statuses.append(f"SQLite · статус недоступен ({payload['failure_code']})")
        else:
            archive = payload.get("archive") or {}
            if account_id:
                archive_phase = ("полный архив загружен" if archive.get("backfill_complete")
                                 else "первичная загрузка/продолжение не завершены")
                archive_last = archive.get("last_success_at") or "ещё не синхронизирован"
                archive_problem = " · ошибка синхронизации" if archive.get("last_error") else ""
                statuses.append(
                    f"Архив Lichess · сохранено {archive.get('imported_count', 0)} · "
                    f"{archive_phase} · последнее успешное обновление: {archive_last}"
                    f"{archive_problem}")
                puzzles = payload.get("puzzles") or {}
                puzzle_phase = ("история полностью загружена" if puzzles.get("backfill_complete")
                                else "история/продолжение не завершены")
                puzzle_last = puzzles.get("last_success_at") or "ещё не синхронизирована"
                puzzle_problem = " · ошибка синхронизации" if puzzles.get("last_error") else ""
                statuses.append(
                    f"Задачи Lichess · сохранено {puzzles.get('imported_count', 0)} попыток · "
                    f"{puzzle_phase} · последнее успешное обновление: {puzzle_last}"
                    f"{puzzle_problem}")
                current_failure_session = (
                    int(getattr(self.lichess, "auth_generation", -1)),
                    str(getattr(self.lichess, "username", "") or "").casefold())
                if (self._ready_analytics_failure_status
                        and getattr(self, "_ready_analytics_failure_session", None)
                        == current_failure_session):
                    statuses.append(
                        f"Готовая аналитика Lichess · {self._ready_analytics_failure_status} "
                        "Учётная запись и игровой поток не отключены.")
            source_summary = (
                "Источник: Lichess · документированный JSON export. "
                f"Партии: {summary.get('total', 0)} · проверено: {summary.get('checked', 0)} · "
                f"готовая точность: {summary.get('accuracy', 0)} партий · "
                f"партии с оценками ходов: {summary.get('evals', 0)} · "
                f"нет серверного анализа: {summary.get('no_data', 0)} · "
                f"ошибки/ограничения: {summary.get('errors', 0)} · ожидают: {summary.get('pending', 0)}. "
                "Chess Coach не запрашивает создание серверного анализа."
                if account_id else
                "Lichess не подключён; локальные фоновые задачи могут работать отдельно.")
            retry = (f"Следующая разрешённая попытка: {summary['retry_at']}"
                     if summary.get("retry_at") else
                     "Ограничение запросов Lichess и Retry-After учитываются сервисом."
                     if summary.get("errors") else "")
            if not account_id:
                statuses.append("Архив и задачи Lichess · аккаунт не выбран; личные данные не запрашивались")
            self._background_operations_page.set_snapshot(
                payload.get("operations", ()), source_summary=source_summary,
                backfill_paused=bool(payload.get("paused")),
                can_check=bool(payload.get("available") and account_id),
                can_pause=bool(account_id), retry_status=retry,
                durable_statuses=statuses, local_metrics_status=metric_text)

    @staticmethod
    def _format_legacy_metrics_status(status: dict) -> str:
        total = max(0, int(status.get("total", 0)))
        remaining = max(0, int(status.get("remaining", 0)))
        done = max(0, min(total, int(status.get("done", total - remaining))))
        if remaining:
            return (f"Локальные сохранённые анализы · метрики обновляются: готово "
                    f"{done}/{total}; осталось {remaining}. UCI и сеть не используются.")
        return (f"Локальные сохранённые анализы · метрики актуальны: "
                f"{done}/{total}; осталось 0.")

    def _legacy_metrics_account_scope(self) -> str | None:
        statistics_page = getattr(self, "_statistics_dialog", None)
        if isinstance(statistics_page, EmbeddedStatisticsPage):
            selected = statistics_page.account.currentData()
            return str(selected) if selected else None
        if hasattr(self, "dashboard_account"):
            value = self.dashboard_account.currentData()
            return str(value) if value else None
        return None

    def _schedule_legacy_metrics_batch(self) -> None:
        if (self._shutdown_requested or not self._library_detail_safety_allowed()
                or any(session.status in {"started", "playing"} and not session.is_ai_opponent
                       for session in self._sessions_for_safety())):
            return
        if self._legacy_metrics_complete_revision == self.game_repository.statistics_revision:
            return
        scope = self._legacy_metrics_account_scope()
        self._legacy_metrics_scope = scope
        if not self._legacy_metrics_timer.isActive():
            self._legacy_metrics_timer.start(500)

    def _cancel_legacy_metrics_batch(self, reason: str) -> None:
        self._legacy_metrics_timer.stop()
        self._legacy_metrics_generation += 1
        if self._legacy_metrics_stop is not None:
            self._legacy_metrics_stop.set()
        handle = self._legacy_metrics_handle
        if handle is not None and handle.is_alive():
            handle.cancel(reason)

    def _start_legacy_metrics_batch(self) -> None:
        if (self._shutdown_requested or not self._library_detail_safety_allowed()
                or any(session.status in {"started", "playing"} and not session.is_ai_opponent
                       for session in self._sessions_for_safety())):
            return
        handle = self._legacy_metrics_handle
        if handle is not None and handle.is_alive():
            # A prior account/safety generation is winding down; wait for its
            # transactional row boundary before starting the next bounded batch.
            return
        self._legacy_metrics_handle = None
        stop = threading.Event()
        generation = self._legacy_metrics_generation
        scope = self._legacy_metrics_account_scope()
        self._legacy_metrics_scope = scope
        repository = self.game_repository
        self._legacy_metrics_text = (
            "Локальные метрики сохранённых анализов обновляются небольшими фоновыми партиями; "
            "движок и сеть для этого не запускаются.")
        statistics_page = getattr(self, "_statistics_dialog", None)
        if isinstance(statistics_page, EmbeddedStatisticsPage):
            statistics_page.set_metric_migration_status(
                self._legacy_metrics_text, safety_allowed=True)
            statistics_page._schedule_refresh()
        def worker() -> None:
            try:
                before = repository.analysis_metrics_migration_status(account_id=scope)
                if stop.is_set():
                    result = {"cancelled": True, "status": before}
                else:
                    batch = repository.materialize_stale_analysis_metrics_batch(
                        limit=10, cancel_check=stop.is_set)
                    after = repository.analysis_metrics_migration_status(account_id=scope)
                    result = {"cancelled": stop.is_set(), "batch": batch, "status": after}
            except Exception as exc:
                result = {"error": type(exc).__name__}
            self.legacy_metrics_finished.emit(generation, {
                "scope": scope, "result": result,
            })

        self._legacy_metrics_stop = stop
        self._legacy_metrics_handle = self._start_registered_work(
            worker, kind="legacy_analysis_metrics_v3", lane=OperationLane.SQLITE_WRITER,
            thread_name="cc-legacy-metrics-v3", stop_event=stop)

    def _legacy_metrics_batch_finished(self, generation: int, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        handle = self._legacy_metrics_handle
        if handle is not None and handle.is_alive():
            QTimer.singleShot(50, lambda: self._legacy_metrics_batch_finished(
                generation, payload))
            return
        if handle is not None:
            self._legacy_metrics_handle = None
            self._legacy_metrics_stop = None
        if (generation != self._legacy_metrics_generation or self._shutdown_requested
                or payload.get("scope") != self._legacy_metrics_account_scope()):
            if self._library_detail_safety_allowed():
                self._schedule_legacy_metrics_batch()
            return
        if not self._library_detail_safety_allowed():
            return
        result = payload.get("result") or {}
        status = result.get("status") or {}
        if result.get("error"):
            self._legacy_metrics_text = (
                f"Локальное обновление метрик временно не выполнено ({result['error']}); "
                "повтор будет отложен.")
            retry_ms = 30_000
        else:
            self._legacy_metrics_text = self._format_legacy_metrics_status(status)
            remaining_global = int((result.get("batch") or {}).get("remaining", 0))
            if remaining_global == 0:
                self._legacy_metrics_complete_revision = self.game_repository.statistics_revision
            retry_ms = 500 if remaining_global > 0 else 0
        statistics_page = getattr(self, "_statistics_dialog", None)
        if isinstance(statistics_page, EmbeddedStatisticsPage):
            statistics_page.set_metric_migration_status(
                self._legacy_metrics_text, safety_allowed=True)
            if int((result.get("batch") or {}).get("processed", 0)) > 0:
                statistics_page.refresh_data_preserving_position()
        if ((result.get("batch") or {}).get("processed", 0) > 0
                and self.screen_stack.currentWidget() is getattr(self, "home_page", None)):
            self._refresh_home_dashboard()
        page = getattr(self, "_background_operations_page", None)
        if page is not None:
            page.local_metrics_status.setText(self._legacy_metrics_text)
            self._refresh_background_operations_page()
        if retry_ms:
            self._legacy_metrics_timer.start(retry_ms)

    def _set_backfill_paused(self, paused: bool) -> None:
        username = str(getattr(self.lichess, "username", "") or "").strip()
        account_id = f"lichess:{username.casefold()}" if username else None
        if not account_id:
            return
        self.settings.setValue("lichess/ready_analytics_paused", bool(paused))
        if not paused:
            self._ready_analytics_failure_status = ""
            self._ready_analytics_failure_session = None
        self.game_repository.set_lichess_analysis_backfill_paused(account_id, paused)
        if paused and self.lichess:
            self._cancel_lichess_backfill_wakeup()
            self.lichess.cancel_lichess_analysis_backfill()
            self.lichess_analysis_status.setText("Lichess · фоновая аналитика приостановлена")
        elif self.lichess and self._lichess_stream_ready_generation == self.lichess.auth_generation:
            self._start_lichess_analysis_backfill(self.lichess.auth_generation)
        self._refresh_background_operations_page()

    def _manual_lichess_analytics_check(self) -> None:
        if (not self.lichess or self.offline_mode or not self._lichess_connected
                or self._lichess_stream_ready_generation != self.lichess.auth_generation):
            return
        self._start_lichess_analysis_backfill(
            self.lichess.auth_generation, manual=True, force_retry=True)
        self._refresh_background_operations_page()

    def _open_lichess_game_page(self, game_id: str) -> None:
        if not game_id or not re.fullmatch(r"[A-Za-z0-9_-]+", game_id):
            return
        QDesktopServices.openUrl(QUrl(f"https://lichess.org/{game_id}"))

    def _lichess_session_error(self, generation: int, message: str) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_error(message)

    def _lichess_session_event(self, generation: int, event: dict) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_event(event, generation=generation)

    def _lichess_session_game_event(self, generation: int, game_id: str, event: dict) -> None:
        if self._current_lichess_generation(generation):
            self._lichess_game_event(game_id, event, generation=generation)

    def _disable_online_controls(self) -> None:
        self._set_connection_header("Офлайн", "Офлайн-режим · Lichess отключён.")
        self.sidebar_connection_label.setText("Офлайн")
        self._set_sidebar_safety_status("Локальные данные · офлайн")
        self.connect_button.setVisible(False)
        self.disconnect_button.setVisible(False)
        self.connect_button.setEnabled(False)
        self.connect_button.setToolTip("В офлайн-smoke авторизация и сетевые запросы отключены.")
        self.disconnect_button.setVisible(False)
        for button in (
            self.friend_link_button, self.username_button, self.ai_button,
            self.cancel_challenge_button,
        ):
            button.setEnabled(False)
            button.setVisible(False)
        self._update_home_game_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.sidebar = self._build_sidebar()
        shell.addWidget(self.sidebar)
        content = QWidget()
        shell.addWidget(content, 1)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 0, 24, 20)
        outer.setSpacing(12)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header_widget = QWidget()
        self.header_widget = header_widget
        header_widget.setFixedHeight(64)
        header_widget.setLayout(header)
        self.back_button = QPushButton("← Назад")
        self.back_button.setAccessibleName("Назад по разделам")
        self.back_button.clicked.connect(self.navigate_back)
        self.back_button.setVisible(False)
        header.addWidget(self.back_button)
        self.return_to_game_button = QPushButton("Вернуться к партии")
        self.return_to_game_button.setAccessibleName("Вернуться к активной партии")
        self.return_to_game_button.clicked.connect(self.resume_current_game)
        self.return_to_game_button.setVisible(False)
        header.addWidget(self.return_to_game_button)
        self.header_brand = QLabel("♟  Chess Coach")
        self.header_brand.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(self.header_brand)
        header.addStretch(1)
        self.connection = QLabel("Нет связи")
        self.connection.setObjectName("connectionStatus")
        self.connection.setStyleSheet(f"color: {TOKENS.secondary};")
        self.connection.setMinimumWidth(0)
        self.connection.setMaximumWidth(210)
        self.connection.setToolTip("Состояние подключения Lichess")
        self._connection_header_label = "Нет связи"
        self._connection_header_tooltip = "Состояние подключения Lichess"
        header.addWidget(self.connection)
        self.lichess_analysis_status = QLabel("Lichess · фоновая аналитика: не запущена")
        self.lichess_analysis_status.setObjectName("lichessAnalyticsStatus")
        self.lichess_analysis_status.setStyleSheet("font-size: 11px; color: #77766f;")
        self.lichess_analysis_status.setMaximumWidth(210)
        self.lichess_analysis_status.setWordWrap(True)
        self.lichess_analysis_status.hide()
        self.background_operations_button = QPushButton("Фоновые операции")
        self.background_operations_button.clicked.connect(self.show_background_analytics)
        self.background_operations_button.setText("Фон")
        self.background_operations_button.setToolTip("Статус и управление фоновыми операциями")
        header.addWidget(self.background_operations_button)
        self.connect_button = QPushButton("Подключить Lichess")
        self.connect_button.clicked.connect(self.connect_lichess)
        self.connect_button.setMinimumHeight(38)
        self.sidebar_layout.insertWidget(4, self.connect_button)
        self.disconnect_button = QPushButton("Отключить сеанс")
        self.disconnect_button.setToolTip(
            "Остановить текущую Lichess-сессию, сохранив выбранные учётные данные локально.")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.setVisible(False)
        self.disconnect_button.clicked.connect(self.disconnect_lichess)
        self.disconnect_button.setMinimumHeight(38)
        self.sidebar_layout.insertWidget(5, self.disconnect_button)
        self.settings_button = QPushButton("Настройки")
        self.settings_button.setAccessibleName("Настройки")
        self.settings_button.clicked.connect(self.open_settings)
        self.settings_button.hide()  # Settings has one persistent entry in the sidebar footer.
        outer.addWidget(header_widget)

        self.screen_title = QLabel("Главная")
        self.screen_title.setStyleSheet("font-size: 18px; font-weight: 700; padding: 2px 4px;")
        outer.addWidget(self.screen_title)
        self.screen_stack = QStackedWidget()
        self.home_page = self._build_home_page()
        self.screen_stack.addWidget(self.home_page)
        self.play_page = self._build_play_page()
        self.screen_stack.addWidget(self.play_page)
        self.analysis_page = AnalysisLandingPage({
            "games": self.show_saved_games,
            "batch": self.show_batch_analysis,
            "progress": self.show_statistics,
            "open_text": self.open_external_study_text,
            "open_file": self.open_external_pgn_file,
        })
        self.screen_stack.addWidget(self.analysis_page)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        board_column = QWidget()
        self.board_column = board_column
        board_layout = QVBoxLayout(board_column)
        board_layout.setContentsMargins(0, 0, 8, 0)
        board_layout.setSpacing(8)
        self.opponent_label = QLabel("Соперник  ·  локальная партия")
        self.opponent_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.opponent_label)
        self.opponent_clock_label = QLabel("Без часов")
        self.opponent_clock_label.setObjectName("opponentClock")
        self.opponent_clock_label.setAccessibleName("Часы соперника")
        self.opponent_clock_label.setStyleSheet("font-size: 28px; font-weight: 700; padding: 2px 8px;")
        board_layout.addWidget(self.opponent_clock_label)
        self.opponent_clock_detail = QLabel("")
        self.opponent_clock_detail.setObjectName("opponentClockDetail")
        self.opponent_clock_detail.setStyleSheet("font-size: 11px; color: #77766f; padding-left: 10px;")
        board_layout.addWidget(self.opponent_clock_detail)
        self.focus_mode_button = QPushButton("Режим фокуса")
        self.focus_mode_button.setCheckable(True)
        self.focus_mode_button.setAccessibleName("Переключить режим фокуса доски")
        self.focus_mode_button.toggled.connect(self._set_focus_mode)
        board_layout.addWidget(self.focus_mode_button)
        self.board = BoardView(self.controller.board)
        self.board.setMinimumSize(160, 160)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self.board.move_intent.connect(self.on_move_intent)
        self.board.wheel_navigate.connect(self.navigate_by)
        self.board.premove_intent.connect(self.on_premove_intent)
        self.board.cancel_premove_requested.connect(self.cancel_premove)
        board_layout.addWidget(self.board, 1, Qt.AlignmentFlag.AlignCenter)
        self.you_label = QLabel("Вы  ·  офлайн")
        self.you_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.you_label)
        self.you_clock_label = QLabel("Без часов")
        self.you_clock_label.setObjectName("ownerClock")
        self.you_clock_label.setAccessibleName("Ваши часы")
        self.you_clock_label.setStyleSheet("font-size: 28px; font-weight: 700; padding: 2px 8px;")
        board_layout.addWidget(self.you_clock_label)
        self.you_clock_detail = QLabel("")
        self.you_clock_detail.setObjectName("ownerClockDetail")
        self.you_clock_detail.setStyleSheet("font-size: 11px; color: #77766f; padding-left: 10px;")
        board_layout.addWidget(self.you_clock_detail)
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
        self.finished_game_card = QFrame()
        self.finished_game_card.setObjectName("card")
        finished_layout = QVBoxLayout(self.finished_game_card)
        finished_layout.setContentsMargins(12, 10, 12, 10)
        self.finished_summary = QLabel("Партия завершена")
        self.finished_summary.setWordWrap(True)
        self.finished_summary.setStyleSheet("font-weight: 700;")
        finished_layout.addWidget(self.finished_summary)
        self.finished_saved_label = QLabel()
        self.finished_saved_label.setWordWrap(True)
        finished_layout.addWidget(self.finished_saved_label)
        self.finished_review_button = QPushButton("Разобрать партию")
        self.finished_review_button.clicked.connect(self.review_finished_game)
        self.finished_review_button.setParent(side)
        self.finished_review_button.setText("Разобрать")
        self.finished_challenge_button = QPushButton("Вызвать снова", side)
        self.finished_challenge_button.clicked.connect(self._challenge_again)
        self.finished_export_button = QPushButton("Экспорт PGN", self.finished_game_card)
        self.finished_export_button.clicked.connect(self.export_current_pgn)
        self.finished_review_button.hide()
        self.finished_challenge_button.hide()
        self.finished_game_card.setVisible(False)
        # Keep the legacy result object as a hidden compatibility surface; the
        # finished state is presented inline on the same board.
        self.finished_summary.hide()
        self.finished_saved_label.hide()
        side_layout.addWidget(self.finished_review_button)
        side_layout.addWidget(self.finished_challenge_button)
        self.local_mode_label = QLabel("Режим локальной доски")
        side_layout.addWidget(self.local_mode_label)
        self.local_mode_combo = QComboBox()
        self.local_mode_combo.addItem("Свободная доска (за обе стороны)", "free")
        self.local_mode_combo.addItem("Тренировка предходов (мой цвет)", "premove_training")
        self.local_mode_combo.currentIndexChanged.connect(self._local_board_mode_changed)
        side_layout.addWidget(self.local_mode_combo)
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
        self.board_size_label = QLabel("Размер доски")
        side_layout.addWidget(self.board_size_label)
        self.board_size_combo = QComboBox()
        for label, size in (("Компактная · 400", 400), ("Стандартная · 480", 480),
                            ("Большая · 560", 560), ("Очень большая · 720", 720),
                            ("На всю доступную область", -1)):
            self.board_size_combo.addItem(label, size)
        stored_size = self.settings.value("board_target_size", 480, type=int)
        index = self.board_size_combo.findData(stored_size)
        self.board_size_combo.setCurrentIndex(index if index >= 0 else 1)
        self.settings.setValue("board_target_size", int(self.board_size_combo.currentData()))
        self.board_size_combo.currentIndexChanged.connect(self._board_size_changed)
        side_layout.addWidget(self.board_size_combo)
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
        self.position_eval_button = QPushButton("Анализировать позицию")
        self.position_eval_button.setCheckable(True)
        self.position_eval_button.setToolTip(
            "Явный учебный запрос. В обычном режиме перед каждым запуском требуется успешная "
            "проверка активных партий Lichess; при человеческой партии, 429 или неизвестном "
            "состоянии Stockfish блокируется.")
        self.position_eval_button.toggled.connect(self._set_position_evaluation_enabled)
        self.position_eval_button.setVisible(False)
        side_layout.addWidget(self.position_eval_button)
        self.position_eval_label = QLabel("Оценка Stockfish выключена.")
        self.position_eval_label.setWordWrap(True)
        self.position_eval_label.setVisible(False)
        side_layout.addWidget(self.position_eval_label)
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
        self.moves = MoveTable()
        self.moves.ply_selected.connect(self.on_move_selected_ply)
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

        self.premove_title = QLabel("ПРЕДХОД")
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
        self.game_splitter = splitter
        splitter.splitterMoved.connect(lambda *_: QTimer.singleShot(0, self._apply_board_size))
        splitter.setOrientation(
            Qt.Orientation.Vertical if self.width() < 1120 else Qt.Orientation.Horizontal)
        # The board workspace itself must not become a page-wide scroll area:
        # the move table owns its own scrolling and the side controls stay in view.
        game_page = QWidget()
        game_page_layout = QVBoxLayout(game_page)
        game_page_layout.setContentsMargins(0, 0, 0, 0)
        game_page_layout.addWidget(splitter)
        self.game_page = game_page
        self.screen_stack.addWidget(self.game_page)
        self.result_page = self._build_result_page()
        self.review_workspace = GameReviewWorkspace(self.game_repository, settings=self.settings,
                                                    parent=self)
        self.review_workspace.return_requested.connect(self._return_from_review)
        self.review_workspace.export_requested.connect(self._export_review_pgn)
        self.review_workspace.analysis_requested.connect(self._request_saved_game_analysis)
        self.review_workspace.open_lichess_game_requested.connect(self._open_lichess_game_page)
        self.review_workspace.safety_requested.connect(self._request_review_training_safety)
        self.review_workspace.position_selected.connect(self._review_position_selected)
        self.review_workspace.tabs.currentChanged.connect(self._schedule_workspace_save)
        self.screen_stack.addWidget(self.review_workspace)
        self.screen_stack.setCurrentWidget(self.home_page)
        self.screen_stack.currentChanged.connect(self._shell_screen_changed)
        outer.addWidget(self.screen_stack, 1)
        self.setCentralWidget(root)
        status_bar = self.statusBar()
        status_bar.setObjectName("inlineNoticeBar")
        status_bar.setAccessibleName("Уведомления Chess Coach")
        self.setStyleSheet(desktop_stylesheet())
        self._apply_board_size()
        self._set_sidebar_collapsed(self.width() < 1160)
        self._apply_header_responsive()
        self._set_active_route("home")
        self._update_play_mode_availability()
        QTimer.singleShot(0, self._apply_board_size)

    def _board_size_changed(self, _index: int) -> None:
        if not hasattr(self, "board_size_combo"):
            return
        size = int(self.board_size_combo.currentData() or 480)
        self.settings.setValue("board_target_size", size)
        self._apply_board_size()

    def _apply_board_size(self) -> None:
        if not hasattr(self, "board"):
            return
        target = int(self.settings.value("board_target_size", 480, type=int))
        # Measure the actual splitter column and the real space between controls;
        # do not guess widths from nominal sidebar/inspector constants.
        column = self.board_column
        contents = column.contentsRect()
        layout = column.layout()
        margins = layout.contentsMargins()
        visible_non_board = [item.widget() for index in range(layout.count())
                             if (item := layout.itemAt(index)).widget() is not None
                             and item.widget() is not self.board
                             and not item.widget().isHidden()]
        occupied_height = sum(widget.sizeHint().height() for widget in visible_non_board)
        occupied_height += max(0, len(visible_non_board)) * layout.spacing()
        available_width = max(160, contents.width() - margins.left() - margins.right())
        available_height = max(
            160, column.height() - margins.top() - margins.bottom() - occupied_height)
        requested = min(available_width, available_height) if target <= 0 else target
        actual = max(160, min(requested, available_width, available_height))
        self.board.setFixedSize(actual, actual)
        if hasattr(self, "review_workspace"):
            sidebar_width = self.sidebar.width() if hasattr(self, "sidebar") else 212
            focus = bool(getattr(self, "_board_focus_mode", False))
            inspector_width = 0 if focus else 340
            review_width = max(320, self.width() - sidebar_width - inspector_width - 96)
            review_height = max(320, self.height() - 64 - 210)
            review_size = max(320, min(requested, review_width, review_height))
            self.review_workspace.review_board.setFixedSize(review_size, review_size)

    def _set_focus_mode(self, enabled: bool) -> None:
        self._board_focus_mode = bool(enabled)
        if hasattr(self, "game_splitter"):
            self.game_splitter.widget(1).setVisible(not enabled)
        self.focus_mode_button.setText("Вернуть панель" if enabled else "Режим фокуса")
        self.settings.setValue("workspace/board_focus_mode", bool(enabled))
        self._apply_board_size()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (hasattr(self, "sidebar") and not self._sidebar_explicit_override
                and self.width() < 1160):
            self._set_sidebar_collapsed(True)
        self._update_sidebar_profile_label()
        self._apply_board_size()
        if hasattr(self, "game_splitter"):
            self.game_splitter.setOrientation(
                Qt.Orientation.Vertical if self.width() < 1120 else Qt.Orientation.Horizontal)
            QTimer.singleShot(0, self._apply_board_size)
        self._apply_header_responsive()
        self._layout_dashboard_metrics()
        self._layout_play_mode_cards()
        self._schedule_workspace_save()

    def _apply_header_responsive(self) -> None:
        if not hasattr(self, "header_widget"):
            return
        width = max(0, self.header_widget.contentsRect().width())
        compact = width < 940
        sidebar_collapsed = self.sidebar.width() <= 60
        if sidebar_collapsed:
            connect_text, disconnect_text = "↗", "⏻"
            connect_description = ("Подключиться вручную"
                                   if self.manual_connect_mode and not self.lichess
                                   else "Подключить Lichess")
            self.connect_button.setToolTip(connect_description)
            self.connect_button.setAccessibleName(connect_description)
            self.disconnect_button.setToolTip("Отключить Lichess")
            self.connect_button.setFixedWidth(34)
            self.disconnect_button.setFixedWidth(34)
        elif self.manual_connect_mode and not self.lichess:
            connect_text = "Подключиться вручную"
            disconnect_text = "Отключить" if compact else "Отключить Lichess"
            self.connect_button.setToolTip("")
        else:
            connect_text = "Подключить" if compact else "Подключить Lichess"
            disconnect_text = "Отключить" if compact else "Отключить Lichess"
            self.connect_button.setToolTip("")
        if not sidebar_collapsed:
            self.connect_button.setFixedWidth(188)
            self.disconnect_button.setFixedWidth(188)
        self.connect_button.setText(connect_text)
        self.disconnect_button.setText(disconnect_text)
        self.background_operations_button.setText("⋯" if compact else "Фон")
        self.header_brand.setVisible(self.sidebar.width() <= 60)
        self.connection.setMaximumWidth(150 if compact else 210)
        self._render_connection_header()

    def _set_connection_header(self, label: str, tooltip: str | None = None) -> None:
        """Keep the global header status short; details live in the sidebar/tooltip."""
        self._connection_header_label = str(label)
        self._connection_header_tooltip = str(tooltip or label)
        self._render_connection_header()

    def _render_connection_header(self) -> None:
        label = str(getattr(self, "_connection_header_label", "Нет связи"))
        available = self.connection.contentsRect().width()
        if available > 0:
            label = QFontMetrics(self.connection.font()).elidedText(
                label, Qt.TextElideMode.ElideRight, available)
        self.connection.setText(label)
        self.connection.setToolTip(str(getattr(
            self, "_connection_header_tooltip", "Состояние подключения Lichess")))

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_workspace_save()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("appSidebar")
        sidebar.setFixedWidth(212)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)
        self.sidebar_toggle = QPushButton("☰")
        self.sidebar_toggle.setObjectName("navItem")
        self.sidebar_toggle.setToolTip("Свернуть или развернуть меню")
        self.sidebar_toggle.setAccessibleName("Свернуть или развернуть боковое меню")
        self.sidebar_toggle.clicked.connect(lambda: self._set_sidebar_collapsed(
            self.sidebar.width() > 60, explicit=True))
        layout.addWidget(self.sidebar_toggle)
        self.sidebar_brand = QLabel("♟  Chess Coach")
        self.sidebar_brand.setStyleSheet("font-size: 18px; font-weight: 700; padding: 4px;")
        layout.addWidget(self.sidebar_brand)
        self.sidebar_profile_label = QLabel("Локальный профиль")
        self.sidebar_profile_label.setObjectName("secondary")
        self.sidebar_profile_label.setWordWrap(False)
        self.sidebar_profile_label.setToolTip("Текущий профиль данных")
        layout.addWidget(self.sidebar_profile_label)
        self.sidebar_connection_label = QLabel("Не подключено")
        self.sidebar_connection_label.setObjectName("muted")
        self.sidebar_connection_label.setWordWrap(False)
        layout.addWidget(self.sidebar_connection_label)
        self.sidebar_safety_label = QLabel("Fair Play: не проверено")
        self.sidebar_safety_label.setObjectName("muted")
        self.sidebar_safety_label.setWordWrap(False)
        layout.addWidget(self.sidebar_safety_label)
        self.safety_retry_button = QPushButton("↻ Проверить безопасность")
        self.safety_retry_button.setObjectName("safetyRetry")
        self.safety_retry_button.setAccessibleName("Повторить проверку безопасности Lichess")
        self.safety_retry_button.setToolTip(
            "Повторно проверить список активных партий. Оценки останутся скрыты до успешного ответа.")
        self.safety_retry_button.clicked.connect(
            self._request_dashboard_display_safety_from_button)
        self.safety_retry_button.hide()
        layout.addWidget(self.safety_retry_button)
        self._nav_buttons: dict[str, QPushButton] = {}
        items = (("home", "⌂", "Главная", self.show_home),
                 ("play", "♟", "Играть", self.show_play),
                 ("games", "▤", "Мои партии", self.show_saved_games),
                 ("analysis", "⌁", "Анализ", self.show_analysis),
                 ("training", "♞", "Обучение", self.open_puzzle_history_local),
                 ("progress", "◷", "Прогресс", self.show_statistics))
        for key, icon, label, callback in items:
            button = QPushButton(f"{icon}   {label}")
            button.setObjectName("navItem")
            button.setProperty("route", key)
            button.setProperty("route_label", label)
            button.setMinimumHeight(44)
            button.setAccessibleName(label)
            button.clicked.connect(callback)
            self._nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)
        self.sidebar_settings = QPushButton("⚙   Настройки")
        self.sidebar_settings.setObjectName("navItem")
        self.sidebar_settings.setAccessibleName("Настройки")
        self.sidebar_settings.clicked.connect(self.open_settings)
        layout.addWidget(self.sidebar_settings)
        self.sidebar_layout = layout
        self._sidebar_explicit_override = False
        return sidebar

    def _set_sidebar_collapsed(self, collapsed: bool, *, explicit: bool = False) -> None:
        if explicit:
            self._sidebar_explicit_override = True
        collapsed = bool(collapsed)
        self.sidebar.setFixedWidth(60 if collapsed else 212)
        self.sidebar_brand.setVisible(not collapsed)
        self.sidebar_profile_label.setVisible(not collapsed)
        self.sidebar_connection_label.setVisible(not collapsed)
        self.sidebar_safety_label.setVisible(not collapsed)
        self._update_sidebar_profile_label()
        self.sidebar_settings.setText("⚙" if collapsed else "⚙   Настройки")
        self.sidebar_settings.setToolTip("Настройки")
        if hasattr(self, "connect_button"):
            self.connect_button.setText("↗" if collapsed else "Подключить Lichess")
            self.disconnect_button.setText("⏻" if collapsed else "Отключить Lichess")
            connect_description = ("Подключиться вручную"
                                   if self.manual_connect_mode and not self.lichess
                                   else "Подключить Lichess")
            self.connect_button.setToolTip(connect_description if collapsed else "")
            self.disconnect_button.setToolTip(
                "Отключить Lichess" if collapsed else
                "Остановить текущую Lichess-сессию, сохранив учётные данные.")
            self.connect_button.setAccessibleName(connect_description)
            self.disconnect_button.setAccessibleName("Отключить Lichess")
            self.connect_button.setFixedWidth(34 if collapsed else 188)
            self.disconnect_button.setFixedWidth(34 if collapsed else 188)
        if hasattr(self, "safety_retry_button"):
            self.safety_retry_button.setText("↻" if collapsed else "↻ Проверить безопасность")
            self.safety_retry_button.setToolTip(
                "Повторить проверку безопасности" if collapsed else
                "Повторно проверить список активных партий; оценки до ответа скрыты.")
            self.safety_retry_button.setAccessibleName(
                "Повторить проверку безопасности Lichess")
            self.safety_retry_button.setFixedWidth(34 if collapsed else 188)
        icons = {"home": "⌂", "play": "♟", "games": "▤", "analysis": "⌁",
                 "training": "♞", "progress": "◷"}
        for key, button in self._nav_buttons.items():
            label = button.property("route_label")
            button.setText(icons[key] if collapsed else f"{icons[key]}   {label}")
            button.setToolTip(label if collapsed else "")
        self.header_brand.setVisible(collapsed)
        self._apply_board_size()
        self._apply_header_responsive()
        self._schedule_workspace_save()

    def _update_sidebar_profile_label(self) -> None:
        label = getattr(self, "sidebar_profile_label", None)
        if label is None:
            return
        username = str(getattr(self, "_lichess_profile_username", "") or "")
        if not username:
            label.setText("Локальный профиль")
            label.setToolTip("Текущий профиль данных")
            return
        label.setText(QFontMetrics(label.font()).elidedText(
            username, Qt.TextElideMode.ElideRight, max(60, label.width())))
        label.setToolTip(username)

    def _set_sidebar_safety_status(self, text: str) -> None:
        label = getattr(self, "sidebar_safety_label", None)
        if label is not None:
            label.setText(str(text))
            label.setToolTip(str(text))

    def _shell_screen_changed(self, _index: int) -> None:
        if not hasattr(self, "sidebar"):
            return
        if self.screen_stack.currentWidget() is not self.analysis_page:
            handle = getattr(self, "_external_study_handle", None)
            if handle is not None and handle.is_alive():
                self._external_study_generation += 1
                handle.cancel("session_replaced")
        if self.screen_stack.currentWidget() is not getattr(self, "library_page", None):
            handle = getattr(self, "_library_query_handle", None)
            if handle is not None and handle.is_alive():
                self._library_query_generation += 1
                handle.cancel("session_replaced")
            detail_handle = getattr(self, "_library_detail_handle", None)
            if detail_handle is not None and detail_handle.is_alive():
                self._library_detail_generation += 1
                detail_handle.cancel("session_replaced")
        if (self.screen_stack.currentWidget() is not self.review_workspace
                and self._review_safety_request_id is not None):
            self._review_safety_request_id = None
            self._review_safety_context = None
            self.review_workspace.revoke_safety(
                "Проверка отменена: вы перешли с экрана разбора. Запросите новую проверку.")
        is_board = self.screen_stack.currentWidget() in (
            self.game_page, self.result_page, getattr(self, "review_workspace", None))
        current = self.screen_stack.currentWidget()
        self.screen_title.setVisible(current is not self.review_workspace)
        if current is getattr(self, "review_workspace", None) and self._ephemeral_study:
            route = WorkspaceRoute.ANALYSIS
            context = None
        elif current is getattr(self, "review_workspace", None) or current is getattr(self, "library_page", None):
            route = WorkspaceRoute.GAMES
            context = ((self._review_workspace_identity() or (None,))[0]
                       if current is getattr(self, "review_workspace", None) else None)
        elif current is getattr(self, "analysis_page", None):
            route, context = WorkspaceRoute.ANALYSIS, None
        elif current is getattr(self, "_batch_dialog", None):
            route, context = WorkspaceRoute.ANALYSIS, "batch"
        elif current is getattr(self, "_background_operations_page", None):
            route, context = WorkspaceRoute.ANALYSIS, "background"
        elif current is getattr(self, "_settings_dialog", None):
            route, context = WorkspaceRoute.SETTINGS, None
        elif current is getattr(self, "_puzzle_page", None):
            route, context = WorkspaceRoute.TRAINING, None
        elif current is getattr(self, "_statistics_dialog", None):
            route, context = WorkspaceRoute.PROGRESS, None
        elif is_board or current is getattr(self, "play_page", None):
            route = WorkspaceRoute.PLAY
            context = self.online_session.game_id if self.online_session else None
        else:
            route = WorkspaceRoute.HOME
            context = None
        titles = {
            WorkspaceRoute.HOME: "Главная", WorkspaceRoute.PLAY: "Играть",
            WorkspaceRoute.GAMES: "Мои партии", WorkspaceRoute.ANALYSIS: "Анализ",
            WorkspaceRoute.TRAINING: "Обучение", WorkspaceRoute.PROGRESS: "Прогресс",
            WorkspaceRoute.SETTINGS: "Настройки",
        }
        if current is self.review_workspace:
            self.screen_title.setText("Временный разбор" if self._ephemeral_study else "Разбор партии")
        elif current is getattr(self, "_batch_dialog", None) and current is not None:
            self.screen_title.setText("Массовый анализ")
        elif current is getattr(self, "_background_operations_page", None) and current is not None:
            self.screen_title.setText("Фоновые операции")
        else:
            self.screen_title.setText(titles[route])
        self.workspace_navigator.navigate(route, context=context)
        self._active_route = route.value
        self._set_active_route(route.value)
        self.back_button.setVisible(
            route is not WorkspaceRoute.HOME and self.workspace_navigator.can_go_back)
        self._update_return_to_game_button()
        if not self._sidebar_explicit_override:
            self._set_sidebar_collapsed(is_board or self.width() < 1160)
        self._schedule_workspace_save()

    def _restore_window_frame(self) -> None:
        saved = self.settings.value("workspace/window_geometry")
        if saved:
            try:
                self.restoreGeometry(saved)
            except (TypeError, ValueError):
                pass
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        frame = self.frameGeometry()
        if screens:
            corrected = reposition_visible_frame(frame, screens)
            if corrected.size() != frame.size():
                self.resize(corrected.size())
            if corrected.topLeft() != frame.topLeft():
                self.move(corrected.topLeft())

    def _schedule_workspace_save(self, *_args) -> None:
        timer = getattr(self, "_workspace_save_timer", None)
        if timer is not None and not self._workspace_state_restoring:
            timer.start()

    def _persist_workspace_state(self) -> None:
        if self._workspace_state_restoring:
            return
        resume_target = None
        review = getattr(self, "review_workspace", None)
        if review is not None and review.review and not self._ephemeral_study:
            line = review.review["line"]
            resume_target = ReviewResumeTarget(
                game_id=str(review.review["game"]["game_id"]),
                line_revision=int(line["line_revision"]),
                line_hash=str(line["line_hash"]),
                viewed_ply=int(review.selected_ply),
                inspector_tab=int(review.tabs.currentIndex()),
            )
        self.workspace_state.save(WorkspaceSnapshot(
            route=self._active_route,
            geometry=self.saveGeometry(),
            sidebar_collapsed=self.sidebar.width() <= 60,
            sidebar_explicit=self._sidebar_explicit_override,
            board_target_size=int(self.settings.value("board_target_size", 480, type=int)),
            review=resume_target,
        ))

    def _review_position_selected(self, _game_id: str, _ply: int) -> None:
        self._schedule_workspace_save()

    def _restore_workspace_after_startup(self) -> None:
        if self.offline_mode or self._shutdown_requested:
            return
        route = self.workspace_state.last_route()
        if route != "games":
            return
        if self.screen_stack.currentWidget() is not self.home_page:
            return
        if self.resume_saved_review(show_message=False):
            return
        self._active_route = "home"
        self._set_active_route("home")
        self.show_home()

    def resume_saved_review(self, *, show_message: bool = True) -> bool:
        target = self.workspace_state.load_review_target()
        if target is None:
            if show_message:
                self._notify_inline("Нет сохранённого разбора для продолжения.")
            return False
        game_id = target.game_id
        game = self.game_repository.get_game(game_id)
        line = self.game_repository.current_line(game_id) if game else None
        if (not game or not line or not self._is_standard_board_game(game)
                or game.get("status") in {"created", "started", "started_soon", "playing"}):
            self._active_route = "home"
            self.workspace_state.remove_review_target()
            self._schedule_workspace_save()
            if show_message:
                self._notify_inline("Сохранённый разбор недоступен; проверьте библиотеку партий.")
            return False
        same_line = (int(line["line_revision"]) == target.line_revision
                     and str(line["line_hash"]) == target.line_hash)
        self._workspace_state_restoring = True
        try:
            if not self._open_saved_game(game):
                return False
            ply = target.viewed_ply if same_line else 0
            self.review_workspace.select_ply(max(0, min(len(self.review_workspace.review["moves"]), ply)))
            self.review_workspace.tabs.setCurrentIndex(max(
                0, min(self.review_workspace.tabs.count() - 1, target.inspector_tab)))
            if not same_line:
                self.screen_title.setText("Разбор продолжен с начала · сохранённая линия изменилась")
                self._notify_inline("Линия партии изменилась; разбор открыт с начальной позиции.")
            else:
                self.screen_title.setText("Продолжение разбора")
            return True
        finally:
            self._workspace_state_restoring = False
            self._schedule_workspace_save()

    def _notify_inline(self, message: str, timeout_ms: int = 9000) -> None:
        status = self.statusBar()
        status.setAccessibleName("Уведомление: " + str(message)[:120])
        status.showMessage(str(message), max(1000, int(timeout_ms)))

    def _start_registered_work(self, target, *, kind: str, lane: OperationLane,
                               thread_name: str, stop_event: threading.Event | None = None):
        """Register a shell-owned worker without serializing it behind other lanes."""
        return self.runtime.work_coordinator.start(
            target, kind=kind, lane=lane,
            auth_generation=(self._analysis_session_generation
                             if hasattr(self, "_analysis_session_generation") else None),
            stop_event=stop_event, thread_name=thread_name)

    def _set_active_route(self, route: str) -> None:
        for key, button in self._nav_buttons.items():
            button.setProperty("active", key == route)
            button.style().unpolish(button)
            button.style().polish(button)

    def _build_home_page(self) -> QWidget:
        view = HomePage(
            self.game_repository, self.settings,
            getattr(self.lichess, "username", None) if self.lichess else None,
            {
                "play_ai": self.play_ai, "start_local_game": self.start_local_game,
                "resume_current_game": self.resume_current_game,
                "show_saved_games": self.show_saved_games,
                "resume_saved_review": self.resume_saved_review,
                "open_friend_game": self.open_friend_game,
                "challenge_username": self.challenge_username,
                "show_active_games": self.show_active_games,
                "show_archive_status": self.show_archive_status,
                "start_archive_sync": self.start_archive_sync,
                "show_statistics": self.show_statistics,
                "show_batch_analysis": self.show_batch_analysis,
                "open_puzzle_history_local": self.open_puzzle_history_local,
                "open_settings": self.open_settings,
            }, self._dashboard_filter_changed)
        self._home_view = view
        self._dashboard_metric_columns = view._dashboard_metric_columns
        for name in (
            "home_status", "dashboard_account", "dashboard_clock", "dashboard_metrics",
            "dashboard_metrics_grid", "_dashboard_metric_values", "_dashboard_metric_details",
            "home_recent_card", "_recent_games_layout", "home_primary_actions",
            "home_activity_button", "activity_status",
            "home_play_card", "home_progress_card", "home_all_progress_button",
            "progress_trend", "recent_safety_notice",
            "home_ai_button", "home_local_button", "resume_game_button",
            "home_review_button", "resume_review_button", "home_friend_button",
            "home_username_button", "active_games_button", "home_games_button",
            "home_archive_status_button", "home_archive_button",
            "home_stats_button", "home_batch_analysis_button", "home_puzzles_button",
            "home_settings_button", "home_more_toggle",
            "home_more", "home_more_content",
        ):
            setattr(self, name, getattr(view, name))
        return view

    def _build_play_page(self) -> QWidget:
        page = PlayPage({
            "local": self.start_local_game,
            "ai": self.play_ai,
            "friend": self.open_friend_game,
            "challenge": self.challenge_username,
            "active": self.show_active_games,
        })
        page.create_requested.connect(self._create_play_request)
        page.cancel_requested.connect(self._cancel_play_challenge_wait)
        page.cancel_invitation_requested.connect(self.cancel_friend_invitation)
        self._play_mode_page = page
        self._play_mode_columns = page._columns
        self.play_local_button = page.buttons["local"]
        self.play_ai_button = page.buttons["ai"]
        self.play_friend_button = page.buttons["friend"]
        self.play_challenge_button = page.buttons["challenge"]
        self.play_active_button = page.buttons["active"]
        return page

    def _layout_play_mode_cards(self) -> None:
        page = getattr(self, "_play_mode_page", None)
        if page is not None:
            page.layout_modes(self.width())
            self._play_mode_columns = page._columns

    def show_play(self) -> None:
        """Show mode selection without resetting the controller or online game."""
        self.screen_title.setText("Играть")
        self.screen_stack.setCurrentWidget(self.play_page)
        self._update_play_mode_availability()

    def _create_play_request(self, options: dict) -> None:
        """Execute the embedded Play form through the existing Lichess façade."""
        mode = str(options.get("mode") or "local")
        if mode == "local":
            self.start_local_game()
            return
        if mode == "active":
            self.show_active_games()
            return
        if not self._online_actions_allowed() or self._awaiting_game_start:
            self._play_mode_page.set_status(
                "Подключитесь к Lichess и дождитесь готовности соединения; повторный запрос не отправлен.")
            return
        color = options.get("color") or "random"
        limit = options.get("clock_limit")
        increment = max(0, int(options.get("clock_increment") or 0))
        rated = bool(options.get("rated"))
        if mode == "ai":
            level = max(1, min(8, int(options.get("level") or 1)))
            self.settings.setValue("workspace/ai_color", color)
            self.settings.setValue("workspace/ai_clock_limit", -1 if limit is None else int(limit))
            self.settings.setValue("workspace/ai_clock_increment", increment)
            self.settings.setValue("workspace/ai_level", level)
            self._awaiting_game_start = True
            self._play_mode_page.set_status("Создаём нерейтинговую партию против Lichess AI…")
            self.lichess.create_ai_game(level, color, limit, increment)
        elif mode == "friend":
            self._awaiting_game_start = True
            self._play_mode_page.set_status("Создаём ссылку-приглашение…")
            self.lichess.open_friend_game(color, limit, increment, rated)
        elif mode == "challenge":
            username = str(options.get("username") or "").strip()
            if not username:
                self._play_mode_page.set_status("Введите имя пользователя Lichess.")
                return
            self._awaiting_game_start = True
            self._play_mode_page.set_status(f"Отправляем вызов игроку {username}…")
            self._play_mode_page.set_request_pending(True, cancellable=True)
            self.lichess.challenge_username(username, color, limit, increment, rated)

    def _cancel_play_challenge_wait(self) -> None:
        if not self._awaiting_game_start or not self.lichess:
            return
        cancellation_started = self.lichess.cancel_username_challenge()
        if cancellation_started:
            # Keep the create action disabled until the server confirms the
            # cancellation (including the case where its challenge ID is late).
            message = "Отмена запрошена; ждём подтверждение Lichess."
            self.game_status.setText(message)
            self._play_mode_page.set_status(message)
            # set_status() clears ordinary pending state for terminal/error
            # messages; re-assert it after setting this non-terminal status.
            self._play_mode_page.set_request_pending(True, cancellable=False)
            self.cancel_challenge_button.setEnabled(False)
            self.cancel_challenge_button.setVisible(False)
            self._awaiting_game_start = True
            return
        self._awaiting_game_start = False
        self._play_mode_page.set_request_pending(False)
        self._play_mode_page.set_status("Ожидание вызова остановлено.")

    def show_analysis(self) -> None:
        """Show analysis entry points without starting an engine or network job."""
        self.screen_title.setText("Анализ")
        self.screen_stack.setCurrentWidget(self.analysis_page)

    def open_external_pgn_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть PGN для временного разбора", "", "PGN (*.pgn);;Все файлы (*.*)")
        if not path:
            return
        self._start_external_study_load(file_path=path)

    def open_external_study_text(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            self.analysis_page.set_external_status("Вставьте одну PGN-партию или FEN.")
            return False
        if len(value.encode("utf-8")) > _MAX_EXTERNAL_STUDY_BYTES:
            self.analysis_page.set_external_status("Ввод превышает безопасный лимит 4 МиБ.")
            return False
        self._start_external_study_load(text=value)
        return True

    def _start_external_study_load(self, *, text: str | None = None,
                                   file_path: str | None = None) -> None:
        self._external_study_generation += 1
        generation = self._external_study_generation
        previous = getattr(self, "_external_study_handle", None)
        if previous is not None:
            previous.cancel("session_replaced")
        stop_event = threading.Event()
        self.analysis_page.set_external_status("Читаю и проверяю PGN/FEN в фоновой задаче…")

        def worker() -> None:
            try:
                if stop_event.is_set():
                    return
                source_text = text
                if file_path is not None:
                    path = Path(file_path)
                    if path.stat().st_size > _MAX_EXTERNAL_STUDY_BYTES:
                        raise ValueError("Файл превышает безопасный лимит 4 МиБ.")
                    source_text = path.read_text(encoding="utf-8-sig")
                parsed = _parse_external_study_text(source_text or "")
                if stop_event.is_set():
                    return
                self.external_study_finished.emit(generation, parsed, None)
            except (OSError, UnicodeError, ValueError, chess.InvalidMoveError,
                    chess.IllegalMoveError) as exc:
                if not stop_event.is_set():
                    self.external_study_finished.emit(generation, None, str(exc))
            except Exception as exc:
                if not stop_event.is_set():
                    self.external_study_finished.emit(
                        generation, None, f"Не удалось разобрать данные ({type(exc).__name__}).")

        self._external_study_handle = self._start_registered_work(
            worker, kind="external_study_parse", lane=OperationLane.INTERACTIVE,
            thread_name="cc-external-study", stop_event=stop_event)

    def _external_study_parse_finished(self, generation: int, parsed: object,
                                       error: object) -> None:
        if generation != self._external_study_generation:
            return
        if self.screen_stack.currentWidget() is not self.analysis_page:
            self.analysis_page.set_external_status(
                "Разбор отменён: вы перешли в другой раздел.")
            return
        if error:
            self.analysis_page.set_external_status(f"Не удалось открыть PGN/FEN: {error}")
            return
        if not isinstance(parsed, dict):
            self.analysis_page.set_external_status("Не удалось прочитать результат разбора.")
            return
        headers = dict(parsed.get("headers") or {})
        initial_fen = str(parsed["initial_fen"])
        moves = list(parsed.get("moves") or [])
        game_id = "external:" + uuid.uuid4().hex
        result = str(headers.get("Result") or "*")
        winner = "white" if result == "1-0" else "black" if result == "0-1" else None
        status = "finished" if result in {"1-0", "0-1", "1/2-1/2"} else "unknownFinish"
        record = {
            "game_id": game_id, "initial_fen": initial_fen, "moves": " ".join(moves),
            "line_revision": 1, "line_hash": "ephemeral", "color": "white",
            "opponent": "Внешняя партия", "rated": False, "clock": "-",
            "status": status, "winner": winner, "source": "external",
            "source_game_id": None, "variant": "standard", "import_error": None,
            "white_player": headers.get("White", "Белые"),
            "black_player": headers.get("Black", "Чёрные"),
            "started_at": headers.get("Date"), "game_time_precision": "unknown",
            "eco": headers.get("ECO"), "opening_name": headers.get("Opening"),
        }
        self._ephemeral_study = True
        self.review_workspace.repository = EphemeralReviewRepository(
            game_id, initial_fen, record["moves"])
        self.online_session = None
        self._review_session = None
        self.online_view_ply = None
        self._screen_mode = "review"
        try:
            self.review_workspace.load_game(
                record, training_allowed=False,
                training_block_reason="Внешняя позиция открыта временно; сохранённой аналитики нет.",
                ephemeral=True)
        except (KeyError, ValueError) as exc:
            self._ephemeral_study = False
            self.review_workspace.repository = self.game_repository
            self.show_analysis()
            self.analysis_page.set_external_status(f"Не удалось открыть внешнюю линию: {exc}")
            return
        self.screen_title.setText("Временный разбор")
        self.screen_stack.setCurrentWidget(self.review_workspace)
        self.analysis_page.set_external_status("Временная партия открыта; исходные данные не сохранены.")

    def _return_from_review(self) -> None:
        if self._ephemeral_study:
            self._ephemeral_study = False
            self.review_workspace.repository = self.game_repository
            self.show_analysis()
            return
        if self.workspace_navigator.can_go_back:
            self.navigate_back()
        else:
            self.show_saved_games()

    def navigate_back(self) -> None:
        entry = self.workspace_navigator.back()
        route = entry.route
        if route is WorkspaceRoute.HOME:
            target = self.home_page
        elif route is WorkspaceRoute.PLAY:
            session = self.online_sessions.get(entry.context) if entry.context else None
            if session is not None:
                self._selected_live_game_id = session.game_id
                self.online_session = session
                self.resume_current_game()
                return
            target = self.play_page
        elif route is WorkspaceRoute.GAMES:
            target = getattr(self, "library_page", self.home_page)
        elif route is WorkspaceRoute.ANALYSIS:
            target = (self._batch_dialog if entry.context == "batch" and
                      self._batch_dialog is not None else
                      self._background_operations_page if entry.context == "background" and
                      self._background_operations_page is not None else self.analysis_page)
        elif route is WorkspaceRoute.PROGRESS:
            target = getattr(self, "_statistics_dialog", self.home_page)
        elif route is WorkspaceRoute.SETTINGS:
            target = getattr(self, "_settings_dialog", self.home_page)
        else:
            target = getattr(self, "_puzzle_page", self.home_page)
        self.screen_stack.setCurrentWidget(target)

    def _update_return_to_game_button(self) -> None:
        session = self.online_sessions.get(self._selected_live_game_id)
        live = bool(session and session.status in {"created", "started", "playing"})
        on_board = self.screen_stack.currentWidget() is self.game_page
        self.return_to_game_button.setVisible(live and not on_board)

    def _update_play_mode_availability(self) -> None:
        if not hasattr(self, "play_ai_button"):
            return
        online = self._online_actions_allowed()
        for button in (self.play_ai_button, self.play_friend_button,
                       self.play_challenge_button, self.play_active_button):
            button.setEnabled(online)
            button.setToolTip("Подключите Lichess; запросы остаются отключены до готовности соединения."
                              if not online else "Доступно при активном соединении Lichess.")
        self._update_home_game_actions()

    def _update_home_game_actions(self) -> None:
        if not hasattr(self, "home_ai_button"):
            return
        online = self._online_actions_allowed()
        reason = ("Подключите Lichess и дождитесь готовности соединения; свободная доска доступна без сети."
                  if not online else "Доступно при активном соединении Lichess.")
        for button in (self.home_ai_button, self.home_friend_button,
                       self.home_username_button, self.active_games_button):
            button.setEnabled(online)
            button.setToolTip(reason)

    def _refresh_home_dashboard(self) -> None:
        if not hasattr(self, "_dashboard_metric_values"):
            return
        account_id = self.dashboard_account.currentData() if hasattr(self, "dashboard_account") else None
        if account_id is None and hasattr(self, "dashboard_account"):
            preferred = str(self.settings.value("workspace/selected_account", "", type=str))
            if preferred.startswith("lichess:"):
                username = preferred.partition(":")[2].casefold()
                for index in range(self.dashboard_account.count()):
                    if self.dashboard_account.itemText(index).casefold() == username:
                        account_id = self.dashboard_account.itemData(index)
                        break
        clock = None
        generation = self.dashboard_queries.begin_request()
        for detail in self._dashboard_metric_details.values():
            if detail.text() in {"Локальные данные", "Обновляем локальную сводку…"}:
                detail.setText("Обновляем локальную сводку…")
        if self._dashboard_thread is not None and self._dashboard_thread.is_alive():
            self.dashboard_queries.defer((generation, account_id, clock))
            return
        self._launch_dashboard_snapshot(generation, account_id, clock)

    def _clear_dashboard_scope(self, message: str) -> None:
        """Immediately remove previous-account dashboard values during a switch."""
        for key, value in self._dashboard_metric_values.items():
            value.setText("0" if key == "games" else "н/д")
        for detail in self._dashboard_metric_details.values():
            detail.setText(message)
            detail.setToolTip("")
        while self._recent_games_layout.count():
            item = self._recent_games_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        placeholder = QLabel(message)
        placeholder.setObjectName("secondary")
        placeholder.setWordWrap(True)
        self._recent_games_layout.addWidget(placeholder)
        self._home_view.progress_trend.setText("Загружаем историю выбранного профиля…")

    def _launch_dashboard_snapshot(self, generation: int, account_id: str | None,
                                   clock: str | None) -> None:
        def work():
            try:
                result = self.dashboard_queries.snapshot(
                    account_id=account_id, clock=clock, query=dashboard_snapshot)
                if not self._dashboard_options_loaded:
                    result["_selector_options"] = dashboard_options(self.game_repository)
                error = None
            except Exception as exc:
                result, error = None, exc
            self.dashboard_finished.emit(generation, result, error, threading.current_thread())
        thread = self._start_registered_work(
            work, kind="dashboard_read", lane=OperationLane.NETWORK_BACKGROUND,
            thread_name="chess-coach-dashboard-read")
        self._dashboard_thread = thread

    def _dashboard_result_ready(self, generation: int, snapshot: object, error: object,
                                thread: object) -> None:
        if isinstance(thread, threading.Thread):
            thread.join(timeout=0.05)
            if thread.is_alive():
                # The worker emits immediately before returning. Delay handling
                # until teardown so a deferred request cannot overlap it.
                QTimer.singleShot(10, lambda: self._dashboard_result_ready(
                    generation, snapshot, error, thread))
                return
        if self._dashboard_thread is not None and not self._dashboard_thread.is_alive():
            self._dashboard_thread = None
        if self._shutdown_requested:
            self.dashboard_queries.clear_pending()
            return
        if self.dashboard_queries.is_current(generation):
            if error is not None:
                for value in self._dashboard_metric_values.values():
                    value.setText("н/д")
                for detail in self._dashboard_metric_details.values():
                    detail.setText("Не удалось прочитать локальные данные. Повторите обновление.")
            elif isinstance(snapshot, dict):
                selector_options = snapshot.get("_selector_options")
                if isinstance(selector_options, dict):
                    self._home_view.set_selector_options(selector_options)
                    self._dashboard_options_loaded = True
                    if (self.dashboard_account.currentData()
                            != snapshot.get("filter", {}).get("account_id")):
                        self._refresh_home_dashboard()
                    else:
                        self._render_dashboard_snapshot(snapshot)
        pending = self.dashboard_queries.take_pending()
        if pending is not None and not self._shutdown_requested:
            self._launch_dashboard_snapshot(*pending)

    def _render_dashboard_snapshot(self, snapshot: dict) -> None:
        self._latest_dashboard_snapshot = snapshot
        trend = snapshot.get("activity_trend") or []
        if trend:
            points = [f"{item['month']}: {item['average_rating']} · {item['attempts']} попыток"
                      for item in trend if item.get("average_rating") is not None]
            self._home_view.progress_trend.setText(
                "Задачи Lichess · "
                f"{sum(int(item.get('attempts') or 0) for item in trend)}. "
                + (" → ".join(points[-4:]) if points else "Нет рейтинговых точек."))
            self._home_view.progress_trend.setToolTip(
                "Источник: локально сохранённые попытки Lichess; сгруппировано по месяцу. "
                "Это исторический прогресс, не рекомендация занятия.")
        elif snapshot.get("filter", {}).get("account_id"):
            self._home_view.progress_trend.setText("Для этого профиля пока нет точек тренда.")
            self._home_view.progress_trend.setToolTip("Здесь отображается только сохранённая история.")
        else:
            self._home_view.progress_trend.setText("Нет сохранённых точек тренда.")
            self._home_view.progress_trend.setToolTip("Нет выбранного профиля с историей попыток.")
        active_human = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        analysis_allowed = bool(self.offline_mode or
                                (self._dashboard_analysis_safety_allowed and not active_human))
        self._home_view.recent_safety_notice.setVisible(
            not analysis_allowed and not self.offline_mode)
        for metric in snapshot["metrics"]:
            masked = metric["key"] in {"accuracy", "analysis", "errors"} and not analysis_allowed
            self._dashboard_metric_values[metric["key"]].setText(
                "скрыто" if masked else metric["value"])
            detail_text = str(metric["detail"])
            if metric["key"] == "analysis" and not masked:
                parts = detail_text.split(" · ")
                if len(parts) >= 5 and parts[0].startswith("Полный охват:"):
                    full = parts[0].partition(":")[2].strip()
                    partial = parts[1].partition(":")[2].strip()
                    own_moves = parts[-1].replace("оценено моих ходов:", "мои ходы:")
                    detail_text = f"Полный {full} · частичный {partial} · {own_moves}"
            self._dashboard_metric_details[metric["key"]].setText(
                "Проверка безопасности Lichess не подтверждена." if masked else detail_text)
            safe_description = ("Аналитика скрыта до свежего разрешения Fair Play."
                                if masked else
                                f"{metric['detail']}\nИсточник: {metric['source']}"
                                if metric["key"] == "analysis" else str(metric["source"]))
            self._dashboard_metric_values[metric["key"]].setToolTip(safe_description)
            self._dashboard_metric_details[metric["key"]].setToolTip(safe_description)
            self._dashboard_metric_values[metric["key"]].setAccessibleDescription(safe_description)
            self._dashboard_metric_details[metric["key"]].setAccessibleDescription(safe_description)
        while self._recent_games_layout.count():
            item = self._recent_games_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        games = snapshot["recent_games"]
        if not games:
            empty = QLabel("Пока нет сохранённых завершённых партий. Импортируйте партии или сыграйте локально.")
            empty.setWordWrap(True)
            empty.setObjectName("secondary")
            self._recent_games_layout.addWidget(empty)
            return
        for game in games:
            heading, details = game_card_text(game)
            metrics = game.get("review_metrics") or {}
            card = _DashboardGameCard()
            card.setObjectName("metricCard")
            card.setAccessibleName(f"Открыть разбор партии: {heading}")
            card.setAccessibleDescription("Нажмите Enter или пробел, чтобы открыть разбор.")
            card.setToolTip("Открыть разбор партии")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(4)
            heading_label = QLabel(f"♟  {heading}")
            heading_label.setStyleSheet("font-size: 13px; font-weight: 700;")
            heading_label.setWordWrap(False)
            card_layout.addWidget(heading_label)
            description = QLabel(details)
            description.setObjectName("secondary")
            description.setWordWrap(True)
            description.setToolTip(opening_tooltip(game.get("opening_name")))
            card_layout.addWidget(description)
            source_code = source_short(metrics.get("source"))[0]
            coverage_text = ("оценок пока нет" if metrics.get("status") == "без анализа" else
                             f"{source_code} · охват {metrics.get('coverage', 'н/д')}")
            coverage = QLabel(coverage_text if analysis_allowed else "Оценки скрыты до проверки безопасности")
            coverage.setObjectName("dashboardAnalysisCoverage")
            coverage.setWordWrap(False)
            if analysis_allowed:
                coverage.setToolTip(
                    f"Источник: {metrics.get('source') or 'нет данных'} · "
                    f"охват собственной стороны: {metrics.get('coverage') or 'н/д'}. "
                    "Серверная аналитика и Stockfish отображаются как отдельные источники.")
            else:
                coverage.setToolTip(
                    "Источник, оценки и охват скрыты до свежего подтверждения Fair Play.")
            coverage.setAccessibleDescription(coverage.toolTip())
            card_layout.addWidget(coverage)
            card.activated.connect(lambda item=dict(game): self._open_home_game_details(item))
            card.watch_children()
            self._recent_games_layout.addWidget(card)

    def _open_home_game_details(self, game: dict) -> None:
        """Open the selected record in Library's details pane, not Review."""
        game_id = str(game.get("game_id") or "")
        if not game_id:
            return
        self._home_detail_game_id = game_id
        self._home_detail_payload = dict(game)
        self._home_detail_lookup_pending = False
        self.show_saved_games()

    def _refresh_dashboard_analysis_visibility(self) -> None:
        safety_allowed = self._library_detail_safety_allowed()
        snapshot = self._latest_dashboard_snapshot
        if snapshot is not None:
            self._render_dashboard_snapshot(snapshot)
        library_page = getattr(self, "library_page", None)
        if library_page is not None:
            detail_game = getattr(library_page, "_detail_game", None)
            if detail_game is None:
                detail_game = library_page.selected_game()
            if detail_game is not None:
                if not safety_allowed:
                    library_page.show_game_detail(detail_game, safety_allowed=False)
                else:
                    self._load_library_game_detail(
                        detail_game,
                        allow_not_selected=(library_page.selected_game() is None or
                            str(library_page.selected_game().get("game_id")) !=
                            str(detail_game.get("game_id"))))
        statistics_page = getattr(self, "_statistics_dialog", None)
        if statistics_page is not None:
            statistics_page.set_safety_display_allowed(
                safety_allowed,
                "Статистика и кэшированные оценки скрыты: подтвердите безопасное состояние Lichess.",
            )
            if isinstance(statistics_page, EmbeddedStatisticsPage):
                statistics_page.set_metric_migration_status(
                    self._legacy_metrics_text, safety_allowed=safety_allowed)
        if safety_allowed:
            self._schedule_legacy_metrics_batch()
        else:
            self._cancel_legacy_metrics_batch("safety_revoked")

    def _request_dashboard_display_safety(self) -> None:
        self._request_dashboard_display_safety_internal(manual=False)

    def _request_dashboard_display_safety_from_button(self) -> None:
        self._request_dashboard_display_safety_internal(manual=True)

    def _request_dashboard_display_safety_internal(self, *, manual: bool = False) -> None:
        if self.offline_mode and self.lichess is None:
            self._dashboard_analysis_safety_allowed = True
            self._set_sidebar_safety_status("Локальные данные · офлайн")
            self._refresh_dashboard_analysis_visibility()
            return
        service = self.lichess
        username = str(getattr(service, "username", "") or "").strip().casefold()
        if any(session.status in {"started", "playing"} and not session.is_ai_opponent
               for session in self._sessions_for_safety()):
            self._dashboard_analysis_safety_allowed = False
            self._cancel_legacy_metrics_batch("safety_revoked")
            self._set_sidebar_safety_status("Аналитика скрыта · активная партия")
            self._dashboard_display_safety_request_id = None
            self._dashboard_display_safety_context = None
            self._cancel_dashboard_safety_retry()
            self._refresh_dashboard_analysis_visibility()
            return
        if (not service or not username or not self._lichess_connected
                or not self._lichess_account_authenticated
                or self._lichess_stream_ready_generation != getattr(
                    service, "auth_generation", None)):
            self._dashboard_analysis_safety_allowed = False
            self._cancel_legacy_metrics_batch("safety_revoked")
            self._set_sidebar_safety_status("Аналитика скрыта · нужна проверка")
            self.safety_retry_button.setVisible(False)
            self._refresh_dashboard_analysis_visibility()
            page = getattr(self, "_statistics_dialog", None)
            if page is not None:
                page.analysis_mask_label.setText(
                    "Нужны подключённый аккаунт Lichess и доступное сетевое соединение. "
                    "Оценки остаются скрыты.")
            return
        cooldown = self._lichess_rate_limit_remaining()
        if cooldown:
            self._dashboard_analysis_safety_allowed = False
            self._cancel_legacy_metrics_batch("safety_revoked")
            self._set_sidebar_safety_status(f"Аналитика скрыта · повтор через {cooldown} с")
            self.safety_retry_button.setVisible(True)
            self._refresh_dashboard_analysis_visibility()
            self._schedule_dashboard_safety_retry(cooldown, automatic=True)
            return
        if manual:
            self._cancel_dashboard_safety_retry()
        active_id = self._dashboard_display_safety_request_id
        if active_id is not None and self._dashboard_display_safety_is_current(active_id):
            return
        request_id = uuid.uuid4().hex
        self._set_sidebar_safety_status("Проверяем активные партии Lichess…")
        self._dashboard_display_safety_request_id = request_id
        self._dashboard_display_safety_context = {
            "service": service,
            "generation": self._analysis_session_generation,
            "username": username,
        }
        self._dashboard_analysis_safety_allowed = False
        self._cancel_legacy_metrics_batch("safety_revoked")
        self.safety_retry_button.setVisible(False)
        self._refresh_dashboard_analysis_visibility()
        page = getattr(self, "_statistics_dialog", None)
        if page is not None:
            page.analysis_safety_button.setEnabled(False)
            page.analysis_mask_label.setText("Проверяем полный список активных партий Lichess…")
        try:
            service.check_active_games_for_analysis(request_id)
        except Exception as exc:
            self._dashboard_display_safety_error(
                request_id, self._safe_safety_error_kind(exc))

    def _dashboard_display_safety_is_current(self, request_id: str) -> bool:
        context = self._dashboard_display_safety_context
        username = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        return bool(
            request_id == self._dashboard_display_safety_request_id and context
            and context["service"] is self.lichess
            and context["generation"] == self._analysis_session_generation
            and context["username"] == username
            and self._lichess_account_authenticated and self._lichess_connected
            and self._lichess_stream_ready_generation == getattr(
                self.lichess, "auth_generation", None))

    def _dashboard_display_safety_reply(self, request_id: str, games: object) -> bool:
        if request_id != self._dashboard_display_safety_request_id:
            return False
        if not self._dashboard_display_safety_is_current(request_id):
            self._dashboard_display_safety_request_id = None
            self._dashboard_display_safety_context = None
            return True
        active_human = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        result = playing_snapshot_allows_analysis(games, active_human_session=active_human)
        self._dashboard_display_safety_request_id = None
        self._dashboard_display_safety_context = None
        self._dashboard_analysis_safety_allowed = result is True
        if result is True:
            self._schedule_legacy_metrics_batch()
        else:
            self._cancel_legacy_metrics_batch("safety_revoked")
        self.safety_retry_button.setVisible(result is not True)
        if result is True:
            self._dashboard_safety_retry_due = False
            self._cancel_dashboard_safety_retry()
        self._set_sidebar_safety_status(
            "Проверка пройдена · аналитика доступна" if result is True else
            "Аналитика скрыта · список небезопасен")
        self._refresh_dashboard_analysis_visibility()
        page = getattr(self, "_statistics_dialog", None)
        if page is not None and result is not True:
            page.analysis_mask_label.setText(
                "Список активных партий неполон, неизвестен или содержит игру против человека. "
                "Кэшированная аналитика остаётся скрыта.")
        return True

    def _dashboard_display_safety_error(self, request_id: str, error_code: str) -> bool:
        if request_id != self._dashboard_display_safety_request_id:
            return False
        if self._dashboard_display_safety_is_current(request_id):
            self._dashboard_display_safety_request_id = None
            self._dashboard_display_safety_context = None
            self._dashboard_analysis_safety_allowed = False
            self._cancel_legacy_metrics_batch("safety_revoked")
            self.safety_retry_button.setVisible(True)
            self._set_sidebar_safety_status(
                f"Аналитика скрыта · {self._safe_safety_error_kind(error_code)}")
            self._refresh_dashboard_analysis_visibility()
            page = getattr(self, "_statistics_dialog", None)
            if page is not None:
                page.analysis_mask_label.setText(
                    f"Проверка Lichess не выполнена ({error_code}); оценки остаются скрыты.")
            if "429" in str(error_code):
                self._schedule_dashboard_safety_retry(
                    max(60, self._lichess_rate_limit_remaining()), automatic=True)
        return True

    def _revoke_dashboard_display_safety(self, status: str) -> None:
        self._invalidate_analysis_preflight()
        self._dashboard_analysis_safety_allowed = False
        self._cancel_legacy_metrics_batch("safety_revoked")
        self._dashboard_display_safety_request_id = None
        self._dashboard_display_safety_context = None
        self._set_sidebar_safety_status(status)
        self._refresh_dashboard_analysis_visibility()

    def _schedule_dashboard_safety_retry(self, seconds: int, *, automatic: bool) -> None:
        if (self.offline_mode or self._shutdown_requested
                or not self._lichess_account_authenticated or not self._lichess_connected
                or not self.lichess):
            return
        self._dashboard_safety_retry_due = bool(automatic)
        self._dashboard_safety_retry_timer.start(max(1, int(seconds)) * 1000)

    def _cancel_dashboard_safety_retry(self) -> None:
        self._dashboard_safety_retry_timer.stop()
        self._dashboard_safety_retry_due = False

    def _retry_dashboard_display_safety(self) -> None:
        if not self._dashboard_safety_retry_due:
            return
        self._dashboard_safety_retry_due = False
        self._request_dashboard_display_safety_internal(manual=False)

    @staticmethod
    def _safe_safety_error_kind(message: object) -> str:
        """Reduce service failures to a safe, non-identifying status category."""
        text = str(message or "").casefold()
        if "429" in text or "retry-after" in text or "огранич" in text:
            return "429 · повтор позже"
        if "401" in text:
            return "401 · нужна повторная авторизация"
        if "403" in text or "scope" in text or "permission" in text or "прав" in text:
            return "403 · недостаточно прав"
        if "trunc" in text or "усеч" in text or "непол" in text:
            return "неполный ответ"
        if "timeout" in text or "тайм-аут" in text:
            return "тайм-аут"
        return "сетевая ошибка"

    def _dashboard_filter_changed(self, *_args) -> None:
        self._cancel_legacy_metrics_batch("account_switched")
        if hasattr(self, "dashboard_account"):
            selected = self.dashboard_account.currentData()
            self.settings.setValue("workspace/selected_account", selected or "")
        if self._library_detail_safety_allowed():
            self._schedule_legacy_metrics_batch()
        self._refresh_home_dashboard()

    def _layout_dashboard_metrics(self) -> None:
        view = getattr(self, "_home_view", None)
        if view is not None:
            view.layout_metrics(self.width())
            self._dashboard_metric_columns = view._dashboard_metric_columns

    def _build_result_page(self) -> QWidget:
        page = ResultPage({
            "review": self.review_finished_game,
            "export": self.export_current_pgn,
            "new": self.show_home,
            "home": self.show_home,
        })
        # Keep the old page object as a compatibility surface for callers, but
        # the finished state is rendered beside the same persistent game board.
        self.result_summary = self.finished_summary
        self.result_saved_label = self.finished_saved_label
        self.result_review_button = self.finished_review_button
        self.result_export_button = self.finished_export_button
        self.result_new_button = self.finished_challenge_button
        self.result_home_button = page.home_button
        return page

    def _is_live_online_session(self) -> bool:
        if self.online_session is None:
            return False
        return self.online_session.status not in {
            "mate", "resign", "stalemate", "timeout", "outoftime", "draw",
            "cheat", "noStart", "variantEnd", "unknownFinish", "aborted",
        }

    def _sync_position_eval_controls(self, available: bool) -> None:
        self.position_eval_button.setVisible(available)
        self.position_eval_label.setVisible(available)
        if not available and (self._position_eval_mode
                              or self._position_eval_preflight_id is not None
                              or self._position_eval_thread is not None):
            self._disable_position_evaluation(
                "Оценка остановлена: локальный экран или учебная позиция изменились.")
        configured = self.settings.value("stockfish_path", "", type=str)
        verified = self.settings.value("stockfish_verified_path", "", type=str)
        engine_ready = bool(verified and verified == configured and Path(verified).is_file())
        offline_safe = bool(self.offline_mode and self.lichess is None and not self._lichess_connected)
        active_human_game = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        connection_ready = bool(offline_safe or
                                (self._online_actions_allowed() and not active_human_game))
        can_toggle_off = self._position_eval_mode or self._position_eval_preflight_id is not None
        self.position_eval_button.setEnabled(
            available and engine_ready and connection_ready
            and (self._position_eval_thread is None or can_toggle_off))
        if not available:
            return
        if not engine_ready:
            self.position_eval_label.setText(
                "Сначала выберите и явно проверьте Stockfish в настройках.")
        elif self.offline_mode:
            if not self._position_eval_mode:
                self.position_eval_label.setText(
                    "Изолированный --offline: оценка доступна только после явного включения.")
        elif not self._online_actions_allowed():
            self.position_eval_label.setText(
                "Для безопасной проверки подключите Lichess; неизвестное состояние блокирует Stockfish.")
        elif not self._position_eval_mode and self._position_eval_preflight_id is None:
            self.position_eval_label.setText(
                "Нажмите «Анализировать позицию»: перед UCI проверим активные партии Lichess.")

    def refresh(self, message: str | None = None) -> None:
        if self.online_session is not None:
            self.undo_local_button.setVisible(False)
            if self._is_live_online_session():
                self._disable_position_evaluation("Оценка остановлена: идёт онлайн-партия.")
                self.position_eval_button.setVisible(False)
                self.position_eval_label.setVisible(False)
            else:
                self._sync_position_eval_controls(self._screen_mode == "review")
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
        # Premove training remains available to its controller/tests but is
        # not a separate user-facing mode. Local play is the free board.
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
        position_eval_available = (is_local_mode and self._local_board_mode == "free")
        self._sync_position_eval_controls(position_eval_available)
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
        self._sync_online_history(self.controller.board, self._local_sound_game_id, viewed_ply)
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
        self._refresh_home_dashboard()
        self.resume_review_button.setVisible(bool(
            self.settings.value("workspace/review_game_id", "", type=str)))
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
            self.home_status.setText("Откройте завершённую партию или начните новую игру.")
        self._update_home_game_actions()
        self.home_archive_button.setEnabled(self._online_actions_allowed())
        self.home_batch_analysis_button.setEnabled(True)
        self.home_puzzles_button.setEnabled(True)

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
        service = self.lichess
        dialog.canceled.connect(lambda: self.archive_sync.cancel(service))
        self._archive_progress_dialog = dialog
        dialog.show()
        self.archive_sync.start_full_sync(service)

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
        service = self.lichess
        dialog.canceled.connect(lambda: self.archive_sync.cancel(service))
        self._archive_progress_dialog = dialog
        dialog.show()
        self.archive_sync.start_pilot(service, max_games=limit)

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
        return self.archive_sync.account_state(account_id)

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
            state = self.archive_sync.account_state(account_id)
            stage = "Полная выгрузка завершена" if state.get("backfill_complete") else "Полная выгрузка не завершена"
            details.append(f"Этап архива: {stage}")
            details.append(f"Последняя полная/инкрементальная синхронизация: "
                           f"{self._format_sync_time(state.get('last_success_at')) if state.get('last_success_at') else 'нет'}")
            details.append(f"Новых партий при последней синхронизации: {state.get('last_sync_new_count', 0)}")
            details.append(f"Можно продолжить: {'да' if state.get('last_error') or not state.get('backfill_complete') else 'нет'}")
            runs = self.archive_sync.recent_pilot_runs(account_id, limit=1)
            if runs:
                pilot = runs[0]
                details.append(f"Последний пробный импорт: получено {pilot['received_count']}, "
                               f"новых {pilot['new_count']} · {pilot['status']}")
            if state.get("last_error"):
                details.append(f"Последняя ошибка/прерывание: {state['last_error']}")
        else:
            details.append("Подключите Lichess, чтобы увидеть статус архива по аккаунту.")
        dialog = QDialog(self)
        dialog.setWindowTitle("Хранилище и статус архива")
        dialog.resize(640, 440)
        layout = QVBoxLayout(dialog)
        status = QLabel("\n".join(details))
        status.setWordWrap(True)
        layout.addWidget(status, 1)
        sync = QPushButton("Синхронизировать / возобновить архив")
        sync.setEnabled(self._online_actions_allowed())
        sync.setToolTip("Использует сохранённый checkpoint; существующие партии не загружаются заново.")
        sync.clicked.connect(lambda: (dialog.accept(), self.start_archive_sync()))
        layout.addWidget(sync)
        if not self._online_actions_allowed():
            layout.addWidget(QLabel(
                "Для сетевого продолжения сначала подключитесь к Lichess и дождитесь окончания ограничения 429."))
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    @staticmethod
    def _format_sync_time(value: str) -> str:
        return format_game_date(value)

    def _archive_game_refresh_finished(self, game_id: str, message: str) -> None:
        if message.startswith("История этой партии обновлена"):
            self.home_status.setText(f"Партия {game_id}: {message}")
        else:
            self.home_status.setText(f"Не удалось обновить партию {game_id}: {message}")

    def show_statistics(self) -> None:
        dialog = getattr(self, "_statistics_dialog", None)
        if dialog is not None:
            self.screen_title.setText("Прогресс")
            self.screen_stack.setCurrentWidget(dialog)
            dialog._schedule_refresh()
            return
        raw_filters = str(self.settings.value("workspace/statistics_filters", "", type=str))
        try:
            saved_filters = json.loads(raw_filters) if raw_filters else {}
        except (TypeError, json.JSONDecodeError):
            saved_filters = {}
        dialog = EmbeddedStatisticsPage(
            self.game_repository, self,
            saved_filters=saved_filters if isinstance(saved_filters, dict) else {},
            filter_options=None)
        self._statistics_dialog = dialog
        dialog.set_safety_display_allowed(
            self._library_detail_safety_allowed(),
            "Статистика и кэшированные оценки скрыты до свежей проверки безопасного состояния Lichess.",
        )
        dialog.set_metric_migration_status(
            self._legacy_metrics_text,
            safety_allowed=self._library_detail_safety_allowed())
        dialog.safety_requested.connect(self._request_dashboard_display_safety)
        # Track workers at application scope, so destroying an embedded page
        # cannot disconnect the completion path needed by window shutdown.
        if not self._statistics_hub_connected:
            dialog._worker_hub.worker_started.connect(self._statistics_worker_started)
            dialog._worker_hub.worker_finished.connect(self._statistics_worker_finished)
            self._statistics_hub_connected = True
        dialog.position_requested.connect(self._open_statistics_position)
        dialog.game_requested.connect(lambda game_id: self._open_statistics_game(dialog, game_id))
        dialog.batch_requested.connect(lambda: (
            dialog.accept(), QTimer.singleShot(0, self.show_batch_analysis)))
        dialog.close_requested.connect(lambda: self._close_statistics_page(dialog))
        dialog.setWindowFlags(Qt.WindowType.Widget)
        self.screen_stack.addWidget(dialog)
        self.screen_title.setText("Прогресс")
        self.screen_stack.setCurrentWidget(dialog)
        dialog._schedule_refresh()
        self._load_statistics_filter_options(dialog)

    def _load_statistics_filter_options(self, dialog: EmbeddedStatisticsPage) -> None:
        self._statistics_options_generation += 1
        generation = self._statistics_options_generation
        previous = self._statistics_options_handle
        if previous is not None and previous.is_alive():
            previous.cancel("session_replaced")
        stop_event = threading.Event()

        def worker() -> None:
            try:
                if stop_event.is_set():
                    return
                result = StatisticsService(self.game_repository).filter_options()
                if not stop_event.is_set():
                    self.statistics_filter_options_finished.emit(generation, result, None)
            except Exception as exc:
                if not stop_event.is_set():
                    self.statistics_filter_options_finished.emit(
                        generation, None, f"Не удалось загрузить фильтры Прогресса ({type(exc).__name__}).")

        self._statistics_options_handle = self._start_registered_work(
            worker, kind="statistics_filter_options", lane=OperationLane.INTERACTIVE,
            thread_name="cc-statistics-filter-options", stop_event=stop_event)

    def _statistics_filter_options_ready(self, generation: int, result: object,
                                         error: object) -> None:
        if (generation != self._statistics_options_generation
                or self._shutdown_requested):
            return
        dialog = getattr(self, "_statistics_dialog", None)
        if not isinstance(dialog, EmbeddedStatisticsPage):
            return
        if isinstance(result, dict):
            dialog.set_filter_options(result)
        else:
            dialog.summary_label.setText(str(error or "Не удалось загрузить параметры выборки."))

    def _close_statistics_page(self, dialog: EmbeddedStatisticsPage) -> None:
        if self._statistics_dialog is not dialog:
            return
        self.settings.setValue("workspace/statistics_filters",
                               json.dumps(dialog.persisted_filter_state(), ensure_ascii=False))
        self._schedule_workspace_save()
        self.show_home()

    def _open_statistics_game(self, dialog: StatisticsDialog, game_id: str) -> None:
        analysis_source = dialog.analysis_source.currentData() or None
        dialog.accept()
        self._open_analysis_position(game_id, 0, analysis_source=analysis_source)

    def show_batch_analysis(self) -> None:
        if self._batch_dialog is not None:
            self.screen_title.setText("Массовый анализ")
            self.screen_stack.setCurrentWidget(self._batch_dialog)
            return
        dialog = EmbeddedBatchAnalysisPage(self)
        dialog.nodes.setValue(self.settings.value("batch_analysis_nodes", 20_000, type=int))
        self._batch_dialog = dialog
        self._refresh_batch_readiness()
        dialog.run_requested.connect(lambda limit, nodes, hash_mb, resume, retry_failed, selective_review:
            self._start_batch_analysis(dialog, limit, nodes, hash_mb, resume,
                                       retry_failed, selective_review))
        dialog.engine_settings_requested.connect(self.open_settings)
        dialog.diagnostics_requested.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_directory()))))
        dialog.lichess_inspector_requested.connect(self.show_background_analytics)
        dialog.pause_requested.connect(self._pause_batch_analysis)
        dialog.cancel_requested.connect(self._cancel_batch_analysis)
        dialog.setModal(False)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.close_requested.connect(self.show_analysis)
        self._batch_dialog = dialog
        self.screen_stack.addWidget(dialog)
        self.screen_title.setText("Массовый анализ")
        self.screen_stack.setCurrentWidget(dialog)

    def _start_batch_analysis(self, dialog: BatchAnalysisDialog, limit: int, nodes: int,
                              hash_mb: int, resume: bool, retry_failed: bool = False,
                              selective_review: bool = False) -> None:
        if self._shutdown_requested or (self._batch_thread and self._batch_thread.is_alive()):
            return
        service = self.lichess
        username = str(getattr(service, "username", "") or "").strip()
        engine_path = str(self.settings.value("stockfish_path", "", type=str)).strip()
        verified_path = str(self.settings.value("stockfish_verified_path", "", type=str)).strip()
        if not service or not username or not engine_path or verified_path != engine_path:
            QMessageBox.warning(dialog, "Анализ партий не готов",
                "Для запуска нужны подключённый аккаунт и проверенный Stockfish. "
                "Неизвестная безопасность блокирует запуск.")
            return
        try:
            options = FastPassOptions(nodes=nodes, hash_mb=hash_mb)
            runner = self.engine_analysis.create_batch_runner(
                self.game_repository, engine_path, options=options)
            resumed_original_budget = False
            if resume:
                saved_parameters = self.game_repository.pending_fast_analysis_parameters(
                    runner.engine_key)
                if saved_parameters:
                    saved_nodes = int(saved_parameters.get("limit_value", nodes))
                    saved_hash = int(saved_parameters.get("hash_mb", hash_mb))
                    if (saved_nodes, saved_hash) != (runner.options.nodes, runner.options.hash_mb):
                        runner = self.engine_analysis.create_batch_runner(
                            self.game_repository, engine_path,
                            options=FastPassOptions(nodes=saved_nodes, hash_mb=saved_hash))
                        nodes, hash_mb = saved_nodes, saved_hash
                        dialog.nodes.setValue(saved_nodes)
                        dialog.hash_mb.setValue(saved_hash)
                        self.settings.setValue("batch_analysis_nodes", saved_nodes)
                        resumed_original_budget = True
            self.settings.setValue("batch_analysis_nodes", runner.options.nodes)
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
        dialog.set_running(True)
        dialog.worker_progress.emit(
            "Продолжаем с исходным бюджетом сохранённой очереди; Stockfish ещё не запущен…"
            if resumed_original_budget else
            "Подготовка очереди; Stockfish ещё не запущен…")

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
                    if stop.is_set() or generation != self._analysis_session_generation:
                        return
                    account_id = self.game_repository.ensure_archive_account(username)
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
                    current = lambda: (
                        not stop.is_set() and not pause.is_set()
                        and self._lichess_connected and self.lichess is service
                        and generation == self._analysis_session_generation
                        and str(getattr(service, "username", "") or "").strip().casefold()
                        == username.casefold())
                    token = getattr(service, "token", None)
                    active_human = any(
                        session.status in {"started", "playing"} and not session.is_ai_opponent
                        for session in self._sessions_for_safety())
                    if not current() or not token:
                        unknown_preflight.set()
                        return None
                    safety = self.engine_analysis.refresh_fair_play(
                        service, token, username, current,
                        active_human_session=active_human)
                    if not safety:
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
                        f"Текущая партия · позиции {done}/{total} · "
                        f"очередь: {counts.get('queued', 0)} · готово: {counts.get('complete', 0)} · "
                        f"ошибки: {counts.get('failed', 0)}")
                    dialog.worker_run_status.emit(
                        f"В текущем запуске: идёт партия · позиции {done}/{total}; "
                        f"общая очередь: {counts.get('complete', 0)} готово, "
                        f"{counts.get('failed', 0)} ошибок")

                result = runner.run(stop_event=stop, pause_event=pause,
                                    safety_check=safety_check, progress=progress,
                                    include_selective_review=selective_review,
                                    account_id=account_id,
                                    max_fast_jobs=limit)
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
                    f"{result.get('reason') or ''}\n"
                    f"Последнее задание с ошибкой: {result.get('last_failed_job_id') or 'нет'} · "
                    f"{result.get('last_failure') or 'ошибок в этом запуске нет'}")
            except Exception as exc:
                dialog.worker_finished.emit(False, f"Очередь не запущена или остановлена: {exc}")

        thread = self._start_registered_work(
            work, kind="stockfish_batch_analysis", lane=OperationLane.ENGINE,
            stop_event=stop, thread_name="chess-coach-batch-analysis")
        self._batch_thread = thread

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
            "По кнопке начнётся явная синхронизация вашей истории задач. Если сохранённый токен "
            "уже имеет puzzle:read, повторный OAuth не потребуется; иначе приложение запросит "
            "отдельное разрешение. Будут сохранены только ваши попытки в локальной SQLite. "
            "Токен для партий не будет заменён. Продолжить?",
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
        self._puzzle_sync_mode = "ожидание разрешения"
        self._puzzle_sync_started_at = time.monotonic()
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
        if account_id:
            self.show_puzzle_history(account_id)

    def _puzzles_progress(self, count: int) -> None:
        self._puzzles_new_count = count
        self._update_puzzle_sync_progress()

    def _puzzles_scanned(self, count: int) -> None:
        self._puzzles_scanned_count = count
        self._update_puzzle_sync_progress()

    def _puzzles_started(self, mode: str) -> None:
        self._puzzle_sync_mode = str(mode)

    def _update_puzzle_sync_progress(self) -> None:
        if self._puzzle_progress_dialog:
            self._puzzle_progress_dialog.setLabelText(
                f"Режим: {getattr(self, '_puzzle_sync_mode', 'синхронизация')}"
                f" · проверено: {getattr(self, '_puzzles_scanned_count', 0)}"
                f" · новых: {getattr(self, '_puzzles_new_count', 0)}"
                f" · уже было: {max(0, getattr(self, '_puzzles_scanned_count', 0) - getattr(self, '_puzzles_new_count', 0))}")

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
            duration = max(0.0, time.monotonic() -
                           getattr(self, "_puzzle_sync_started_at", time.monotonic()))
            checked = getattr(self, "_puzzles_scanned_count", count)
            result = (f"Режим: {getattr(self, '_puzzle_sync_mode', 'синхронизация')}\n"
                      f"Проверено: {checked} · новых: {count} · уже было: {max(0, checked-count)}\n"
                      f"Итог: {'ошибка/прерывание' if sync_state.get('last_error') else 'завершено'} · "
                      f"время: {duration:.1f} с\n"
                      f"Всего попыток: {stats['attempts']}\n"
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

    def _puzzle_history_result_ready(self, request_id: str, result: object,
                                     error: object) -> None:
        entry = self._puzzle_history_callbacks.pop(str(request_id), None)
        if entry is None:
            return
        dialog, generation, callback = entry
        if not shiboken6.isValid(dialog):
            return
        callback(result, error)

    def open_puzzle_history_local(self) -> None:
        """Open stored attempts without authentication, network, or progress UI."""
        self.show_puzzle_history("")

    def show_puzzle_history(self, account_id: str, *, pilot_run_id: str | None = None,
                            _accounts: list[dict] | None = None) -> None:
        """Browse a local account's puzzle history; refresh remains a separate action."""
        self.screen_title.setText("Обучение")
        if _accounts is None:
            previous_handle = self._puzzle_accounts_handle
            if previous_handle is not None and previous_handle.is_alive():
                previous_handle.cancel("session_replaced")
            self._puzzle_accounts_generation += 1
            generation = self._puzzle_accounts_generation
            request_id = uuid.uuid4().hex
            loading = QWidget()
            layout = QVBoxLayout(loading)
            title = QLabel("Обучение")
            title.setObjectName("pageTitle")
            message = QLabel("Читаю локальную историю задач из SQLite…")
            message.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(message)
            layout.addStretch(1)
            previous_page = getattr(self, "_puzzle_page", None)
            if previous_page is not None:
                self.screen_stack.removeWidget(previous_page)
                previous_page.deleteLater()
            self._puzzle_page = loading
            self.screen_stack.addWidget(loading)
            self.screen_stack.setCurrentWidget(loading)
            stop_event = threading.Event()

            def worker() -> None:
                try:
                    if stop_event.is_set():
                        return
                    accounts = self.game_repository.list_puzzle_accounts()
                    self.puzzle_history_finished.emit(request_id, {"accounts": accounts}, None)
                except Exception as exc:
                    self.puzzle_history_finished.emit(
                        request_id, None,
                        f"Не удалось прочитать список задач ({type(exc).__name__}).")

            def apply_accounts(payload, error) -> None:
                if (generation != self._puzzle_accounts_generation
                        or self._puzzle_page is not loading):
                    return
                if error or not isinstance(payload, dict):
                    message.setText(str(error or "Не удалось прочитать историю задач."))
                    return
                accounts = payload.get("accounts")
                accounts = accounts if isinstance(accounts, list) else []
                selected = account_id
                if not selected and accounts:
                    username = ((getattr(self.lichess, "puzzle_username", None)
                                 or getattr(self.lichess, "username", None)) if self.lichess else None)
                    preferred = (f"lichess:{str(username).strip().lower()}" if username else None)
                    account_ids = {str(row["account_id"]) for row in accounts}
                    selected = preferred if preferred in account_ids else str(accounts[0]["account_id"])
                self.show_puzzle_history(selected, pilot_run_id=pilot_run_id,
                                         _accounts=accounts)

            self._puzzle_history_callbacks[request_id] = (loading, generation, apply_accounts)
            handle = self._start_registered_work(
                worker, kind="puzzle_account_list", lane=OperationLane.INTERACTIVE,
                thread_name="cc-puzzle-account-list", stop_event=stop_event)
            self._puzzle_accounts_handle = handle
            loading.destroyed.connect(
                lambda *_: handle.cancel("shutdown"))
            return
        accounts = _accounts
        previous_page = getattr(self, "_puzzle_page", None)
        if previous_page is not None:
            self.screen_stack.removeWidget(previous_page)
            previous_page.deleteLater()
        if not accounts:
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("Обучение")
            title.setObjectName("pageTitle")
            layout.addWidget(title)
            empty = QLabel("Локально сохранённых попыток пока нет. История задач не "
                           "гарантирует выдачу новых задач.")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            refresh = QPushButton("Обновить историю Lichess")
            refresh.setEnabled(bool(self._online_actions_allowed() and self.lichess))
            refresh.clicked.connect(self.start_puzzle_sync)
            layout.addWidget(refresh)
            close = QPushButton("Главная")
            close.clicked.connect(self.show_home)
            layout.addWidget(close)
            layout.addStretch(1)
            self._puzzle_page = page
            self.screen_stack.addWidget(page)
            self.screen_title.setText("Обучение")
            self.screen_stack.setCurrentWidget(page)
            return
        account_rows = {str(row["account_id"]): row for row in accounts}
        if account_id not in account_rows:
            account_id = str(accounts[0]["account_id"])
        dialog = EmbeddedPuzzleHistoryPage(self)
        dialog.setWindowTitle("Мои задачи Lichess · сохранённая история")
        dialog.resize(1040, 680)
        page = PuzzleHistoryPage(
            accounts, account_id,
            refresh_enabled=bool(self._online_actions_allowed() and self.lichess),
            parent=dialog)
        root = QVBoxLayout(dialog)
        root.addWidget(page)
        account_selector = page.account_selector
        summary = page.summary
        refresh = page.refresh
        search = page.search
        result_filter = page.result_filter
        history = page.history
        previous = page.previous
        following = page.following
        page_label = page.page_label
        board = page.board
        solution = page.solution
        replay = page.replay
        reveal = page.reveal
        reset_replay = page.reset_replay
        close = page.close_button
        refresh.clicked.connect(lambda _checked=False: (dialog.accept(), self.start_puzzle_sync()))
        close.clicked.connect(dialog.accept)
        state = {"page": 0, "items": [], "selected": None,
                 "replay_board": None, "solution_moves": [], "solution_index": 0,
                 "replay_active": False, "solution_visible": False,
                 "account_id": account_id, "generation": 0,
                 "request_id": None, "handle": None}

        def refresh_account_summary() -> None:
            summary.setText("Загружаю локальную статистику и историю…")
            summary.setToolTip("Чтение SQLite выполняется в фоновом потоке.")

        def render_page() -> None:
            previous_handle = state.get("handle")
            previous_request = state.get("request_id")
            if previous_handle is not None and previous_handle.is_alive():
                previous_handle.cancel("session_replaced")
            if previous_request:
                self._puzzle_history_callbacks.pop(previous_request, None)
            state["generation"] += 1
            generation = state["generation"]
            request_id = uuid.uuid4().hex
            state["request_id"] = request_id
            query = search.text()
            outcome = result_filter.currentData() or None
            page_number = state["page"]
            selected_account = state["account_id"]
            stop_event = threading.Event()
            page_label.setText("Загружаю историю…")
            previous.setEnabled(False)
            following.setEnabled(False)

            def worker() -> None:
                try:
                    if stop_event.is_set():
                        return
                    result = self.game_repository.puzzle_history_page(
                        selected_account, query=query, outcome=outcome,
                        page=page_number, page_size=100)
                    summary_data = {
                        "statistics": self.game_repository.puzzle_statistics(selected_account),
                        "rating_trend": self.game_repository.puzzle_rating_trend(selected_account),
                        "sync_state": self.game_repository.puzzle_sync_state(selected_account),
                    }
                    if stop_event.is_set():
                        return
                    self.puzzle_history_finished.emit(
                        request_id, {"page": result, "summary": summary_data}, None)
                except Exception as exc:
                    self.puzzle_history_finished.emit(
                        request_id, None, f"Не удалось прочитать локальную историю ({type(exc).__name__}).")

            def apply_result(payload, error) -> None:
                if (not shiboken6.isValid(dialog) or generation != state["generation"]
                        or request_id != state.get("request_id")):
                    return
                if error or not isinstance(payload, dict):
                    page_label.setText(str(error or "Не удалось загрузить историю."))
                    return
                result = payload["page"]
                state["items"] = result["items"]
                history.clear()
                for attempt in state["items"]:
                    try:
                        date = datetime.fromtimestamp(
                            attempt["attempt_date_ms"] / 1000).astimezone().strftime("%d.%m.%Y · %H:%M")
                    except (TypeError, ValueError, OverflowError, OSError):
                        date = "Дата неизвестна"
                    themes = _puzzle_themes_label(attempt.get("themes_json"))
                    outcome_label = "Решено" if attempt["win"] else "Ошибка"
                    item = QListWidgetItem(
                        f"{date} · {outcome_label} · {attempt['puzzle_rating'] or 'без рейтинга'} · {themes}")
                    item.setData(Qt.ItemDataRole.UserRole, attempt)
                    history.addItem(item)
                page_count = max(1, result["pages"])
                state["page"] = min(state["page"], page_count - 1)
                page_label.setText(f"Страница {state['page'] + 1}/{page_count} · {result['total']} попыток")
                previous.setEnabled(state["page"] > 0)
                following.setEnabled(state["page"] + 1 < page_count)
                attempt_tooltip = _puzzle_attempt_summary(state["items"]) if state["items"] else ""
                summary_data = payload["summary"]
                overall = summary_data["statistics"]
                rating_trend = summary_data["rating_trend"]
                sync_state = summary_data["sync_state"]
                top_themes = sorted(overall["themes"].items(),
                                    key=lambda item: (item[1]["attempts"], item[0]), reverse=True)[:8]
                theme_summary = ", ".join(_PUZZLE_THEME_LABELS.get(theme, theme)
                                            for theme, _stats in top_themes[:4]) or "нет данных"
                trend_summary = (f"· средний рейтинг {rating_trend[-1]['average_rating']}"
                                 if rating_trend and rating_trend[-1].get("average_rating") is not None
                                 else "")
                summary.setText(
                    f"{account_rows[selected_account]['username']} · {overall['attempts']} попыток · "
                    f"решено {overall['wins']} ({overall['success_percent'] if overall['success_percent'] is not None else 'н/д'}%)"
                    f"{trend_summary}\nЧастые темы: {theme_summary} · история и даты сохранены локально.")
                sync_tooltip = (
                    f"Последняя успешная синхронизация: "
                    f"{self._format_sync_time(sync_state.get('last_success_at')) if sync_state.get('last_success_at') else 'неизвестно'}"
                    +(f"\nОшибка синхронизации: {sync_state['last_error']}" if sync_state.get("last_error") else ""))
                summary.setToolTip("\n\n".join(part for part in (sync_tooltip, attempt_tooltip) if part))
                if state["items"]:
                    history.setCurrentRow(0)
                else:
                    state["selected"] = None
                    replay.setEnabled(False)
                    reveal.setEnabled(False)
                    solution.setText("В этой выборке нет попыток.")

            self._puzzle_history_callbacks[request_id] = (dialog, generation, apply_result)
            handle = self._start_registered_work(
                worker, kind="puzzle_history_query", lane=OperationLane.INTERACTIVE,
                thread_name="cc-puzzle-history-query", stop_event=stop_event)
            state["handle"] = handle
            dialog.destroyed.connect(lambda *_: handle.cancel("shutdown"))

        def select_attempt(row: int) -> None:
            if row < 0 or row >= history.count():
                return
            attempt = history.item(row).data(Qt.ItemDataRole.UserRole)
            state["selected"] = attempt
            state["replay_active"] = False
            state["replay_board"] = None
            state["solution_visible"] = False
            board.set_interaction_enabled(False)
            reveal.setEnabled(False)
            try:
                position = chess.Board(attempt["fen"])
                if not position.is_valid():
                    raise ValueError("invalid FEN")
                moves = json.loads(attempt["solution_json"])
                if not isinstance(moves, list) or len(moves) < 2:
                    raise ValueError("missing setup or solver move")
                # Official Lichess puzzle data defines FEN before the opponent's
                # setup move; moves[0] is that move, and moves[1] begins the
                # player's answer. Show the solver-to-move position without PV.
                for uci in moves:
                    move = chess.Move.from_uci(uci)
                    if move not in position.legal_moves:
                        raise ValueError("illegal solution")
                    position.push(move)
                puzzle_start = chess.Board(attempt["fen"])
                puzzle_start.push(chess.Move.from_uci(moves[0]))
                board.set_position(puzzle_start)
                board.set_flipped(puzzle_start.turn == chess.BLACK)
                themes = ", ".join(json.loads(attempt.get("themes_json") or "[]")) or "нет данных"
                result_text = "решено" if attempt.get("win") else "ошибка"
                solution.setText(
                    f"Задача {attempt.get('puzzle_id') or ''} · рейтинг "
                    f"{attempt.get('puzzle_rating') or 'н/д'} · темы: {themes}\n"
                    f"Исходная попытка: {result_text}. Позиция показана за решающую сторону. "
                    "Правильный вариант скрыт.")
                replay.setEnabled(bool(moves))
                reveal.setEnabled(bool(moves))
            except (ValueError, TypeError, chess.InvalidMoveError):
                board.set_position(chess.Board())
                solution.setText("Позиция или решение повреждены; повторить задачу нельзя.")
                replay.setEnabled(False)
                reveal.setEnabled(False)
            reset_replay.setEnabled(False)

        def reveal_solution() -> None:
            attempt = state.get("selected")
            if not attempt:
                return
            answer = QMessageBox.question(
                dialog, "Показать решение",
                "Правильный вариант будет раскрыт. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                position = chess.Board(attempt["fen"])
                moves = json.loads(attempt["solution_json"])
                position.push(chess.Move.from_uci(moves[0]))
                san = []
                for uci in moves[1:]:
                    move = chess.Move.from_uci(uci)
                    san.append(position.san(move))
                    position.push(move)
                state["solution_visible"] = True
                solution.setText(
                    f"Решение: {' '.join(san)}\n"
                    "Это сохранённый вариант Lichess; локальная попытка не отправляется.")
            except (ValueError, TypeError, IndexError, chess.InvalidMoveError):
                solution.setText("Не удалось раскрыть повреждённый вариант задачи.")

        def begin_replay() -> None:
            attempt = state["selected"]
            if not attempt:
                return
            try:
                position = chess.Board(attempt["fen"])
                moves = [chess.Move.from_uci(uci) for uci in json.loads(attempt["solution_json"])]
                if not position.is_valid() or not moves or moves[0] not in position.legal_moves:
                    raise ValueError("invalid puzzle")
                # Lichess puzzle FEN precedes the opponent's setup move; replay it
                # locally so the user's side to solve is on move.
                position.push(moves[0])
                state["solution_moves"] = moves
                state["solution_index"] = 1
                state["replay_board"] = position
                state["replay_active"] = True
                state["solution_visible"] = False
                board.set_position(position)
                board.set_flipped(position.turn == chess.BLACK)
                board.set_interaction_enabled(True)
                replay.setEnabled(False)
                reset_replay.setEnabled(True)
                reveal.setEnabled(True)
                solution.setText("Офлайн-упражнение: сделайте следующий ход из сохранённого решения.")
            except (ValueError, TypeError, chess.InvalidMoveError):
                solution.setText("Нельзя начать повтор: позиция или сохранённый вариант некорректны.")

        def play_replay_move(move: chess.Move) -> None:
            position = state.get("replay_board")
            moves = state["solution_moves"]
            index = state["solution_index"]
            if not state["replay_active"] or position is None or index >= len(moves):
                return
            if move != moves[index] or move not in position.legal_moves:
                solution.setText("Ход легален, но не совпал с сохранённым решением. Сбросьте упражнение.")
                state["replay_active"] = False
                board.set_interaction_enabled(False)
                return
            position.push(move)
            index += 1
            if index < len(moves):
                reply = moves[index]
                if reply not in position.legal_moves:
                    state["replay_active"] = False
                    board.set_interaction_enabled(False)
                    solution.setText("Сохранённый ответный ход оказался нелегальным.")
                    return
                position.push(reply)
                index += 1
            state["solution_index"] = index
            board.set_position(position)
            if index >= len(moves):
                state["replay_active"] = False
                board.set_interaction_enabled(False)
                solution.setText("Вариант завершён. Это локальная тренировка; история не изменена.")
            else:
                solution.setText("Верно. Продолжайте локально.")

        history.currentRowChanged.connect(select_attempt)
        board.move_intent.connect(play_replay_move)
        replay.clicked.connect(begin_replay)
        reset_replay.clicked.connect(begin_replay)
        reveal.clicked.connect(reveal_solution)
        search_timer = QTimer(dialog)
        search_timer.setSingleShot(True)
        search_timer.setInterval(250)
        search_timer.timeout.connect(lambda: (state.update(page=0), render_page()))
        search.textChanged.connect(lambda _text: search_timer.start())
        result_filter.currentIndexChanged.connect(lambda _index: (state.update(page=0), render_page()))
        previous.clicked.connect(lambda: (state.update(page=max(0, state["page"] - 1)), render_page()))
        following.clicked.connect(lambda: (state.update(page=state["page"] + 1), render_page()))
        def select_account(index: int) -> None:
            selected = account_selector.itemData(index)
            if not selected or selected == state["account_id"]:
                return
            state.update(account_id=selected, page=0, selected=None,
                         replay_active=False, replay_board=None, solution_visible=False)
            board.set_position(chess.Board())
            board.set_interaction_enabled(False)
            replay.setEnabled(False)
            reveal.setEnabled(False)
            reset_replay.setEnabled(False)
            solution.setText("Выберите попытку для локального просмотра.")
            refresh_account_summary()
            render_page()

        account_selector.currentIndexChanged.connect(select_account)
        refresh_account_summary()
        render_page()
        dialog.close_requested.connect(self.show_home)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        self._puzzle_page = dialog
        self.screen_stack.addWidget(dialog)
        self.screen_title.setText("Обучение")
        self.screen_stack.setCurrentWidget(dialog)

    def start_local_game(self) -> None:
        self._disable_position_evaluation("Локальная доска изменена; прежняя оценка остановлена.")
        self.online_session = None
        self._review_session = None
        self.board.clear_annotations()
        self.controller.new_game()
        self._local_sound_game_id = uuid.uuid4().hex
        self._local_board_mode = "free"
        self.controller.set_premove_training(False)
        self.local_mode_combo.blockSignals(True)
        self.local_mode_combo.setCurrentIndex(self.local_mode_combo.findData("free"))
        self.local_mode_combo.blockSignals(False)
        self._screen_mode = "local"
        self.screen_title.setText("Локальная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.sound_player.play_game_start(self._local_sound_game_id)
        self.refresh("Новая свободная партия. Можно ходить за обе стороны.")

    def _local_board_mode_changed(self, _index: int) -> None:
        if self._screen_mode != "local":
            return
        mode = self.local_mode_combo.currentData()
        if mode not in {"free", "premove_training"} or mode == self._local_board_mode:
            return
        if mode != "free" and self._position_eval_mode:
            self.position_eval_button.blockSignals(True)
            self.position_eval_button.setChecked(False)
            self.position_eval_button.blockSignals(False)
            self._set_position_evaluation_enabled(False)
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
        if session.status in {"created", "started", "playing"}:
            self._screen_mode = "online"
            self.screen_title.setText("Активная партия")
            self.screen_stack.setCurrentWidget(self.game_page)
            self.refresh("Возврат к текущей партии.")
        else:
            self._show_result(session, not session.final_sync_pending)

    def _show_result(self, session: LichessGameSession, saved: bool, detail: str | None = None) -> None:
        self._screen_mode = "result"
        self.screen_title.setText("Партия завершена")
        outcome = _result_label(session.color, session.winner, session.status)
        self.result_summary.setText(
            f"{outcome}\nСоперник: {session.opponent}\nСтатус Lichess: {session.status}"
        )
        if session.final_sync_pending:
            saved = False
            saved_label = "Результат получен; ожидаем подтверждённую финальную историю от Lichess."
        elif not saved:
            saved = False
            saved_label = "Проверяем сохранение финальной партии в SQLite…"
        else:
            # A caller's saved flag is only a hint. The asynchronous repository
            # probe below verifies the exact terminal line before enabling review.
            saved = False
            saved_label = "Проверяем сохранение финальной партии в SQLite…"
        self.result_saved_label.setText(
            saved_label
        )
        self.result_saved_label.setStyleSheet("color: #39704a;" if saved else "color: #9b3434;")
        self._finish_save_status = saved_label
        self.result_review_button.setEnabled(False)
        self.result_export_button.setEnabled(False)
        self.finished_review_button.setEnabled(False)
        self.finished_challenge_button.setEnabled(self._online_actions_allowed())
        self.finished_game_card.setVisible(False)
        self.finished_review_button.setVisible(True)
        self.finished_challenge_button.setVisible(True)
        self.finished_export_button.setVisible(False)
        # A result never replaces the authoritative game board. The selected
        # session, clocks and move history stay attached to their original view.
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh()
        self._track_game_finalization(session)

    def _track_game_finalization(self, session: LichessGameSession) -> None:
        record = self.game_finalization.track(
            session.game_id, server_sync_pending=session.final_sync_pending,
            storage_write_pending=not session.final_sync_pending)
        if record.phase == FinalizationPhase.LOCAL_WRITE:
            self._start_finalization_probe(session)

    def _schedule_finalization_check(self, game_id: str) -> None:
        timer = self._finalization_timers.get(game_id)
        if timer is not None and timer.isActive():
            return
        if self._shutdown_requested:
            return
        delay = self.game_finalization.storage_check_delay(game_id)
        if delay is None:
            record = self.game_finalization.get(game_id)
            session = self.online_sessions.get(game_id)
            if (record is not None and record.phase == FinalizationPhase.LOCAL_WRITE
                    and session is not None and game_id == self._selected_live_game_id
                    and self.online_session is session):
                detail = self.game_repository.write_error(game_id)
                self.result_saved_label.setText(
                    "Не удалось подтвердить запись партии в SQLite после нескольких попыток. "
                    "Данные останутся ожидающими восстановления при следующем запуске."
                    + (f" Причина: {detail}" if detail else ""))
                self.result_saved_label.setStyleSheet("color: #9b3434;")
            return
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda game_id=game_id: self._poll_result_write(game_id))
            self._finalization_timers[game_id] = timer
        timer.start(delay)

    def _poll_result_write(self, game_id: str) -> None:
        session = self.online_sessions.get(game_id)
        if not session or session.final_sync_pending:
            return
        self._start_finalization_probe(session)

    def _start_finalization_probe(self, session: LichessGameSession) -> None:
        game_id = str(session.game_id)
        existing = self._finalization_probe_handles.get(game_id)
        if existing is not None and existing.is_alive():
            return
        request_id = uuid.uuid4().hex
        self._finalization_probe_generation[game_id] = request_id
        stop_event = threading.Event()

        def worker() -> None:
            result = {"pending": False, "confirmed": False, "has_pgn": False,
                      "error": None, "game": None}
            try:
                if stop_event.is_set():
                    return
                result["pending"] = bool(self.game_repository.has_pending_write(game_id))
                stored = None if result["pending"] else self.game_repository.get_game(game_id)
                result["confirmed"] = bool(
                    stored and stored.get("moves") == session.moves
                    and stored.get("status") == session.status
                    and not stored.get("final_sync_pending", 0))
                if not result["pending"] and not result["confirmed"]:
                    # Retry only the already-authoritative terminal snapshot.
                    session._save()
                    result["pending"] = bool(self.game_repository.has_pending_write(game_id))
                    if not result["pending"]:
                        stored = self.game_repository.get_game(game_id)
                        result["confirmed"] = bool(
                            stored and stored.get("moves") == session.moves
                            and stored.get("status") == session.status
                            and not stored.get("final_sync_pending", 0))
                if result["confirmed"] and not stop_event.is_set():
                    result["has_pgn"] = bool(self.game_repository.export_pgn(game_id))
                    result["game"] = stored
            except Exception as exc:
                result["error"] = type(exc).__name__
            if not stop_event.is_set():
                self.finalization_probe_finished.emit(game_id, request_id, result)

        handle = self._start_registered_work(
            worker, kind="verify_final_game_write", lane=OperationLane.SQLITE_WRITER,
            thread_name="cc-final-game-check", stop_event=stop_event)
        self._finalization_probe_handles[game_id] = handle
        if game_id == self._selected_live_game_id and self._screen_mode == "result":
            self.result_saved_label.setText("Проверяем или завершаем локальную запись…")
            self.result_review_button.setEnabled(False)
            self.finished_review_button.setEnabled(False)
            self.result_export_button.setEnabled(False)

    def _finalization_probe_result_ready(self, game_id: str, request_id: str,
                                         result: object) -> None:
        if self._finalization_probe_generation.get(str(game_id)) != str(request_id):
            return
        self._finalization_probe_handles.pop(str(game_id), None)
        session = self.online_sessions.get(str(game_id))
        if session is None or not isinstance(result, dict) or session.final_sync_pending:
            return
        confirmed = bool(result.get("confirmed"))
        pending = bool(result.get("pending"))
        if confirmed:
            game = result.get("game")
            if isinstance(game, dict):
                self._confirmed_terminal_games[str(game_id)] = dict(game)
            self.game_finalization.track(game_id, server_sync_pending=False,
                                         storage_write_pending=False)
            if game_id == self._selected_live_game_id and self.online_session is session:
                self.result_saved_label.setText("Партия сохранена локально.")
                self.result_saved_label.setStyleSheet("color: #39704a;")
                self.result_review_button.setEnabled(bool(session.moves))
                self.finished_review_button.setEnabled(bool(session.moves))
                self.result_export_button.setEnabled(bool(result.get("has_pgn")))
            return
        self.game_finalization.track(game_id, server_sync_pending=False,
                                     storage_write_pending=True)
        if game_id == self._selected_live_game_id and self.online_session is session:
            message = ("Финальная история подтверждена; сохраняем её локально…"
                       if pending else
                       "Не удалось подтвердить запись финальной партии; повторим проверку.")
            self.result_saved_label.setText(message)
            self.result_saved_label.setStyleSheet("color: #9b3434;" if not pending else "")
        self._schedule_finalization_check(game_id)

    def _challenge_again(self) -> None:
        """Start an explicit new challenge through the supported Lichess flow."""
        if not self._online_actions_allowed():
            self._notify_inline("Повторная партия доступна после готовности Lichess и окончания Retry-After.")
            return
        session = self.online_session
        if session is None:
            return
        if session.is_ai_opponent:
            self.play_ai()
            return
        opponent = str(session.opponent or "").strip()
        if opponent and opponent.casefold() not in {"?", "unknown", "lichess"}:
            self.challenge_username(initial_username=opponent)
        else:
            self.challenge_username()

    def review_finished_game(self) -> None:
        session = self.online_session
        if not session or session.status in {"started", "playing", "created"}:
            return
        game = self._confirmed_terminal_games.get(session.game_id)
        if (game is None or game.get("moves") != session.moves
                or game.get("status") != session.status
                or game.get("final_sync_pending", 0)):
            self.result_review_button.setEnabled(False)
            self.finished_review_button.setEnabled(False)
            self._notify_inline("Разбор откроется после подтверждения точной финальной записи в SQLite.")
            self._start_finalization_probe(session)
            return
        self._open_saved_game(dict(game))

    def _review_training_safety(self) -> tuple[bool, str]:
        if self.offline_mode:
            return True, ""
        if any(session.status in {"started", "playing"} and not session.is_ai_opponent
               for session in self._sessions_for_safety()):
            return False, "Заблокировано: у вас есть активная партия с человеком."
        if self.lichess and self._lichess_connected:
            return False, "Проверьте активные партии Lichess перед упражнением."
        return False, "Заблокировано: безопасность аккаунта не подтверждена. Подключите Lichess."

    def _review_workspace_identity(self) -> tuple[str, int, str] | None:
        review = getattr(self, "review_workspace", None)
        if not review or not review.review:
            return None
        game = review.review.get("game") or {}
        line = review.review.get("line") or {}
        try:
            return (str(game["game_id"]), int(line["line_revision"]),
                    str(line["line_hash"]))
        except (KeyError, TypeError, ValueError):
            return None

    def _revoke_review_safety(self, reason: str, *, preserve_offline: bool = False) -> None:
        self._review_safety_request_id = None
        self._review_safety_context = None
        if preserve_offline and self.offline_mode:
            return
        workspace = getattr(self, "review_workspace", None)
        if workspace is not None:
            workspace.revoke_safety(reason)

    def _request_review_training_safety(self, game_id: str) -> None:
        identity = self._review_workspace_identity()
        if (identity is None or identity[0] != str(game_id)
                or self.screen_stack.currentWidget() is not self.review_workspace
                or self._screen_mode != "review"):
            self._revoke_review_safety(
                "Заблокировано: разбор изменился до проверки безопасности.")
            return
        service = self.lichess
        username = str(getattr(service, "username", "") or "").strip()
        if not service or not username or not self._online_actions_allowed():
            self._revoke_review_safety(
                "Заблокировано: не удалось проверить активные партии Lichess.")
            return
        active_human = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        if active_human:
            self._revoke_review_safety(
                "Заблокировано: у вас есть активная партия с человеком.")
            return
        request_id = uuid.uuid4().hex
        self._dashboard_analysis_safety_allowed = False
        self._refresh_dashboard_analysis_visibility()
        self._review_safety_request_id = request_id
        self._review_safety_context = {
            "generation": self._analysis_session_generation,
            "account_id": f"lichess:{username.casefold()}",
            "username": username.casefold(), "service": service,
            "game_id": identity[0], "line_revision": identity[1],
            "line_hash": identity[2],
        }
        self.review_workspace.set_safety_pending(
            "Проверяем активные партии; оценки и варианты пока скрыты.")
        try:
            service.check_active_games_for_analysis(request_id)
        except Exception as exc:
            self._review_safety_error_received(request_id, str(exc))

    def _review_safety_reply_is_current(self, request_id: str) -> bool:
        context = self._review_safety_context
        if request_id != self._review_safety_request_id or not context:
            return False
        identity = self._review_workspace_identity()
        current_widget = self.screen_stack.currentWidget()
        current_account = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        current_line = (self.game_repository.current_line(context["game_id"])
                        if context.get("game_id") else None)
        current_identity = ((str(context["game_id"]), int(current_line["line_revision"]),
                            str(current_line["line_hash"])) if current_line else None)
        if (context["service"] is not self.lichess
                or context["generation"] != self._analysis_session_generation
                or context["username"] != current_account
                or context["account_id"] != f"lichess:{current_account}"
                or not self._lichess_connected or not self._online_actions_allowed()
                or current_widget is not self.review_workspace or self._screen_mode != "review"
                or identity != (context["game_id"], context["line_revision"], context["line_hash"])
                or current_identity != identity):
            self._review_safety_request_id = None
            self._review_safety_context = None
            return False
        return True

    def _review_safety_games_received(self, request_id: str, games: object) -> bool:
        if request_id != self._review_safety_request_id:
            return False
        if not self._review_safety_reply_is_current(request_id):
            return True
        if not isinstance(games, list) or len(games) >= 50:
            reason = "Заблокировано: список активных партий неполный или некорректный."
            safe = False
        elif any(classify_lichess_opponent(game) != "official_ai" for game in games):
            reason = "Заблокировано: обнаружена активная партия с человеком или неизвестным соперником."
            safe = False
        else:
            safe, reason = True, "Безопасность подтверждена Lichess; упражнение использует только сохранённый анализ."
        context = self._review_safety_context
        self._review_safety_request_id = None
        self._review_safety_context = None
        self._dashboard_analysis_safety_allowed = bool(safe)
        if safe:
            self._schedule_legacy_metrics_batch()
        else:
            self._cancel_legacy_metrics_batch("safety_revoked")
        self._refresh_dashboard_analysis_visibility()
        if context and self._review_workspace_identity() == (
                context["game_id"], context["line_revision"], context["line_hash"]):
            self.review_workspace.set_training_safety_result(safe, reason)
        return True

    def _review_safety_error_received(self, request_id: str, message: str) -> bool:
        if request_id != self._review_safety_request_id:
            return False
        if self._review_safety_reply_is_current(request_id):
            self._review_safety_request_id = None
            self._review_safety_context = None
            self._dashboard_analysis_safety_allowed = False
            self._cancel_legacy_metrics_batch("safety_revoked")
            self._refresh_dashboard_analysis_visibility()
            self._revoke_review_safety(
                f"Заблокировано: проверка Lichess не выполнена ({message}).")
        return True

    def _export_review_pgn(self, game_id: str) -> None:
        game = self.game_repository.get_game(game_id)
        if not game:
            return
        self.online_session = self.online_session or LichessGameSession(
            game_id, self.game_repository)
        self.online_session.game_id = game_id
        self.export_current_pgn()

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
        self.board.set_flipped(self._effective_board_orientation())
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
        before_board = self.controller.board.copy(stack=True)
        previous = tuple(self.controller.board.move_stack)
        if self.controller.apply_move_intent(move):
            if self._position_eval_thread is not None:
                self._cancel_position_evaluation("Позиция изменилась; предыдущая оценка отменена.")
            if self._position_eval_mode:
                self.position_eval_label.setText("Позиция обновлена; ждём завершения ходов…")
                self._position_eval_timer.start(300)
            if tuple(self.controller.board.move_stack) != previous:
                accepted_move = self.controller.board.move_stack[-1]
                self.sound_player.play_move(
                    self._local_sound_game_id, len(self.controller.board.move_stack), before_board, accepted_move,
                    game_finished=self.controller.board.is_game_over())
                self.board.clear_annotations()
            self.refresh("Ход записан в локальную партию.")
        else:
            self.refresh("Ход не выполнен: позиция не изменилась.")

    def analyze_local_position(self) -> None:
        """Explicit one-shot action, retained for callers and UI integration tests."""
        if not self._position_eval_mode and self._position_eval_preflight_id is None:
            self.position_eval_button.setChecked(True)
            return
        self._start_position_evaluation()

    def _position_eval_context_available(self) -> bool:
        if self._is_live_online_session():
            return False
        if self.online_session is not None:
            return self._screen_mode == "review" and self.online_session.status in {
                "mate", "resign", "stalemate", "timeout", "outoftime", "draw",
                "cheat", "noStart", "variantEnd", "unknownFinish", "aborted",
            }
        return (self._screen_mode == "local" and self._local_board_mode == "free")

    def _set_position_evaluation_enabled(self, enabled: bool) -> None:
        if not enabled:
            self._disable_position_evaluation("Оценка Stockfish выключена.")
            return
        if not self._position_eval_context_available():
            self._disable_position_evaluation(
                "Оценка запрещена: выберите свободную доску или завершённую партию.")
            return
        engine_path = self.settings.value("stockfish_verified_path", "", type=str)
        if (not engine_path or engine_path != self.settings.value("stockfish_path", "", type=str)
                or not Path(engine_path).is_file()):
            self._disable_position_evaluation(
                "Stockfish не проверен. Откройте настройки и выполните UCI-проверку.")
            return
        if self.offline_mode and self.lichess is None and not self._lichess_connected:
            self._position_eval_mode = True
            self.position_eval_button.setText("Выключить оценку позиции")
            self.position_eval_label.setText(
                "Изолированный --offline: анализируется только текущая позиция.")
            self._position_eval_timer.start(300)
            return
        self._request_position_eval_preflight()

    def _request_position_eval_preflight(self) -> None:
        service = self.lichess
        username = str(getattr(service, "username", "") or "").strip()
        active_human_game = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        if not service or not username or not self._online_actions_allowed():
            self._disable_position_evaluation(
                "Безопасность не подтверждена. Подключите Lichess; при 429 дождитесь Retry-After.")
            return
        if active_human_game:
            self._disable_position_evaluation(
                "Stockfish заблокирован: в аккаунте уже есть активная партия с человеком.")
            return
        request_id = uuid.uuid4().hex
        self._position_eval_preflight_id = request_id
        self._position_eval_preflight_context = (
            self._analysis_session_generation, username.casefold(), service)
        self._position_eval_safety_lease = None
        self._position_eval_mode = False
        self.position_eval_button.setText("Проверяем безопасность…")
        self.position_eval_label.setText(
            "Проверяем /api/account/playing; Stockfish пока не запущен.")
        service.check_active_games_for_analysis(request_id)

    def _position_eval_preflight_is_current(self, request_id: str) -> bool:
        context = self._position_eval_preflight_context
        if (request_id != self._position_eval_preflight_id or not context
                or len(context) != 3):
            return False
        generation, account_id, service = context
        current = str(getattr(self.lichess, "username", "") or "").strip().casefold()
        if (service is not self.lichess or generation != self._analysis_session_generation
                or account_id != current or not self._online_actions_allowed()
                or not self._position_eval_context_available()):
            self._disable_position_evaluation(
                "Ответ проверки устарел; состояние изменилось, Stockfish не запущен.")
            return False
        return True

    def _position_eval_preflight_received(self, request_id: str, games: object) -> None:
        if not self._position_eval_preflight_is_current(request_id):
            return
        if (not isinstance(games, list) or len(games) >= 50
                or any(not isinstance(game, dict) or not game.get("gameId") for game in games)):
            self._disable_position_evaluation(
                "Не удалось подтвердить полноту /api/account/playing; Stockfish заблокирован.")
            return
        active_human_sessions = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        if (active_human_sessions
                or self.engine_analysis.playing_snapshot_allows_analysis(games) is not True):
            self._disable_position_evaluation(
                "Stockfish заблокирован: у аккаунта есть активная партия с человеком.")
            return
        context = self._position_eval_preflight_context
        self._position_eval_preflight_id = None
        self._position_eval_preflight_context = None
        self._position_eval_safety_lease = (*context, time.monotonic())
        self._position_eval_mode = True
        self._position_eval_queued = False
        self.position_eval_button.setText("Выключить оценку позиции")
        self.position_eval_label.setText(
            "Lichess не сообщает об активной человеческой партии; локальная оценка включена.")
        self._position_eval_timer.start(300)

    def _position_eval_preflight_error(self, request_id: str, message: str) -> None:
        if not self._position_eval_preflight_is_current(request_id):
            return
        self._disable_position_evaluation(
            f"Проверка безопасности не прошла; Stockfish не запущен ({message}).")

    def _disable_position_evaluation(self, message: str) -> None:
        self.position_eval_button.blockSignals(True)
        self.position_eval_button.setChecked(False)
        self.position_eval_button.blockSignals(False)
        self.position_eval_button.setText("Анализировать позицию")
        self._position_eval_mode = False
        self._position_eval_preflight_id = None
        self._position_eval_preflight_context = None
        self._position_eval_safety_lease = None
        self._position_eval_timer.stop()
        self._position_eval_queued = False
        self._cancel_position_evaluation(message)

    def _start_position_evaluation(self) -> None:
        """Evaluate the latest board after debounce; only one UCI job may be active."""
        if not self._position_eval_mode:
            return
        if not self._position_eval_context_available():
            self._disable_position_evaluation(
                "Оценка приостановлена: контекст партии неизвестен или небезопасен.")
            return
        if self.offline_mode and (self.lichess is not None or self._lichess_connected):
            self._disable_position_evaluation(
                "Оценка приостановлена: безопасность --offline нарушена; Stockfish остановлен.")
            return
        if not self.offline_mode:
            lease = self._position_eval_safety_lease
            if (not lease or len(lease) != 4 or lease[1] != str(self.lichess.username).casefold()
                    or lease[2] is not self.lichess
                    or lease[0] != self._analysis_session_generation
                    or time.monotonic() - lease[3] > 4.0
                    or not self._online_actions_allowed()):
                self._position_eval_queued = True
                self._request_position_eval_preflight()
                return
        if self._position_eval_thread is not None and self._position_eval_thread.is_alive():
            self._position_eval_queued = True
            return
        engine_path = self.settings.value("stockfish_verified_path", "", type=str)
        configured = self.settings.value("stockfish_path", "", type=str)
        if not engine_path or engine_path != configured or not Path(engine_path).is_file():
            self.position_eval_label.setText("Stockfish не проверен. Откройте настройки и выполните проверку UCI.")
            return
        self._position_eval_generation += 1
        generation = self._position_eval_generation
        board = (self.online_session.render_board if self.online_session is not None
                 else self.controller.snapshot().board).copy(stack=False)
        stop_event = threading.Event()
        active: dict = {}
        active["on_update"] = lambda info: self.position_eval_update.emit(generation, info)
        self._position_eval_stop = stop_event
        self._position_eval_handle = active
        self.position_eval_button.setEnabled(False)
        self.position_eval_label.setText("Оцениваем текущую позицию…")

        lease = self._position_eval_safety_lease
        position_service = self.lichess
        position_generation = self._analysis_session_generation
        position_token = getattr(position_service, "token", None) if position_service else None
        position_username = str(getattr(position_service, "username", "") or "").strip().casefold()
        isolated_offline = bool(self.offline_mode and position_service is None
                                and not self._lichess_connected)
        active_human_ids = {session.game_id for session in self._sessions_for_safety()
                            if session.status in {"started", "playing"} and not session.is_ai_opponent}

        def position_safety_check() -> bool:
            if stop_event.is_set() or active_human_ids:
                return False
            if isolated_offline:
                return (self.offline_mode and self.lichess is None
                        and not self._lichess_connected
                        and position_generation == self._analysis_session_generation)
            if not lease:
                return False
            return self.engine_analysis.refresh_fair_play(
                position_service, position_token, position_username,
                is_current=lambda: (
                    not stop_event.is_set() and position_service is self.lichess
                    and self._lichess_connected
                    and position_generation == self._analysis_session_generation
                    and self._online_actions_allowed()),
                active_human_session=bool(active_human_ids))

        def work() -> None:
            try:
                payload = self.engine_analysis.evaluate_position(
                    engine_path, board, stop_event, active,
                    safety_check=position_safety_check, evaluator=evaluate_position)
            except Exception as exc:
                payload = {"error": str(exc)}
            active["worker_thread"] = threading.current_thread()
            self.position_eval_finished.emit(generation, payload, threading.current_thread())

        thread = self._start_registered_work(
            work, kind="stockfish_position_evaluation", lane=OperationLane.ENGINE,
            stop_event=stop_event, thread_name="chess-coach-position-eval")
        self._position_eval_thread = thread
        self._statistics_workers.add(thread)

    def _cancel_position_evaluation(self, message: str) -> None:
        self._position_eval_generation += 1
        if self._position_eval_stop is not None:
            self._position_eval_stop.set()
        analysis = self._position_eval_handle.get("analysis")
        if analysis is not None:
            try:
                analysis.stop()
            except Exception:
                pass
        if self.position_eval_label.isVisible():
            self.position_eval_label.setText(message)

    def _position_eval_finished(self, generation: int, payload: object,
                                 thread: threading.Thread) -> None:
        thread.join(timeout=0.05)
        self._statistics_workers.discard(thread)
        operation = self._position_eval_thread
        if operation is not None:
            self._statistics_workers.discard(operation)
        if self._position_eval_handle.get("worker_thread") is thread:
            self._position_eval_thread = None
            self._position_eval_stop = None
            self._position_eval_handle = {}
        if generation == self._position_eval_generation and isinstance(payload, dict):
            if payload.get("error"):
                self.position_eval_label.setText(f"Ошибка Stockfish: {payload['error']}")
            elif payload.get("cancelled"):
                self.position_eval_label.setText("Оценка остановлена.")
            else:
                cp = payload.get("score_white_cp")
                score = (f"мат {'белым' if payload['mate_white'] > 0 else 'чёрным'} через "
                         f"{abs(payload['mate_white'])}" if payload.get("mate_white") is not None
                         else (f"{cp / 100:+.2f} пешки за белых" if cp is not None else "оценка недоступна"))
                shown_board = (self.online_session.render_board if self.online_session is not None
                               else self.controller.snapshot().board).copy(stack=False)
                pv_san = []
                best = "не сохранён"
                try:
                    pv = payload.get("pv") or ([payload["best_move"]]
                                                  if payload.get("best_move") else [])
                    for raw in pv:
                        candidate = chess.Move.from_uci(str(raw))
                        if candidate not in shown_board.legal_moves:
                            break
                        if not pv_san:
                            best = shown_board.san(candidate)
                        pv_san.append(shown_board.san(candidate))
                        shown_board.push(candidate)
                except (ValueError, chess.InvalidMoveError):
                    best, pv_san = "не удалось отобразить", []
                self.position_eval_label.setText(
                    f"Stockfish · {score} · лучший ход {best} · "
                    f"глубина {payload.get('depth') or 'н/д'} · вариант "
                    f"{' '.join(pv_san) or 'н/д'}; это оценка движка, не точность Lichess.")
                live_board = (self.online_session.render_board if self.online_session is not None
                              else self.controller.snapshot().board)
                current_ply = len(live_board.move_stack)
                self.moves.set_score(current_ply, white_cp=payload.get("score_white_cp"),
                                     mate_white=payload.get("mate_white"), source="Stockfish",
                                     depth=payload.get("depth"))
        if self._position_eval_queued and self._position_eval_mode:
            self._position_eval_queued = False
            self._position_eval_timer.start(0)
        self.refresh()

    def _position_eval_update(self, generation: int, info: object) -> None:
        if (generation != self._position_eval_generation or not isinstance(info, dict)
                or self._position_eval_stop is None or self._position_eval_stop.is_set()):
            return
        score = info.get("score")
        if score is None:
            label = "оценка пока недоступна"
        else:
            white_score = score.pov(chess.WHITE)
            cp = white_score.score(mate_score=None)
            mate = white_score.mate()
            if mate is not None and mate != 0:
                label = (f"мат за белых через {mate}" if mate > 0 else
                         f"мат за чёрных через {abs(mate)}")
            elif mate == 0:
                board = (self.online_session.render_board if self.online_session is not None
                         else self.controller.snapshot().board)
                if board.is_checkmate():
                    winner = "чёрные" if board.turn == chess.WHITE else "белые"
                    label = f"мат уже поставлен; победили {winner}"
                else:
                    label = "мат в текущей позиции не подтверждён доской"
            elif cp is not None:
                label = f"{cp / 100:+.2f} за белых"
            else:
                label = "оценка недоступна"
        self.position_eval_label.setText(
            f"Stockfish · текущая позиция · {label} · глубина {info.get('depth') or '…'}")

    def on_premove_intent(self, move: chess.Move) -> None:
        if self.online_session is not None:
            if self.online_session.queue_premove(move):
                self.sound_player.play_premove(uuid.uuid4().hex)
                self.board.set_premove(move, self.online_session.color)
                self.refresh("Предход записан и будет проверен после хода соперника.")
            else:
                self.refresh("Предход не принят в текущей позиции.")
            return
        if self.controller.queue_premove(move):
            self.sound_player.play_premove(uuid.uuid4().hex)
            self.refresh("Предход поставлен. Он будет проверен после хода соперника.")
        else:
            self.refresh("Предход не принят в текущем состоянии.")

    def cancel_premove(self) -> None:
        if self.online_session is not None:
            if self.online_session.premove is None and self.online_session.pending_uci:
                self._refresh_online("Этот ход уже отправлен Lichess; отменить его на сервере нельзя.")
                return
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
        before_board = self.controller.board.copy(stack=True)
        previous = tuple(self.controller.board.move_stack)
        _, message = self.controller.apply_test_opponent_move(move)
        added = self.controller.board.move_stack[len(previous):]
        sound_board = before_board
        for ply, confirmed in enumerate(added, start=len(previous) + 1):
            self.sound_player.play_move(
                self._local_sound_game_id, ply, sound_board, confirmed,
                game_finished=self.controller.board.is_game_over() and ply == len(self.controller.board.move_stack))
            sound_board.push(confirmed)
        if tuple(self.controller.board.move_stack) != previous:
            self.board.clear_annotations()
        self.refresh(message)

    def on_move_selected(self, item: QListWidgetItem) -> None:
        ply = int(item.data(Qt.ItemDataRole.UserRole))
        self.on_move_selected_ply(ply)

    def on_move_selected_ply(self, ply: int) -> None:
        ply = int(ply)
        if self.online_session is not None:
            self._play_history_sound(self.online_session.game_id, self.online_session.board, ply)
            self.online_view_ply = None if ply >= len(self.online_session.board.move_stack) else ply
            self.refresh()
            return
        self._play_history_sound(self._local_sound_game_id, self.controller.board, ply)
        self.controller.go_to(ply)
        self.refresh()

    def _play_history_sound(self, game_id: str, board: chess.Board, ply: int) -> None:
        """Play history audio only for an explicit move-row click, never scrolling."""
        if not 1 <= ply <= len(board.move_stack):
            return
        before = board.root()
        for move in board.move_stack[:ply - 1]:
            before.push(move)
        move = board.move_stack[ply - 1]
        self.sound_player.play_move(game_id, ply, before, move, source="history")

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
        flipped = not self.board._flipped
        if self.online_session is not None:
            self._orientation_overrides[self.online_session.game_id] = flipped
        elif self._screen_mode == "local" and self._local_board_mode == "free":
            self.settings.setValue("free_board_flipped", flipped)
            self.settings.sync()
        else:
            self._orientation_overrides["local:premove-training"] = flipped
        self.board.set_flipped(flipped)

    def _effective_board_orientation(self) -> bool:
        """Return the board orientation for the current game, never for the current ply."""
        if self.online_session is not None:
            key = self.online_session.game_id
            return self._orientation_overrides.get(key, self.online_session.color == chess.BLACK)
        if self._screen_mode == "local" and self._local_board_mode == "premove_training":
            return self._orientation_overrides.get(
                "local:premove-training", self.controller.player_color == chess.BLACK)
        if self._screen_mode == "local" and self._local_board_mode == "free":
            return self.settings.value(
                "free_board_flipped",
                self.settings.value("board_flipped", False, type=bool), type=bool)
        return self.settings.value("board_flipped", False, type=bool)

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
        self._local_sound_game_id = uuid.uuid4().hex
        self.board.clear_annotations()
        self.sound_player.play_game_start(self._local_sound_game_id)
        self.refresh("Новая локальная партия.")
        if self._position_eval_mode:
            self._position_eval_timer.start(300)

    def open_settings(self) -> None:
        current = getattr(self, "_settings_dialog", None)
        if current is not None:
            self.screen_stack.setCurrentWidget(current)
            return
        dialog = SettingsDialog(self.settings, self, repository=self.game_repository)
        dialog._auto_analytics_was_enabled = self.settings.value(
            "lichess/auto_ready_analytics", True, type=bool)
        dialog._auto_archive_was_enabled = self.settings.value(
            "lichess/auto_archive_sync", True, type=bool)
        dialog._auto_puzzles_was_enabled = self.settings.value(
            "lichess/auto_puzzle_history_sync", True, type=bool)
        dialog.sound_preview_requested.connect(
            lambda cue: self.sound_player.preview(cue, dialog.sound_selection(cue)))
        dialog.install_requested.connect(
            lambda current=dialog: self._install_official_stockfish(current))
        dialog.finished.connect(lambda _result: self._cancel_stockfish_install())
        dialog.accepted.connect(lambda current=dialog: self._save_settings_dialog(current))
        dialog.rejected.connect(lambda current=dialog: self._close_settings_page(current))
        dialog.setModal(False)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        self._settings_dialog = dialog
        self.screen_stack.addWidget(dialog)
        self.screen_title.setText("Настройки")
        self.screen_stack.setCurrentWidget(dialog)

    def _close_settings_page(self, dialog: SettingsDialog) -> None:
        if getattr(self, "_settings_dialog", None) is not dialog:
            return
        # Move first: removing the current QStackedWidget page would select a
        # fallback widget and accidentally record that transient route as Back.
        self.navigate_back()
        self.screen_stack.removeWidget(dialog)
        dialog.deleteLater()
        self._settings_dialog = None

    def _save_settings_dialog(self, dialog: SettingsDialog) -> None:
        if getattr(self, "_settings_dialog", None) is not dialog:
            return
        auto_analytics_was_enabled = bool(
            getattr(dialog, "_auto_analytics_was_enabled", True))
        auto_archive_was_enabled = bool(getattr(dialog, "_auto_archive_was_enabled", True))
        auto_puzzles_was_enabled = bool(getattr(dialog, "_auto_puzzles_was_enabled", True))
        dialog.save(self.settings)
        username = str(getattr(self.lichess, "username", "") or "").strip()
        if username and not self.offline_mode:
            sync_ready = bool(self._lichess_connected and self.lichess and
                              self._lichess_stream_ready_generation == getattr(
                                  self.lichess, "auth_generation", None))
            auto_archive = self.settings.value(
                "lichess/auto_archive_sync", True, type=bool)
            auto_puzzles = self.settings.value(
                "lichess/auto_puzzle_history_sync", True, type=bool)
            if auto_archive or auto_puzzles:
                if sync_ready:
                    if ((auto_archive and not auto_archive_was_enabled)
                            or (auto_puzzles and not auto_puzzles_was_enabled)):
                        QTimer.singleShot(0, self._run_background_sync)
                    self._auto_sync_timer.start()
                else:
                    self._auto_sync_timer.stop()
            else:
                self._auto_sync_timer.stop()
            if (auto_puzzles_was_enabled and not self.settings.value(
                    "lichess/auto_puzzle_history_sync", True, type=bool)
                    and hasattr(self.lichess, "cancel_puzzle_sync")):
                self.lichess.cancel_puzzle_sync()
            paused = self.settings.value("lichess/ready_analytics_paused", False, type=bool)
            service = self.lichess
            generation = getattr(service, "auth_generation", None)

            def persist_backfill_pause() -> None:
                if (service is not self.lichess
                        or generation != getattr(self.lichess, "auth_generation", None)):
                    return
                account_id = self.game_repository.ensure_archive_account(username)
                self.game_repository.set_lichess_analysis_backfill_paused(account_id, paused)

            self._start_registered_work(
                persist_backfill_pause, kind="save_analytics_pause",
                lane=OperationLane.SQLITE_WRITER, thread_name="cc-save-analytics-pause")
            if not self.settings.value("lichess/auto_ready_analytics", True, type=bool):
                self._cancel_lichess_backfill_wakeup()
                self.lichess.cancel_lichess_analysis_backfill()
                self.lichess_analysis_status.setText("Lichess · фоновая аналитика выключена")
            elif paused:
                self._cancel_lichess_backfill_wakeup()
                self.lichess.cancel_lichess_analysis_backfill()
                self.lichess_analysis_status.setText("Lichess · фоновая аналитика приостановлена")
            elif (not auto_analytics_was_enabled and self._lichess_connected
                  and self._lichess_stream_ready_generation == getattr(
                      self.lichess, "auth_generation", None)):
                self._start_lichess_analysis_backfill(
                    self.lichess.auth_generation, manual=False)
        self.sound_player.configure(
            moves_enabled=self.settings.value("sound_moves_enabled", True, type=bool),
            notifications_enabled=self.settings.value("sound_notifications_enabled", True, type=bool),
            history_enabled=self.settings.value("sound_history_enabled", False, type=bool),
            volume=self.settings.value("sound_volume", 45, type=int),
            selections={
                cue: self.settings.value(f"sound_selection_{cue}", default_id, type=str)
                for cue, default_id in DEFAULT_SOUND_SELECTIONS.items()
            })
        new_color = chess.Color(int(self.settings.value("player_color", chess.WHITE)))
        self.controller.player_color = new_color
        if not self.settings.value("allow_premoves", True, type=bool):
            self.cancel_premove()
        self.refresh("Настройки сохранены.")
        self._refresh_batch_readiness()
        self._close_settings_page(dialog)

    def _cancel_stockfish_install(self) -> None:
        if self._engine_install_stop is not None:
            self._engine_install_stop.set()

    def _install_official_stockfish(self, settings_dialog: SettingsDialog) -> None:
        thread = self._engine_install_thread
        if thread is not None and thread.is_alive():
            return
        answer = QMessageBox.question(
            settings_dialog, "Установить Stockfish 19",
            "Chess Coach скачает около 82 МБ из официального GitHub Stockfish, "
            "проверит опубликованный SHA-256 и UCI-ответ, затем установит движок в "
            "%LOCALAPPDATA%\\ChessCoach\\engines\\stockfish\\19. "
            "Это не запускает анализ партий. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        target = local_app_data / "ChessCoach" / "engines" / "stockfish" / "19"
        stop = threading.Event()
        self._engine_install_stop = stop
        progress = QProgressDialog("Подключаемся к официальному выпуску…", "Отменить", 0, 0,
                                   settings_dialog)
        progress.setWindowTitle("Установка Stockfish 19")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.canceled.connect(stop.set)
        self._engine_install_dialog = progress
        progress.show()

        def work() -> None:
            try:
                result = install_official_stockfish(
                    destination=target, stop_event=stop,
                    progress=self.engine_install_progress.emit)
                self.engine_install_finished.emit({"result": result, "dialog": settings_dialog})
            except Exception as exc:
                self.engine_install_finished.emit({"error": str(exc), "dialog": settings_dialog})

        thread = self._start_registered_work(
            work, kind="stockfish_install", lane=OperationLane.NETWORK_BACKGROUND,
            stop_event=stop, thread_name="chess-coach-stockfish-install")
        self._engine_install_thread = thread

    def _engine_install_progress_changed(self, message: str) -> None:
        if self._engine_install_dialog is not None:
            self._engine_install_dialog.setLabelText(message)

    def _engine_install_finished(self, outcome: object) -> None:
        self._engine_install_stop = None
        if self._engine_install_dialog is not None:
            self._engine_install_dialog.close()
            self._engine_install_dialog = None
        if not isinstance(outcome, dict):
            return
        settings_dialog = outcome.get("dialog")
        if "result" in outcome:
            result = outcome["result"]
            path = str(result["path"])
            self.settings.setValue("stockfish_path", path)
            self.settings.setValue("stockfish_verified_path", path)
            self.settings.setValue("stockfish_name", str(result["name"]))
            self.settings.sync()
            if settings_dialog is not None and shiboken6.isValid(settings_dialog):
                settings_dialog.engine_path.setText(path)
                settings_dialog._engine_verified_path = path
                settings_dialog.engine_test_status.setText(
                    f"Готов: {result['name']} · SHA-256 {result['sha256']}\n{path}")
            if self._batch_dialog is not None:
                self._refresh_batch_readiness()
            self.home_status.setText(f"Stockfish установлен и проверен: {result['name']} · {path}")
        else:
            message = str(outcome.get("error") or "Неизвестная ошибка установки.")
            if settings_dialog is not None and shiboken6.isValid(settings_dialog):
                settings_dialog.engine_test_status.setText(f"Установка не завершена: {message}")
            if "отменена" not in message.casefold():
                QMessageBox.warning(self, "Не удалось установить Stockfish", message)
        self._engine_install_thread = None

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
        self._open_lichess_connection_dialog()

    def _open_lichess_connection_dialog(self) -> None:
        try:
            oauth_saved = bool(self.lichess.oauth_token_store.get())
            personal_saved = bool(self.lichess.personal_token_store.get())
        except CredentialStorageError as exc:
            QMessageBox.warning(self, "Lichess", str(exc))
            return
        method = self.settings.value("lichess/selected_auth_method", "oauth", type=str)
        dialog = LichessConnectionDialog(method, oauth_saved=oauth_saved,
                                         personal_token_saved=personal_saved, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._connection_dialog = dialog
        dialog.method_changed.connect(self._select_lichess_auth_method)
        dialog.restore_requested.connect(self._restore_selected_lichess_auth)
        dialog.oauth_requested.connect(self._start_lichess_oauth)
        dialog.personal_token_requested.connect(self._save_personal_lichess_token)
        dialog.delete_personal_token_requested.connect(self._delete_personal_lichess_token)
        dialog.diagnostics_requested.connect(self._show_lichess_diagnostic_report)
        def update_dialog_status(generation: int, status: str) -> None:
            if (self._current_lichess_generation(generation)
                    and shiboken6.isValid(dialog)):
                dialog.set_status(status)
        self.lichess.session_status_changed.connect(update_dialog_status)
        dialog.revoke_oauth_requested.connect(self._revoke_oauth_lichess)
        def close_dialog(_result: int) -> None:
            try:
                self.lichess.session_status_changed.disconnect(update_dialog_status)
            except (RuntimeError, TypeError):
                pass
            if self._connection_dialog is dialog:
                self._connection_dialog = None
        dialog.finished.connect(close_dialog)
        dialog.open()

    def _show_lichess_diagnostic_report(self) -> None:
        report = anonymized_report(
            schema_version=SCHEMA_VERSION,
            engine_verified=(self.settings.value("stockfish_verified_path", "", type=str)
                             == self.settings.value("stockfish_path", "", type=str)
                             and bool(self.settings.value("stockfish_path", "", type=str))),
            recent_counts=(self.game_repository.analysis_job_counts()
                           if self.game_repository is not None else {}))
        QMessageBox.information(self._connection_dialog or self, "Обезличенная диагностика", report)

    def _select_lichess_auth_method(self, method: str) -> None:
        self._invalidate_analysis_preflight()
        self._lichess_connected = False
        self._reset_backfill_status("Lichess · способ подключения изменён; ожидаем аккаунт")
        self.settings.setValue("lichess/selected_auth_method", method)
        self.settings.sync()
        if self.lichess is None:
            return
        self.lichess.set_auth_method(method)
        # A deliberate mode switch validates only that method's own saved token.
        try:
            if self.lichess.selected_token_store.get():
                self.lichess.restore()
            else:
                self._set_connection_header("Нет связи", "Учётные данные не выбраны.")
        except CredentialStorageError as exc:
            if self._connection_dialog:
                self._connection_dialog.set_status(str(exc))

    def _restore_selected_lichess_auth(self) -> None:
        if not self.lichess:
            return
        self._invalidate_analysis_preflight()
        self.connect_button.setEnabled(False)
        self.lichess.restore()

    def _start_lichess_oauth(self) -> None:
        if not self.lichess:
            return
        self._invalidate_analysis_preflight()
        self._lichess_connected = False
        self._reset_backfill_status("Lichess · ожидаем OAuth-подключение")
        self.settings.setValue("lichess/selected_auth_method", "oauth")
        self.settings.sync()
        self.lichess.set_auth_method("oauth")
        self.lichess.connect_account()

    def _save_personal_lichess_token(self, token: str) -> None:
        if not self.lichess:
            return
        self._invalidate_analysis_preflight()
        self._lichess_connected = False
        self._reset_backfill_status("Lichess · проверяем новое подключение")
        self.settings.setValue("lichess/selected_auth_method", "personal_token")
        self.settings.sync()
        self.lichess.set_auth_method("personal_token")
        self.lichess.connect_personal_token(token)

    def _delete_personal_lichess_token(self) -> None:
        if not self.lichess:
            return
        answer = QMessageBox.question(
            self, "Удалить локальный токен",
            "Удалить сохранённую копию из Windows Credential Manager? Это не отзывает токен на Lichess.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.lichess.delete_saved_personal_token()
            if self.lichess.auth_method == "personal_token":
                self._invalidate_analysis_preflight()
                self._lichess_connected = False
            if self._connection_dialog:
                self._connection_dialog.personal_saved_badge.setText(
                    "Локально сохранённый токен удалён; на Lichess он не отозван")
                self._connection_dialog.personal_delete_button.setEnabled(False)
        except CredentialStorageError as exc:
            QMessageBox.warning(self, "Lichess", str(exc))

    def _revoke_oauth_lichess(self) -> None:
        if not self.lichess:
            return
        answer = QMessageBox.question(
            self, "Отозвать OAuth-токен",
            "Lichess отзовёт сохранённый OAuth-токен. Это действие не затрагивает PAT. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Drop the review safety lease before starting asynchronous revocation.
        # Neither a successful nor failed revoke can restore this authorization.
        self._lichess_status("Отзываем сохранённый OAuth-токен…")
        self.lichess.revoke_oauth_credential()
        if self._connection_dialog and shiboken6.isValid(self._connection_dialog):
            self._connection_dialog.oauth_revoke_button.setEnabled(False)

    def disconnect_lichess(self) -> None:
        self._detach_lichess_sessions()
        self._lichess_session_selected = False
        self._auto_sync_timer.stop()
        self._cancel_dashboard_safety_retry()
        self._invalidate_analysis_preflight()
        self._dashboard_analysis_safety_allowed = bool(self.offline_mode)
        self._refresh_dashboard_analysis_visibility()
        self._lichess_connected = False
        self._set_sidebar_safety_status("Аналитика скрыта · отключено")
        self._reset_backfill_status("Lichess · отключено")
        self._pause_batch_analysis()
        if self.lichess:
            self.lichess.disconnect_account()
        if getattr(self, "_background_operations_page", None) is not None:
            self._refresh_background_operations_page()

    def _invalidate_analysis_preflight(self) -> None:
        """Fail closed and stop pending/running post-game work on trust changes."""
        self._analysis_session_generation += 1
        self._cancel_dashboard_safety_retry()
        if not self.offline_mode:
            self._dashboard_display_safety_request_id = None
            self._dashboard_display_safety_context = None
            self._dashboard_analysis_safety_allowed = False
            self._refresh_dashboard_analysis_visibility()
        self._revoke_review_safety(
            "Заблокировано: изменилось состояние подключения; проверьте безопасность повторно.",
            preserve_offline=True)
        self._pause_batch_analysis()
        self._disable_position_evaluation(
            "Проверка Stockfish остановлена: изменилось состояние подключения Lichess.")
        if self._analysis_safety_phase == "preflight" and self._analysis_safety_request_id:
            self._cancel_analysis_action(self._analysis_safety_request_id)
        elif self._analysis_safety_phase == "running" and self._analysis_stop is not None:
            self._analysis_stop.set()
            if self._analysis_dialog is not None:
                self._analysis_dialog.setLabelText(
                    "Остановлено: нужна повторная проверка безопасности.")
                button = self._analysis_dialog.findChild(QPushButton)
                if button is not None:
                    button.setEnabled(False)

    def _refresh_batch_readiness(self) -> None:
        dialog = self._batch_dialog
        if dialog is None or not shiboken6.isValid(dialog):
            return
        engine_path = self.settings.value("stockfish_path", "", type=str)
        verified = self.settings.value("stockfish_verified_path", "", type=str) == engine_path
        username = str(getattr(self.lichess, "username", "") or "").strip()
        active_human_game = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        remaining = self._lichess_rate_limit_remaining()
        account_connected = bool(self._online_actions_allowed() and username
                                 and not active_human_game)
        if active_human_game:
            reason = "Stockfish приостановлен: у аккаунта активная партия с человеком."
        elif remaining:
            reason = f"Ограничение Lichess (429): проверка безопасности через {remaining} с."
        elif not username:
            reason = "Подключите аккаунт Lichess; Stockfish не запускается без проверки активных игр."
        elif not self._lichess_connected:
            reason = "Подключение Lichess не подтверждено; безопасность неизвестна."
        else:
            reason = "Безопасность аккаунта не подтверждена."
        dialog.set_readiness(engine_path, verified, account_connected, reason)

    def cancel_username_challenge(self) -> None:
        if self.lichess:
            self.lichess.cancel_username_challenge()

    def show_active_games(self) -> None:
        if not self._online_actions_allowed():
            self.home_status.setText("Подключите Lichess, чтобы запросить активные партии.")
            return
        self.show_saved_games()
        self.home_status.setText("Обновляю список активных партий Lichess…")
        self._active_games_request_generation = getattr(self.lichess, "auth_generation", None)
        self._active_games_request_id = self.lichess.fetch_active_games()

    def _request_active_games(self) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if not dialog or not self.lichess:
            return
        dialog.set_loading()
        self._active_games_request_generation = getattr(self.lichess, "auth_generation", None)
        self._active_games_request_id = self.lichess.fetch_active_games()

    def _active_games_received(self, games: list[dict], *, generation: int | None = None,
                               request_id: str | None = None) -> None:
        if (generation is not None and (not self.lichess
                or generation != self.lichess.auth_generation
                or generation != self._active_games_request_generation
                or request_id != self._active_games_request_id)):
            return
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_games(games)
        self._remote_active_games = [dict(game) for game in games if isinstance(game, dict)]
        if self.lichess and generation is not None:
            owner = self.lichess.username or ""
            for game in self._remote_active_games:
                game_id = str(game.get("gameId") or "")
                session = self.online_sessions.get(game_id) if game_id else None
                if session is None:
                    continue
                try:
                    session.bind_owner(owner, generation)
                except ValueError:
                    session.detach()
                    replacement = LichessGameSession(game_id, self.game_repository)
                    replacement.status = "started"
                    replacement.bind_owner(owner, generation)
                    self.game_sessions.register(replacement)
                    session = replacement
                session.set_server_game_identity(game)
        self._update_library_active_games()

    def _active_games_error(self, message: str) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_error(message)

    def open_active_game(self, game: dict) -> None:
        game_id = game.get("gameId")
        if not game_id or not self._online_actions_allowed():
            return
        # The snapshot is account/generation scoped. Never start a game stream
        # merely because a stale row or foreign caller supplied a game ID.
        if self.lichess and getattr(self.lichess, "username", None):
            if not any(str(item.get("gameId") or "") == str(game_id)
                       for item in getattr(self, "_remote_active_games", ())):
                return
        session = self.online_sessions.get(str(game_id))
        if session is not None and session.status not in {"created", "started", "playing"}:
            QMessageBox.information(self, "Активная партия", "Lichess сообщает, что эта партия уже завершена.")
            return
        owner = getattr(self.lichess, "username", None)
        generation = getattr(self.lichess, "auth_generation", None)
        if session is None:
            session = LichessGameSession(str(game_id), self.game_repository)
            session.status = "started"
            session.clock_control = str(game.get("speed") or "-")
            if owner and generation is not None:
                session.bind_owner(owner, generation)
            self.game_sessions.register(session)
        elif owner and generation is not None and not session.belongs_to(
                owner, generation=generation):
            try:
                session.bind_owner(owner, generation)
            except ValueError:
                session.detach()
                session = LichessGameSession(str(game_id), self.game_repository)
                session.status = "started"
                session.bind_owner(owner, generation)
                self.game_sessions.register(session)
        color = game.get("color")
        if color in {"white", "black"}:
            session.color = chess.WHITE if color == "white" else chess.BLACK
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

    @staticmethod
    def _lichess_status_invalidates_safety(status: str) -> bool:
        """Return whether a service status makes Lichess safety unknown."""
        return status.startswith((
            "Не подключено", "Ошибка подключения", "Ошибка проверки аккаунта",
            "Ограничение Lichess (429)",
            "Переподключение", "Проверка аккаунта отклонена",
            "Требуется повторное подключение", "Проверяем подключение",
            "Проверяем аккаунт", "Отзываем сохранённый OAuth-токен",
            "OAuth-токен отозван", "Сохранённый OAuth-токен не найден",
            "Не удалось отозвать OAuth-токен", "Ожидание авторизации",
            "Финальная синхронизация партии", "Потеряно соединение с партией",
        ))

    @staticmethod
    def _lichess_status_clears_authenticated_identity(status: str) -> bool:
        """Whether Lichess has rejected or explicitly ended this account session."""
        return status.startswith((
            "Не подключено", "Ошибка подключения", "Ошибка проверки аккаунта",
            "Проверка аккаунта отклонена", "Требуется повторное подключение",
            "Проверяем подключение", "Проверяем аккаунт",
            "Отзываем сохранённый OAuth-токен", "OAuth-токен отозван",
            "Сохранённый OAuth-токен не найден", "Не удалось отозвать OAuth-токен",
            "Ожидание авторизации",
        ))

    def _lichess_status(self, status: str) -> None:
        status_text = str(status or "")
        if status_text.startswith(("Проверяем подключение", "Ожидание авторизации",
                                   "Подключено:", "Поток восстановлен:",
                                   "Переподключение", "Ограничение Lichess (429)")):
            self._lichess_session_selected = True
        elif status_text == "Не подключено":
            self._lichess_session_selected = False
        if status_text.startswith("Поток восстановлен:"):
            compact_status = "Lichess · поток готов"
        elif status_text.startswith("Подключено:"):
            compact_status = "Аккаунт подтверждён · поток подключается"
        elif status_text.startswith("Ограничение Lichess (429)") or "HTTP 429" in status_text:
            compact_status = "Lichess · пауза API"
        elif "403" in status_text:
            compact_status = "Lichess · нет нужного разрешения"
        elif "401" in status_text:
            compact_status = "Lichess · нужна авторизация"
        elif status_text.startswith("Переподключение"):
            compact_status = "Lichess · восстановление потока"
        elif status_text.startswith("Не подключено"):
            compact_status = "Не подключено"
        elif status_text.startswith("Ошибка"):
            compact_status = "Lichess · ошибка соединения"
        elif status_text.startswith("Проверка аккаунта отклонена") or status_text.startswith(
                "Требуется повторное подключение"):
            compact_status = "Lichess · нужна авторизация"
        elif status_text.startswith("Ошибка проверки аккаунта"):
            compact_status = "Lichess · ошибка проверки"
        elif status_text.startswith("Проверяем"):
            compact_status = "Lichess · проверяем аккаунт"
        else:
            compact_status = status_text[:34]
        display_labels = {
            "Lichess · поток готов": "Готов",
            "Аккаунт подтверждён · поток подключается": "Вход",
            "Lichess · пауза API": "429",
            "Lichess · нет нужного разрешения": "403",
            "Lichess · нужна авторизация": "401",
            "Lichess · восстановление потока": "Связь…",
            "Не подключено": "Нет связи",
            "Lichess · ошибка соединения": "Ошибка",
            "Lichess · ошибка проверки": "Проверка",
            "Lichess · проверяем аккаунт": "Проверка",
        }
        display_status = display_labels.get(compact_status, "…")
        self._set_connection_header(display_status, "Подключение Lichess: " + compact_status)
        if not self.offline_mode and self._lichess_status_invalidates_safety(status):
            # Revoke review leases/exercises as well as live engine and batch
            # work. A recovered transport never grants a new review lease.
            self._invalidate_analysis_preflight()
            self._dashboard_display_safety_request_id = None
            self._dashboard_display_safety_context = None
            self._dashboard_analysis_safety_allowed = False
            self._set_sidebar_safety_status("Аналитика скрыта · проверка устарела")
            self._refresh_dashboard_analysis_visibility()
            if self._lichess_status_clears_authenticated_identity(status):
                self._lichess_connected = False
                self._lichess_account_authenticated = False
                self._lichess_stream_ready_generation = None
                self._event_stream_recovery_pending = False
            elif (status.startswith("Переподключение")
                  or (status.startswith("Ограничение Lichess (429)")
                      and "переподключение" in status.casefold())):
                # The account identity remains authenticated, but live actions
                # stay gated until the current generation confirms stream recovery.
                self._lichess_connected = False
                self._lichess_stream_ready_generation = None
                self._event_stream_recovery_pending = True
        elif status.startswith("Поток восстановлен: ") and not self.offline_mode:
            # This status arrives through _lichess_session_status, which rejects
            # stale auth generations before reaching this method.
            self._lichess_connected = True
            self._lichess_account_authenticated = True
            self._lichess_stream_ready_generation = getattr(self.lichess, "auth_generation", None)
            self._event_stream_recovery_pending = False
            QTimer.singleShot(0, self._request_dashboard_display_safety)
        connected = self._online_actions_allowed()
        if not self.offline_mode:
            if not self._lichess_account_authenticated:
                self._lichess_profile_username = ""
                self.sidebar_profile_label.setText("Локальный профиль")
                if status_text.startswith("Ограничение Lichess (429)") or "HTTP 429" in status_text:
                    import re
                    match = re.search(r"через (\d+) с", status_text)
                    cooldown = f" · {match.group(1)} с" if match else ""
                    self.sidebar_connection_label.setText("Пауза API" + cooldown)
                elif "403" in status_text:
                    self.sidebar_connection_label.setText("Недостаточно прав")
                elif "401" in status_text:
                    self.sidebar_connection_label.setText("Нужна авторизация")
                elif status.startswith("Ошибка"):
                    self.sidebar_connection_label.setText("Ошибка подключения")
                else:
                    self.sidebar_connection_label.setText(
                        "Ошибка проверки" if status_text.startswith("Ошибка проверки")
                        else "Нужна авторизация")
                self.connect_button.setVisible(True)
                self.disconnect_button.setVisible(self._lichess_session_selected)
            elif not connected:
                if status_text.startswith("Ограничение Lichess (429)") or "HTTP 429" in status_text:
                    import re
                    match = re.search(r"через (\d+) с", status_text)
                    cooldown = f" · {match.group(1)} с" if match else ""
                    self.sidebar_connection_label.setText("Пауза API" + cooldown)
                elif "403" in status_text:
                    self.sidebar_connection_label.setText("Недостаточно прав")
                elif "401" in status_text:
                    self.sidebar_connection_label.setText("Нужна авторизация")
                elif status_text.startswith("Ошибка проверки"):
                    self.sidebar_connection_label.setText("Ошибка проверки")
                else:
                    self.sidebar_connection_label.setText(
                        "Восстановление…" if self._event_stream_recovery_pending else
                        "Ожидание потока…")
                self.connect_button.setVisible(False)
                self.disconnect_button.setVisible(True)
            else:
                self.sidebar_connection_label.setText("Поток готов")
                self.connect_button.setVisible(False)
                self.disconnect_button.setVisible(True)
        if status == "Не подключено":
            self._auto_sync_timer.stop()
            self._cancel_dashboard_safety_retry()
            self.home_status.setText(
                "Не подключено к Lichess. Подключите аккаунт, если хотите играть онлайн.")
            for session in self._sessions_for_safety():
                session.clear_unconfirmed_move_intent()
            if self.online_session is not None:
                self._refresh_online("Соединение потеряно; неподтверждённый ход и предход сняты. Повторно отправлять их нельзя.")
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
        elif (status in {"Не подключено", "Ошибка подключения", "Ошибка проверки аккаунта"}
              or status.startswith("Проверка аккаунта отклонена")
              or status.startswith("Требуется повторное подключение")):
            self._restore_retry_ready = False
            self.connect_button.setText("Подключить Lichess")
            self.connect_button.setEnabled(True)
        elif status.startswith("Подключено"):
            self._restore_retry_ready = False
            self.connect_button.setText("Подключение…")
            self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(bool(
            not self.offline_mode and self.lichess is not None
            and (self._lichess_session_selected or self._lichess_account_authenticated
                 or connected)))
        online_allowed = connected and self._lichess_rate_limit_remaining() == 0
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setEnabled(online_allowed)
        self._update_home_game_actions()
        self.home_archive_button.setEnabled(online_allowed)
        self.home_batch_analysis_button.setEnabled(True)
        self.home_puzzles_button.setEnabled(True)
        if status.startswith("Ограничение Lichess"):
            self.home_status.setText(status + ". Автоматические запросы приостановлены.")
        self._refresh_batch_readiness()
        self._sync_position_eval_controls(self._position_eval_context_available())

    def _enable_restore_retry(self) -> None:
        if (self.lichess and getattr(self.lichess, "token", None) and not self._lichess_connected
                and not self.offline_mode and not self._event_stream_recovery_pending):
            self._restore_retry_ready = True
            self.connect_button.setText("Повторить подключение")
            self.connect_button.setEnabled(True)

    def _lichess_rate_limit_remaining(self) -> int:
        if not self.lichess:
            return 0
        client = getattr(self.lichess, "client", None)
        return max(0, int(getattr(client, "rate_limit_remaining", 0)))

    def _online_actions_allowed(self) -> bool:
        return bool(self.lichess and not self.offline_mode and self._lichess_connected
                    and self._lichess_stream_ready_generation == getattr(
                        self.lichess, "auth_generation", None)
                    and self._lichess_rate_limit_remaining() == 0)

    def _refresh_online_controls(self) -> None:
        self._update_play_mode_availability()
        result_available = self._online_actions_allowed()
        if hasattr(self, "finished_challenge_button"):
            self.finished_challenge_button.setEnabled(result_available)
        if (not self.lichess or self.offline_mode or not self._lichess_connected):
            return
        remaining = self._lichess_rate_limit_remaining()
        if remaining > 0:
            QTimer.singleShot(remaining * 1000, self._refresh_online_controls)
            return
        for button in (self.friend_link_button, self.username_button, self.ai_button,
                       self.home_ai_button, self.home_friend_button, self.home_username_button,
                       self.active_games_button, self.home_archive_button,
                       self.home_batch_analysis_button, self.home_puzzles_button):
            button.setEnabled(True)
        self._refresh_batch_readiness()

    def _lichess_authenticated(self, username: str) -> None:
        # Signals from a canceled authorization/restore worker may arrive after
        # a user changed auth mode; only accept the service's current identity.
        if not self.lichess or self.lichess.username != username:
            return
        # Existing PGN/history remains persisted, but online capabilities are
        # invalid until this account supplies a fresh active-game snapshot.
        self._detach_lichess_sessions()
        self._invalidate_analysis_preflight()
        self._cancel_dashboard_safety_retry()
        self._dashboard_display_safety_request_id = None
        self._dashboard_display_safety_context = None
        self._dashboard_analysis_safety_allowed = False
        self._analysis_session_generation += 1
        self.dashboard_queries.begin_request()
        self._latest_dashboard_snapshot = None
        self._ready_analytics_failure_status = ""
        self._ready_analytics_failure_session = None
        generation = int(getattr(self.lichess, "auth_generation", 0))
        self._archive_account_ready_generation = None
        self._account_background_started_generation = None
        previous_account_handle = self._archive_account_handle
        if previous_account_handle is not None and previous_account_handle.is_alive():
            previous_account_handle.cancel("account_switched")
        self._home_view.select_profile(f"lichess:{username.strip().lower()}", notify=False)
        self._clear_dashboard_scope("Загружаем данные выбранного аккаунта…")
        self._refresh_dashboard_analysis_visibility()
        self._lichess_account_authenticated = True
        self._lichess_session_selected = True
        self._lichess_profile_username = username
        self._lichess_connected = False
        self._lichess_stream_ready_generation = None
        self._event_stream_recovery_pending = False
        self.lichess_analysis_status.setText("Lichess · аккаунт подтверждён, подключаем поток")
        self.lichess_analysis_status.setToolTip("")
        self.settings.setValue("workspace/selected_account", f"lichess:{username.strip().lower()}")
        self._set_connection_header(
            "Вход", "Аккаунт подтверждён; ожидается готовность игрового потока.")
        self._update_sidebar_profile_label()
        self.sidebar_connection_label.setText("Подключаем поток…")
        self._set_sidebar_safety_status("Ожидание проверки активных партий")
        if self._connection_dialog is not None:
            self._connection_dialog.accept()
        self.connect_button.setVisible(False)
        self.disconnect_button.setVisible(True)
        self.disconnect_button.setEnabled(True)
        self._dashboard_options_loaded = False
        self._refresh_home_dashboard()
        paused = self.settings.value("lichess/ready_analytics_paused", False, type=bool)

        def ensure_account_worker() -> None:
            try:
                account_id = self.game_repository.ensure_archive_account(username)
                self.game_repository.set_lichess_analysis_backfill_paused(account_id, paused)
                pending = self.game_repository.list_pending_final_sync(account_id=account_id)
                result, error = {"account_id": account_id,
                                 "pending_final_syncs": pending}, None
            except Exception as exc:
                result, error = None, type(exc).__name__
            self.archive_account_ready_finished.emit(generation, username, result, error)

        self._archive_account_handle = self._start_registered_work(
            ensure_account_worker, kind="lichess_archive_account", lane=OperationLane.SQLITE_WRITER,
            thread_name="cc-auth-account-sqlite")
        self._refresh_online_controls()
        self._refresh_batch_readiness()
        self._sync_position_eval_controls(self._position_eval_context_available())
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(self.online_session is None)

    def _run_background_sync(self) -> None:
        if (self._shutdown_requested or self.offline_mode or not self._online_actions_allowed()
                or self.lichess is None or not self.lichess.username):
            return
        if any(s.status in {"started", "playing"} and not s.is_ai_opponent
               for s in self._sessions_for_safety()):
            return  # Prioritize live play over background network traffic.
        generation = getattr(self.lichess, "auth_generation", None)
        if generation is not None and self._lichess_stream_ready_generation == generation:
            if self.settings.value("lichess/auto_archive_sync", True, type=bool):
                # sync_archive(full=False) resumes an unfinished initial
                # backfill, then uses its durable incremental cursor.
                self.archive_sync.start_full_sync(self.lichess, background=True)
            if (self.settings.value("lichess/auto_puzzle_history_sync", True, type=bool)
                    and hasattr(self.lichess, "sync_puzzle_activity_background")):
                # This checks only the separate saved puzzle:read credential;
                # it never launches an OAuth flow.
                self.lichess.sync_puzzle_activity_background(self.game_repository)
            self._start_lichess_analysis_backfill(generation)

    def _background_sync_finished(self, kind: str, message: str) -> None:
        if not self._shutdown_requested:
            self.connection.setToolTip(f"Последнее фоновое обновление {kind}: {message}")
            if "Ошибка" in message or "не совпадают" in message:
                self._notify_inline(f"Синхронизация {kind}: {message}")
            elif "новых партий: 0" not in message and "новых попыток: 0" not in message:
                if "новых партий:" in message or "новых попыток:" in message:
                    self._notify_inline(f"Синхронизация {kind}: {message}", timeout_ms=6000)

    def _resume_pending_final_syncs(self) -> None:
        """Resume persisted terminal games without taking over the visible screen."""
        if (not self.lichess or not self._lichess_account_authenticated
                or self._archive_account_ready_generation != self.lichess.auth_generation):
            return
        username = str(self.lichess.username or "").strip()
        if not username:
            return
        pending_rows = getattr(self, "_pending_final_sync_resume_rows", [])
        self._pending_final_sync_resume_rows = []
        for row in pending_rows if isinstance(pending_rows, list) else []:
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
                self.game_sessions.register(session)
            if session.owner_username not in (None, username.casefold()):
                continue
            session.bind_owner(username, self.lichess.auth_generation)
            self.lichess.mark_game_finished(game_id)
            self.lichess.start_game(game_id, expected_generation=self.lichess.auth_generation)

    def _lichess_error(self, message: str) -> None:
        self._awaiting_game_start = False
        self.cancel_challenge_button.setEnabled(False)
        self.cancel_challenge_button.setVisible(False)
        if getattr(self, "_open_challenge_id", None):
            self.cancel_invitation_button.setEnabled(True)
        self.game_status.setText(message)
        if self._connection_dialog is not None and shiboken6.isValid(self._connection_dialog):
            self._connection_dialog.set_status(message)
        elif "HTTP 403" in message or "HTTP 401" in message:
            self._notify_inline(message + " Локальная библиотека и история остаются доступны.")
        else:
            self._notify_inline(message)
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
        if not self._online_actions_allowed() or self._awaiting_game_start:
            return
        dialog = OnlineGameDialog("Партия с другом по ссылке", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText("Создаём вызов и присоединяем вас…")
        self.home_status.setText("Создаём вызов и присоединяем вас…")
        self.lichess.open_friend_game(color, limit, increment, rated)

    def challenge_username(self, *, initial_username: str | None = None) -> None:
        if not self._online_actions_allowed() or self._awaiting_game_start:
            return
        username, ok = QInputDialog.getText(
            self, "Вызов игрока", "Имя пользователя Lichess:",
            text=str(initial_username or ""))
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
        if not self._online_actions_allowed() or self._awaiting_game_start:
            return
        saved_limit = self.settings.value("workspace/ai_clock_limit", 180, type=int)
        dialog = OnlineGameDialog("Игра против Lichess AI", include_level=True, parent=self,
                                  defaults={
                                      "color": self.settings.value("workspace/ai_color", "random", type=str),
                                      "clock_limit": None if saved_limit < 0 else saved_limit,
                                      "clock_increment": self.settings.value("workspace/ai_clock_increment", 2, type=int),
                                      "level": self.settings.value("workspace/ai_level", 3, type=int),
                                  })
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, level, _ = dialog.selection()
        self.settings.setValue("workspace/ai_color", color)
        self.settings.setValue("workspace/ai_clock_limit", -1 if limit is None else int(limit))
        self.settings.setValue("workspace/ai_clock_increment", int(increment))
        self.settings.setValue("workspace/ai_level", int(level))
        self._awaiting_game_start = True
        self.game_status.setText("Создаём партию против Lichess AI…")
        self.home_status.setText("Создаём партию против Lichess AI…")
        self.lichess.create_ai_game(level, color, limit, increment)

    def _challenge_ready(self, result: dict) -> None:
        url = result.get("friendUrl")
        if not url:
            self.game_status.setText("Вызов отправлен; ждём ответ Lichess…")
            self._play_mode_page.set_status("Вызов отправлен; ждём ответа соперника и сервера.")
            return
        self._play_mode_page.set_status("Приглашение создано. Скопируйте ссылку и отправьте другу.")
        self._play_mode_page.set_request_pending(False)
        self._play_mode_page.show_invitation(str(url))
        self._open_challenge_id = result.get("id")
        if self.lichess:
            self._open_challenge_generation = self.lichess.auth_generation
        active_game = self.online_session and self.online_session.status in {"started", "playing"}
        self.cancel_invitation_button.setEnabled(bool(self._open_challenge_id) and not active_game)
        self.cancel_invitation_button.setVisible(bool(self._open_challenge_id) and not active_game)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(False)
        self.game_status.setText("Приглашение готово. Ссылка находится на странице «Играть».")
        self._notify_inline("Ссылка приглашения готова: скопируйте её на странице «Играть».")

    def _challenge_status(self, message: str, can_cancel: bool) -> None:
        if not can_cancel and any(word in message.casefold() for word in ("отклонил", "отменён", "отменено", "завершено")):
            self._awaiting_game_start = False
        self.game_status.setText(message)
        if hasattr(self, "_play_mode_page"):
            self._play_mode_page.set_status(message)
            self._play_mode_page.set_request_pending(can_cancel, cancellable=can_cancel)
        self.cancel_challenge_button.setEnabled(can_cancel)
        self.cancel_challenge_button.setVisible(can_cancel)

    def cancel_friend_invitation(self) -> None:
        if not getattr(self, "_open_challenge_id", None):
            return
        if QMessageBox.question(self, "Отменить приглашение", "Отменить приглашение по ссылке?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.lichess.cancel_open_invitation(
            self._open_challenge_id, expected_generation=self._open_challenge_generation)
        self.cancel_invitation_button.setEnabled(False)
        self._play_mode_page.cancel_invitation_button.setEnabled(False)
        self.game_status.setText("Отменяем приглашение…")

    def _invitation_cancelled(self, challenge_id: str) -> None:
        if challenge_id == getattr(self, "_open_challenge_id", None):
            self._awaiting_game_start = False
            self._open_challenge_id = None
            self._open_challenge_generation = None
            self._play_mode_page.clear_invitation()
            self.cancel_invitation_button.setVisible(False)
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(self._lichess_connected and self.online_session is None)
            self.game_status.setText("Приглашение отменено.")

    def _lichess_event(self, event: dict, *, generation: int | None = None) -> None:
        if generation is not None and not self._current_lichess_generation(generation):
            return
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
            if generation is not None:
                try:
                    session.bind_owner(self.lichess.username or "", generation)
                except ValueError:
                    session.detach()
                    session = LichessGameSession(game_id, self.game_repository)
                    session.bind_owner(self.lichess.username or "", generation)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            session.status = "started"
            session.set_server_game_identity(game)
            self.game_sessions.register(session)
            if not session.is_ai_opponent:
                self._cancel_dashboard_safety_retry()
                self._set_sidebar_safety_status("Аналитика скрыта · активная партия")
                self._cancel_lichess_backfill_wakeup()
                self._backfill_deferred_for_human_game = True
                self._dashboard_analysis_safety_allowed = False
                self._refresh_dashboard_analysis_visibility()
                self._revoke_review_safety(
                    "Заблокировано: началась человеческая/непроверенная онлайн-партия.")
            self._refresh_batch_readiness()
            if (not session.is_ai_opponent
                    and getattr(self, "_analysis_stop", None) is not None):
                self._analysis_stop.set()
            if not session.is_ai_opponent:
                self._disable_position_evaluation(
                    "Stockfish остановлен: получено событие о человеческой онлайн-партии.")
                self._sync_position_eval_controls(self._position_eval_context_available())
            if not session.is_ai_opponent and self._batch_pause is not None:
                self._pause_batch_analysis()
            if self._awaiting_game_start and previous is None:
                self._awaiting_game_start = False
                self.sound_player.play_game_start(game_id)
                self.board.clear_annotations()
                self._selected_live_game_id = game_id
                self.online_session = session
                self._review_session = None
                self.online_view_ply = None
                self._screen_mode = "online"
                self.screen_title.setText("Активная партия")
                self.screen_stack.setCurrentWidget(self.game_page)
                self.refresh(f"Партия началась против {session.opponent}.")
            self._update_library_active_games()
        elif kind == "gameFinish":
            game_id = event.get("game", {}).get("gameId")
            if game_id in self.online_sessions:
                game = event.get("game", {})
                game_status = game.get("status", {})
                status = game_status.get("name", "unknownFinish") if isinstance(game_status, dict) else str(game_status)
                session = self.online_sessions[game_id]
                was_active = session.status in {"started", "playing", "created"}
                error = None
                if session.status in {"started", "playing", "created"}:
                    try:
                        session.finish(status, game.get("winner"))
                    except Exception as exc:
                        error = str(exc)
                        session.status = status
                        session.winner = game.get("winner")
                if session.final_sync_pending:
                    self._track_game_finalization(session)
                    self.lichess.mark_game_finished(game_id)
                    self.lichess.start_game(game_id)
                self._update_library_active_games()
                if was_active and game_id == self._selected_live_game_id:
                    self.sound_player.play_game_end(game_id, len(session.board.move_stack))
                self._refresh_batch_readiness()
                if (self._backfill_deferred_for_human_game
                        and not any(item.status in {"started", "playing"}
                                    and not item.is_ai_opponent
                                    for item in self._sessions_for_safety())
                        and self.lichess and self._lichess_connected
                        and self._lichess_stream_ready_generation == self.lichess.auth_generation):
                    self._backfill_deferred_for_human_game = False
                    self._schedule_lichess_backfill_wakeup(
                        self.lichess.auth_generation, str(self.lichess.username or ""),
                        datetime.now(timezone.utc).isoformat(), debounce_if_due=False)
                if (not any(item.status in {"started", "playing"}
                            and not item.is_ai_opponent
                            for item in self._sessions_for_safety())
                        and self.lichess and self._lichess_connected):
                    QTimer.singleShot(3000, self._run_background_sync)
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
            challenge_id = str(event.get("challenge", {}).get("id") or uuid.uuid4().hex)
            self.sound_player.play_notification(challenge_id)
            if self._screen_mode == "home":
                self.home_status.setText("Получено или обновлено приглашение Lichess.")

    def _lichess_game_event(self, game_id: str, event: dict,
                            *, generation: int | None = None) -> None:
        if generation is not None and not self._current_lichess_generation(generation):
            return
        session = self.online_sessions.get(game_id)
        if session is None:
            session = LichessGameSession(game_id, self.game_repository)
            if generation is not None and self.lichess:
                session.bind_owner(self.lichess.username or "", generation)
            self.game_sessions.register(session)
        elif generation is not None and self.lichess and not session.belongs_to(
                self.lichess.username, generation=generation):
            # Only an authenticated current-generation stream can rebind a
            # detached same-account cache; different accounts get a new session.
            if session.owner_username in (None, str(self.lichess.username or "").casefold()):
                session.bind_owner(self.lichess.username or "", generation)
            else:
                session.detach()
                session = LichessGameSession(game_id, self.game_repository)
                session.bind_owner(self.lichess.username or "", generation)
                self.game_sessions.register(session)
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
        previous_board = session.board.copy(stack=True)
        previous_status = session.status
        previous_view_ply = self.online_view_ply if (
            game_id == self._selected_live_game_id and self.online_session is session
        ) else None
        try:
            session.apply_event(event)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError) as exc:
            self._lichess_error(f"Не удалось синхронизировать позицию Lichess: {exc}")
            return
        except Exception as exc:
            if session.status not in {"started", "playing", "created"}:
                # The terminal event mutates the authoritative in-memory state
                # before a synchronous SQLite failure can escape from _save().
                self._track_game_finalization(session)
            if (game_id == self._selected_live_game_id and self.online_session is session
                    and self._screen_mode == "online"):
                if session.status not in {"started", "playing", "created"}:
                    self._show_result(session, False, str(exc))
                else:
                    self.refresh(f"Позиция обновлена с Lichess, но локальная запись не удалась: {exc}")
            return
        if (session.status in {"started", "playing"} and not session.is_ai_opponent):
            self._dashboard_analysis_safety_allowed = False
            self._refresh_dashboard_analysis_visibility()
            self._revoke_review_safety(
                "Заблокировано: активная партия с человеком или неизвестным соперником.")
        if session.status not in {"started", "playing", "created"}:
            # Observe durability after every authoritative terminal snapshot,
            # even when this game is not the currently visible route.
            self._track_game_finalization(session)
        self._update_library_active_games()
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
            if (len(current_moves) == len(previous_moves) + 1
                    and current_moves[:-1] == previous_moves):
                confirmed = chess.Move.from_uci(current_moves[-1])
                self.sound_player.play_move(
                    game_id, len(current_moves), previous_board, confirmed,
                    game_finished=session.status not in {"started", "playing", "created"})
            elif (session.status not in {"started", "playing", "created"}
                  and previous_status in {"started", "playing", "created"}):
                self.sound_player.play_game_end(game_id, len(current_moves))
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
        # Archive and puzzle history are explicit user actions; a game finish
        # must never schedule those potentially large downloads.

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
        finished_view = session.status not in {"created", "started", "playing"} and not active
        self.finished_game_card.setVisible(False)
        self.finished_review_button.setVisible(finished_view)
        self.finished_challenge_button.setVisible(finished_view)
        # While a move POST is pending, the optimistic rendering is already one
        # ply ahead. Allow only the dedicated premove gesture on that position;
        # begin_submission still rejects a second real POST until server ACK.
        input_available = (not session.input_locked
                           or session.can_queue_speculative_premove)
        self.board.set_interaction_enabled(live and active and input_available)
        self._apply_preferences()
        self.board.set_premove(session.premove, session.color)
        self.opponent_label.setText(f"Соперник  ·  {session.opponent}")
        self.you_label.setText("Вы  ·  " + ("белыми" if session.color == chess.WHITE else "чёрными"))
        turn = ("Синхронизация позиции…" if session.awaiting_authoritative_snapshot
                else "Ваш ход" if session.is_my_turn else "Ход соперника")
        self.turn_label.setText(turn + (" · история" if not live else ""))
        if finished_view:
            outcome = _result_label(session.color, session.winner, session.status)
            save_status = getattr(self, "_finish_save_status", "")
            self.game_status.setText(
                f"Партия завершена · {outcome}"
                + (f" · {save_status}" if save_status else ""))
        elif message:
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
        self.leave_game_button.setVisible(
            session.status not in {"started", "playing", "created"} and not finished_view)
        self.leave_game_button.setText("Главная")
        pending_action = any(
            game_id == session.game_id
            for game_id, _command in self._pending_game_actions)
        if pending_action:
            for button in (self.resign_button, self.draw_button, self.decline_draw_button,
                           self.claim_draw_button):
                button.setEnabled(False)
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
        """Append SAN cells for confirmed plies; show white/black in one fullmove row."""
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
                rows = []
                for index in range(start_ply, len(moves)):
                    move = moves[index]
                    ply = index + 1
                    rows.append({"ply": ply, "san": position.san(move),
                                 "mover": "white" if position.turn == chess.WHITE else "black",
                                 "before_fen": position.fen()})
                    position.push(move)
                for row in rows:
                    self.moves.append_move(row, enabled=self.online_session is None or
                                           not self._is_live_online_session())
            desired_ply = min(selected_ply, len(moves))
            self.moves.select_ply(desired_ply)
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
        if not session:
            self.clock_label.setText("Без часов")
            self.opponent_clock_label.setText("Без часов")
            self.you_clock_label.setText("Без часов")
            self.opponent_clock_detail.clear()
            self.you_clock_detail.clear()
            self.opponent_clock_label.setStyleSheet("font-size: 28px; font-weight: 700; padding: 2px 8px;")
            self.you_clock_label.setStyleSheet("font-size: 28px; font-weight: 700; padding: 2px 8px;")
            return
        if session.clock_state_valid is False:
            message = "—:— · часы синхронизируются"
            self.opponent_clock_label.setText(message)
            self.you_clock_label.setText(message)
            self.clock_label.setText("Ожидаем корректные часы сервера")
            self.opponent_clock_detail.clear()
            self.you_clock_detail.clear()
            self.opponent_clock_label.setStyleSheet(
                f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
            self.you_clock_label.setStyleSheet(
                f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
            return
        if session.white_ms is None or session.black_ms is None:
            waiting = self._screen_mode == "online" and session.clock_control != "-"
            fallback = "—:— · ожидаем часы Lichess" if waiting else "Без часов"
            self.opponent_clock_label.setText(fallback)
            self.you_clock_label.setText(fallback)
            owner_is_white = session.color == chess.WHITE
            opponent_increment = session.black_inc if owner_is_white else session.white_inc
            owner_increment = session.white_inc if owner_is_white else session.black_inc
            self.opponent_clock_detail.setText(
                _clock_increment_label(opponent_increment))
            self.you_clock_detail.setText(
                _clock_increment_label(owner_increment))
            self.clock_label.setText(session.clock_control if waiting else "Без часов")
            self.opponent_clock_label.setStyleSheet(
                f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
            self.you_clock_label.setStyleSheet(
                f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
            return
        history_view = self._screen_mode != "online" or self.online_view_ply is not None
        terminal_statuses = {"mate", "resign", "draw", "stalemate", "repetition", "50moves",
                             "insufficientMaterial", "timeout", "outoftime", "cheat",
                             "noStart", "variantEnd", "finished", "aborted"}
        status_unknown = session.status not in {"started", "playing", *terminal_statuses}
        paused = bool(session.awaiting_authoritative_snapshot or history_view
                      or session.status not in {"started", "playing"})
        syncing = bool(session.awaiting_authoritative_snapshot or
                       history_view or status_unknown or
                       (self._screen_mode == "online" and self._event_stream_recovery_pending))
        elapsed = (max(0, int((time.monotonic() - session._last_clock_at) * 1000))
                   if not paused and not syncing else 0)
        white_text, white_warning = format_remaining_clock(
            session.white_ms, elapsed_ms=elapsed,
            active=(session.board.turn == chess.WHITE and not paused and not syncing), syncing=syncing)
        black_text, black_warning = format_remaining_clock(
            session.black_ms, elapsed_ms=elapsed,
            active=(session.board.turn == chess.BLACK and not paused and not syncing), syncing=syncing)
        owner_is_white = session.color == chess.WHITE
        own_text, own_warning = (white_text, white_warning) if owner_is_white else (black_text, black_warning)
        opponent_text, opponent_warning = (black_text, black_warning) if owner_is_white else (white_text, white_warning)
        opponent_increment = session.black_inc if owner_is_white else session.white_inc
        owner_increment = session.white_inc if owner_is_white else session.black_inc
        self.opponent_clock_detail.setText(
            _clock_increment_label(opponent_increment))
        self.you_clock_detail.setText(
            _clock_increment_label(owner_increment))
        owner_active = session.board.turn == session.color and not paused and not syncing
        opponent_active = session.board.turn != session.color and not paused and not syncing
        owner_marker = (("  ⚠ МЕНЬШЕ 30 СЕК" if own_warning else "")
                        + ("  ◀ ХОД" if owner_active else ""))
        opponent_marker = (("  ⚠ МЕНЬШЕ 30 СЕК" if opponent_warning else "")
                           + ("  ◀ ХОД" if opponent_active else ""))
        self.you_clock_label.setText(own_text + owner_marker)
        self.opponent_clock_label.setText(opponent_text + opponent_marker)
        self.you_clock_label.setStyleSheet(
            f"font-size: 28px; font-weight: 800; padding: 2px 8px; color: #F3C969;" if own_warning else
            f"font-size: 28px; font-weight: 800; padding: 2px 8px; color: #FFFFFF;" if owner_active else
            f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
        self.opponent_clock_label.setStyleSheet(
            f"font-size: 28px; font-weight: 800; padding: 2px 8px; color: #F3C969;" if opponent_warning else
            f"font-size: 28px; font-weight: 800; padding: 2px 8px; color: #FFFFFF;" if opponent_active else
            f"font-size: 28px; font-weight: 600; padding: 2px 8px; color: {TOKENS.secondary};")
        self.clock_label.setText(f"Контроль {format_clock_control(session.clock_control)} · часы Lichess")

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
        self.screen_title.setText("Мои партии")
        existing = getattr(self, "library_page", None)
        if existing is not None:
            self.screen_stack.setCurrentWidget(existing)
            self._update_library_active_games()
            self._library_reload(existing.current_filters(), existing._page)
            return
        page = LibraryPage(self)
        saved_raw = str(self.settings.value("workspace/library_filters", "", type=str))
        try:
            saved_filters = json.loads(saved_raw) if saved_raw else {}
        except (TypeError, json.JSONDecodeError):
            saved_filters = {}
        if isinstance(saved_filters, dict):
            page._pending_filter_values = dict(saved_filters)
            page.search.setText(str(saved_filters.get("query") or ""))
            page.opponent_filter.setText(str(saved_filters.get("opponent") or ""))
            page.opening_filter.setText(str(saved_filters.get("opening") or ""))
            for name, widget in (("account_id", page.account_filter), ("color", page.side_filter),
                                 ("outcome", page.result_filter), ("source", page.source_filter),
                                 ("clock", page.clock_filter), ("analysis", page.analysis_filter),
                                 ("analysis_source", page.analysis_source_filter),
                                 ("period_days", page.period_filter)):
                index = widget.findData(saved_filters.get(name) or None if name == "account_id"
                                        else saved_filters.get(name) or "")
                if index >= 0:
                    widget.setCurrentIndex(index)
        page.refresh_game.setEnabled(bool(self._lichess_connected and not self.offline_mode))
        page.active_requested.connect(self._resume_library_game)
        page.detail_requested.connect(self._load_library_game_detail)
        page.detail_position_requested.connect(self._open_library_position)
        page.sync_requested.connect(self.start_archive_sync)
        page.background_requested.connect(self.show_background_analytics)
        page.settings_requested.connect(self.open_settings)
        username = str(getattr(self.lichess, "username", "") or "").strip()
        page.set_sync_status(
            (f"Lichess · {username} · "
             f"{'подключено' if self._lichess_connected else 'не подключено'} · "
             "автоматическая готовая аналитика и ручная синхронизация используют общую очередь."
             if username else
             "Локальная библиотека доступна без подключения; синхронизация Lichess появится после авторизации."))

        def selected_game():
            return page.selected_game()

        def load_page(query: dict | str | None = None, page_number: int = 0):
            filters = query if isinstance(query, dict) else {"query": query or ""}
            self.settings.setValue("workspace/library_filters",
                                   json.dumps(filters, ensure_ascii=False))
            self.settings.setValue("workspace/library_page", max(0, int(page_number)))
            safe_filters = dict(filters)
            safe_filters.setdefault("query", page.search.text())
            previous = self._library_query_handle
            if previous is not None and previous.is_alive():
                previous.cancel("session_replaced")
            self._library_query_generation += 1
            generation = self._library_query_generation
            target_page = max(0, int(page_number))
            page.page_label.setText("Загружаю страницу…")
            stop_event = threading.Event()

            def worker() -> None:
                try:
                    if stop_event.is_set():
                        return
                    result = self.library_queries.search(safe_filters, target_page)
                    if not self._library_filter_options_loaded:
                        result["_selector_options"] = dashboard_options(self.game_repository)
                    if stop_event.is_set():
                        return
                    self.library_query_finished.emit(generation, result, None)
                except Exception as exc:
                    if not stop_event.is_set():
                        self.library_query_finished.emit(
                            generation, None,
                            f"Не удалось загрузить партии ({type(exc).__name__}).")

            self._library_query_handle = self._start_registered_work(
                worker, kind="library_query", lane=OperationLane.INTERACTIVE,
                thread_name="cc-library-query", stop_event=stop_event)

        def open_selected(game=None):
            game = game or selected_game()
            if game:
                if self._open_saved_game(game):
                    return
                else:
                    QMessageBox.information(self, "Партия сохранена как PGN",
                        self._raw_only_game_message(game))

        def export_selected(game=None):
            game = game or selected_game()
            if not game:
                return
            pgn = self.game_repository.export_pgn(game["game_id"])
            path, _ = QFileDialog.getSaveFileName(
                self, "Экспорт PGN", f"{game['game_id']}.pgn", "PGN (*.pgn)")
            if path and pgn is not None:
                Path(path).write_text(pgn.rstrip() + "\n", encoding="utf-8")

        def save_selected_note(game=None, text=""):
            game = game or selected_game()
            if not game:
                return
            game_id = str(game["game_id"])
            text = str(text).strip()
            if not text:
                return
            request_id = uuid.uuid4().hex
            draft_game_id = str(page._note_draft_game_id or "")
            if draft_game_id != game_id:
                return
            page.save_note.setEnabled(False)
            page.save_note.setText("Сохраняю…")
            page.save_note.setToolTip("Запись заметки выполняется в фоновом потоке SQLite.")
            stop_event = threading.Event()

            def worker() -> None:
                error = None
                try:
                    if stop_event.is_set():
                        return
                    current = self.game_repository.current_line(game_id)
                    self.game_repository.save_note(
                        game_id, text,
                        line_revision=current["line_revision"] if current else None,
                        ply=0 if current else None)
                except Exception as exc:
                    error = ("Не удалось сохранить заметку "
                             f"({type(exc).__name__}); текст черновика сохранён в поле.")
                if not stop_event.is_set():
                    self.note_save_finished.emit(request_id, game_id, error)

            self._note_save_requests[request_id] = (text, game_id, page)
            handle = self._start_registered_work(
                worker, kind="save_game_note", lane=OperationLane.SQLITE_WRITER,
                thread_name="cc-save-game-note", stop_event=stop_event)
            page.destroyed.connect(lambda *_: handle.cancel("shutdown"))

        def analyze_selected(game=None):
            game = game or selected_game()
            if not game:
                return
            if not self._is_standard_board_game(game):
                QMessageBox.information(self, "Stockfish",
                    "Для этой записи доска не поддерживается. Оригинальный PGN можно экспортировать.")
                return
            if game["status"] in {"created", "started", "started_soon", "playing"}:
                QMessageBox.information(self, "Stockfish",
                                        "Движок доступен только после завершения партии.")
                return
            engine_path = self.settings.value("stockfish_path", "", type=str).strip()
            if not engine_path:
                QMessageBox.information(
                    self, "Stockfish не настроен",
                    "Сначала укажите локальный executable и проверьте его в Настройках. "
                    "Приложение не скачивает движок автоматически.")
                return
            self._start_game_analysis(game["game_id"], engine_path)

        def refresh_selected(game=None):
            game = game or selected_game()
            if not game or not self.lichess or not self._lichess_connected:
                return
            if game.get("source") != "lichess_archive":
                QMessageBox.information(self, "Обновить партию",
                    "Обновление доступно для партий, импортированных из текущего аккаунта Lichess.")
                return
            self.archive_sync.refresh_game(self.lichess, game["game_id"])

        page.query_requested.connect(load_page)
        page.open_requested.connect(open_selected)
        page.export_requested.connect(export_selected)
        page.note_requested.connect(save_selected_note)
        page.analyze_requested.connect(analyze_selected)
        page.refresh_requested.connect(refresh_selected)
        load_page(page.current_filters(), int(self.settings.value(
            "workspace/library_page", 0, type=int)))
        page.close_button.setText("Главная")
        page.close_button.clicked.connect(self.show_home)
        self.library_page = page
        self._library_reload = load_page
        self._update_library_active_games()
        self.screen_stack.addWidget(page)
        self.screen_stack.setCurrentWidget(page)

    def _library_query_result_ready(self, generation: int, result: object,
                                    error: object) -> None:
        if generation != self._library_query_generation:
            return
        page = getattr(self, "library_page", None)
        if page is None:
            return
        if error:
            page.page_label.setText(str(error))
            return
        if not isinstance(result, dict):
            page.page_label.setText("Не удалось прочитать список партий.")
            return
        selector_options = result.get("_selector_options")
        if isinstance(selector_options, dict):
            page.set_filter_options(selector_options)
            self._library_filter_options_loaded = True
        rows = [(game, *_archive_row_presentation(game)) for game in result["items"]]
        page.set_results(rows, page=int(result.get("page", 0)),
                         pages=int(result.get("pages", 1)),
                         total=int(result.get("total", 0)))
        target = self._home_detail_game_id
        if target:
            found = False
            for index in range(page.games.count()):
                item = page.games.item(index)
                game = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(game, dict) and str(game.get("game_id")) == target:
                    page.games.setCurrentItem(item)
                    page.games.scrollToItem(item)
                    found = True
                    break
            if found:
                self._home_detail_game_id = None
                self._home_detail_payload = None
            elif not self._home_detail_lookup_pending:
                self._home_detail_lookup_pending = True
                stop_event = threading.Event()
                target_generation = generation
                def lookup_target() -> None:
                    try:
                        record = self.game_repository.get_game(target) if not stop_event.is_set() else None
                        self.library_target_finished.emit(
                            target_generation, target, record, None)
                    except Exception as exc:
                        self.library_target_finished.emit(
                            target_generation, target, None,
                            f"Не удалось открыть выбранную партию ({type(exc).__name__}).")
                self._start_registered_work(
                    lookup_target, kind="library_target_lookup",
                    lane=OperationLane.INTERACTIVE, thread_name="cc-library-target")

    def _library_target_result_ready(self, generation: int, target: str,
                                     game: object, error: object) -> None:
        if (generation != self._library_query_generation
                or target != self._home_detail_game_id):
            return
        self._home_detail_lookup_pending = False
        page = getattr(self, "library_page", None)
        if page is None:
            return
        if error or not isinstance(game, dict):
            self._home_detail_game_id = None
            self._home_detail_payload = None
            page.detail_status.setText(
                str(error or "Выбранная партия уже недоступна в локальной библиотеке."))
            page.detail_metrics.clear()
            page.detail_graph.setVisible(False)
            page.detail_graph.set_safety_gate(False, "Партия недоступна")
            return
        record = dict(self._home_detail_payload or {})
        record.update(game)
        payload = self._home_detail_payload or {}
        for key in ("account_color", "account_opponent"):
            if key in payload:
                record[key] = payload[key]
        self._home_detail_game_id = None
        self._home_detail_payload = None
        self._library_detail_target_id = target
        page.show_game_detail(record, safety_allowed=self._library_detail_safety_allowed())
        if not self._library_detail_safety_allowed():
            self._library_detail_target_id = None
            return
        self._load_library_game_detail(record, allow_not_selected=True)

    def _library_detail_safety_allowed(self) -> bool:
        active_human = any(
            session.status in {"started", "playing"} and not session.is_ai_opponent
            for session in self._sessions_for_safety())
        return bool(self.offline_mode or
                    (self._dashboard_analysis_safety_allowed and not active_human))

    def _load_library_game_detail(self, game: dict, *, allow_not_selected: bool = False) -> None:
        page = getattr(self, "library_page", None)
        if page is None or not isinstance(game, dict):
            return
        pending_target = self._home_detail_game_id
        if pending_target and str(game.get("game_id")) != pending_target:
            return
        page.show_game_detail(game, safety_allowed=self._library_detail_safety_allowed())
        if not self._library_detail_safety_allowed():
            handle = self._library_detail_handle
            self._library_detail_generation += 1
            if handle is not None and handle.is_alive():
                handle.cancel("safety_revoked")
            return
        previous = self._library_detail_handle
        if previous is not None and previous.is_alive():
            previous.cancel("session_replaced")
        self._library_detail_generation += 1
        generation = self._library_detail_generation
        game_id = str(game.get("game_id") or "")
        if not game_id:
            return
        self._library_detail_target_id = game_id if allow_not_selected else None
        stop_event = threading.Event()

        def worker() -> None:
            try:
                if stop_event.is_set():
                    return
                metrics_materialization = self.game_repository.materialize_stale_analysis_for_game(
                    game_id)
                review = load_game_review(self.game_repository, game)
                review["metrics_materialization"] = metrics_materialization
                if not stop_event.is_set():
                    self.library_detail_finished.emit(generation, game_id, review, None)
            except Exception as exc:
                if not stop_event.is_set():
                    self.library_detail_finished.emit(
                        generation, game_id, None,
                        f"Не удалось загрузить детали ({type(exc).__name__}).")

        self._library_detail_handle = self._start_registered_work(
            worker, kind="library_detail", lane=OperationLane.INTERACTIVE,
            thread_name="cc-library-detail", stop_event=stop_event)

    def _library_detail_result_ready(self, generation: int, game_id: str,
                                     review: object, error: object) -> None:
        page = getattr(self, "library_page", None)
        target_match = self._library_detail_target_id == str(game_id)
        if (page is None or generation != self._library_detail_generation
                or (not target_match and (page.selected_game() is None
                    or str(page.selected_game().get("game_id")) != str(game_id)))):
            return
        if not self._library_detail_safety_allowed():
            page.show_game_detail(page.selected_game(), safety_allowed=False)
            return
        if error or not isinstance(review, dict):
            page.detail_status.setText(str(error or "Данные партии недоступны."))
            page.detail_metrics.clear()
            page.detail_graph.setVisible(False)
            return
        selected = page.selected_game()
        detail_game = (dict(page._detail_game or {}) if target_match else selected)
        if detail_game is None or str(detail_game.get("game_id")) != str(game_id):
            return
        page.show_game_detail(detail_game, review, safety_allowed=True)
        materialization = review.get("metrics_materialization") or {}
        if int(materialization.get("total") or 0) > 0:
            page.detail_status.setText(
                "Метрики сохранённых анализов: пересчитано "
                f"{int(materialization.get('processed') or 0)}/"
                f"{int(materialization.get('total') or 0)}; "
                f"осталось {int(materialization.get('remaining') or 0)}. Движок не запускался.")
        if target_match:
            self._library_detail_target_id = None

    def _note_save_result_ready(self, request_id: str, game_id: str, error: object) -> None:
        request = self._note_save_requests.pop(str(request_id), None)
        if request is None:
            return
        draft_text, draft_game_id, page = request
        if not shiboken6.isValid(page):
            return
        if str(error or ""):
            page.save_note.setEnabled(True)
            page.save_note.setText("Повторить сохранение")
            page.save_note.setToolTip(str(error))
            self._notify_inline("Не удалось сохранить заметку. Черновик оставлен на месте.")
            return
        # A completed write belongs to the captured game. Clear only the same
        # unchanged draft; selection changes and edits made while writing survive.
        selected = page.selected_game() or {}
        if (page._note_draft_game_id == draft_game_id
                and page.note.text() == draft_text
                and str(selected.get("game_id") or "") == draft_game_id):
            page.note.clear()
        page.save_note.setText("Сохранить заметку")
        selected = page.selected_game() or {}
        page.save_note.setEnabled(
            bool(page.note.text().strip())
            and page._note_draft_game_id == str(selected.get("game_id") or ""))
        page.save_note.setToolTip("Заметка сохранена к исходной партии.")
        self._notify_inline("Заметка сохранена к исходной партии.")

    def _open_library_position(self, game: dict, ply: int) -> None:
        if self._open_saved_game(game):
            self.review_workspace.select_ply(
                max(0, min(len(self.review_workspace.review["moves"]), int(ply))))

    def _update_library_active_games(self) -> None:
        if hasattr(self, "return_to_game_button"):
            self._update_return_to_game_button()
        page = getattr(self, "library_page", None)
        if page is None:
            return
        rows = []
        for session in self._current_account_sessions():
            if session.status not in {"created", "started", "playing"}:
                continue
            if session.awaiting_authoritative_snapshot:
                turn = "синхронизация позиции"
            else:
                turn = "ваш ход" if session.is_my_turn else "ход соперника"
            rows.append({
                "game_id": session.game_id,
                "opponent": session.opponent,
                "clock": session.clock_control,
                "color": "белые" if session.color == chess.WHITE else "чёрные",
                "turn": turn,
            })
        known = {row["game_id"] for row in rows}
        for game in getattr(self, "_remote_active_games", ()):
            game_id = str(game.get("gameId") or "")
            if not game_id or game_id in known:
                continue
            opponent = game.get("opponent") or {}
            rows.append({
                "game_id": game_id,
                "opponent": opponent.get("username") or opponent.get("id") or "Соперник",
                "clock": game.get("speed") or "без контроля",
                "color": {"white": "белые", "black": "чёрные"}.get(
                    game.get("color"), "сторона неизвестна"),
                "turn": ("ваш ход" if game.get("isMyTurn") is True else
                         "ход соперника" if game.get("isMyTurn") is False else
                         "чей ход неизвестен"),
                "server_payload": game,
            })
        page.set_active_games(rows)

    def _resume_library_game(self, game_id: str | dict) -> None:
        if isinstance(game_id, dict):
            self.open_active_game(game_id)
            return
        session = self.online_sessions.get(str(game_id))
        if (session is None or session.status not in {"created", "started", "playing"}
                or session not in self._current_account_sessions()):
            return
        self._selected_live_game_id = session.game_id
        self.online_session = session
        self.resume_current_game()


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
        isolated_offline = bool(self.offline_mode and self.lichess is None
                                and not self._lichess_connected)
        if isolated_offline:
            request_id = uuid.uuid4().hex
            self._analysis_safety_request_id = request_id
            self._analysis_safety_phase = "running"
            self._analysis_safety_context = (
                "offline", request_id, game_id, engine_path,
                self._analysis_session_generation, None, None)
            self.home_status.setText("Изолированный офлайн-анализ завершённой партии.")
            self._launch_game_analysis(game_id, engine_path, options,
                                       request_id=request_id)
            return
        if (not self.lichess or not self._lichess_connected
                or self._lichess_rate_limit_remaining() > 0):
            self.home_status.setText(
                "Stockfish заблокирован: подключите Lichess и дождитесь новой проверки "
                "активных партий. Подтверждение пользователя не заменяет проверку безопасности.")
            QMessageBox.information(
                self, "Проверка перед анализом",
                "В обычном режиме Stockfish запускается только после успешной проверки "
                "активных партий Lichess. Подключитесь и повторите анализ.")
            return
        if not self.lichess:
            # Defensive branch: offline/manual mode must never become a user-confirmed bypass.
            self.home_status.setText("Stockfish заблокирован: состояние Lichess неизвестно.")
            return
        if not self.lichess.username:
            self.home_status.setText("Не удалось определить подключённый аккаунт; анализ не запущен.")
            return
        if not self._online_actions_allowed():
            self.home_status.setText("Stockfish заблокирован: подключение или Retry-After не готовы.")
            return
        request_id = uuid.uuid4().hex
        self._dashboard_analysis_safety_allowed = False
        self._refresh_dashboard_analysis_visibility()
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

    def _request_saved_game_analysis(self, game_id: str) -> None:
        """Route the review CTA through the existing post-game safety preflight."""
        review = self.review_workspace.review
        if (not review or str(review["game"].get("game_id")) != str(game_id)
                or review["game"].get("status") in {"created", "started", "started_soon", "playing"}):
            return
        game = self.game_repository.get_game(str(game_id))
        if not game or not self._is_standard_board_game(game):
            self._notify_inline("Эту запись нельзя анализировать как стандартную завершённую партию.")
            return
        engine_path = str(self.settings.value("stockfish_path", "", type=str)).strip()
        verified_path = str(self.settings.value("stockfish_verified_path", "", type=str)).strip()
        if not engine_path or engine_path != verified_path or not Path(engine_path).is_file():
            QMessageBox.information(
                self, "Stockfish не настроен",
                "Укажите локальный Stockfish в настройках и проверьте его перед анализом.")
            return
        self._start_game_analysis(str(game_id), engine_path)

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
        if (not self._online_actions_allowed() or self.lichess is not service
                or generation != self._analysis_session_generation
                or not account_id or current_account != account_id):
            self._cancel_analysis_action(request_id)
            return False
        return True

    @staticmethod
    def _active_game_blocks_engine(game: dict) -> bool:
        return classify_lichess_opponent(game) != "official_ai"

    def _analysis_safety_games_received(self, request_id: str, games: list[dict]) -> None:
        if self._dashboard_display_safety_reply(request_id, games):
            return
        if self._review_safety_games_received(request_id, games):
            return
        if request_id == self._position_eval_preflight_id:
            self._position_eval_preflight_received(request_id, games)
            return
        if not self._analysis_safety_reply_is_current(request_id):
            return
        if not isinstance(games, list) or len(games) >= 50:
            self._dashboard_analysis_safety_allowed = False
            self._refresh_dashboard_analysis_visibility()
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
        active_human_ids = {session.game_id for session in self._sessions_for_safety()
                            if session.status in {"started", "playing"} and not session.is_ai_opponent}
        if any(self._active_game_blocks_engine(game) for game in games) or active_human_ids:
            self._dashboard_analysis_safety_allowed = False
            self._refresh_dashboard_analysis_visibility()
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
        self._dashboard_analysis_safety_allowed = True
        self._schedule_legacy_metrics_batch()
        self._refresh_dashboard_analysis_visibility()
        _request, game_id, engine_path, options, _generation, _account_id, _service = context
        self._analysis_safety_phase = "running"
        if self._analysis_dialog is not None:
            self._analysis_dialog.setLabelText("Анализ партии Stockfish…")
        self._launch_game_analysis(game_id, engine_path, options,
                                   stop=stop, dialog=self._analysis_dialog,
                                   request_id=request_id)

    def _analysis_safety_error(self, request_id: str, message: str) -> None:
        if self._dashboard_display_safety_error(
                request_id, self._safe_safety_error_kind(message)):
            return
        if self._review_safety_error_received(request_id, message):
            return
        if request_id == self._position_eval_preflight_id:
            self._position_eval_preflight_error(request_id, message)
            return
        if not self._analysis_safety_reply_is_current(request_id):
            return
        self._analysis_safety_request_id = None
        self._analysis_safety_context = None
        self._analysis_safety_phase = None
        self._dashboard_analysis_safety_allowed = False
        self._refresh_dashboard_analysis_visibility()
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
        """Start UCI only from isolated offline or a current successful preflight."""
        request_id = request_id or self._analysis_safety_request_id
        context = self._analysis_safety_context
        isolated_offline = bool(
            self.offline_mode and self.lichess is None and not self._lichess_connected
            and context and context[0] == "offline" and context[1] == request_id
            and context[2] == game_id
            and context[4] == self._analysis_session_generation)
        connected_preflight = bool(
            not self.offline_mode and self._online_actions_allowed()
            and self._analysis_safety_phase == "running"
            and request_id and request_id == self._analysis_safety_request_id
            and context and context[0] == request_id and context[1] == game_id
            and context[4] == self._analysis_session_generation
            and context[5] == str(getattr(self.lichess, "username", "") or "").strip().casefold()
            and context[6] is self.lichess)
        if not (isolated_offline or connected_preflight):
            self.home_status.setText("Stockfish заблокирован: требуется актуальная проверка Fair Play.")
            if dialog is not None:
                dialog.close()
            return
        stop = stop or self._analysis_stop or threading.Event()
        if dialog is None:
            dialog = QProgressDialog("Запуск Stockfish…", "Отменить", 0, 0, self)
            dialog.setWindowTitle("Послепартийный анализ")
            dialog.setMinimumDuration(0)
            dialog.show()
            self._analysis_dialog = dialog
            dialog.canceled.connect(lambda request=request_id: self._cancel_analysis_action(request))
        self._analysis_stop = stop
        self._analysis_worker_context = (
            request_id, game_id, self._analysis_session_generation, self.lichess,
            isolated_offline, stop)
        active_human_ids = {session.game_id for session in self._sessions_for_safety()
                            if session.status in {"started", "playing"} and not session.is_ai_opponent}

        def safety_check_after_engine_wait() -> bool:
            if stop.is_set() or active_human_ids:
                return False
            if isolated_offline:
                return (self.offline_mode and self.lichess is None
                        and not self._lichess_connected
                        and self._analysis_session_generation == context[4])
            service = context[6]
            username = str(context[5]).casefold()
            token = getattr(service, "token", None)
            generation = context[4]
            return self.engine_analysis.refresh_fair_play(
                service, token, username,
                is_current=lambda: (
                    not stop.is_set() and service is self.lichess
                    and self._lichess_connected
                    and generation == self._analysis_session_generation
                    and self._online_actions_allowed()),
                active_human_session=bool(active_human_ids))

        def work():
            try:
                result = self.engine_analysis.analyze_completed_game(
                    self.game_repository, game_id, engine_path, options, stop_event=stop,
                    active_human_game_ids=active_human_ids,
                    progress=lambda done, total: self.analysis_progress.emit(game_id, done, total),
                    safety_check=safety_check_after_engine_wait,
                    analyzer_factory=CompletedGameAnalyzer)
                payload = dict(result) if isinstance(result, dict) else {"result": result}
                payload["_worker_request_id"] = request_id
                self.analysis_finished.emit(game_id, payload)
            except Exception as exc:
                self.analysis_finished.emit(game_id, {
                    "error": str(exc), "_worker_request_id": request_id})

        thread = self._start_registered_work(
            work, kind="stockfish_game_analysis", lane=OperationLane.ENGINE,
            stop_event=stop, thread_name="chess-coach-analysis")
        self._analysis_thread = thread
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
        context = self._analysis_worker_context
        worker_request_id = (result.get("_worker_request_id")
                             if isinstance(result, dict) else None)
        owns_current_context = bool(
            context and (worker_request_id == context[0]
                         if worker_request_id is not None
                         else context[0] == self._analysis_safety_request_id))
        stale_or_cancelled = bool(context and (
            (worker_request_id is not None and worker_request_id != context[0])
            or
            context[1] != game_id
            or context[2] != self._analysis_session_generation
            or context[5].is_set()
            or (not context[4] and (context[3] is not self.lichess
                                    or not self._online_actions_allowed()))))
        if context is None and self._analysis_safety_phase == "running":
            # Defensive fail-closed handling for any worker signal that lost its
            # immutable launch context.
            stale_or_cancelled = True
        if context is None or owns_current_context:
            self._analysis_stop = None
            self._analysis_safety_request_id = None
            self._analysis_safety_context = None
            self._analysis_safety_phase = None
            self._analysis_worker_context = None
        if getattr(self, "_analysis_dialog", None):
            self._analysis_dialog.close()
            self._analysis_dialog = None
        if self._shutdown_requested:
            return
        if stale_or_cancelled:
            self.home_status.setText("Анализ остановлен: требуется новая проверка Fair Play.")
            self._notify_inline("Анализ остановлен: изменилось состояние безопасности Lichess.")
            return
        if isinstance(result, dict) and result.get("error"):
            self._notify_inline(f"Stockfish: {result['error']}")
        else:
            count = len(result.get("assessments", [])) if isinstance(result, dict) else 0
            if count:
                selected_ply = (self.review_workspace.selected_ply
                                if self.review_workspace.review
                                and self.review_workspace.review["game"]["game_id"] == game_id else 0)
                if self._open_analysis_position(game_id, selected_ply):
                    self._notify_inline(
                        f"Анализ сохранён: оценено {count} позиций; открыт общий разбор партии.")
                else:
                    self._notify_inline(f"Анализ сохранён: оценено {count} позиций.")
            else:
                self._notify_inline("Stockfish не сохранил оценки для этой партии.")

    def _open_analysis_position(self, game_id: str, ply: int, *,
                                analysis_source: str | None = None) -> bool:
        game = self.game_repository.get_game(game_id)
        if not game:
            return False
        if not self._open_saved_game(game, analysis_source=analysis_source):
            self.home_status.setText(self._raw_only_game_message(game))
            return False
        self.review_workspace.select_ply(ply)
        return True

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

    def _open_saved_game(self, game: dict, *, analysis_source: str | None = None) -> bool:
        if not self._is_standard_board_game(game):
            self.home_status.setText(self._raw_only_game_message(game))
            return False
        self._ephemeral_study = False
        self.review_workspace.repository = self.game_repository
        self._disable_position_evaluation("Открыта другая партия; выполните новую проверку безопасности.")
        self._revoke_review_safety(
            "Оценки и варианты скрыты до проверки актуальной безопасности.",
            preserve_offline=True)
        self.board.clear_annotations()
        session = LichessGameSession(game["game_id"], self.game_repository)
        session.initial_fen = game["initial_fen"]
        session.moves = game["moves"]
        account_color = game.get("account_color") or game.get("color")
        session.color = chess.WHITE if account_color == "white" else chess.BLACK
        session.opponent = (game.get("account_opponent") or game.get("opponent") or
                            game.get("black_player") or "Соперник")
        session.status = game["status"]
        session.winner = game["winner"]
        session.clock_control = game["clock"]
        session._rebuild_board()
        self.online_session = session
        self._review_session = session
        self.online_view_ply = None
        self._screen_mode = "review"
        self.board.set_flipped(session.color == chess.BLACK)
        self.screen_title.setText("Разбор партии")
        allowed, reason = self._review_training_safety()
        try:
            self.review_workspace.load_game(game, training_allowed=allowed,
                                            training_block_reason=reason,
                                            analysis_source=analysis_source)
        except (KeyError, ValueError) as exc:
            self.home_status.setText(f"Не удалось открыть сохранённую линию: {exc}")
            return False
        self.screen_stack.setCurrentWidget(self.review_workspace)
        self.game_status.setText("Сохранённая завершённая партия · только чтение")
        self._active_route = "games"
        self._schedule_workspace_save()
        if not self.offline_mode and self._lichess_connected:
            self._request_review_training_safety(str(game["game_id"]))
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
        self._workspace_save_timer.stop()
        for timer in self._finalization_timers.values():
            timer.stop()
        self._persist_workspace_state()
        self._shutdown_requested = True
        self._legacy_metrics_timer.stop()
        self._cancel_legacy_metrics_batch("shutdown")
        self._cancel_lichess_backfill_wakeup()
        for handle_name in ("_library_query_handle", "_library_detail_handle",
                            "_external_study_handle"):
            handle = getattr(self, handle_name, None)
            if handle is not None and handle.is_alive():
                handle.cancel("shutdown")
        self._position_eval_timer.stop()
        if self._position_eval_thread is not None:
            self._cancel_position_evaluation("Останавливаем локальную оценку перед закрытием…")
        install_thread = self._engine_install_thread
        install_running = bool(install_thread and install_thread.is_alive())
        if install_running and self._engine_install_stop is not None:
            self._engine_install_stop.set()
        if self._analysis_safety_request_id is not None:
            self._cancel_analysis_action(self._analysis_safety_request_id)
        analysis_thread = self._analysis_thread
        analysis_running = bool(analysis_thread and analysis_thread.is_alive())
        batch_thread = self._batch_thread
        batch_running = bool(batch_thread and batch_thread.is_alive())
        dashboard_thread = self._dashboard_thread
        if dashboard_thread is not None and dashboard_thread.is_alive():
            dashboard_thread.join(timeout=0.5)
        dashboard_running = bool(dashboard_thread and dashboard_thread.is_alive())
        if not dashboard_running:
            self._dashboard_thread = None
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
            close_method = getattr(self.lichess, "close", None)
            if close_method is None:
                close_result = True
            else:
                try:
                    close_result = close_method(timeout=0.15)
                except TypeError:
                    close_result = close_method()
            workers_stopped = close_result is not False
        analysis_stopped = not (analysis_thread and analysis_thread.is_alive())
        batch_stopped = not (batch_thread and batch_thread.is_alive())
        install_stopped = not (install_thread and install_thread.is_alive())
        statistics_stopped = not any(thread.is_alive() for thread in self._statistics_workers)
        # Do not destroy the QProcess parent until every child's finished handler
        # has observed and released that child, even if state() already says stopped.
        engine_probes_stopped = not self._engine_probe_processes
        if (not workers_stopped or not analysis_stopped or not batch_stopped or not install_stopped
                or not statistics_stopped or dashboard_running
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
        if not self.runtime.close():
            event.ignore()
            self.home_status.setText(
                "Завершаем фоновые операции; база останется открытой до безопасного завершения.")
            if not self._close_retry_scheduled:
                self._close_retry_scheduled = True
                self.setEnabled(False)
                self._close_retry_timer.start()
            return
        from .position_analysis import close_position_engines
        close_position_engines()
        event.accept()
        super().closeEvent(event)

    def _retry_close_after_workers(self) -> None:
        self._close_retry_scheduled = False
        self.close()
