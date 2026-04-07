"""Tests for CLAMP blunder taxonomy."""
import chess

from chess_tools.analysis.clamp import (
    classify_clamp,
    clamp_summary,
    _is_passed_pawn,
    _refutation_includes_significant_check,
)


def test_clamp_summary():
    assert clamp_summary(["C", "L"]) == "C · L"
    assert clamp_summary([]) == ""


def test_is_passed_pawn_simple():
    b = chess.Board("8/8/8/3P4/8/8/8/8 w - - 0 1")
    d5 = chess.parse_square("d5")
    assert _is_passed_pawn(b, d5, chess.WHITE) is True

    b2 = chess.Board("8/4p3/8/3P4/8/8/8/8 w - - 0 1")
    assert _is_passed_pawn(b2, d5, chess.WHITE) is False


def test_refutation_mate_triggers_c():
    b = chess.Board(chess.STARTING_FEN)
    assert _refutation_includes_significant_check(b, [], chess.WHITE, mate_in=3) is True


def test_classify_hanging_maps_to_l():
    b = chess.Board(chess.STARTING_FEN)
    tags = classify_clamp(b, [], "hanging_piece", None, chess.WHITE)
    assert "L" in tags
    assert "A" not in tags


def test_classify_fork_maps_to_a():
    b = chess.Board(chess.STARTING_FEN)
    tags = classify_clamp(b, [], "fork", None, chess.WHITE)
    assert tags == ["A"]


def test_classify_trapped_maps_to_m():
    b = chess.Board(chess.STARTING_FEN)
    tags = classify_clamp(b, [], "trapped_piece", None, chess.WHITE)
    assert tags == ["M"]


def test_order_c_before_a():
    b = chess.Board(chess.STARTING_FEN)
    tags = classify_clamp(b, [], "fork", mate_in=4, mover_color=chess.WHITE)
    assert tags.index("C") < tags.index("A")
