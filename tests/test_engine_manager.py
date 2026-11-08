from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from chess_coach import engine_manager as em
from chess_coach.position_analysis import evaluate_position
import chess
import threading


class _Response:
    def __init__(self, body: bytes, *, url: str | None = None):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}
        self._url = url or em.STOCKFISH_URL

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self, size=-1): return self._body.read(size)
    def geturl(self): return self._url


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return target.getvalue()


def _patch_asset(monkeypatch, payload: bytes):
    monkeypatch.setattr(em, "STOCKFISH_SIZE", len(payload))
    monkeypatch.setattr(em, "STOCKFISH_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(em.urllib.request, "urlopen", lambda request, timeout: _Response(payload))


def test_discovery_checks_only_configured_path_and_small_standard_paths(tmp_path, monkeypatch):
    custom = tmp_path / "selected.exe"
    custom.write_bytes(b"engine")
    found = em.discover_stockfish(str(custom), environ={"PATH": ""})
    assert found == str(custom.resolve())


def test_discovery_finds_nested_managed_universal_install_for_unicode_user(tmp_path):
    local_app_data = tmp_path / "Пользователь" / "AppData" / "Local"
    installed = (local_app_data / "ChessCoach" / "engines" / "stockfish" / "19"
                 / "stockfish" / "stockfish-windows-x86-64-universal.exe")
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"managed synthetic engine")
    unrelated = tmp_path / "outside" / "stockfish.exe"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"must not be discovered")

    found = em.discover_stockfish(
        environ={"LOCALAPPDATA": str(local_app_data), "PATH": ""})

    assert found == str(installed.resolve())
    # Discovery is path-only; verification remains an explicit separate action.


def test_discovery_does_not_search_outside_allowlisted_managed_names(tmp_path):
    local_app_data = tmp_path / "AppData" / "Local"
    unexpected = (local_app_data / "ChessCoach" / "engines" / "stockfish" / "19"
                  / "nested" / "other-engine.exe")
    unexpected.parent.mkdir(parents=True)
    unexpected.write_bytes(b"not a supported Stockfish filename")

    assert em.discover_stockfish(
        environ={"LOCALAPPDATA": str(local_app_data), "PATH": ""}) is None


def test_official_install_verifies_hash_extracts_and_installs_atomically(tmp_path, monkeypatch):
    payload = _zip_bytes({"stockfish/stockfish.exe": b"fake-engine", "stockfish/Copying.txt": b"GPL"})
    _patch_asset(monkeypatch, payload)
    monkeypatch.setattr(em, "verify_uci_engine", lambda path, timeout=5.0: {
        "path": str(path), "name": "Stockfish 19"})
    target = tmp_path / "ChessCoach" / "engines" / "stockfish" / "19"
    result = em.install_official_stockfish(destination=target)
    assert result["version"] == "19"
    assert result["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["license"] == "GPL-3.0"
    assert (target / "stockfish" / "stockfish.exe").read_bytes() == b"fake-engine"
    assert (target / "stockfish" / "Copying.txt").exists()


@pytest.mark.parametrize("payload", [b"not a zip", _zip_bytes({"../escape.exe": b"x"})])
def test_invalid_or_zip_slip_archive_never_installs(tmp_path, monkeypatch, payload):
    _patch_asset(monkeypatch, payload)
    monkeypatch.setattr(em, "verify_uci_engine", lambda *args, **kwargs: {"name": "fake"})
    target = tmp_path / "engines" / "19"
    with pytest.raises((zipfile.BadZipFile, ValueError)):
        em.install_official_stockfish(destination=target)
    assert not target.exists()
    assert not (tmp_path / "escape.exe").exists()


def test_install_rejects_unbounded_zip_entry_count(tmp_path, monkeypatch):
    payload = _zip_bytes({f"files/{index}.txt": b"x" for index in range(em.MAX_ARCHIVE_FILES + 1)})
    monkeypatch.setattr(em, "STOCKFISH_SIZE", len(payload))
    monkeypatch.setattr(em, "STOCKFISH_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(em, "verify_uci_engine", lambda *_args, **_kwargs:
                        pytest.fail("UCI must not run for archive over entry limit"))
    _patch_asset(monkeypatch, payload)
    target = tmp_path / "engines" / "19"
    with pytest.raises(ValueError, match="элементов"):
        em.install_official_stockfish(destination=target)
    assert not target.exists()


def test_failed_uci_probe_leaves_installation_absent(tmp_path, monkeypatch):
    payload = _zip_bytes({"stockfish.exe": b"bad-engine"})
    _patch_asset(monkeypatch, payload)
    def fail_probe(*args, **kwargs): raise RuntimeError("UCI failed")
    monkeypatch.setattr(em, "verify_uci_engine", fail_probe)
    target = tmp_path / "engines" / "19"
    with pytest.raises(RuntimeError, match="UCI failed"):
        em.install_official_stockfish(destination=target)
    assert not target.exists()


def test_install_can_be_cancelled_before_network_request(tmp_path):
    from threading import Event
    stop = Event(); stop.set()
    with pytest.raises(InterruptedError):
        em.install_official_stockfish(destination=tmp_path / "engines" / "19",
                                      stop_event=stop,
                                      opener=lambda *_args, **_kwargs: pytest.fail("network called"))


def test_install_can_be_cancelled_during_download_without_partial_target(tmp_path, monkeypatch):
    from threading import Event
    payload = _zip_bytes({"stockfish.exe": b"fake"})
    _patch_asset(monkeypatch, payload)
    stop = Event()
    response = _Response(payload)
    read = response.read
    def read_then_cancel(size=-1):
        block = read(size)
        if block:
            stop.set()
        return block
    response.read = read_then_cancel
    target = tmp_path / "engines" / "19"
    with pytest.raises(InterruptedError):
        em.install_official_stockfish(destination=target, stop_event=stop,
                                      opener=lambda *_args, **_kwargs: response)
    assert not target.exists()


def test_install_rejects_redirect_to_untrusted_host_and_hash_mismatch(tmp_path, monkeypatch):
    payload = _zip_bytes({"stockfish.exe": b"fake"})
    _patch_asset(monkeypatch, payload)
    target = tmp_path / "engines" / "19"
    with pytest.raises(ValueError, match="домен"):
        em.install_official_stockfish(destination=target,
            opener=lambda *_args, **_kwargs: _Response(payload, url="https://example.com/engine.zip"))
    monkeypatch.setattr(em, "STOCKFISH_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        em.install_official_stockfish(destination=target)
    assert not target.exists()


def test_uci_verification_runs_readiness_handshake_and_hashes_executable(tmp_path, monkeypatch):
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"uci fixture")
    calls = []
    class FakeEngine:
        id = {"name": "Stockfish fixture"}
        def ping(self): calls.append("ready")
        def quit(self): calls.append("quit")
    monkeypatch.setattr(em.chess.engine.SimpleEngine, "popen_uci",
                        lambda command, **kwargs: (calls.append((command, kwargs["timeout"], kwargs["cwd"]))
                                                  or FakeEngine()))
    result = em.verify_uci_engine(executable)
    assert result["name"] == "Stockfish fixture"
    assert result["sha256"] == hashlib.sha256(b"uci fixture").hexdigest()
    assert calls[1:] == ["ready", "quit"]


def test_position_evaluator_uses_python_chess_analysis_and_returns_white_pov(monkeypatch, tmp_path):
    class Score:
        def pov(self, color): return self
        def score(self, mate_score=None): return 35
        def mate(self): return None
    class Analysis:
        info = {"score": Score(), "pv": [chess.Move.from_uci("e2e4")],
                "depth": 12, "nodes": 1000}
        def __iter__(self): return iter([self.info])
        def wait(self): return type("Best", (), {"move": chess.Move.from_uci("e2e4")})()
        def stop(self): pass
    class Engine:
        def analysis(self, board, limit, **kwargs):
            assert board.fen() == chess.Board().fen()
            assert limit.time == 2.0
            assert kwargs["info"] == chess.engine.INFO_ALL
            return Analysis()
        def quit(self): pass
    import chess_coach.position_analysis as position_module
    monkeypatch.setattr(position_module, "popen_uci_hidden", lambda *args, **kwargs: Engine())
    result = evaluate_position(str(tmp_path / "stockfish.exe"), chess.Board(),
                               threading.Event(), {})
    assert result["score_white_cp"] == 35
    assert result["best_move"] == "e2e4"
    assert result["depth"] == 12
    # The process is deliberately retained for the next position in this session.
    from chess_coach.position_analysis import close_position_engines
    close_position_engines()


def test_position_evaluator_stops_stale_analysis(monkeypatch, tmp_path):
    stop = threading.Event()
    calls = []
    class Analysis:
        info = {}
        def __iter__(self): return iter(())
        def wait(self): return type("Best", (), {"move": None})()
        def stop(self): calls.append("stop")
    class Engine:
        def analysis(self, *_args, **_kwargs):
            stop.set()
            return Analysis()
        def quit(self): calls.append("quit")
    import chess_coach.position_analysis as position_module
    monkeypatch.setattr(position_module, "popen_uci_hidden", lambda *args, **kwargs: Engine())
    result = evaluate_position(str(tmp_path / "stockfish.exe"), chess.Board(), stop, {})
    assert result == {"cancelled": True}
    assert calls == ["stop"]
    from chess_coach.position_analysis import close_position_engines
    close_position_engines()
    assert calls[-1] == "quit"


def test_uci_launch_options_hide_console_on_windows():
    import os
    from chess_coach.uci import hidden_process_options
    options = hidden_process_options()
    if os.name == "nt":
        assert options["creationflags"] & __import__("subprocess").CREATE_NO_WINDOW
        assert options["startupinfo"].dwFlags & __import__("subprocess").STARTF_USESHOWWINDOW
        assert options["startupinfo"].wShowWindow == __import__("subprocess").SW_HIDE
    else:
        assert options == {}


def test_stockfish_manager_owns_engine_release_and_refuses_creation_after_shutdown(tmp_path):
    import chess_coach.engine_manager as manager_module
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic executable")
    calls = []

    class FakeEngine:
        id = {"name": "Synthetic UCI"}
        def quit(self):
            calls.append("quit")

    manager = manager_module.StockfishManager(
        engine_factory=lambda command, **kwargs: (
            calls.append((command, kwargs["timeout"], kwargs["cwd"])) or FakeEngine()))
    engine = manager.open_engine(executable)
    assert engine.id["name"] == "Synthetic UCI"
    assert manager.active_process_count == 1
    engine.quit()
    engine.quit()
    assert calls.count("quit") == 1
    assert manager.active_process_count == 0
    manager.close_all()
    with pytest.raises(RuntimeError, match="shutting down"):
        manager.open_engine(executable)


def test_stockfish_manager_reuses_position_engine_and_closes_it_on_shutdown(tmp_path):
    import chess_coach.engine_manager as manager_module
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic executable")
    started, stopped = [], []

    class FakeEngine:
        def quit(self):
            stopped.append(True)

    manager = manager_module.StockfishManager(
        engine_factory=lambda command, **_kwargs: (started.append(command) or FakeEngine()))
    first = manager.position_engine(executable)
    first.quit()
    second = manager.position_engine(executable)
    assert len(started) == manager.active_process_count == 1
    second.quit()
    manager.close_all()
    assert stopped == [True]
    assert manager.active_process_count == 0


def test_stockfish_manager_bounds_all_analysis_modes_and_rechecks_safety_after_wait(tmp_path):
    import time
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic")
    started, stopped = [], []
    lock = threading.Lock()
    active = maximum = 0
    first_ready, release_first, second_done = threading.Event(), threading.Event(), threading.Event()
    denied = []

    class FakeEngine:
        id = {"name": "Synthetic"}
        def __init__(self, index): self.index = index
        def quit(self):
            nonlocal active
            with lock:
                active -= 1
            stopped.append(self.index)

    def factory(*_args, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            index = len(started)
            started.append(index)
        return FakeEngine(index)

    manager = em.StockfishManager(factory, max_concurrent=1)
    first = manager.open_engine(executable)

    def queued_open():
        try:
            manager.open_engine(executable, safety_check=lambda: False)
        except Exception as exc:
            denied.append(exc)
        finally:
            second_done.set()

    thread = threading.Thread(target=queued_open)
    thread.start()
    deadline = time.monotonic() + 1
    while manager.active_analysis_count != 1 and time.monotonic() < deadline:
        time.sleep(.005)
    first_ready.set()
    first.quit()
    thread.join(1)
    assert not thread.is_alive()
    assert second_done.is_set()
    assert any(isinstance(error, InterruptedError) for error in denied)
    assert started == [0], "unsafe waiter must be rejected before engine creation"
    assert maximum == 1 and manager.active_analysis_count == 0
    assert stopped == [0]
    manager.close_all()


def test_stockfish_manager_cancelled_waiter_never_creates_engine_and_position_shares_gate(tmp_path):
    import time
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic")
    starts, quits = [], []

    class FakeEngine:
        def quit(self): quits.append(True)

    manager = em.StockfishManager(
        lambda *_args, **_kwargs: (starts.append(True) or FakeEngine()))
    lease = manager.position_engine(executable)
    stop = threading.Event()
    errors = []

    def waiting_position():
        try:
            manager.position_engine(executable, stop_event=stop)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=waiting_position)
    thread.start()
    deadline = time.monotonic() + 1
    while manager.active_analysis_count != 1 and time.monotonic() < deadline:
        time.sleep(.005)
    stop.set()
    thread.join(1)
    assert not thread.is_alive()
    assert any(isinstance(error, InterruptedError) for error in errors)
    assert starts == [True]
    lease.quit()
    position = manager.position_engine(executable)
    assert starts == [True], "position evaluation should reuse its cached process"
    assert manager.active_analysis_count == 1
    position.quit()
    assert manager.active_analysis_count == 0
    manager.close_all()
    assert len(quits) == 1


def test_stockfish_pre_cancelled_jobs_and_safety_denial_do_not_create_engine(tmp_path):
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic")
    starts = []
    manager = em.StockfishManager(
        lambda *_args, **_kwargs: (starts.append(True) or object()))
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        manager.open_engine(executable, stop_event=stop)
    with pytest.raises(InterruptedError):
        manager.open_engine(executable, safety_check=lambda: None)
    assert starts == []
    assert manager.active_analysis_count == 0


def test_stockfish_manager_reuses_the_position_process_for_review_and_batch(tmp_path):
    executable = tmp_path / "stockfish.exe"
    executable.write_bytes(b"synthetic")
    starts, quits = [], []

    class FakeEngine:
        def quit(self): quits.append(True)

    manager = em.StockfishManager(
        lambda *_args, **_kwargs: (starts.append(True) or FakeEngine()))
    position = manager.position_engine(executable)
    position.quit()
    review = manager.open_engine(executable)
    assert len(starts) == 1
    assert manager.active_process_count == 1
    review.quit()  # cached process remains available for the next sequential use
    next_position = manager.position_engine(executable)
    assert len(starts) == 1
    next_position.quit()
    manager.close_all()
    assert quits == [True]


def test_position_evaluator_reuses_one_engine_for_a_local_session(monkeypatch, tmp_path):
    import chess_coach.position_analysis as position_module
    position_module.close_position_engines()
    starts, quits = [], []
    class Score:
        def pov(self, _color): return self
        def score(self, mate_score=None): return 0
        def mate(self): return None
    class Analysis:
        def __init__(self):
            self.info = {"score": Score(), "pv": [chess.Move.from_uci("e2e4")], "depth": 1}
        def __iter__(self): return iter([self.info])
        def wait(self): return type("Best", (), {"move": chess.Move.from_uci("e2e4")})()
        def stop(self): pass
    class Engine:
        def analysis(self, *_args, **_kwargs): return Analysis()
        def quit(self): quits.append("quit")
    engine = Engine()
    monkeypatch.setattr(position_module, "popen_uci_hidden",
                        lambda *args, **kwargs: (starts.append(args[0]) or engine))
    path = str(tmp_path / "stockfish.exe")
    updates = []
    first = position_module.evaluate_position(
        path, chess.Board(), threading.Event(), {}, on_update=updates.append)
    second = position_module.evaluate_position(path, chess.Board(), threading.Event(), {})
    assert first["depth"] == second["depth"] == 1
    assert len(updates) == 1 and updates[0]["depth"] == 1
    assert len(starts) == 1
    position_module.close_position_engines()
    assert quits == ["quit"]


def test_position_evaluator_never_serializes_mate_as_synthetic_cp(monkeypatch, tmp_path):
    import chess_coach.position_analysis as position_module
    position_module.close_position_engines()

    class Analysis:
        info = {"score": chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE),
                "pv": [chess.Move.from_uci("e2e4")], "depth": 22}
        def __iter__(self): return iter([self.info])
        def wait(self): return type("Best", (), {"move": chess.Move.from_uci("e2e4")})()
        def stop(self): pass

    class Engine:
        def analysis(self, *_args, **_kwargs): return Analysis()
        def quit(self): pass

    monkeypatch.setattr(position_module, "popen_uci_hidden",
                        lambda *_args, **_kwargs: Engine())
    result = position_module.evaluate_position(
        str(tmp_path / "synthetic-fish.exe"), chess.Board(), threading.Event(), {})
    assert result["score_white_cp"] is None
    assert result["mate_white"] == 3
    assert result["depth"] == 22
