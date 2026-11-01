"""Application entry point."""

import sys
import argparse

from PySide6.QtWidgets import QApplication

from .diagnostics import configure_logging, log_event

from .window import MainWindow


def _exception_hook(exc_type, exc_value, exc_traceback) -> None:
    """Log only the exception class; traceback paths may reveal local usernames."""
    log_event("unhandled_exception", "application", level=40,
              error=exc_value if isinstance(exc_value, BaseException) else RuntimeError())


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
    configure_logging()
    log_event("app_start", "application", mode=("offline" if args.offline else
              "manual_connect" if args.manual_connect else "standard"))
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("ChessCoach")
    app.setApplicationName("ChessCoach")
    window = MainWindow(asynchronous_storage=True, offline_mode=args.offline,
                        manual_connect_mode=args.manual_connect)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
