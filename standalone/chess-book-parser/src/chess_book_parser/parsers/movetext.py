"""Parse MOVETEXT strings from EPUB hidden inputs into chess.pgn.Game trees."""

import io
import re
from typing import Dict, Optional, Tuple

import chess
import chess.pgn


STANDARD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def parse_movetext(
    movetext: str, fen: Optional[str] = None
) -> Tuple[Optional[chess.pgn.Game], Dict[Tuple[int, int], chess.pgn.GameNode]]:
    """Parse a MOVETEXT string into a game tree with position mapping.

    Args:
        movetext: Raw MOVETEXT value from the EPUB hidden input (starts with "root").
        fen: Optional FEN string for the starting position.

    Returns:
        game: chess.pgn.Game with full variation tree, or None if movetext is empty.
        mapping: dict of (half_move, variation_id) -> GameNode.
            half_move is 1-indexed ply from the starting position.
            variation_id 0 = mainline, 1+ = branches in PGN serialization order.
    """
    cleaned = _clean_movetext(movetext)
    if not cleaned or not cleaned.strip():
        return None, {}

    pgn_str = _wrap_as_pgn(cleaned, fen)
    game = chess.pgn.read_game(io.StringIO(pgn_str))
    if game is None:
        return None, {}

    mapping = _build_mv_mapping(game)
    return game, mapping


def _clean_movetext(movetext: str) -> str:
    """Clean a raw MOVETEXT string for python-chess parsing."""
    # Strip the "root" prefix
    text = re.sub(r"^root\s*", "", movetext)

    # Normalize castling: 0-0-0 before 0-0 to avoid partial replacement
    text = text.replace("0-0-0", "O-O-O").replace("0-0", "O-O")

    return text.strip()


def _wrap_as_pgn(movetext: str, fen: Optional[str] = None) -> str:
    """Wrap cleaned movetext in PGN headers for parsing."""
    headers = ['[Event "?"]', '[Result "*"]']
    if fen and fen.strip() != STANDARD_FEN:
        headers.append(f'[FEN "{fen.strip()}"]')
        headers.append('[SetUp "1"]')
    header_block = "\n".join(headers)
    return f"{header_block}\n\n{movetext} *"


def _build_mv_mapping(
    game: chess.pgn.Game,
) -> Dict[Tuple[int, int], chess.pgn.GameNode]:
    """Build a (half_move, variation_id) -> GameNode mapping.

    Walks the game tree in PGN serialization order:
    - Mainline child inherits parent's variation ID
    - Sub-variations get incrementing IDs
    - Sub-variations are fully recursed before continuing mainline

    This matches the gXmYvZ encoding in the EPUB HTML.
    """
    mapping: Dict[Tuple[int, int], chess.pgn.GameNode] = {}
    var_counter = [0]  # mutable counter shared across recursion

    def walk(node: chess.pgn.GameNode, ply: int, current_v: int) -> None:
        if not node.variations:
            return

        main = node.variations[0]
        main_ply = ply + 1
        mapping[(main_ply, current_v)] = main

        # Process sub-variations (get new variation IDs)
        for i in range(1, len(node.variations)):
            var = node.variations[i]
            var_counter[0] += 1
            new_v = var_counter[0]
            mapping[(main_ply, new_v)] = var
            walk(var, main_ply, new_v)

        # Continue mainline
        walk(main, main_ply, current_v)

    walk(game, 0, 0)
    return mapping
