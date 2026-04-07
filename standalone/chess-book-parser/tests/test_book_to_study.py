"""Tests for chess_book_parser.converter."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from chess_book_parser.converter import (
    BookParser,
    NotationParser,
)


class TestBookParser:
    """Tests for BookParser class."""

    def test_extract_chapters_with_chapter_markers(self):
        """Test chapter extraction with standard chapter headers."""
        text = """Introduction text here.

Chapter 1
This is chapter one content with moves 1.e4 e5.

Chapter 2
This is chapter two content with moves 1.d4 d5.
"""
        chapters = BookParser.extract_chapters(text)

        assert len(chapters) == 2
        assert chapters[0]['title'] == 'Chapter 1'
        assert '1.e4 e5' in chapters[0]['content']
        assert chapters[1]['title'] == 'Chapter 2'
        assert '1.d4 d5' in chapters[1]['content']

    def test_extract_chapters_no_markers(self):
        """Test fallback when no chapter markers found."""
        text = "Just some text without chapter headers."
        chapters = BookParser.extract_chapters(text)

        assert len(chapters) == 1
        assert chapters[0]['title'] == 'Full Book'
        assert chapters[0]['content'] == text

    def test_extract_chapters_uppercase(self):
        """Test extraction with uppercase CHAPTER markers."""
        text = """CHAPTER 1
First chapter content.

CHAPTER 2
Second chapter content.
"""
        chapters = BookParser.extract_chapters(text)

        assert len(chapters) == 2
        assert chapters[0]['title'] == 'CHAPTER 1'
        assert chapters[1]['title'] == 'CHAPTER 2'

    def test_parse_pdf_file_not_found(self):
        """Test PDF parsing with nonexistent file."""
        with pytest.raises(FileNotFoundError):
            BookParser.parse_pdf('/nonexistent/file.pdf')

    def test_parse_epub_file_not_found(self):
        """Test EPUB parsing with nonexistent file."""
        with pytest.raises(FileNotFoundError):
            BookParser.parse_epub('/nonexistent/file.epub')


class TestNotationParser:
    """Tests for NotationParser class."""

    def test_text_to_pgn_basic(self):
        """Test basic PGN conversion."""
        text = "1.e4 e5 2.Nf3 Nc6"
        pgn = NotationParser.text_to_pgn(text, "Test Chapter")

        assert '[Event "Test Chapter"]' in pgn
        assert '[Site "Chess Book"]' in pgn
        assert '1. e4 e5 2. Nf3 Nc6' in pgn

    def test_text_to_pgn_with_intro(self):
        """Test PGN conversion with introduction text."""
        text = """This is an introduction.
1.e4 e5 2.Nf3 Nc6"""
        pgn = NotationParser.text_to_pgn(text, "Opening Study")

        assert '[Event "Opening Study"]' in pgn
        assert 'This is an introduction.' in pgn
        assert '1. e4 e5 2. Nf3' in pgn

    def test_text_to_pgn_no_moves(self):
        """Test PGN conversion with no recognizable moves."""
        text = "Just descriptive text without any chess notation."
        pgn = NotationParser.text_to_pgn(text, "No Moves")

        assert '[Event "No Moves"]' in pgn
        # Full text should be in comment
        assert 'Just descriptive text' in pgn

    def test_text_to_pgn_long_intro_preserved(self):
        """Test that long introductions are preserved."""
        long_intro = "A" * 1000
        text = f"{long_intro}\n1.e4 e5"
        pgn = NotationParser.text_to_pgn(text, "Long Intro")

        # Text should be in comment (may be normalized)
        assert 'AAAA' in pgn
        assert '1. e4 e5' in pgn

    def test_extract_games_finds_move_sequences(self):
        """Test game extraction from text."""
        text = """Some intro text.

1.e4 e5 2.Nf3 Nc6 3.Bb5 a6

More descriptive text here.

1.d4 d5 2.c4 e6 3.Nc3 Nf6
"""
        games = NotationParser.extract_games(text)

        assert len(games) >= 1
        # python-chess outputs with space: "1. e4" not "1.e4"
        assert '1. e4' in games[0] or '1. d4' in games[0]

    def test_parenthetical_variation_restores_main_line(self):
        """After a () variation the main line must continue from the correct node.

        1.e4 e5 2.Nf3 (2.d4 exd4) 2...Nc6 3.Bb5

        Without proper variation stack handling, 2...Nc6 would be attached to
        the end of the d4 variation instead of after 2.Nf3, producing a board
        where Black has two pawns on e5 and d4, or pieces missing altogether.
        """
        import io
        import chess.pgn

        text = "1.e4 e5 2.Nf3 (2.d4 exd4) 2...Nc6 3.Bb5"
        pgn = NotationParser.text_to_pgn(text, "Variation Test")

        game = chess.pgn.read_game(io.StringIO(pgn))
        assert game is not None

        # Main line must be exactly 1.e4 e5 2.Nf3 Nc6 3.Bb5
        mainline = list(game.mainline_moves())
        import chess
        expected = [
            chess.Move.from_uci("e2e4"),
            chess.Move.from_uci("e7e5"),
            chess.Move.from_uci("g1f3"),
            chess.Move.from_uci("b8c6"),
            chess.Move.from_uci("f1b5"),
        ]
        assert mainline == expected, f"Expected Ruy Lopez main line, got: {mainline}"

        # The position after 3.Bb5 must be a valid chess position
        node = game
        for _ in mainline:
            node = node.variations[0]
        board = node.board()
        assert board.is_valid()
        # White bishop must be on b5
        assert board.piece_at(chess.B5) == chess.Piece(chess.BISHOP, chess.WHITE)

    def test_nested_variation_does_not_corrupt_outer_context(self):
        """Nested () variations must fully restore outer variation context on each ).

        1.e4 e5 2.Nf3 (2.d4 exd4 3.Nf3 (3.c3)) 2...Nc6

        After the outer ) the main line must resume after 2.Nf3, not after 3.c3.
        """
        import io
        import chess.pgn
        import chess

        text = "1.e4 e5 2.Nf3 (2.d4 exd4 3.Nf3 (3.c3)) 2...Nc6"
        pgn = NotationParser.text_to_pgn(text, "Nested Variation Test")

        game = chess.pgn.read_game(io.StringIO(pgn))
        assert game is not None

        mainline = list(game.mainline_moves())
        expected = [
            chess.Move.from_uci("e2e4"),
            chess.Move.from_uci("e7e5"),
            chess.Move.from_uci("g1f3"),
            chess.Move.from_uci("b8c6"),
        ]
        assert mainline == expected, f"Nested variation corrupted main line: {mainline}"

    def test_board_positions_are_legal_throughout_main_line(self):
        """Every board position in the main line must pass chess legality checks."""
        import io
        import chess.pgn

        text = "1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.Bg5 Be7 5.e3 0-0"
        pgn = NotationParser.text_to_pgn(text, "Legal Positions Test")

        game = chess.pgn.read_game(io.StringIO(pgn))
        assert game is not None

        node = game
        for move_node in game.mainline():
            board = move_node.board()
            assert board.is_valid(), (
                f"Illegal position after {move_node.san()}: {board.fen()}"
            )


