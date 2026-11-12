from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QStandardPaths

from chess_coach.bootstrap import create_runtime
from chess_coach.lichess.repository import GameRepository


def test_runtime_uses_configured_primary_database_in_place(tmp_path, monkeypatch):
    primary = tmp_path / "owner-primary.sqlite3"
    GameRepository(primary).shutdown()
    profile_root = tmp_path / "app-profile"
    profile_root.mkdir()
    monkeypatch.setattr(
        QStandardPaths, "writableLocation", lambda _location: str(profile_root))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/database_file", str(primary))

    runtime = create_runtime(settings=settings, asynchronous_storage=True)
    try:
        assert Path(runtime.repository.path) == primary
        assert runtime.data_root == profile_root
    finally:
        runtime.close()


def test_runtime_fails_closed_when_configured_primary_is_missing(tmp_path, monkeypatch):
    profile_root = tmp_path / "app-profile"
    profile_root.mkdir()
    monkeypatch.setattr(
        QStandardPaths, "writableLocation", lambda _location: str(profile_root))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/database_file", str(tmp_path / "missing-primary.sqlite3"))

    with pytest.raises(FileNotFoundError, match="другая база не открыта"):
        create_runtime(settings=settings, asynchronous_storage=True)

    assert not (profile_root / "games.sqlite3").exists()
