import chess

from chess_coach.controller import GameController


def test_legal_and_illegal_moves_keep_authoritative_state() -> None:
    game = GameController()
    initial = game.board.fen()
    assert not game.apply_move_intent(chess.Move.from_uci("e2e5"))
    assert game.board.fen() == initial
    assert game.apply_move_intent(chess.Move.from_uci("e2e4"))
    assert game.board.peek() == chess.Move.from_uci("e2e4")
    assert game.san_moves() == [(1, "e4", "")]


def test_history_snapshots_do_not_rewind_live_game() -> None:
    game = GameController()
    for uci in ("e2e4", "e7e5", "g1f3"):
        assert game.apply_move_intent(chess.Move.from_uci(uci))
    live_fen = game.board.fen()
    game.premove = chess.Move.from_uci("b8c6")
    game.go_to(1)
    assert game.premove is None
    snapshot = game.snapshot()
    assert not snapshot.live
    assert snapshot.board.fen() == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    assert game.board.fen() == live_fen
    assert not game.apply_move_intent(chess.Move.from_uci("e7e5"))
    game.go_live()
    assert game.apply_move_intent(chess.Move.from_uci("b8c6"))


def test_premove_is_separate_until_rechecked_and_applied() -> None:
    game = GameController(chess.WHITE)
    game.apply_move_intent(chess.Move.from_uci("e2e4"))
    confirmed_fen = game.board.fen()
    assert game.queue_premove(chess.Move.from_uci("g1f3"))
    assert game.board.fen() == confirmed_fen
    assert game.premove == chess.Move.from_uci("g1f3")
    accepted, message = game.apply_test_opponent_move(chess.Move.from_uci("e7e5"))
    assert accepted
    assert message == "Предход выполнен."
    assert game.board.peek() == chess.Move.from_uci("g1f3")
    assert game.premove is None


def test_invalid_premove_is_removed_after_opponent_captures_piece() -> None:
    game = GameController(chess.WHITE)
    game.board = chess.Board("4k3/8/8/8/8/4r3/4P3/4K3 b - - 0 1")
    assert game.queue_premove(chess.Move.from_uci("e2e4"))
    accepted, message = game.apply_test_opponent_move(chess.Move.from_uci("e3e2"))
    assert accepted
    assert message == "Предход отменён: недопустим."
    assert game.premove is None
    assert game.board.peek() == chess.Move.from_uci("e3e2")
    assert len(game.board.move_stack) == 1


def test_illegal_test_opponent_move_keeps_position_and_premove() -> None:
    game = GameController(chess.WHITE)
    game.apply_move_intent(chess.Move.from_uci("e2e4"))
    assert game.queue_premove(chess.Move.from_uci("g1f3"))
    before = game.board.fen()
    accepted, message = game.apply_test_opponent_move(chess.Move.from_uci("e7e4"))
    assert not accepted
    assert "недопустим" in message
    assert game.board.fen() == before
    assert game.premove == chess.Move.from_uci("g1f3")


def test_promotion_requires_explicit_piece_and_supports_underpromotion() -> None:
    game = GameController()
    game.board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
    before = game.board.fen()
    assert not game.apply_move_intent(chess.Move.from_uci("a7a8"))
    assert game.board.fen() == before
    assert game.apply_move_intent(chess.Move.from_uci("a7a8n"))
    assert game.board.piece_at(chess.A8) == chess.Piece(chess.KNIGHT, chess.WHITE)


def test_castling_and_en_passant_follow_python_chess_rules() -> None:
    castling = GameController()
    castling.board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert castling.apply_move_intent(chess.Move.from_uci("e1g1"))
    assert castling.board.piece_at(chess.G1) == chess.Piece(chess.KING, chess.WHITE)
    assert castling.board.piece_at(chess.F1) == chess.Piece(chess.ROOK, chess.WHITE)

    en_passant = GameController()
    en_passant.board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    assert en_passant.apply_move_intent(chess.Move.from_uci("e5d6"))
    assert en_passant.board.piece_at(chess.D6) == chess.Piece(chess.PAWN, chess.WHITE)
    assert en_passant.board.piece_at(chess.D5) is None


def test_new_game_clears_moves_history_and_premove() -> None:
    game = GameController()
    game.apply_move_intent(chess.Move.from_uci("e2e4"))
    game.queue_premove(chess.Move.from_uci("g1f3"))
    game.new_game()
    assert game.ply == 0
    assert game.premove is None
    assert game.viewing_live_position
    assert game.board == chess.Board()
