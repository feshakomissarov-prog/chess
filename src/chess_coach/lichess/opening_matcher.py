"""Offline matcher for the bundled CC0 Lichess opening reference data."""

from __future__ import annotations

import csv
import hashlib
import io
from importlib.resources import files

import chess
import chess.pgn


DATASET_REVISION = "lichess-org/chess-openings@712a58be746c143f4ab935b68acc50f4bac813eb"
DATASET_SHA256 = "10ec43243582babcfc9751a3beb1a735b8391604b83b1164d61313038b6ad1a5"
ALGORITHM_VERSION = "cc-opening-epd-v1"


def epd_key(board: chess.Board) -> str:
    """Normalize a position to the four EPD fields, retaining only legal EP rights."""
    return " ".join(board.fen(en_passant="legal").split()[:4])


def _dataset_rows() -> list[tuple[str, str, str, int, str]]:
    root = files("chess_coach").joinpath("opening_data")
    by_position: list[tuple[str, str, str, int, str]] = []
    digest = hashlib.sha256()
    for letter in "abcde":
        data = root.joinpath(f"{letter}.tsv").read_bytes()
        digest.update(f"{letter}.tsv".encode()); digest.update(b"\0")
        digest.update(data); digest.update(b"\0")
        for row in csv.DictReader(io.StringIO(data.decode("utf-8")), delimiter="\t"):
            text = str(row.get("pgn") or "").strip()
            if not text:
                continue
            try:
                game = chess.pgn.read_game(io.StringIO(text))
                if game is None or game.errors or not game.board().is_valid():
                    continue
                board = game.board()
                ply = 0
                for move in game.mainline_moves():
                    if move not in board.legal_moves:
                        break
                    board.push(move)
                    ply += 1
                if ply:
                    name = str(row["name"]).strip()
                    family, sep, variation = name.partition(":")
                    by_position.append((epd_key(board), str(row["eco"]).strip(), name,
                                        ply, family.strip() or name))
            except (ValueError, KeyError, chess.InvalidMoveError, chess.IllegalMoveError):
                continue
    if digest.hexdigest() != DATASET_SHA256:
        raise RuntimeError("Bundled opening dataset checksum does not match its pinned revision")
    return by_position


class OpeningMatcher:
    def __init__(self) -> None:
        self._index: dict[str, list[tuple[str, str, int, str, str | None]]] = {}
        for key, eco, name, ply, family in _dataset_rows():
            _, sep, variation = name.partition(":")
            self._index.setdefault(key, []).append((eco, name, ply, family,
                                                     variation.strip() if sep else None))
        for choices in self._index.values():
            # Multiple named lines can reach the same transposed position. Prefer
            # the most specific (longest) reference, then stable ECO/name ordering.
            choices.sort(key=lambda item: (-item[2], item[0], item[1]))

    @property
    def position_count(self) -> int:
        return len(self._index)

    def match_boards(self, game_id: str, line_revision: int,
                     boards: list[chess.Board]) -> dict | None:
        match = None
        for ply, board in enumerate(boards):
            choices = self._index.get(epd_key(board))
            if not choices:
                continue
            eco, name, _reference_ply, family, variation = choices[0]
            match = {"game_id": game_id, "line_revision": line_revision,
                     "dataset_revision": DATASET_REVISION,
                     "algorithm_version": ALGORITHM_VERSION,
                     "eco": eco, "name": name, "family": family,
                     "variation": variation, "matched_ply": ply}
        return match

    def match_game(self, repository, game_id: str) -> dict | None:
        game = repository.get_game(game_id)
        if not game:
            return None
        if game.get("variant", "standard") != "standard":
            repository.save_opening_match(game_id, int(game.get("line_revision", 1)), None)
            return None
        initial_fen = game.get("initial_fen") or "startpos"
        try:
            board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
            if not board.is_valid():
                return None
            boards = [board.copy(stack=False)]
            for raw in str(game.get("moves") or "").split():
                move = chess.Move.from_uci(raw)
                if move not in board.legal_moves:
                    return None
                board.push(move)
                boards.append(board.copy(stack=False))
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
            return None
        match = self.match_boards(game_id, int(game.get("line_revision", 1)), boards)
        repository.save_opening_match(game_id, int(game.get("line_revision", 1)), match)
        return match

    def match_recent(self, repository, limit: int = 10) -> int:
        """Explicit bounded backfill; it performs no network or engine operations."""
        limit = max(1, min(int(limit), 100))
        with repository._connection() as db:
            ids = [row[0] for row in db.execute(
                "SELECT g.game_id FROM games g LEFT JOIN opening_matches om "
                "ON om.game_id=g.game_id AND om.line_revision=g.line_revision "
                "WHERE g.status NOT IN ('created','started','started_soon','playing') "
                "AND g.variant='standard' AND om.game_id IS NULL "
                "ORDER BY COALESCE(g.started_at,g.updated_at) DESC,g.game_id LIMIT ?", (limit,))]
        matched = 0
        for game_id in ids:
            matched += int(self.match_game(repository, str(game_id)) is not None)
        return matched
