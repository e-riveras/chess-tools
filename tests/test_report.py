"""Tests for report generation utilities."""
import pytest
import chess
from chess_tools.analysis.report import generate_line_player_html


class TestGenerateLinePlayerHtml:
    START_FEN = chess.STARTING_FEN

    def test_empty_line_returns_empty(self):
        result = generate_line_player_html("p1", self.START_FEN, "", chess.WHITE)
        assert result == ""

    def test_frame_count_matches_line_length(self):
        line = "e4 e5 Nf3"
        result = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE)
        # 1 start frame + 3 moves = 4 frames
        assert result.count('<div class="lp-frame"') == 4

    def test_html_contains_player_class(self):
        line = "e4 e5"
        result = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE)
        assert 'class="line-player refutation"' in result

    def test_refutation_kind(self):
        line = "e4 e5"
        result = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE,
                                           kind="refutation")
        assert 'class="line-player refutation"' in result

    def test_initial_move_advances_position(self):
        # After 1.e4, refutation line starts from after e4 (black to move)
        line = "e5 Nf3"
        result_with = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE,
                                                initial_move_san="e4", kind="refutation")
        # Without initial_move_san, use a line valid from starting position
        line_from_start = "e4 e5"
        result_without = generate_line_player_html("p2", self.START_FEN, line_from_start, chess.WHITE,
                                                   initial_move_san=None, kind="refutation")
        assert result_with.count('<div class="lp-frame"') == 3  # start + 2 moves
        assert result_without.count('<div class="lp-frame"') == 3  # start + 2 moves

    def test_respects_max_six_plies(self):
        # 8-move line should be capped at 6
        line = "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6"
        result = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE)
        # 1 start frame + 6 move frames = 7
        assert result.count('<div class="lp-frame"') == 7

    def test_invalid_san_stops_early(self):
        line = "e4 INVALID e5"
        result = generate_line_player_html("p1", self.START_FEN, line, chess.WHITE)
        # Only start + 1 valid move; "INVALID" stops iteration -> returns "" since < 2 frames?
        # Actually start (1) + e4 (1) = 2 frames, which is >= 2, so valid
        assert result.count('<div class="lp-frame"') == 2
