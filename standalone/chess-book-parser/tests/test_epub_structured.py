"""Tests for the HTML-aware structured EPUB parser."""

import io
from pathlib import Path
from unittest.mock import patch

import chess.pgn
import pytest

from chess_book_parser.parsers.movetext import parse_movetext, _clean_movetext, _build_mv_mapping
from chess_book_parser.parsers.epub_structured import (
    has_movetext_data,
    parse_structured_epub,
    _extract_game_headers,
    _extract_commentary,
    _extract_movetexts,
    _merge_comments,
)
from bs4 import BeautifulSoup


EPUB_PATH = Path(__file__).parent.parent / "samples" / "annas-arch-dab3647cdba4.epub"
EPUB_AVAILABLE = EPUB_PATH.exists()


class TestMovetext:
    """Tests for MOVETEXT parsing."""

    def test_parse_basic(self):
        game, mapping = parse_movetext("root 1.c4 Nf6 2.Nc3 d6")
        assert game is not None
        moves = list(game.mainline_moves())
        assert len(moves) == 4
        assert mapping[(1, 0)].san() == "c4"
        assert mapping[(4, 0)].san() == "d6"

    def test_parse_with_variation(self):
        game, mapping = parse_movetext(
            "root 1.e4 e5 ( 1...c5 2.Nf3 ) 2.Nf3 Nc6"
        )
        assert game is not None
        # Mainline: e4 e5 Nf3 Nc6
        assert len(list(game.mainline_moves())) == 4
        # Variation: 1...c5 should be at (2, 1)
        assert mapping[(2, 1)].san() == "c5"
        # Variation continuation: 2.Nf3 in variation at (3, 1)
        assert mapping[(3, 1)].san() == "Nf3"

    def test_parse_castling_normalization(self):
        game, mapping = parse_movetext(
            "root 1.e4 e5 2.Nf3 Nf6 3.Bc4 Bc5 4.0-0 0-0"
        )
        assert game is not None
        moves = [m.uci() for m in game.mainline_moves()]
        assert "e1g1" in moves  # White castles
        assert "e8g8" in moves  # Black castles

    def test_parse_queenside_castling(self):
        cleaned = _clean_movetext("root 1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.Bg5 Be7 5.e3 0-0 6.Nf3 Nbd7 7.Qc2 c5 8.0-0-0")
        assert "O-O-O" in cleaned
        assert "0-0" not in cleaned

    def test_parse_empty_movetext(self):
        game, mapping = parse_movetext("root ")
        assert game is None
        assert mapping == {}

    def test_parse_root_only(self):
        game, mapping = parse_movetext("root")
        assert game is None
        assert mapping == {}

    def test_parse_with_annotations(self):
        game, mapping = parse_movetext("root 1.e4 e5 2.Nf3! Nc6?!")
        assert game is not None
        # python-chess converts inline annotations to NAGs
        node = mapping[(3, 0)]
        assert node.san() == "Nf3"

    def test_parse_with_custom_fen(self):
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        game, mapping = parse_movetext("root 1...e5 2.Nf3 Nc6", fen=fen)
        assert game is not None
        assert game.headers.get("FEN") == fen
        moves = list(game.mainline_moves())
        assert len(moves) == 3

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_mv_mapping_chapter1_game1(self):
        """Verify (m,v) mapping against known HTML span data from Chapter 1."""
        import zipfile
        with zipfile.ZipFile(str(EPUB_PATH)) as zf:
            html = zf.read("6_Chapter 1_converted.html").decode("utf-8")
            soup = BeautifulSoup(html, "html.parser")
            mt = soup.find("input", id="MOVETEXT0")
            movetext = mt.get("value", "")

        game, mapping = parse_movetext(movetext)
        assert game is not None

        # Known mappings from HTML g0mXvY spans
        assert mapping[(1, 0)].san() == "c4"      # g0m1v0 = 1.c4
        assert mapping[(6, 0)].san() == "g6"      # g0m6v0 = 3...g6
        assert mapping[(6, 1)].san() == "e5"      # g0m6v1 = 3...e5
        assert mapping[(16, 0)].san() == "Be6"    # g0m16v0 = 8...Be6
        assert "exf4" in mapping[(16, 2)].san()    # g0m16v2 = 8...exf4!
        assert mapping[(17, 0)].san() == "Nd5"    # g0m17v0 = 9.Nd5
        assert mapping[(17, 3)].san() == "d3"     # g0m17v3 = 9.d3
        assert mapping[(19, 4)].san() == "Nd5"    # g0m19v4 = 10.Nd5


class TestGameHeaders:
    """Tests for game header extraction."""

    def test_extract_game_header_basic(self):
        html = '''
        <p class="game">
            <span name="game1"></span>
            <span class="italic">Game 1</span>
            <span class="bold1">S.Williams-V.Locatelli</span>
            Cappelle-la-Grande 1995
        </p>
        <p class="bold">
            <span name="g0m1v0">1.c4</span>
        </p>
        '''
        soup = BeautifulSoup(html, "html.parser")
        headers = _extract_game_headers(soup)

        assert 0 in headers
        assert headers[0]["white"] == "S.Williams"
        assert headers[0]["black"] == "V.Locatelli"
        assert headers[0]["number"] == "Game 1"
        assert "Cappelle-la-Grande 1995" in headers[0]["event"]

    def test_extract_game_header_en_dash(self):
        html = '''
        <p class="game">
            <span name="game5"></span>
            <span class="italic">Game 5</span>
            <span class="bold1">M.Carlsen\u2013L.Aronian</span>
            Clutch Chess 2020
        </p>
        <p class="bold"><span name="g0m1v0">1.c4</span></p>
        '''
        soup = BeautifulSoup(html, "html.parser")
        headers = _extract_game_headers(soup)

        assert 0 in headers
        assert headers[0]["white"] == "M.Carlsen"
        assert headers[0]["black"] == "L.Aronian"


class TestCommentary:
    """Tests for commentary extraction."""

    def test_commentary_after_move_span(self):
        html = '''
        <p class="bold"><span name="g0m1v0">1.e4</span> <span name="g0m2v0">e5</span></p>
        <p class="normal1">A classical opening.</p>
        <p class="bold"><span name="g0m3v0">2.Nf3</span></p>
        <p class="normal1">The most natural developing move.</p>
        '''
        soup = BeautifulSoup(html, "html.parser")
        comments = _extract_commentary(soup, 0)

        assert (2, 0) in comments
        assert "classical opening" in comments[(2, 0)]
        assert (3, 0) in comments
        assert "natural developing" in comments[(3, 0)]

    def test_commentary_with_inline_moves(self):
        html = '''
        <p class="bold"><span name="g0m1v0">1.e4</span> <span name="g0m2v0">e5</span></p>
        <p class="normal1">Instead, <span name="g0m2v1">1...c5</span> leads to the Sicilian.</p>
        '''
        soup = BeautifulSoup(html, "html.parser")
        comments = _extract_commentary(soup, 0)

        # "Instead," before the move ref -> attached to last_move_ref (2, 0)
        assert (2, 0) in comments
        assert "Instead," in comments[(2, 0)]
        # "leads to the Sicilian." after 1...c5 -> attached to (2, 1)
        assert (2, 1) in comments
        assert "Sicilian" in comments[(2, 1)]

    def test_merge_comments(self):
        game, mapping = parse_movetext("root 1.e4 e5 2.Nf3 Nc6")
        comments = {
            (2, 0): "Good response.",
            (3, 0): "Knight develops towards center.",
        }
        _merge_comments(game, mapping, comments)

        # Check comments on the correct nodes
        node_e5 = mapping[(2, 0)]
        assert "Good response" in node_e5.comment

        node_nf3 = mapping[(3, 0)]
        assert "center" in node_nf3.comment


class TestFullEpubParse:
    """Integration tests using the actual EPUB file."""

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_game_count(self):
        games = parse_structured_epub(str(EPUB_PATH))
        assert len(games) == 69

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_all_games_valid_pgn(self):
        games = parse_structured_epub(str(EPUB_PATH))
        for i, game in enumerate(games):
            pgn_str = str(game)
            reparsed = chess.pgn.read_game(io.StringIO(pgn_str))
            assert reparsed is not None, f"Game {i+1} failed to re-parse"
            assert not list(reparsed.errors), f"Game {i+1} has parse errors"

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_named_games_count(self):
        games = parse_structured_epub(str(EPUB_PATH))
        named = [g for g in games if g.headers.get("White") != "Study"]
        assert len(named) == 33

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_first_game_details(self):
        games = parse_structured_epub(str(EPUB_PATH))
        # First named game should be Game 1
        g1 = games[1]
        assert g1.headers["White"] == "S.Williams"
        assert g1.headers["Black"] == "V.Locatelli"
        assert "Game 1" in g1.headers["Event"]

        # Check it has commentary
        def count_comments(node):
            total = 1 if node.comment else 0
            for var in node.variations:
                total += count_comments(var)
            return total

        assert count_comments(g1) > 50

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_commentary_placement_spot_check(self):
        """Spot-check that commentary lands on the right moves."""
        games = parse_structured_epub(str(EPUB_PATH))
        g1 = games[1]  # Game 1: S.Williams-V.Locatelli

        # After 3...g6 (mainline move 6), commentary about King's Indian
        node = g1
        for _ in range(6):  # Walk 6 plies to reach 3...g6
            node = node.variations[0]
        assert "Indian" in node.comment

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_split_file_game_has_comments(self):
        """Games 10, 15, 17, 22 were in split files - verify they have comments."""
        games = parse_structured_epub(str(EPUB_PATH))

        # Find these games by their player names
        target_names = {
            "S.Skembris": False,      # Game 10
            "M.Botvinnik": False,     # Game 15 (Botvinnik-Geller)
            "K.Spraggett": False,     # Game 17
            "B.Gelfand": False,       # Game 22
        }

        for g in games:
            white = g.headers.get("White", "")
            if white in target_names:
                def count_comments(node):
                    total = 1 if node.comment else 0
                    for var in node.variations:
                        total += count_comments(var)
                    return total
                cc = count_comments(g)
                assert cc > 0, f"{white} game has no comments"
                target_names[white] = True

        # Verify all targets were found
        for name, found in target_names.items():
            assert found, f"Game with White={name} not found"


class TestStrategySelection:
    """Test that the correct parser strategy is selected."""

    @pytest.mark.skipif(not EPUB_AVAILABLE, reason="EPUB file not available")
    def test_structured_epub_detected(self):
        assert has_movetext_data(str(EPUB_PATH)) is True

    def test_nonexistent_file(self):
        assert has_movetext_data("/nonexistent/file.epub") is False
