from __future__ import annotations

import json
import logging
import threading

from chess_coach import diagnostics
from chess_coach.lichess.api import LichessClient


def test_diagnostics_are_allowlisted_redacted_and_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_HANDLER", None)
    diagnostics._LOGGER.handlers.clear()
    assert diagnostics.configure_logging(tmp_path)
    diagnostics.log_event(
        "puzzle_sync_page_imported", "puzzle_sync",
        mode=("Bearer secret-token token=second-secret user@example.test "
              "C:\\Users\\Михаил\\private 8/8/8/8/8/8/8/8 w - - 0 1"),
        count=1,
        username="Alice",
    )
    for handler in diagnostics._LOGGER.handlers:
        handler.flush()
    contents = (tmp_path / "chess-coach.log").read_text(encoding="utf-8")
    event = json.loads(contents)
    assert event["mode"].startswith("[redacted]")
    assert "secret-token" not in contents
    assert "second-secret" not in contents
    assert "8/8/8/8/8/8/8/8 w" not in contents
    assert "user@example.test" not in contents
    assert "Михаил" not in contents
    assert "Alice" not in contents
    assert event["count"] == 1
    assert "time" in event and "thread" in event
    diagnostics._LOGGER.handlers.clear()
    diagnostics._HANDLER = None


def test_diagnostics_use_rotating_bounded_files(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_HANDLER", None)
    diagnostics._LOGGER.handlers.clear()
    assert diagnostics.configure_logging(tmp_path)
    handler = diagnostics._HANDLER
    handler.maxBytes = 512
    for index in range(40):
        diagnostics.log_event("batch_job_completed", "batch", count=index)
    handler.flush()
    assert (tmp_path / "chess-coach.log").exists()
    assert (tmp_path / "chess-coach.log.1").exists()
    diagnostics._LOGGER.handlers.clear()
    diagnostics._HANDLER = None


def test_anonymized_report_has_versions_and_no_account_data():
    report = diagnostics.anonymized_report(
        schema_version=9, engine_verified=True, last_error_code="RateLimitError",
        recent_counts={"queued": 2, "complete": 3, "username": 1},
    )
    data = json.loads(report)
    assert data["schema_version"] == 9
    assert data["engine_verified"] is True
    assert data["recent_batch_counts"] == {"queued": 2, "complete": 3}
    assert "username" not in report and "token" not in report


def test_puzzle_api_diagnostics_never_include_request_url_or_token(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_HANDLER", None)
    diagnostics._LOGGER.handlers.clear()
    assert diagnostics.configure_logging(tmp_path)

    class Transport:
        def stream(self, method, url, headers, body, timeout, stop_event):
            assert "secret-token" in headers["Authorization"]
            assert "/api/puzzle/activity" in url
            yield b'{"date":1,"puzzle":{"id":"private-puzzle"}}\n'

    client = LichessClient(Transport(), base_url="https://lichess.test")
    list(client.stream_puzzle_activity(
        "secret-token", threading.Event(), max_items=1))
    for handler in diagnostics._LOGGER.handlers:
        handler.flush()
    text = (tmp_path / "chess-coach.log").read_text(encoding="utf-8")
    assert "secret-token" not in text
    assert "private-user" not in text
    assert "private-puzzle" not in text
    assert '"records":1' in text
    diagnostics._LOGGER.handlers.clear()
    diagnostics._HANDLER = None


def test_settings_diagnostic_report_requires_explicit_opt_in(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from chess_coach.window import SettingsDialog

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    clipboard = QApplication.clipboard()
    clipboard.setText("unchanged")
    dialog._copy_diagnostic_report()
    assert clipboard.text() == "unchanged"
    dialog.share_report.setChecked(True)
    dialog._copy_diagnostic_report()
    report = json.loads(clipboard.text())
    assert report["schema_version"] == 17
    assert "recent_batch_counts" in report
