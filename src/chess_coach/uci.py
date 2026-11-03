"""UCI process helpers shared by every Stockfish launch site."""

from __future__ import annotations

import os
import subprocess

import chess.engine


def hidden_process_options() -> dict:
    """Return subprocess options that keep child consoles hidden on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def popen_uci_hidden(command, **kwargs) -> chess.engine.SimpleEngine:
    """Start python-chess UCI with pipes and no visible Windows console."""
    for key, value in hidden_process_options().items():
        kwargs.setdefault(key, value)
    return chess.engine.SimpleEngine.popen_uci(command, **kwargs)
