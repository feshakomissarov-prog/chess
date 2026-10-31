"""Keep every pytest-qt run headless and isolate per-test application data."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["CHESS_COACH_OFFLINE_TEST"] = "1"

import pytest
from PySide6.QtCore import QStandardPaths


@pytest.fixture(autouse=True)
def isolate_app_data(tmp_path, monkeypatch):
    app_data = tmp_path / "ChessCoach"
    monkeypatch.setattr(
        QStandardPaths,
        "writableLocation",
        lambda _location: str(app_data),
    )
