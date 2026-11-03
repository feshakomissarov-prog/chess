"""Application-owned persistence contract for the resumable desktop workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class SettingsPort(Protocol):
    """Small settings boundary implemented by QSettings in the desktop shell."""

    def value(self, key: str, defaultValue: Any = None, *, type: type | None = None) -> Any: ...
    def setValue(self, key: str, value: Any) -> None: ...
    def remove(self, key: str) -> None: ...
    def sync(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReviewResumeTarget:
    game_id: str
    line_revision: int
    line_hash: str
    viewed_ply: int
    inspector_tab: int


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    route: str
    geometry: Any = None
    sidebar_collapsed: bool = False
    sidebar_explicit: bool = False
    board_target_size: int = 480
    review: ReviewResumeTarget | None = None


class WorkspaceStateStore:
    """Serializes durable workspace preferences without depending on Qt widgets."""

    def __init__(self, settings: SettingsPort) -> None:
        self._settings = settings

    def last_route(self) -> str:
        return str(self._settings.value("workspace/last_route", "home", type=str))

    def load_review_target(self) -> ReviewResumeTarget | None:
        game_id = str(self._settings.value("workspace/review_game_id", "", type=str)).strip()
        if not game_id:
            return None
        return ReviewResumeTarget(
            game_id=game_id,
            line_revision=int(self._settings.value("workspace/review_line_revision", -1, type=int)),
            line_hash=str(self._settings.value("workspace/review_line_hash", "", type=str)),
            viewed_ply=int(self._settings.value("workspace/review_viewed_ply", 0, type=int)),
            inspector_tab=int(self._settings.value("workspace/review_inspector_tab", 0, type=int)),
        )

    def remove_review_target(self) -> None:
        self._settings.remove("workspace/review_game_id")

    def save(self, snapshot: WorkspaceSnapshot) -> None:
        self._settings.setValue("workspace/window_geometry", snapshot.geometry)
        self._settings.setValue("workspace/theme", "dark-v1")
        self._settings.setValue("workspace/last_route", snapshot.route)
        self._settings.setValue("workspace/sidebar_collapsed", snapshot.sidebar_collapsed)
        self._settings.setValue("workspace/sidebar_explicit", snapshot.sidebar_explicit)
        self._settings.setValue("workspace/board_target_size", snapshot.board_target_size)
        if snapshot.review is not None:
            review = snapshot.review
            self._settings.setValue("workspace/review_game_id", review.game_id)
            self._settings.setValue("workspace/review_line_revision", review.line_revision)
            self._settings.setValue("workspace/review_line_hash", review.line_hash)
            self._settings.setValue("workspace/review_viewed_ply", review.viewed_ply)
            self._settings.setValue("workspace/review_inspector_tab", review.inspector_tab)
        self._settings.sync()
