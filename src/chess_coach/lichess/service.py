"""Background OAuth, challenge, and NDJSON orchestration for the Qt UI."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from .api import LichessClient
from .service_auth import LichessAuthMixin
from .service_sync import LichessSyncMixin
from .service_games import LichessGameActionsMixin
from .auth import WindowsCredentialStore

class LichessService(QObject, LichessAuthMixin, LichessSyncMixin, LichessGameActionsMixin):
    status_changed = Signal(str)
    event_received = Signal(dict)
    game_event_received = Signal(str, dict)
    # Session-bound signals carry a generation so queued Qt deliveries from a
    # previous account/method can be discarded by the receiver.
    session_status_changed = Signal(int, str)
    session_authenticated = Signal(int, str)
    session_event_stream_ready = Signal(int, str)
    session_error = Signal(int, str)
    session_event_received = Signal(int, dict)
    session_game_event_received = Signal(int, str, dict)
    challenge_ready = Signal(dict)
    challenge_status_changed = Signal(str, bool)
    invitation_cancelled = Signal(str)
    active_games_received = Signal(object)
    active_games_error = Signal(str)
    analysis_safety_games_received = Signal(str, object)
    analysis_safety_error = Signal(str, str)
    error = Signal(str)
    move_failed = Signal(str, str, str, int)
    takeback_action_completed = Signal(str, str, int)
    authenticated = Signal(str)
    archive_progress = Signal(int, str)
    archive_finished = Signal(int, str)
    archive_pilot_progress = Signal(int, int, str)
    archive_pilot_finished = Signal(int, int, str)
    archive_game_refresh_finished = Signal(str, str)
    archive_eval_refresh_progress = Signal(int, int, str)
    archive_eval_refresh_finished = Signal(int, int, str)
    puzzles_progress = Signal(int)
    puzzles_scanned = Signal(int)
    puzzles_started = Signal(str)
    puzzles_finished = Signal(int, str)
    background_sync_finished = Signal(str, str)
    lichess_analysis_backfill_progress = Signal(int, str, int, int, object)
    lichess_analysis_backfill_finished = Signal(int, str, int, int, int, int, int, int, str, str)
    puzzle_pilot_progress = Signal(int, int)
    puzzle_pilot_finished = Signal(int, int, str, str)

    def __init__(self, client: LichessClient | None = None, token_store=None,
                 repository=None, parent=None, personal_token_store=None,
                 auth_method: str = "oauth", work_coordinator=None):
        super().__init__(parent)
        self.client = client or LichessClient()
        self.oauth_token_store = token_store or WindowsCredentialStore()
        self.personal_token_store = personal_token_store or WindowsCredentialStore(
            user=getattr(WindowsCredentialStore, "PERSONAL_TOKEN_USER",
                         "personal-access-token"))
        self.auth_method = auth_method if auth_method in {"oauth", "personal_token"} else "oauth"
        self._auth_generation = 0
        self._auth_lock = threading.RLock()
        self.repository = repository
        self.work_coordinator = work_coordinator
        self._archive_stop: threading.Event | None = None
        self._analysis_backfill_stop: threading.Event | None = None
        self._puzzle_stop: threading.Event | None = None
        self._puzzle_callback: OAuthCallbackServer | None = None
        self._stop = threading.Event()
        self._connection_stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._thread_lock = threading.Lock()
        self._closing = False
        self._game_stops: dict[str, threading.Event] = {}
        self._final_sync_game_ids: set[str] = set()
        self._event_lock = threading.Lock()
        self._restore_lock = threading.Lock()
        self._restore_running = False
        self._restore_generations: set[int] = set()
        self._event_stream_started = False
        self.username: str | None = None
        self.puzzle_username: str | None = None
        self._callback: OAuthCallbackServer | None = None
        self._username_challenge_stop: threading.Event | None = None
        self._username_challenge_id: str | None = None
