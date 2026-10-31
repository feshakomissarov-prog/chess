"""Application entry point."""

import sys
from datetime import datetime, timezone
import os
from pathlib import Path
import traceback

from PySide6.QtWidgets import QApplication

from .window import MainWindow


def _exception_hook(exc_type, exc_value, exc_traceback) -> None:
    """Write stack-only diagnostics for pythonw launches; never log game data."""
    try:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        logs = root / "ChessCoach" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / "crash.log"
        if path.exists() and path.stat().st_size >= 1_000_000:
            rotated = logs / "crash.log.1"
            if rotated.exists():
                rotated.unlink()
            path.replace(rotated)
        frames = traceback.extract_tb(exc_traceback, limit=16)
        summary = "\n".join(
            f"  {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
            for frame in frames
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"{datetime.now(timezone.utc).isoformat()} "
                f"{exc_type.__name__}\n{summary}\n\n"
            )
    except Exception:
        # Diagnostics must not replace the original exception or create a loop.
        pass


def main() -> int:
    sys.excepthook = _exception_hook
    app = QApplication(sys.argv)
    app.setOrganizationName("ChessCoach")
    app.setApplicationName("ChessCoach")
    window = MainWindow(asynchronous_storage=True)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
