"""Typed shell routes and navigation history for the desktop workspace."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkspaceRoute(StrEnum):
    HOME = "home"
    PLAY = "play"
    GAMES = "games"
    ANALYSIS = "analysis"
    TRAINING = "training"
    PROGRESS = "progress"
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class RouteEntry:
    route: WorkspaceRoute
    context: str | None = None


class WorkspaceNavigator:
    """Keeps typed route/back context without owning or resetting game sessions."""

    def __init__(self, initial: WorkspaceRoute = WorkspaceRoute.HOME) -> None:
        self._current = RouteEntry(initial)
        self._back: list[RouteEntry] = []

    @property
    def current(self) -> RouteEntry:
        return self._current

    @property
    def can_go_back(self) -> bool:
        return bool(self._back)

    def navigate(self, route: WorkspaceRoute, *, context: str | None = None,
                 remember: bool = True) -> RouteEntry:
        entry = RouteEntry(route, context)
        # Home is the root workspace, not another breadcrumb. Re-selecting it
        # or restoring it from a nested screen must clear stale Back entries.
        if route is WorkspaceRoute.HOME:
            self._back.clear()
            self._current = entry
            return entry
        if entry == self._current:
            return entry
        if remember:
            self._back.append(self._current)
        self._current = entry
        return entry

    def back(self) -> RouteEntry:
        if self._back:
            self._current = self._back.pop()
        return self._current

    def reset(self, route: WorkspaceRoute = WorkspaceRoute.HOME) -> RouteEntry:
        self._back.clear()
        self._current = RouteEntry(route)
        return self._current
