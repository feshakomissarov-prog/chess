import pytest
from pathlib import Path

from chess_coach import __main__ as launcher
from scripts.create_app_icon import make_icon


def test_offline_cli_passes_isolation_mode_to_window(monkeypatch):
    calls = {}

    class FakeApplication:
        def __init__(self, argv):
            calls["argv"] = argv

        def setOrganizationName(self, value):
            calls["organization"] = value

        def setApplicationName(self, value):
            calls["application"] = value

        def exec(self):
            return 0

    class FakeWindow:
        def __init__(self, **kwargs):
            calls["window_kwargs"] = kwargs

        def show(self):
            calls["shown"] = True

    monkeypatch.setattr(launcher, "QApplication", FakeApplication)
    monkeypatch.setattr(launcher, "MainWindow", FakeWindow)

    assert launcher.main(["--offline"]) == 0
    assert calls["window_kwargs"] == {
        "asynchronous_storage": True,
        "offline_mode": True,
        "manual_connect_mode": False,
    }
    assert calls["shown"] is True


def test_manual_connect_cli_passes_non_isolated_mode_to_window(monkeypatch):
    calls = {}

    class FakeApplication:
        def __init__(self, _argv): pass
        def setOrganizationName(self, _value): pass
        def setApplicationName(self, _value): pass
        def exec(self): return 0

    class FakeWindow:
        def __init__(self, **kwargs): calls["window_kwargs"] = kwargs
        def show(self): calls["shown"] = True

    class FakeGuard:
        def set_activation_callback(self, callback): calls["activation_callback"] = callback
        def close(self): calls["guard_closed"] = True

    monkeypatch.setattr(launcher, "acquire_single_instance", lambda _path: FakeGuard())

    monkeypatch.setattr(launcher, "QApplication", FakeApplication)
    monkeypatch.setattr(launcher, "MainWindow", FakeWindow)

    assert launcher.main(["--manual-connect"]) == 0
    assert calls["window_kwargs"] == {
        "asynchronous_storage": True,
        "offline_mode": False,
        "manual_connect_mode": True,
    }
    assert calls["shown"] is True
    assert "activation_callback" in calls


def test_offline_and_manual_connect_cli_modes_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(launcher, "QApplication", lambda _argv: pytest.fail("app must not start"))
    with pytest.raises(SystemExit):
        launcher.main(["--offline", "--manual-connect"])


def test_second_process_returns_without_creating_a_second_window(monkeypatch):
    calls = {}

    class FakeApplication:
        def __init__(self, _argv): pass
        def setOrganizationName(self, _value): pass
        def setApplicationName(self, _value): pass
        def exec(self): return 0

    monkeypatch.setattr(launcher, "QApplication", FakeApplication)
    monkeypatch.setattr(launcher, "acquire_single_instance", lambda _path: None)
    monkeypatch.setattr(launcher, "MainWindow",
                        lambda **_kwargs: calls.setdefault("created", True))
    assert launcher.main(["--manual-connect"]) == 0
    assert "created" not in calls


def test_windows_launch_files_are_relative_and_do_not_install_or_use_offline_mode():
    root = Path(__file__).resolve().parents[1]
    cmd = (root / "launch_chess_coach.cmd").read_text(encoding="utf-8")
    debug = (root / "debug_chess_coach.cmd").read_text(encoding="utf-8")
    shortcut = (root / "scripts" / "create_desktop_shortcut.ps1").read_text(encoding="utf-8")
    assert "%~dp0" in cmd and ".venv312\\Scripts\\pythonw.exe" in cmd
    assert "--manual-connect" in cmd and "--offline" not in cmd
    assert "pip install" not in cmd.lower()
    assert "chess-coach-launcher.log" in cmd
    assert "python.exe" in debug and "--manual-connect" in debug
    assert "-m chess_coach --manual-connect" in shortcut
    assert "chess_coach.ico" in shortcut


def test_project_icon_is_a_valid_original_64px_ico_payload():
    icon = make_icon()
    assert icon[:6] == bytes((0, 0, 1, 0, 1, 0))
    assert icon[6:10] == bytes((64, 64, 0, 0))
    assert len(icon) > 16_000
