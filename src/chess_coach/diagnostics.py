"""Privacy-conscious local diagnostics for Chess Coach.

Only allow-listed scalar fields are written. Callers must not pass API payloads,
usernames, FENs, PGNs, URLs, or exception messages.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import threading
import traceback
import contextvars
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_LOGGER = logging.getLogger("chess_coach.diagnostics")
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False
_HANDLER: logging.Handler | None = None
_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "chess_coach_diagnostic_context", default={})
_LAST_ERROR_CODE = "none"
_ALLOWED = {"duration_ms", "run_id", "job_id", "error_code", "status", "mode",
            "count", "new_count", "checked_count", "page", "http_status",
            "bytes", "records", "schema_version", "engine_verified", "thread",
            "response_type", "method", "route", "stage", "correlation_id",
            "retry_after"}
_SECRET = re.compile(
    r"(?i)(bearer\s+\S+|(?:access[_-]?token|refresh[_-]?token|token|authorization|"
    r"client[_-]?secret|oauth[_-]?code|code)[=:]\s*\S+)")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_FEN = re.compile(
    r"\b[prnbqkPRNBQK1-8]{1,8}(?:/[prnbqkPRNBQK1-8]{1,8}){7}\s+[wb]\s+"
    r"(?:-|K?Q?k?q?)\s+(?:-|[a-h][36])\s+\d+\s+\d+\b")


def logs_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "ChessCoach" / "logs"
    return Path.home() / "AppData" / "Local" / "ChessCoach" / "logs"


def _safe_text(value: object, limit: int = 120) -> str:
    text = str(value)
    text = _SECRET.sub("[redacted]", text)
    text = _EMAIL.sub("[redacted-email]", text)
    text = _FEN.sub("[redacted-position]", text)
    text = re.sub(r"(?i)([a-z]:\\|/home/|/users/|/Users/)[^\s\"']+", "[redacted-path]", text)
    text = re.sub(r"\b(?:[a-h][1-8]){4,}\b", "[redacted-position]", text)
    return text.replace("\r", " ").replace("\n", " ")[:limit]


def configure_logging(directory: Path | None = None) -> bool:
    """Install a bounded rotating UTF-8 file handler; failure is non-fatal."""
    global _HANDLER
    with _LOCK:
        if _HANDLER is not None:
            return True
        try:
            target = directory or logs_directory()
            target.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                target / "chess-coach.log", maxBytes=5 * 1024 * 1024,
                backupCount=4, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _LOGGER.addHandler(handler)
            _HANDLER = handler
            return True
        except (OSError, ValueError):
            return False


def log_event(event: str, component: str, *, level: int = logging.INFO,
              error: BaseException | None = None, **fields: Any) -> None:
    """Write a structured event using an allow-list; never serialize arbitrary objects."""
    configure_logging()
    safe: dict[str, object] = {}
    for key, value in fields.items():
        if key not in _ALLOWED or value is None:
            continue
        if key in {"duration_ms", "count", "new_count", "checked_count", "page", "http_status", "bytes", "records", "schema_version", "retry_after"}:
            try:
                safe[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        elif key == "engine_verified":
            safe[key] = bool(value)
        else:
            safe[key] = _safe_text(value)
    global _LAST_ERROR_CODE
    if error is not None:
        safe["error_type"] = type(error).__name__
        safe["error_code"] = safe.get("error_code", type(error).__name__)
        _LAST_ERROR_CODE = str(safe["error_code"])
        frames = traceback.extract_tb(error.__traceback__, limit=6) if error.__traceback__ else []
        safe["trace"] = [{ "function": _safe_text(frame.name, 48), "line": int(frame.lineno) }
                         for frame in frames]
    for key, value in _CONTEXT.get().items():
        safe.setdefault(key, _safe_text(value))
    payload = {"time": datetime.now(timezone.utc).isoformat(), "level": logging.getLevelName(level),
               "event": _safe_text(event, 64), "component": _safe_text(component, 64),
               "thread": _safe_text(threading.current_thread().name, 64), **safe}
    try:
        _LOGGER.log(level, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        for handler in _LOGGER.handlers:
            handler.flush()
    except Exception:
        pass


def set_context(**values: str) -> contextvars.Token:
    allowed = {key: _safe_text(value, 64) for key, value in values.items()
               if key in {"run_id", "job_id"} and value}
    return _CONTEXT.set({**_CONTEXT.get(), **allowed})


def reset_context(token: contextvars.Token) -> None:
    _CONTEXT.reset(token)


def anonymized_report(*, schema_version: int | None = None,
                      engine_verified: bool = False,
                      last_error_code: str | None = None,
                      recent_counts: dict[str, int] | None = None) -> str:
    """Return a deliberately small report without account or filesystem data."""
    try:
        import chess
        chess_version = getattr(chess, "__version__", "unknown")
    except Exception:
        chess_version = "unknown"
    try:
        import PySide6
        qt_version = getattr(PySide6, "__version__", "unknown")
    except Exception:
        qt_version = "unknown"
    try:
        from . import __version__ as app_version
    except Exception:
        app_version = "unknown"
    counts = {k: max(0, int(v)) for k, v in (recent_counts or {}).items()
              if k in {"queued", "complete", "failed", "cancelled"}}
    return json.dumps({"app_version": app_version, "python": platform.python_version(),
        "qt": qt_version, "python_chess": chess_version, "schema_version": schema_version,
        "engine_verified": bool(engine_verified),
        "last_error_code": _safe_text(last_error_code or _LAST_ERROR_CODE),
        "recent_batch_counts": counts, "reported_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, indent=2)
