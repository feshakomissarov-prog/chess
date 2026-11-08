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
    window._external_study_owner = (4, "paste", 1, "", None,
                                    window.analysis_page.breakdown_page)
    window_module_parsed = {"games": [
        {"headers": {}, "moves": ["e2e4"], "initial_fen": "startpos"},
        {"headers": {}, "moves": ["d2d4"], "initial_fen": "startpos"},
    ]}
    window._external_study_parse_finished(4, window_module_parsed, None)
    assert window.analysis_page.candidate_list.count() == 2
    assert window.review_workspace.review is None
    window.close()


@pytest.mark.parametrize("source", ["paste", "file"])
def test_multiple_pgn_from_paste_and_file_share_inline_chooser(qtbot, tmp_path, source):
    window = MainWindow(QSettings(str(tmp_path / f"multi-{source}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    pgn = ('[Event "First"]\n[Result "*"]\n\n1. e4 e5 *\n\n'
           '[Event "Second"]\n[Result "*"]\n\n1. d4 d5 *')
    draft = "preserved paste draft"
    window.analysis_page.external_text.setPlainText(draft)
    if source == "paste":
        assert window.open_external_study_text(pgn)
    else:
        path = tmp_path / "two-games.pgn"
        path.write_text(pgn, encoding="utf-8")
        window._start_external_study_load(file_path=str(path))
    qtbot.waitUntil(lambda: window.analysis_page.candidate_list.count() == 2, timeout=5000)
    assert window._external_study_owner[1] == source
    assert window.analysis_page.external_text.toPlainText() == draft
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
    assert window._external_review_origin is None
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
    assert window._external_review_origin["source"] == "editor"
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
    assert window._external_study_owner[1] == "paste"
    assert entered.wait(2)
    window.analysis_page.tabs.setCurrentIndex(2)
    release.set()
    qtbot.waitUntil(lambda: not window._external_study_handle.is_alive(), timeout=5000)
    assert window.screen_stack.currentWidget() is window.analysis_page
    assert window.review_workspace.review is None
    window.close()


@pytest.mark.parametrize("return_to_landing", ["breadcrumb", "show_analysis"])
def test_external_parser_callback_cannot_steal_analysis_landing_route(
    qtbot, tmp_path, monkeypatch, return_to_landing
):
    import threading
    import chess_coach.window as window_module

    window = MainWindow(QSettings(str(tmp_path / f"external-landing-{return_to_landing}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_analysis()
    window.analysis_page.show_breakdown(1)
    entered, release = threading.Event(), threading.Event()
    parsed = {"games": [
        {"initial_fen": "startpos", "moves": ["e2e4"], "headers": {}},
        {"initial_fen": "startpos", "moves": ["d2d4"], "headers": {}},
    ]}
    monkeypatch.setattr(window_module, "_parse_external_study_text", lambda _text: (
        entered.set(), release.wait(3), parsed)[-1])
    engine_calls = []
    monkeypatch.setattr(window.engine_analysis, "analyze_completed_game",
                        lambda *args, **kwargs: engine_calls.append((args, kwargs)))
    window.analysis_page.external_text.setPlainText("draft preserved")
    assert window.open_external_study_text("1. e4")
    assert entered.wait(2)
    old_generation = window._external_study_generation

    if return_to_landing == "breadcrumb":
        window.analysis_page.back_button.click()
    else:
        # This route is important when the shell is already on Analysis: the
        # outer QStackedWidget emits no currentChanged signal.
        window.show_analysis()
    assert window.analysis_page.workspace.currentIndex() == 0
    assert window.analysis_page.external_text.toPlainText() == "draft preserved"
    release.set()
    qtbot.waitUntil(lambda: not window._external_study_handle.is_alive(), timeout=5000)
    qtbot.wait(30)
    assert window.screen_stack.currentWidget() is window.analysis_page
    assert window.analysis_page.workspace.currentIndex() == 0
    assert window.analysis_page.candidate_list.count() == 0
    assert window.review_workspace.review is None
    assert engine_calls == []

    # Simulate completion already queued in Qt before the user reopens tab 1.
    window.analysis_page.show_breakdown(1)
    window._external_study_parse_finished(old_generation, parsed, None)
    assert window.analysis_page.candidate_list.count() == 0
    assert window.review_workspace.review is None
    assert engine_calls == []
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
    window._external_study_owner = (2, "paste", 1, "", None,
                                    window.analysis_page.breakdown_page)
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


def test_human_rematch_prefills_confirmed_custom_clock_color_and_rated(qtbot, tmp_path,
                                                                       monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-human-confirmed-rematch.ini"),
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
            return "request-custom-human"

    service = FakeService()
    window.lichess = service
    session = LichessGameSession("rematch-human-confirmed", window.game_repository)
    session.opponent_info = {"id": "opponent", "username": "VerifiedOpponent"}
    session.game_source = "lobby"
    session.status = "mate"
    session.awaiting_authoritative_snapshot = False
    session.color = chess.BLACK
    session.clock_initial = 7 * 60 * 1000
    session.white_inc = session.black_inc = 3 * 1000
    session.clock_metadata_invalid = False
    session.rated = True
    session.rated_metadata_valid = True
    window.online_session = session

    window._challenge_again()
    page = window._play_mode_page
    assert page.mode_selector.currentData() == "challenge"
    assert page.username.text() == "VerifiedOpponent"
    assert page.color.currentData() == "black"
    assert page.clock.currentData() == ("custom", None)
    assert page.custom_minutes.value() == 7
    assert page.custom_increment.value() == 3
    assert page.rated.isChecked()
    assert service.calls == []

    page.start_button.click()
    assert service.calls == [("VerifiedOpponent", "black", 420, 3, True)]
    window.close()


def test_ai_rematch_prefills_confirmed_custom_clock_and_sends_exact_values(qtbot, tmp_path,
                                                                           monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-ai-custom-rematch.ini"),
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
            return "request-custom-ai"

    service = FakeService()
    window.lichess = service
    session = LichessGameSession("rematch-ai-custom", window.game_repository)
    session.game_source = "ai"
    session.opponent_info = {"id": None, "ai": 7}
    session.status = "mate"
    session.awaiting_authoritative_snapshot = False
    session.color = chess.WHITE
    session.clock_initial = 11 * 60 * 1000
    session.white_inc = session.black_inc = 5 * 1000
    session.clock_metadata_invalid = False
    window.online_session = session

    window._challenge_again()
    page = window._play_mode_page
    assert page.level.currentData() == 7
    assert page.color.currentData() == "white"
    assert page.clock.currentData() == ("custom", None)
    assert page.custom_minutes.value() == 11
    assert page.custom_increment.value() == 5
    assert service.calls == []

    page.start_button.click()
    assert service.calls == [(7, "white", 660, 5)]
    window.close()


def test_rematch_preserves_preferences_for_unconfirmed_or_unrepresentable_clock(
    qtbot, tmp_path, monkeypatch
):
    settings = QSettings(str(tmp_path / "play-unknown-clock.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings, offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)
    page = window._play_mode_page
    saved_5_0_index = next(i for i in range(page.clock.count())
                           if page.clock.itemData(i) == (300, 0))
    page.select_mode("challenge")
    page.clock.setCurrentIndex(saved_5_0_index)
    page.rated.setChecked(True)
    page.select_mode("ai")

    session = LichessGameSession("rematch-human-unknown-clock", window.game_repository)
    session.opponent_info = {"id": "opponent", "username": "VerifiedOpponent"}
    session.game_source = "lobby"
    session.status = "mate"
    session.awaiting_authoritative_snapshot = False
    session.clock_initial = 0
    session.clock_metadata_invalid = False
    window.online_session = session
    window._challenge_again()
    assert page.clock.currentData() == (300, 0)
    assert page.rated.isChecked()  # unknown rated flag keeps the saved preference
    assert "не подтверждено" in page.form_status.text()

    # A valid server clock that this minute-only custom control cannot encode
    # is stated truthfully; it must not silently become 3+2 or another preset.
    session.clock_initial = 7 * 60 * 1000 + 30 * 1000
    session.white_inc = 2 * 1000
    session.clock_metadata_invalid = False
    window._challenge_again()
    assert page.clock.currentData() == (300, 0)
    assert "7:30 + 2 сек" in page.form_status.text()
    window.close()


def test_rematch_keeps_exact_confirmed_preset_clock(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "play-preset-rematch.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_refresh_active_games_for_play", lambda **_kwargs: None)
    session = LichessGameSession("rematch-human-preset", window.game_repository)
    session.opponent_info = {"id": "opponent", "username": "VerifiedOpponent"}
    session.game_source = "lobby"
    session.status = "mate"
    session.awaiting_authoritative_snapshot = False
    session.clock_initial = 5 * 60 * 1000
    session.white_inc = 0
    session.clock_metadata_invalid = False
    window.online_session = session
    window._challenge_again()
    assert window._play_mode_page.clock.currentData() == (300, 0)
    assert window._play_mode_page.clock.currentText() == "5 + 0"
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
