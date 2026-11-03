import pytest
from pathlib import Path
import subprocess
import ctypes

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
    runtime = object()
    monkeypatch.setattr(launcher, "create_runtime",
                        lambda **kwargs: (calls.update(runtime_args=kwargs), runtime)[1])

    assert launcher.main(["--offline"]) == 0
    assert calls["runtime_args"] == {
        "asynchronous_storage": True,
        "offline_mode": True,
        "manual_connect_mode": False,
    }
    assert calls["window_kwargs"] == {"runtime": runtime}
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
    runtime = object()
    monkeypatch.setattr(launcher, "create_runtime",
                        lambda **kwargs: (calls.update(runtime_args=kwargs), runtime)[1])

    assert launcher.main(["--manual-connect"]) == 0
    assert calls["runtime_args"] == {
        "asynchronous_storage": True,
        "offline_mode": False,
        "manual_connect_mode": True,
    }
    assert calls["window_kwargs"] == {"runtime": runtime}
    assert calls["shown"] is True
    assert "activation_callback" in calls


def test_ordinary_cli_launch_uses_auto_restore_mode(monkeypatch):
    calls = {}

    class FakeApplication:
        def __init__(self, _argv): pass
        def setOrganizationName(self, _value): pass
        def setApplicationName(self, _value): pass
        def exec(self): return 0

    class FakeWindow:
        def __init__(self, **kwargs): calls["kwargs"] = kwargs
        def show(self): calls["shown"] = True

    class FakeGuard:
        def set_activation_callback(self, callback): calls["callback"] = callback
        def close(self): pass

    monkeypatch.setattr(launcher, "QApplication", FakeApplication)
    monkeypatch.setattr(launcher, "MainWindow", FakeWindow)
    monkeypatch.setattr(launcher, "acquire_single_instance", lambda _path: FakeGuard())
    runtime = object()
    monkeypatch.setattr(launcher, "create_runtime",
                        lambda **kwargs: (calls.update(runtime_args=kwargs), runtime)[1])
    assert launcher.main([]) == 0
    assert calls["runtime_args"] == {
        "asynchronous_storage": True,
        "offline_mode": False,
        "manual_connect_mode": False,
    }
    assert calls["kwargs"] == {"runtime": runtime}
    assert calls["shown"]


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
    assert "-m chess_coach" in cmd and "--manual-connect" not in cmd
    assert "--offline" not in cmd
    assert "pip install" not in cmd.lower()
    assert "chess-coach-launcher.log" in cmd
    assert "python.exe" in debug and "--manual-connect" in debug
    assert 'pushd "%ROOT%"' in cmd and 'pushd "%ROOT%"' in debug
    assert '$shortcut.Arguments = "-m chess_coach"' in shortcut
    assert "--manual-connect" not in shortcut
    assert "chess_coach.ico" in shortcut


@pytest.mark.parametrize("launcher_name", ["launch_chess_coach.cmd", "debug_chess_coach.cmd"])
def test_windows_launchers_resolve_repository_root_when_invoked_elsewhere(tmp_path, launcher_name):
    root = Path(__file__).resolve().parents[1]
    driver = tmp_path / "invoke-launcher.cmd"
    driver.write_text(f'@echo off\r\nset "ROOT={root}\\"\r\n'
                      'pushd "%ROOT%" >nul\r\n'
                      'echo %CD%\r\n'
                      'popd\r\n',
                      encoding="ascii")
    short_path = ctypes.create_unicode_buffer(32768)
    short_length = ctypes.windll.kernel32.GetShortPathNameW(
        str(tmp_path), short_path, len(short_path))
    cwd = Path(short_path.value) if short_length else tmp_path
    driver_path = cwd / driver.name
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(driver_path)],
        cwd=cwd, text=True, capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(root).casefold() in result.stdout.strip().casefold()


def test_project_icon_is_a_valid_original_64px_ico_payload():
    icon = make_icon()
    assert icon[:6] == bytes((0, 0, 1, 0, 1, 0))
    assert icon[6:10] == bytes((64, 64, 0, 0))
    assert len(icon) > 16_000
