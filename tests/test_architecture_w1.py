"""W1 domain contracts and import-boundary regression tests."""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chess_coach.domain import (
    AnalysisScore,
    AnalysisSource,
    ClockSnapshot,
    GameRef,
    GameSnapshot,
    JobContext,
    ScoreKind,
    SourceCoverage,
)
from chess_coach import bootstrap


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chess_coach"


def test_domain_value_objects_validate_identity_and_coverage():
    with pytest.raises(ValueError):
        GameRef("account", "game", 0, "hash")
    with pytest.raises(ValueError):
        GameRef("", "game", 1, "hash")
    with pytest.raises(ValueError):
        ClockSnapshot(-1, 500, True)
    with pytest.raises(ValueError):
        SourceCoverage(AnalysisSource.LICHESS, "api-v1", 2, 1)
    with pytest.raises(ValueError):
        JobContext("", "archive", 1)


def test_analysis_score_models_cp_mate_and_missing_without_chess_library_objects():
    assert AnalysisScore.cp(34) == AnalysisScore(ScoreKind.CP, 34, "white")
    assert AnalysisScore.mate(0, "black").kind is ScoreKind.MATE
    assert AnalysisScore.missing().value is None
    with pytest.raises(ValueError):
        AnalysisScore(ScoreKind.MISSING, 0)


def test_game_snapshot_is_immutable_and_contains_line_identity():
    ref = GameRef("acct", "game", 3, "sha256:line")
    snapshot = GameSnapshot(
        ref, "lichess", "finished", "startfen", ("e2e4",),
        datetime(2026, 11, 3, tzinfo=timezone.utc),
    )
    assert snapshot.ref.line_revision == 3
    assert snapshot.moves_uci == ("e2e4",)
    with pytest.raises((AttributeError, TypeError)):
        snapshot.status = "active"


def _imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_domain_has_no_ui_infrastructure_or_external_io_imports():
    forbidden_prefixes = (
        "PySide6", "sqlite3", "requests", "urllib", "httpx", "chess_coach.ui",
        "chess_coach.infrastructure", "chess_coach.lichess", "chess_coach.bootstrap",
    )
    for path in (SRC / "domain").glob("*.py"):
        imports = _imports(path)
        violations = {name for name in imports
                      if any(name == prefix or name.startswith(prefix + ".")
                             for prefix in forbidden_prefixes)}
        assert not violations, f"{path.name} has forbidden imports: {sorted(violations)}"


def test_application_package_starts_as_framework_independent_boundary():
    forbidden_prefixes = (
        "PySide6", "sqlite3", "requests", "urllib", "httpx", "chess_coach.ui",
        "chess_coach.infrastructure", "chess_coach.lichess", "chess_coach.bootstrap",
    )
    for path in (SRC / "application").glob("*.py"):
        imports = _imports(path)
        violations = {name for name in imports
                      if any(name == prefix or name.startswith(prefix + ".")
                             for prefix in forbidden_prefixes)}
        assert not violations, f"{path.name} has forbidden imports: {sorted(violations)}"


def test_infrastructure_never_imports_ui_modules():
    for path in (SRC / "infrastructure").rglob("*.py"):
        imports = _imports(path)
        violations = {name for name in imports
                      if name == "chess_coach.ui" or name.startswith("chess_coach.ui.")}
        assert not violations, f"{path.relative_to(SRC)} imports UI: {sorted(violations)}"


def test_offline_runtime_uses_and_cleans_only_its_temporary_profile(monkeypatch):
    observed = {}

    class FakeRepository:
        def __init__(self, path, *, async_writes):
            observed["db_path"] = Path(path)
            observed["async_writes"] = async_writes

        def shutdown(self):
            observed["shutdown"] = True

    monkeypatch.setattr(bootstrap, "GameRepository", FakeRepository)
    runtime = bootstrap.create_runtime(offline_mode=True, asynchronous_storage=True)
    root = runtime.data_root
    try:
        assert runtime.repository is not None
        assert observed["db_path"].parent == root
        assert observed["db_path"].name == "games.sqlite3"
        assert observed["async_writes"] is True
        assert runtime.create_lichess_service(None) is None
        assert Path(runtime.settings.fileName()) == root / "settings.ini"
    finally:
        runtime.close()
    assert observed["shutdown"] is True
    assert not root.exists()


def test_manual_connect_runtime_creates_one_service_only_on_user_intent(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    constructed = []

    class FakeCredentialStore:
        PERSONAL_TOKEN_USER = "personal"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeLichessService:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    auth_module = __import__("chess_coach.lichess.auth", fromlist=["WindowsCredentialStore"])
    service_module = __import__("chess_coach.lichess.service", fromlist=["LichessService"])
    monkeypatch.setattr(auth_module, "WindowsCredentialStore", FakeCredentialStore)
    monkeypatch.setattr(service_module, "LichessService", FakeLichessService)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    runtime = bootstrap.ApplicationRuntime(
        settings, tmp_path, object(), False, True)

    assert runtime.create_lichess_service(None) is None
    service = runtime.create_lichess_service(None, user_requested=True)
    assert service is runtime.create_lichess_service(None, user_requested=True)
    assert len(constructed) == 1
    assert constructed[0]["repository"] is runtime.repository
