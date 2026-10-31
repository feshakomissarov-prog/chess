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
    assert calls["window_kwargs"] == {"asynchronous_storage": True, "offline_mode": True}
    assert calls["shown"] is True
