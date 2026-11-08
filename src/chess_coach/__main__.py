"""Application entry point."""

import sys
import argparse
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .diagnostics import configure_logging, log_event
from .bootstrap import create_runtime
from .single_instance import acquire_single_instance

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
    icon_path = Path(__file__).resolve().parent / "resources" / "chess_coach.ico"
    if icon_path.is_file() and hasattr(app, "setWindowIcon"):
        app.setWindowIcon(QIcon(str(icon_path)))
    instance_guard = None
    if not args.offline:
        profile_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation)
        if not profile_location:
            log_event("single_instance_failed", "application", level=40,
                      error_code="profile_path_unavailable")
            QMessageBox.critical(
                None, "Профиль недоступен",
                "Windows не сообщил папку данных Chess Coach. Приложение не запущено, "
                "чтобы не открыть другую базу вместо вашей.")
            return 2
        profile_path = Path(profile_location)
        try:
            instance_guard = acquire_single_instance(profile_path)
        except OSError as exc:
            log_event("single_instance_failed", "application", level=40,
                      error=exc, error_code="profile_lock_failed")
            QMessageBox.critical(
                None, "Chess Coach уже запущен",
                "Не удалось безопасно открыть профиль Chess Coach. "
                "Проверьте, не запущено ли приложение, и повторите попытку.")
            return 2
        if instance_guard is None:
            log_event("second_launch_activated", "application")
            return 0
    runtime = create_runtime(offline_mode=args.offline,
                             manual_connect_mode=args.manual_connect,
                             asynchronous_storage=True)
    window = MainWindow(runtime=runtime)
    if instance_guard is not None:
        instance_guard.set_activation_callback(lambda: _activate_window(window))
    window.show()
    result = app.exec()
    if instance_guard is not None:
        instance_guard.close()
    return result


def _activate_window(window) -> None:
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


if __name__ == "__main__":
    raise SystemExit(main())
