"""Account-scoped session registry with explicit visible-game selection."""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar


class SessionIdentity(Protocol):
    game_id: str


SessionT = TypeVar("SessionT", bound=SessionIdentity)


class GameSessionCoordinator(Generic[SessionT]):
    """Owns session identity and selection; registration never changes the UI route."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionT] = {}
        self._selected_game_id: str | None = None

    @property
    def sessions(self) -> dict[str, SessionT]:
        """Compatibility mapping for existing readers during incremental migration."""
        return self._sessions

    @property
    def selected_game_id(self) -> str | None:
        return self._selected_game_id

    def register(self, session: SessionT, *, select: bool = False) -> SessionT:
        game_id = str(session.game_id)
        if not game_id:
            raise ValueError("session game_id is required")
        existing = self._sessions.get(game_id)
        if (existing is not None and existing is not session
                and not bool(getattr(existing, "detached", False))):
            raise ValueError("a different session already owns this game_id")
        self._sessions[game_id] = session
        if select:
            self._selected_game_id = game_id
        return session

    def get(self, game_id: str | None) -> SessionT | None:
        return self._sessions.get(str(game_id)) if game_id else None

    def for_owner(self, username: str | None, *, generation: int | None = None) -> tuple[SessionT, ...]:
        account = str(username or "").strip().casefold()
        if not account:
            return ()
        return tuple(
            session for session in self._sessions.values()
            if callable(getattr(session, "belongs_to", None))
            and session.belongs_to(account, generation=generation)
        )

    def select(self, game_id: str | None) -> SessionT | None:
        session = self.get(game_id)
        self._selected_game_id = str(game_id) if session is not None else None
        return session

    def clear_selection(self) -> None:
        self._selected_game_id = None

    def is_selected(self, game_id: str) -> bool:
        return self._selected_game_id == str(game_id)
