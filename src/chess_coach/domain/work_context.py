"""Thread-local execution metadata shared by app policy and infrastructure ports."""

from contextvars import ContextVar, Token


_PRIORITY: ContextVar[int] = ContextVar("chess_coach_work_priority", default=0)


def current_work_priority() -> int:
    return _PRIORITY.get()


def set_work_priority(priority: int) -> Token:
    return _PRIORITY.set(max(0, int(priority)))


def reset_work_priority(token: Token) -> None:
    _PRIORITY.reset(token)
