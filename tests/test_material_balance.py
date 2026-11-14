from __future__ import annotations

import chess
from PySide6.QtCore import QSettings

from chess_coach.bootstrap import ApplicationRuntime
from chess_coach.domain.material_balance import (
    format_material_balance,
    material_balance,
)
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.session import LichessGameSession
from chess_coach.window import MainWindow


def _play(board: chess.Board, moves: str) -> chess.Board:
    for uci in moves.split():
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        board.push(move)
    return board


def test_material_balance_uses_piece_values_and_player_perspective():
    assert material_balance(chess.Board()) == 0
    after_pawn = _play(chess.Board(), "e2e4 d7d5 e4d5")
    assert material_balance(after_pawn, chess.WHITE) == 1
    assert material_balance(after_pawn, chess.BLACK) == -1

    # Same board with a captured white bishop and a captured black pawn.
    uneven = chess.Board()
    uneven.remove_piece_at(chess.C1)
    uneven.remove_piece_at(chess.A7)
    assert uneven.is_valid()
    assert material_balance(uneven, chess.WHITE) == -2
    assert material_balance(uneven, chess.BLACK) == 2
    assert format_material_balance(-2, player_color=chess.WHITE) == "Материал: -2 · за вас"
    assert format_material_balance(2, player_color=None) == "Материал: +2 · Белые − Чёрные"


def test_material_balance_handles_piece_values_en_passant_and_promotions():
    for piece_fen, expected in (("N", 3), ("B", 3), ("R", 5), ("Q", 9)):
        board = chess.Board(f"4k3/8/8/8/8/8/8/4K{piece_fen}2 w - - 0 1")
        assert board.is_valid()
        assert material_balance(board) == expected

    en_passant = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    assert en_passant.is_valid()
    assert material_balance(_play(en_passant, "e5d6"), chess.WHITE) == 1

    promotion = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    before = material_balance(promotion, chess.WHITE)
    assert material_balance(_play(promotion, "a7a8q"), chess.WHITE) - before == 8
    underpromotion = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    before = material_balance(underpromotion, chess.WHITE)
    assert material_balance(_play(underpromotion, "a7a8n"), chess.WHITE) - before == 2


def test_material_balance_rejects_invalid_and_chess960_positions():
    assert material_balance(chess.Board("8/8/8/8/8/8/8/8")) is None
    chess960 = chess.Board.from_chess960_pos(0)
    assert chess960.is_valid()
    assert material_balance(chess960) is None
    assert format_material_balance(None, player_color=None) == "Материал: н/д"


def test_main_window_material_uses_confirmed_online_board_and_player_side(qtbot, tmp_path):
    repository = GameRepository(tmp_path / "material-ui.sqlite3")
    settings = QSettings(str(tmp_path / "material-ui.ini"), QSettings.Format.IniFormat)
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    session = LichessGameSession("material-confirmation", repository)
    session.color = chess.BLACK
    session.bind_owner("synthetic", 1)
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 d7d5", "status": "started"}})
    window.online_session = session
    window.online_sessions[session.game_id] = session
    window._selected_live_game_id = session.game_id
    window._screen_mode = "online"
    window._event_stream_recovery_pending = False
    session.pending_uci = "e4d5"
    window.refresh()
    assert window.material_balance_label.text() == "Материал: 0 · за вас"

    session.pending_uci = None
    session.apply_event({"type": "gameState", "moves": "e2e4 d7d5 e4d5",
                         "status": "started"})
    window.refresh()
    assert window.material_balance_label.text() == "Материал: -1 · за вас"
    assert runtime.stockfish_manager._engines == {}
    window.close()
    runtime.close()
