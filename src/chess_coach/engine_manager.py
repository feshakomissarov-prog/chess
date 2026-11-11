"""Safe, explicit Stockfish discovery, verification, and official install helpers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import threading
import time
import urllib.request
import uuid
from urllib.parse import urlparse
import zipfile

import chess.engine
from .uci import popen_uci_hidden


STOCKFISH_RELEASE = "19"
STOCKFISH_ASSET = "stockfish-windows-x86-64-universal.zip"
STOCKFISH_URL = (
    "https://github.com/official-stockfish/Stockfish/releases/download/sf_19/"
    + STOCKFISH_ASSET
)
# Published as the asset digest by the official GitHub release API, tag sf_19.
STOCKFISH_SHA256 = "3c8bf1f9ea66a09350a40df4f632288285ac206d99f33ab5842c408fc30b48a7"
STOCKFISH_SIZE = 81_431_614
MAX_ARCHIVE_SIZE = 100_000_000
MAX_UNPACKED_SIZE = 150_000_000
MAX_ARCHIVE_FILES = 1000


class _ManagedEngine:
    """Transparent python-chess engine proxy whose quit returns ownership to manager."""

    def __init__(self, manager: "StockfishManager", engine, *, release_process: bool = True) -> None:
        self._manager = manager
        self._engine = engine
        self._release_process = release_process
        self._closed = False
        self._close_lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def quit(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._manager.release(self._engine, release_process=self._release_process)


class StockfishManager:
    """Own UCI process creation, reuse, cancellation-safe release, and shutdown.

    Callers still control the search and stop it through python-chess Analysis.stop;
    the manager centralizes process ownership and refuses creation during shutdown.
    """

    def __init__(self, engine_factory=None, *, max_concurrent: int = 1) -> None:
        self._engine_factory = engine_factory or popen_uci_hidden
        self._lock = threading.Condition(threading.RLock())
        self._max_concurrent = max(1, int(max_concurrent))
        self._active_leases = 0
        self._engines: dict[int, object] = {}
        self._position_engines: dict[str, object] = {}
        self._closing = False

    def _admit(self, stop_event: threading.Event | None, safety_check) -> None:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Stockfish отменён до допуска к общему ресурсу.")
        with self._lock:
            while self._active_leases >= self._max_concurrent:
                if self._closing:
                    raise RuntimeError("Stockfish manager is shutting down")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stockfish отменён во время ожидания ресурса.")
                self._lock.wait(timeout=0.05)
            if self._closing:
                raise RuntimeError("Stockfish manager is shutting down")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Stockfish отменён до допуска к общему ресурсу.")
            self._active_leases += 1
        try:
            # This is deliberately after admission: a permission snapshot taken
            # before waiting for the shared engine is not sufficient.
            if safety_check is not None and safety_check() is not True:
                raise InterruptedError("Fair Play check failed after waiting for Stockfish.")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Stockfish отменён до создания процесса.")
        except BaseException:
            with self._lock:
                self._active_leases -= 1
                self._lock.notify_all()
            raise

    def _release_lease(self) -> None:
        with self._lock:
            self._active_leases = max(0, self._active_leases - 1)
            self._lock.notify_all()

    def open_engine(self, path: str | os.PathLike[str], *, timeout: float = 10.0,
                    stop_event: threading.Event | None = None, safety_check=None):
        self._admit(stop_event, safety_check)
        try:
            executable = Path(path).expanduser().resolve(strict=True)
            if not executable.is_file():
                raise FileNotFoundError("Укажите существующий локальный Stockfish executable.")
            with self._lock:
                if self._closing:
                    raise RuntimeError("Stockfish manager is shutting down")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stockfish отменён до создания процесса.")
                cached = self._position_engines.get(str(executable))
                if cached is not None:
                    return _ManagedEngine(self, cached, release_process=False)
                stale = tuple(self._position_engines.values())
                self._position_engines.clear()
                for cached_engine in stale:
                    self._engines.pop(id(cached_engine), None)
                    try:
                        cached_engine.quit()
                    except Exception:
                        pass
                engine = self._engine_factory(
                    str(executable), timeout=timeout, cwd=str(executable.parent))
                managed = _ManagedEngine(self, engine)
                self._engines[id(engine)] = engine
                return managed
        except BaseException:
            self._release_lease()
            raise

    def engine_factory(self, path: str, **kwargs) -> _ManagedEngine:
        return self.open_engine(path, **kwargs)

    def position_engine(self, path: str, *, stop_event=None, safety_check=None) -> _ManagedEngine:
        self._admit(stop_event, safety_check)
        try:
            executable = str(Path(path).expanduser().resolve(strict=True))
            with self._lock:
                if self._closing:
                    raise RuntimeError("Stockfish manager is shutting down")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stockfish отменён до создания процесса.")
                stale = tuple(engine for key, engine in self._position_engines.items()
                              if key != executable)
                for old_engine in stale:
                    self._position_engines = {
                        key: value for key, value in self._position_engines.items()
                        if value is not old_engine
                    }
                    self._engines.pop(id(old_engine), None)
                    try:
                        old_engine.quit()
                    except Exception:
                        pass
                engine = self._position_engines.get(executable)
                if engine is None:
                    engine = self._engine_factory(
                        executable, timeout=5.0, cwd=str(Path(executable).parent))
                    self._engines[id(engine)] = engine
                    self._position_engines[executable] = engine
                return _ManagedEngine(self, engine, release_process=False)
        except BaseException:
            with self._lock:
                self._active_leases -= 1
                self._lock.notify_all()
            raise

    def release(self, engine, *, release_process: bool = True) -> None:
        should_quit = False
        with self._lock:
            if release_process:
                owned = self._engines.pop(id(engine), None)
                if owned is not None:
                    should_quit = True
                    self._position_engines = {
                        path: value for path, value in self._position_engines.items()
                        if value is not engine
                    }
            self._active_leases = max(0, self._active_leases - 1)
            self._lock.notify_all()
        if should_quit:
            engine.quit()

    def close_all(self) -> None:
        with self._lock:
            self._closing = True
            self._lock.notify_all()
            engines = tuple(self._engines.values())
            self._engines.clear()
            self._position_engines.clear()
        for engine in engines:
            try:
                engine.quit()
            except Exception:
                pass

    @property
    def active_process_count(self) -> int:
        with self._lock:
            return len(self._engines)

    @property
    def active_analysis_count(self) -> int:
        with self._lock:
            return self._active_leases


def discover_stockfish(configured: str = "", *, environ: dict | None = None) -> str | None:
    """Find Stockfish only in explicit/user paths, PATH, and a short standard list."""
    env = os.environ if environ is None else environ
    candidates: list[Path] = []
    if configured.strip():
        candidates.append(Path(configured.strip()).expanduser())
    local = env.get("LOCALAPPDATA")
    if local:
        root = Path(local)
        managed_release = root / "ChessCoach" / "engines" / "stockfish" / "19"
        # The official Windows ZIP keeps the executable in a nested `stockfish/`
        # directory. Keep discovery bounded to documented names under our own
        # managed release root; never walk the user's disk looking for EXEs.
        candidates.extend(
            managed_release / "stockfish" / name
            for name in (
                "stockfish-windows-x86-64-universal.exe",
                "stockfish-windows-x86-64-avx2.exe",
                "stockfish-windows-x86-64-avx512.exe",
                "stockfish.exe",
            )
        )
        candidates.extend((managed_release / "stockfish.exe",
                           managed_release / "stockfish-windows-x86-64-avx2.exe",
                           root / "Programs" / "Stockfish" / "stockfish.exe"))
    for program_root in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)")):
        if program_root:
            candidates.append(Path(program_root) / "Stockfish" / "stockfish.exe")
    for name in ("stockfish.exe", "stockfish"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            candidates.append(Path(found))
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve(strict=True))
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(resolved)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and candidate.suffix.casefold() == ".exe":
            return resolved
    return None


def verify_uci_engine(path: str | os.PathLike[str], *, timeout: float = 5.0) -> dict:
    """Perform the python-chess UCI handshake, ready check, version read, and hash."""
    executable = Path(path).expanduser().resolve(strict=True)
    if not executable.is_file():
        raise FileNotFoundError(f"Файл движка не найден: {executable}")
    started = time.monotonic()
    engine = popen_uci_hidden(
        str(executable), timeout=timeout, cwd=str(executable.parent))
    try:
        engine.ping()
        name = str(engine.id.get("name") or "UCI engine")
    finally:
        engine.quit()
    if time.monotonic() - started > timeout + 1.0:
        raise TimeoutError("UCI handshake превысил допустимое время.")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    return {"path": str(executable), "name": name, "sha256": digest}


def install_official_stockfish(*, destination: str | os.PathLike[str],
                               stop_event=None, progress=None,
                               opener=None) -> dict:
    """Install the pinned official Stockfish 19 asset with hash and atomic rollback."""
    target = Path(destination).expanduser()
    opener = opener or urllib.request.urlopen
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".stockfish-install-", dir=parent))
    backup: Path | None = None

    def check_cancelled() -> None:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Установка Stockfish отменена.")

    try:
        check_cancelled()
        archive_path = work / STOCKFISH_ASSET
        digest = hashlib.sha256()
        total = 0
        request = urllib.request.Request(
            STOCKFISH_URL, headers={"User-Agent": "ChessCoach", "Accept": "application/octet-stream"})
        with opener(request, timeout=20) as response, archive_path.open("wb") as output:
            final_url = response.geturl()
            if not final_url.startswith("https://"):
                raise ValueError("Загрузка Stockfish разрешена только по HTTPS.")
            host = urlparse(final_url).hostname
            if host not in {"github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com"}:
                raise ValueError("Загрузка перенаправлена на неожиданный домен.")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) != STOCKFISH_SIZE:
                raise ValueError("Размер официального архива не совпадает с release metadata.")
            while True:
                check_cancelled()
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARCHIVE_SIZE:
                    raise ValueError("Архив превышает ограничение размера.")
                output.write(block)
                digest.update(block)
                if progress:
                    progress(f"Загружено: {total // (1024 * 1024)} МБ")
        if total != STOCKFISH_SIZE or digest.hexdigest() != STOCKFISH_SHA256:
            raise ValueError(
                "Stockfish 19 size/SHA-256 mismatch: "
                f"received {total} bytes, sha256={digest.hexdigest()}; "
                f"expected {STOCKFISH_SIZE} bytes, sha256={STOCKFISH_SHA256}.")
        check_cancelled()

        extracted = work / "extracted"
        extracted.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError(
                    f"Архив содержит {len(members)} элементов; лимит — {MAX_ARCHIVE_FILES}.")
            expanded_size = sum(member.file_size for member in members)
            if expanded_size > MAX_UNPACKED_SIZE:
                raise ValueError("Распакованный архив превышает ограничение размера.")
            for member in members:
                check_cancelled()
                path = PurePosixPath(member.filename)
                mode = (member.external_attr >> 16) & 0xFFFF
                if (path.is_absolute() or ".." in path.parts or not path.parts
                        or ":" in member.filename or "\\" in member.filename
                        or mode & 0o170000 == 0o120000):
                    raise ValueError("Архив содержит небезопасный путь или ссылку.")
                output = extracted.joinpath(*path.parts)
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, output.open("wb") as destination_file:
                    shutil.copyfileobj(source, destination_file, 1024 * 1024)
        engines = [path for path in extracted.rglob("*.exe")
                   if path.name.casefold().startswith("stockfish")]
        if len(engines) != 1:
            raise ValueError("В официальном архиве не найден однозначный Stockfish executable.")
        staged = work / "verified-install"
        shutil.copytree(extracted, staged)
        staged_engine = staged / engines[0].relative_to(extracted)
        verified = verify_uci_engine(staged_engine, timeout=5.0)
        check_cancelled()

        if target.exists():
            backup = parent / f".{target.name}.previous-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
            target.rename(backup)
        try:
            staged.rename(target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                backup.rename(target)
                backup = None
            raise
        installed_engine = target / engines[0].relative_to(extracted)
        verified["path"] = str(installed_engine.resolve())
        verified.update(version=STOCKFISH_RELEASE, archive_sha256=digest.hexdigest(),
                        license="GPL-3.0", source_url=STOCKFISH_URL)
        retained = 0
        for previous in parent.glob(f".{target.name}.previous-*"):
            if backup is not None and previous == backup:
                retained += 1
                continue
            try:
                if previous.is_dir():
                    shutil.rmtree(previous)
                else:
                    previous.unlink()
            except OSError:
                # A Windows process may still have a file open from an older
                # install. Keep that verified copy and report it for cleanup.
                retained += 1
        verified["previous_backups_retained"] = retained
        return verified
    finally:
        shutil.rmtree(work, ignore_errors=True)
