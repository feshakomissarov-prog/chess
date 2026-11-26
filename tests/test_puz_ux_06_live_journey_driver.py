from __future__ import annotations

from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from PySide6.QtTest import QTest

from scripts import puz_ux_05_mainwindow_live_journey as journey


def test_unexpected_dialog_is_reported_and_exec_is_closed(qtbot):
    app = QApplication.instance()
    dialog = QMessageBox(QMessageBox.Icon.Warning, "Unexpected consent", "Unexpected UI")
    dialog.addButton("Continue", QMessageBox.ButtonRole.ActionRole)
    safety_close = QTimer()
    safety_close.setSingleShot(True)
    safety_close.timeout.connect(dialog.reject)

    def trigger_unexpected_modal():
        # Keeps the RED test bounded even against the old helper that leaves
        # a mismatched nested QDialog.exec() running.
        safety_close.start(500)
        dialog.exec()
        safety_close.stop()

    started = time.monotonic()

    with pytest.raises(AssertionError, match="unexpected dialog title='Unexpected consent'"):
        journey.click_dialog_button_with_mouse(
            app, "Expected confirmation", "Да", trigger_unexpected_modal,
        )

    assert time.monotonic() - started < 12.0
    assert not dialog.isVisible()


def test_cleanup_resolves_partial_rated_close_prompt_with_bounded_mouse_action(
        qtbot, monkeypatch, capsys):
    app = QApplication.instance()

    class PartialRatedWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.runtime = SimpleNamespace(_closed=False)
            self.safe_close_clicked = False
            self.close_dialog = None

        def closeEvent(self, event):
            dialog = QMessageBox(self)
            self.close_dialog = dialog
            dialog.setWindowTitle("Незавершённая рейтинговая задача")
            dialog.setText("CI teardown fixture")
            keep = dialog.addButton("Продолжить решение", QMessageBox.ButtonRole.RejectRole)
            close = dialog.addButton("Закрыть без отправки", QMessageBox.ButtonRole.AcceptRole)
            # Bounds the baseline cleanup's missing modal guard so RED fails
            # by assertion rather than consuming the full journey timeout.
            QTimer.singleShot(500, dialog.reject)
            dialog.exec()
            if dialog.clickedButton() is close:
                self.safe_close_clicked = True
                self.runtime._closed = True
                event.accept()
            else:
                event.ignore()

    window = PartialRatedWindow()
    monkeypatch.setattr(journey, "_ACTIVE_APP", app)
    monkeypatch.setattr(journey, "_ACTIVE_WINDOW", window)
    monkeypatch.setattr(journey, "_CLEANUP_DONE", False)
    monkeypatch.setattr(journey, "shutdown_logging", lambda: None)

    error = journey.cleanup()

    assert error is None
    assert window.runtime._closed
    assert window.safe_close_clicked
    assert "PUZ06_CLEANUP_SAFE_CLOSE_CLICK" in capsys.readouterr().out
