"""Application entry point."""

import sys
import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chess Coach")
    connection_mode = parser.add_mutually_exclusive_group()
    connection_mode.add_argument(
        "--offline", action="store_true",
        help="запустить изолированное окно без чтения OAuth-данных и сетевых соединений",
    )
    connection_mode.add_argument(
        "--manual-connect", action="store_true",
        help="использовать обычные данные, но подключать Lichess только после нажатия в приложении",
    )
    args = parser.parse_args(argv)
    sys.excepthook = _exception_hook
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("ChessCoach")
    app.setApplicationName("ChessCoach")
    window = MainWindow(asynchronous_storage=True, offline_mode=args.offline,
                        manual_connect_mode=args.manual_connect)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
