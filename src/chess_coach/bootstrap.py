"""Composition root: create one runtime and explicitly attach UI-owned resources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile

from PySide6.QtCore import QSettings, QStandardPaths

from .application.background_work import BackgroundWorkCoordinator
from .engine_manager import StockfishManager
from .lichess.repository import GameRepository


@dataclass(slots=True)
class ApplicationRuntime:
    settings: QSettings
    data_root: Path
    repository: GameRepository
    offline_mode: bool
    manual_connect_mode: bool
    work_coordinator: BackgroundWorkCoordinator = field(default_factory=BackgroundWorkCoordinator)
    stockfish_manager: StockfishManager = field(default_factory=StockfishManager)
    offline_data_dir: tempfile.TemporaryDirectory | None = None
    _lichess_service: object | None = None
    _closed: bool = False

    def create_lichess_service(self, parent, *, user_requested: bool = False):
        if self._lichess_service is not None:
            return self._lichess_service
        if self.offline_mode or (self.manual_connect_mode and not user_requested):
            return None
        from .lichess.auth import CredentialStorageError, WindowsCredentialStore
        from .lichess.service import LichessService

        try:
            self._lichess_service = LichessService(
                token_store=WindowsCredentialStore(),
                personal_token_store=WindowsCredentialStore(
                    user=WindowsCredentialStore.PERSONAL_TOKEN_USER),
                auth_method=self.settings.value("lichess/selected_auth_method", "oauth", type=str),
                repository=self.repository,
                work_coordinator=self.work_coordinator,
                parent=parent,
            )
            return self._lichess_service
        except CredentialStorageError:
            return None

    def create_sound_player(self, parent):
        from .ui.sounds import DEFAULT_SOUND_SELECTIONS, ChessSoundPlayer

        return ChessSoundPlayer(
            self.data_root / "sounds",
            moves_enabled=self.settings.value("sound_moves_enabled", True, type=bool),
            notifications_enabled=self.settings.value(
                "sound_notifications_enabled", True, type=bool),
            history_enabled=self.settings.value("sound_history_enabled", False, type=bool),
            volume=self.settings.value("sound_volume", 45, type=int),
            selections={
                cue: self.settings.value(f"sound_selection_{cue}", default_id, type=str)
                for cue, default_id in DEFAULT_SOUND_SELECTIONS.items()
            },
            parent=parent,
        )

    def close(self) -> bool:
        if self._closed:
            return True
        if not self.work_coordinator.close(timeout=0.5):
            return False
        self.stockfish_manager.close_all()
        self._closed = True
        self.repository.shutdown()
        if self.offline_data_dir is not None:
            self.offline_data_dir.cleanup()
            self.offline_data_dir = None
        return True


def create_runtime(*, settings: QSettings | None = None, offline_mode: bool = False,
                   manual_connect_mode: bool = False,
                   asynchronous_storage: bool = False) -> ApplicationRuntime:
    """Create one profile, repository and explicit connection-mode context."""
    if offline_mode and manual_connect_mode:
        raise ValueError("--offline и --manual-connect нельзя включить одновременно")
    offline_data_dir = None
    if offline_mode:
        # An offline runtime never reads user settings or touches the user database.
        offline_data_dir = tempfile.TemporaryDirectory(prefix="chess-coach-offline-")
        data_root = Path(offline_data_dir.name)
        active_settings = QSettings(str(data_root / "settings.ini"), QSettings.Format.IniFormat)
    else:
        active_settings = settings if settings is not None else QSettings("ChessCoach", "ChessCoach")
        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation)
        if not location:
            raise RuntimeError("Windows не сообщил папку данных Chess Coach")
        data_root = Path(location)
        data_root.mkdir(parents=True, exist_ok=True)
    try:
        repository = GameRepository(data_root / "games.sqlite3", async_writes=asynchronous_storage)
    except Exception:
        if offline_data_dir is not None:
            offline_data_dir.cleanup()
        raise
    return ApplicationRuntime(active_settings, data_root, repository, offline_mode,
                              manual_connect_mode, offline_data_dir=offline_data_dir)
