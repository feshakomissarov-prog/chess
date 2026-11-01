import pytest

from chess_coach import __main__ as launcher


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

    monkeypatch.setattr(launcher, "QApplication", FakeApplication)
    monkeypatch.setattr(launcher, "MainWindow", FakeWindow)

    assert launcher.main(["--manual-connect"]) == 0
    assert calls["window_kwargs"] == {
        "asynchronous_storage": True,
        "offline_mode": False,
        "manual_connect_mode": True,
    }
    assert calls["shown"] is True


def test_offline_and_manual_connect_cli_modes_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(launcher, "QApplication", lambda _argv: pytest.fail("app must not start"))
    with pytest.raises(SystemExit):
        launcher.main(["--offline", "--manual-connect"])
