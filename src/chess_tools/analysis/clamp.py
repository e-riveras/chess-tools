"""
CLAMP blunder taxonomy (blunder-prevention checklist).

C — Checks: move allows a damaging check (or mate) in the opponent's best line.
L — Loose pieces / squares: hanging material, losing exchange, undefended targets.
A — Alignments: forks, pins, skewers, discovered attacks, mating patterns on lines.
M — Mobility / trappable pieces: piece restricted with no safe escape.
P — Passed pawns: opponent creates or already has a dangerous passed pawn in the PV.

Detection uses the engine refutation PV from the position after the blunder (board_after),
plus the existing tactic_type label from classify_tactic.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import chess

CLAMP_ORDER: Tuple[str, ...] = ("C", "L", "A", "M", "P")

CLAMP_NAMES = {
    "C": "Checks",
    "L": "Loose pieces / squares",
    "A": "Alignments (forks, pins, lines)",
    "M": "Mobility / trapped piece",
    "P": "Passed pawns",
}

CLAMP_DESCRIPTIONS = {
    "C": "Opponent's best line includes a damaging check or forced mate.",
    "L": "Material was loose, hanging, or lost in an unfavorable exchange.",
    "A": "Tactical alignment: fork, pin, skewer, discovered attack, or mating line.",
    "M": "A piece lost mobility and could not escape safely.",
    "P": "Opponent's play creates or exploits a dangerous passed pawn.",
}


def _is_passed_pawn(board: chess.Board, sq: int, color: chess.Color) -> bool:
    """
    True if this pawn has no enemy pawns on the same or adjacent files ahead of it
    (standard passed-pawn test on the three files in front of the pawn).
    """
    p = board.piece_at(sq)
    if not p or p.piece_type != chess.PAWN or p.color != color:
        return False
    f = chess.square_file(sq)
    r = chess.square_rank(sq)
    enemy = not color
    if color == chess.WHITE:
        for g in (f - 1, f, f + 1):
            if g < 0 or g > 7:
                continue
            for rr in range(r + 1, 8):
                op = board.piece_at(chess.square(g, rr))
                if op and op.piece_type == chess.PAWN and op.color == enemy:
                    return False
    else:
        for g in (f - 1, f, f + 1):
            if g < 0 or g > 7:
                continue
            for rr in range(0, r):
                op = board.piece_at(chess.square(g, rr))
                if op and op.piece_type == chess.PAWN and op.color == enemy:
                    return False
    return True


def _opponent_has_passed_pawn_in_line(
    board_after: chess.Board,
    refutation_pv: Sequence[chess.Move],
    opp_color: chess.Color,
    max_plies: int = 10,
) -> bool:
    """
    True if the opponent already has a passed pawn on board_after, or one appears
    while stepping through the engine refutation PV.
    """
    for sq in board_after.pieces(chess.PAWN, opp_color):
        if _is_passed_pawn(board_after, sq, opp_color):
            return True

    b = board_after.copy()
    for move in refutation_pv[:max_plies]:
        try:
            b.push(move)
        except ValueError:
            break
        for sq in b.pieces(chess.PAWN, opp_color):
            if _is_passed_pawn(b, sq, opp_color):
                return True
    return False


def _refutation_includes_significant_check(
    board_after: chess.Board,
    refutation_pv: Sequence[chess.Move],
    hero_color: chess.Color,
    mate_in: Optional[int],
    max_plies: int = 6,
) -> bool:
    """C: forced mate in the refutation, or a check delivered by opponent in the PV."""
    if mate_in is not None and mate_in <= 9:
        return True
    if not refutation_pv:
        return False
    b = board_after.copy()
    for move in refutation_pv[:max_plies]:
        if b.turn != (not hero_color):
            break
        try:
            b.push(move)
        except ValueError:
            break
        if b.turn == hero_color and b.is_check():
            return True
    return False


def classify_clamp(
    board_after: chess.Board,
    refutation_pv: Sequence[chess.Move],
    tactic_type: str,
    mate_in: Optional[int],
    mover_color: chess.Color,
) -> List[str]:
    """
    Return CLAMP letters that apply to this blunder, in C-L-A-M-P order.

    Uses refutation_pv (opponent's best line from board_after) and tactic_type
    from classify_tactic().
    """
    tags: List[str] = []
    opp = not mover_color

    # C — Checks (including mate threats)
    if _refutation_includes_significant_check(
        board_after, refutation_pv, mover_color, mate_in
    ):
        tags.append("C")

    # L — Loose pieces / squares
    if tactic_type in (
        "hanging_piece",
        "hanging_pawn",
        "losing_exchange",
    ):
        tags.append("L")

    # A — Alignments (tactical geometry on lines / knight hops)
    if tactic_type in (
        "fork",
        "pin",
        "skewer",
        "discovered_attack",
        "forced_mate",
        "back_rank_mate",
    ):
        tags.append("A")

    # M — Mobility / trapped
    if tactic_type == "trapped_piece":
        tags.append("M")

    # P — Passed pawns (structural, from PV)
    if refutation_pv and _opponent_has_passed_pawn_in_line(
        board_after, refutation_pv, opp
    ):
        tags.append("P")

    # Dedup while preserving CLAMP order
    seen = set()
    ordered: List[str] = []
    for letter in CLAMP_ORDER:
        if letter in tags and letter not in seen:
            seen.add(letter)
            ordered.append(letter)
    return ordered


def clamp_summary(tags: Sequence[str]) -> str:
    """Human-readable CLAMP string, e.g. 'C · L · A'."""
    if not tags:
        return ""
    return " · ".join(tags)
