"""Immutable identity captured when a Lichess operation is requested."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any


@dataclass(frozen=True)
class LichessOperationContext:
    generation: int
    account: str | None
    auth_method: str
    credential_store: Any
    connection_stop: Event
    request_id: str
    game_id: str | None = None
    command: str = ""
