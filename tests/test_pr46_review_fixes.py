from types import SimpleNamespace

import chess
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog

from chess_coach.ui.pages.analysis import AnalysisLandingPage
from chess_coach.window import EphemeralReviewRepository, MainWindow, SettingsDialog
from chess_coach.lichess.session import LichessGameSession


def test_multi_pgn_chooser_requires_row_selection_and_explicit_open(qtbot):
    opened = []
    page = AnalysisLandingPage({
        "games": lambda: None, "openings": lambda: None,
        "open_text": lambda value: opened.append(value),
        "open_file": lambda: None,
    })
    qtbot.addWidget(page)
    page.show_breakdown(1)
    games = [
        {"headers": {"White": "A", "Black": "B", "Result": "1-0"},
         "preview": "e4 e5", "initial_fen": "startpos", "moves": ["e2e4"]},
        {"headers": {"Result": "*"}, "preview": "d4 d5",
         "initial_fen": "startpos", "moves": ["d2d4"]},
    ]
    page.set_candidates(games)
    assert page.candidate_list.count() == 2
    page.candidate_list.setCurrentRow(1)
    assert opened == []
    page.open_text_button.click()
    assert opened == [games[1]]
    page.clear_candidates()
    assert page.candidate_list.count() == 0
    page.external_text.setPlainText("paste draft preserved")
    page.set_candidates(games)
    page.clear_candidates()
    assert page.external_text.toPlainText() == "paste draft preserved"


def test_multi_game_parse_uses_inline_chooser_without_modal(qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    window = MainWindow(QSettings(str(tmp_path / "inline-chooser.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("modal chooser used")))
    window._external_study_generation = 4
    window._external_study_owner = (4, 1, "", None)
    window_module_parsed = {"games": [
        {"headers": {}, "moves": ["e2e4"], "initial_fen": "startpos"},
        {"headers": {}, "moves": ["d2d4"], "initial_fen": "startpos"},
    ]}
    window._external_study_parse_finished(4, window_module_parsed, None)
    assert window.analysis_page.candidate_list.count() == 2
    assert window.review_workspace.review is None
    window.close()


def test_external_review_back_restores_import_tab_and_text(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "external-back.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    draft = "1. e4 e5"
    window.analysis_page.external_text.setPlainText(draft)
    assert window._open_external_study_candidate({
        "initial_fen": "startpos", "moves": ["e2e4", "e7e5"],
        "headers": {}, "preview": "e4 e5",
    })
    window._return_from_review()
    assert window.screen_stack.currentWidget() is window.analysis_page
    assert window.analysis_page.workspace.currentWidget() is window.analysis_page.breakdown_page
    assert window.analysis_page.tabs.currentIndex() == 1
    assert window.analysis_page.external_text.toPlainText() == draft
    window.close()


def test_editor_review_back_restores_editor_source_tab(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "editor-back.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(2)
    assert window._open_external_study_candidate({
        "initial_fen": chess.STARTING_FEN, "moves": [], "headers": {},
    })
    window._return_from_review()
    assert window.analysis_page.tabs.currentIndex() == 2
    assert window.screen_stack.currentWidget() is window.analysis_page
    window.close()


def test_external_parser_callback_is_invalidated_on_nested_tab_change(qtbot, tmp_path,
                                                                        monkeypatch):
    import threading
    import chess_coach.window as window_module

    window = MainWindow(QSettings(str(tmp_path / "external-tab-owner.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(window_module, "_parse_external_study_text", lambda _text: (
        entered.set(), release.wait(3),
        {"initial_fen": "startpos", "moves": ["e2e4"], "headers": {}})[-1])
    assert window.open_external_study_text("1. e4")
    assert entered.wait(2)
    window.analysis_page.tabs.setCurrentIndex(2)
    release.set()
    qtbot.waitUntil(lambda: not window._external_study_handle.is_alive(), timeout=5000)
    assert window.screen_stack.currentWidget() is window.analysis_page
    assert window.review_workspace.review is None
    window.close()


def test_external_parser_callback_is_invalidated_on_auth_generation_change(qtbot, tmp_path,
                                                                            monkeypatch):
    import threading
    import chess_coach.window as window_module

    window = MainWindow(QSettings(str(tmp_path / "external-auth-owner.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    window.lichess = SimpleNamespace(username="owner", auth_generation=7)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(window_module, "_parse_external_study_text", lambda _text: (
        entered.set(), release.wait(3),
        {"initial_fen": "startpos", "moves": ["e2e4"], "headers": {}})[-1])
    assert window.open_external_study_text("1. e4")
    assert entered.wait(2)
    window.lichess.auth_generation = 8
    release.set()
    qtbot.waitUntil(lambda: not window._external_study_handle.is_alive(), timeout=5000)
    assert window.review_workspace.review is None
    assert window.screen_stack.currentWidget() is window.analysis_page
    window.close()


def test_older_external_parse_generation_cannot_replace_new_candidate_set(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "external-generation.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    window._external_study_generation = 2
    window._external_study_owner = (2, 1, "", None)
    window._external_study_parse_finished(
        1, {"initial_fen": "startpos", "moves": ["e2e4"], "headers": {}}, None)
    assert window.analysis_page.candidate_list.count() == 0
    assert window.review_workspace.review is None
    window.close()


def test_ephemeral_analysis_repository_keeps_results_out_of_personal_storage():
    repository = EphemeralReviewRepository("external:test", "startpos", "e2e4 e7e5")
    repository.bind_game({"game_id": "external:test", "status": "unknownFinish"})
    result_id = repository.save_analysis(
        "external:test",
        assessments=[{"ply": 0, "white_cp": 12}, {"ply": 1, "white_cp": 18},
                     {"ply": 2, "white_cp": 20}],
        classifications=[{"ply": 1, "mover": "white", "label": "good"},
                         {"ply": 2, "mover": "black", "label": "good"}],
    )
    assert result_id == "ephemeral"
    detail = repository.latest_analysis_detail("external:test")
    assert detail["assessments"][1]["white_cp"] == 18
    assert len(detail["move_metrics"]) == 2
    assert repository.get_game("not-external") is None


@pytest.mark.parametrize(("study", "expected_positions"), [
    ({"initial_fen": "startpos", "moves": ["e2e4", "e7e5"], "headers": {}}, 3),
    ({"initial_fen": chess.STARTING_FEN, "moves": [], "headers": {}}, 1),
])
def test_explicit_external_open_queues_transient_analysis_for_mainline_or_position(
    qtbot, tmp_path, monkeypatch, study, expected_positions
):
    window = MainWindow(QSettings(str(tmp_path / "external-engine-route.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    engine = tmp_path / "verified-stockfish.exe"
    engine.write_bytes(b"test-only fake engine path")
    window.settings.setValue("stockfish_path", str(engine))
    window.settings.setValue("stockfish_verified_path", str(engine))
    observed = []

    def analyze(repository, game_id, *_args, **_kwargs):
        assert isinstance(repository, EphemeralReviewRepository)
        line = repository.current_line(game_id)
        observed.append((repository, line))
        count = len(line["moves"].split()) + 1
        assessments = [{"ply": ply, "white_cp": ply * 5, "depth": 12}
                       for ply in range(count)]
        classifications = [{"ply": ply, "mover": "white" if ply % 2 else "black",
                            "label": "good"} for ply in range(1, count)]
        repository.save_analysis(game_id, assessments=assessments,
                                 classifications=classifications)
        return {"assessments": assessments}

    monkeypatch.setattr(window.engine_analysis, "analyze_completed_game", analyze)
    assert window._open_external_study_candidate(study)
    qtbot.waitUntil(lambda: bool(observed) and window._analysis_thread is not None
                    and not window._analysis_thread.is_alive(), timeout=5000)
    qtbot.waitUntil(lambda: window.review_workspace.position_evaluations_available, timeout=5000)
    assert len(observed[0][1]["moves"].split()) + 1 == expected_positions
    assert window.game_repository.get_game(window.review_workspace.review["game"]["game_id"]) is None
    assert window.review_workspace.review["analysis"]["source"] == "stockfish"
    window.close()


def test_external_analysis_setting_defaults_on_and_persists_explicit_off(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "external-setting.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.auto_external_analysis.isChecked()
    dialog.auto_external_analysis.setChecked(False)
    dialog.save(settings)
    assert settings.value("analysis/auto_external_studies", True, type=bool) is False
    reloaded = SettingsDialog(settings)
    qtbot.addWidget(reloaded)
    assert not reloaded.auto_external_analysis.isChecked()
    dialog.close()
    reloaded.close()


def test_external_auto_off_keeps_manual_analysis_cta_and_reports_missing_engine(
    qtbot, tmp_path
):
    window = MainWindow(QSettings(str(tmp_path / "external-manual.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    window.settings.setValue("analysis/auto_external_studies", False)
    assert window._open_external_study_candidate({
        "initial_fen": "startpos", "moves": ["e2e4"], "headers": {},
    })
    assert not window.review_workspace.analysis_button.isHidden()
    window.review_workspace.analysis_button.click()
    assert window.review_workspace.training_status.text().startswith("NO_ENGINE")
    assert window._analysis_thread is None
    window.close()


def test_external_analysis_fails_closed_during_active_human_game(qtbot, tmp_path,
                                                                  monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "external-fair-play.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    engine = tmp_path / "verified-stockfish.exe"
    engine.write_bytes(b"test-only fake engine path")
    window.settings.setValue("stockfish_path", str(engine))
    window.settings.setValue("stockfish_verified_path", str(engine))
    monkeypatch.setattr(window, "_sessions_for_safety", lambda: [
        SimpleNamespace(status="playing", is_ai_opponent=False)])
    calls = []
    monkeypatch.setattr(window.engine_analysis, "analyze_completed_game",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    assert window._open_external_study_candidate({
        "initial_fen": "startpos", "moves": ["e2e4"], "headers": {},
    })
    assert window.review_workspace.training_status.text().startswith("BLOCKED_FAIR_PLAY")
    assert calls == []
    window.close()


def test_rematch_prefills_shared_play_form_without_create_request(qtbot, tmp_path,
                                                                  monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-rematch-prefill.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)
    calls = []
    window._create_play_request = lambda options: calls.append(options)
    session = LichessGameSession("rematch-ai", window.game_repository)
    session.game_source = "ai"
    session.opponent_info = {"id": None, "ai": 6}
    session.color = chess.BLACK
    session.status = "mate"
    window.online_session = session
    window.settings.setValue("workspace/ai_level", 6)
    window.settings.setValue("workspace/ai_color", "black")
    window._challenge_again()
    assert window.screen_stack.currentWidget() is window.play_page
    assert window._play_mode_page.mode_selector.currentData() == "ai"
    assert window._play_mode_page.level.currentData() == 6
    assert window._play_mode_page.color.currentData() == "black"
    assert calls == []
    window.close()


def test_ai_rematch_start_emits_one_explicit_create_request(qtbot, tmp_path,
                                                             monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-ai-start.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)

    class FakeService:
        auth_generation = 2
        username = "owner"
        def __init__(self):
            self.calls = []
        def create_ai_game(self, *args):
            self.calls.append(args)
            return "request-ai"

    service = FakeService()
    window.lichess = service
    session = LichessGameSession("rematch-ai", window.game_repository)
    session.game_source = "ai"
    session.opponent_info = {"id": None, "ai": 6}
    session.status = "mate"
    window.online_session = session
    window._challenge_again()
    assert service.calls == []
    window._play_mode_page.start_button.click()
    assert len(service.calls) == 1
    assert service.calls[0][0] == 6
    window.close()


def test_human_rematch_prefills_challenge_form_without_create_request(qtbot, tmp_path,
                                                                      monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-human-rematch.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)
    calls = []
    window._create_play_request = lambda options: calls.append(options)
    session = LichessGameSession("rematch-human", window.game_repository)
    session.opponent = "OpponentName"
    session.opponent_info = {"id": "opponent", "username": "OpponentName"}
    session.game_source = "lobby"
    session.status = "mate"
    window.online_session = session
    window._challenge_again()
    assert window.screen_stack.currentWidget() is window.play_page
    assert window._play_mode_page.mode_selector.currentData() == "challenge"
    assert window._play_mode_page.username.text() == "OpponentName"
    assert calls == []
    window.close()


def test_human_rematch_start_sends_exactly_one_explicit_challenge(qtbot, tmp_path,
                                                                  monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-human-start.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)

    class FakeService:
        auth_generation = 2
        username = "owner"
        def __init__(self):
            self.calls = []
        def challenge_username(self, *args):
            self.calls.append(args)
            return "request-1"

    service = FakeService()
    window.lichess = service
    session = LichessGameSession("rematch-human", window.game_repository)
    session.opponent = "OpponentName"
    session.opponent_info = {"id": "opponent", "username": "OpponentName"}
    session.game_source = "lobby"
    session.status = "mate"
    window.online_session = session
    window._challenge_again()
    assert service.calls == []
    window._play_mode_page.start_button.click()
    assert len(service.calls) == 1
    assert service.calls[0][0] == "OpponentName"
    window.close()


def test_play_challenge_invalid_username_is_blocked_before_request(qtbot, tmp_path,
                                                                    monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-invalid-username.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)

    class FakeService:
        auth_generation = 2
        username = "owner"
        calls = []
        def challenge_username(self, *args):
            self.calls.append(args)
            return "request-invalid"

    service = FakeService()
    window.lichess = service
    window.show_play()
    window._play_mode_page.select_mode("challenge")
    window._play_mode_page.username.setText("invalid name!")
    window._play_mode_page.start_button.click()
    assert service.calls == []
    assert "2–30" in window._play_mode_page.form_status.text()
    window.close()
