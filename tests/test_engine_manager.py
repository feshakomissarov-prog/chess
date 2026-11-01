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
                        lambda command, timeout, cwd: (calls.append((command, timeout, cwd)) or FakeEngine()))
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
        def wait(self): return type("Best", (), {"move": chess.Move.from_uci("e2e4")})()
        def stop(self): pass
    class Engine:
        def analysis(self, board, limit):
            assert board.fen() == chess.Board().fen()
            assert limit.time == 2.0
            return Analysis()
        def quit(self): pass
    monkeypatch.setattr("chess_coach.position_analysis.chess.engine.SimpleEngine.popen_uci",
                        lambda *args, **kwargs: Engine())
    result = evaluate_position(str(tmp_path / "stockfish.exe"), chess.Board(),
                               threading.Event(), {})
    assert result["score_white_cp"] == 35
    assert result["best_move"] == "e2e4"
    assert result["depth"] == 12


def test_position_evaluator_stops_stale_analysis(monkeypatch, tmp_path):
    stop = threading.Event()
    calls = []
    class Analysis:
        info = {}
        def wait(self): return type("Best", (), {"move": None})()
        def stop(self): calls.append("stop")
    class Engine:
        def analysis(self, *_args, **_kwargs):
            stop.set()
            return Analysis()
        def quit(self): calls.append("quit")
    monkeypatch.setattr("chess_coach.position_analysis.chess.engine.SimpleEngine.popen_uci",
                        lambda *args, **kwargs: Engine())
    result = evaluate_position(str(tmp_path / "stockfish.exe"), chess.Board(), stop, {})
    assert result == {"cancelled": True}
    assert calls == ["stop", "quit"]
