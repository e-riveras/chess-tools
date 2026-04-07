#!/usr/bin/env python3
"""
Extract complete annotated games from The Iron English EPUB and output as PGN.

Each game chapter in the book becomes a PGN game with:
  - Proper headers (White, Black, Event, Date, Result)
  - Main line moves
  - Book commentary as { } annotations on the relevant move

Usage:
    python run_epub_to_pgn.py [output.pgn]
    (defaults to iron_english_annotated.pgn)
"""

import re
import sys
import zipfile
from io import StringIO

import chess
import chess.pgn
from bs4 import BeautifulSoup

EPUB_PATH = "samples/annas-arch-dab3647cdba4.epub"

CHAPTER_FILES = [
    "5_Introduction_converted.html",
    "6_Chapter 1_converted.html",
    "7_Chapter 2_converted.html",
    "8_Chapter 3_converted_split_000.html",
    "8_Chapter 3_converted_split_001.html",
    "9_Chapter 4_converted_split_000.html",
    "9_Chapter 4_converted_split_001.html",
    "9_Chapter 4_converted_split_002.html",
    "9_Chapter 4_converted_split_003.html",
    "10_Chapter 5_converted_split_000.html",
    "10_Chapter 5_converted_split_001.html",
    "11_Chapter 6_converted_split_000.html",
    "11_Chapter 6_converted_split_001.html",
    "11_Chapter 6_converted_split_002.html",
    "11_Chapter 6_converted_split_003.html",
    "12_Chapter 7_converted.html",
    "13_Chapter 8_converted.html",
    "14_Chapter 9_converted.html",
]

# Map annotation strings to PGN NAG codes
NAG_MAP = {
    "!": chess.pgn.NAG_GOOD_MOVE,
    "?": chess.pgn.NAG_MISTAKE,
    "!!": chess.pgn.NAG_BRILLIANT_MOVE,
    "??": chess.pgn.NAG_BLUNDER,
    "!?": chess.pgn.NAG_SPECULATIVE_MOVE,
    "?!": chess.pgn.NAG_DUBIOUS_MOVE,
}

RESULT_RE = re.compile(r"\s*(1-0|0-1|1/2-1/2|\*)\s*$")

# Matches an individual chess move token, optionally prefixed by a move number.
# Handles both "O-O" (letter O) and "0-0" (digit zero) castling notation.
# Groups: (san_base, annotation)
MOVE_TOKEN_RE = re.compile(
    r"(?:\d+\.{1,3})?"                                          # optional move number
    r"("                                                          # capture: SAN base
    r"[O0]-[O0]-[O0][+#]?"                                      #   queenside castling
    r"|[O0]-[O0][+#]?"                                          #   kingside castling
    r"|[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQK])?[+#]?"   #   regular move
    r")"
    r"([!?]{0,2})"                                               # annotation suffix
)

# Normalise digit-zero castling ("0-0", "0-0-0") to letter-O form for parse_san
def _normalise_san(san: str) -> str:
    return san.replace("0-0-0", "O-O-O").replace("0-0", "O-O")


def parse_game_header(p_tag):
    """Parse a <p class="game"> tag into a header dict."""
    italic = p_tag.find("span", class_="italic")
    bold1 = p_tag.find("span", class_="bold1")

    game_num = italic.get_text().strip() if italic else ""
    players = bold1.get_text().strip() if bold1 else ""

    # Remaining text after removing the known spans
    full = p_tag.get_text()
    if italic:
        full = full.replace(italic.get_text(), "", 1)
    if bold1:
        full = full.replace(bold1.get_text(), "", 1)
    rest = full.strip()

    # Split players on first hyphen (handles "D'Costa.L-Teske.H" etc.)
    white = black = "?"
    if "-" in players:
        idx = players.index("-")
        white = players[:idx].strip()
        black = players[idx + 1 :].strip()

    # Extract year from end of rest ("Cappelle-la-Grande 1995", "Wijk aan Zee 2016")
    year_m = re.search(r"(\d{4})\s*$", rest)
    year = year_m.group(1) if year_m else "????"
    event = rest[: year_m.start()].strip().rstrip(",") if year_m else rest

    return {
        "game_num": game_num,
        "white": white,
        "black": black,
        "event": event or "?",
        "year": year,
    }


def flush_comments(node, pending_comments, pgn_game):
    """Attach collected commentary to the given node."""
    if not pending_comments or node is pgn_game:
        return
    text = " ".join(" ".join(c.split()) for c in pending_comments)
    if text:
        node.comment = text


def process_bold(text, board, pgn_game_node, current_node):
    """
    Parse moves from a bold paragraph and add them to the game tree.

    Returns (updated_node, result_string_or_None).
    """
    result = None
    result_m = RESULT_RE.search(text)
    if result_m:
        result = result_m.group(1)
        text = text[: result_m.start()]

    node = current_node
    for m in MOVE_TOKEN_RE.finditer(text):
        san, nag_str = m.group(1), m.group(2)
        if not san:
            continue
        try:
            move = board.parse_san(_normalise_san(san))
            board.push(move)
            node = node.add_variation(move)
            if nag_str in NAG_MAP:
                node.nags.add(NAG_MAP[nag_str])
        except Exception:
            pass  # not a legal move in this position — skip

    return node, result


def extract_games(epub_path):
    """Extract all annotated games from the EPUB, returning chess.pgn.Game objects."""
    games = []

    with zipfile.ZipFile(epub_path) as z:
        available = set(z.namelist())

        for chapter_file in CHAPTER_FILES:
            if chapter_file not in available:
                continue

            with z.open(chapter_file) as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            pgn_game = None
            current_node = None
            board = None
            pending_comments = []

            for p in soup.find_all("p"):
                cls = p.get("class", [])
                text = p.get_text().strip()
                if not text:
                    continue

                if "game" in cls:
                    # Save the completed previous game
                    if pgn_game is not None:
                        flush_comments(current_node, pending_comments, pgn_game)
                        games.append(pgn_game)
                        pending_comments = []

                    # Start a new game
                    header = parse_game_header(p)
                    pgn_game = chess.pgn.Game()
                    pgn_game.headers["Event"] = header["event"]
                    pgn_game.headers["Site"] = "?"
                    pgn_game.headers["Date"] = f"{header['year']}.??.??"
                    pgn_game.headers["Round"] = "?"
                    pgn_game.headers["White"] = header["white"]
                    pgn_game.headers["Black"] = header["black"]
                    pgn_game.headers["Result"] = "*"
                    if header["game_num"]:
                        pgn_game.headers["Annotator"] = header["game_num"]

                    current_node = pgn_game
                    board = chess.Board()
                    pending_comments = []

                elif "bold" in cls and pgn_game is not None:
                    # Attach any collected comments to the last move node
                    flush_comments(current_node, pending_comments, pgn_game)
                    pending_comments = []

                    current_node, result = process_bold(
                        text, board, pgn_game, current_node
                    )
                    if result:
                        pgn_game.headers["Result"] = result

                elif "normal1" in cls and pgn_game is not None:
                    pending_comments.append(text)

            # End of chapter: save the last game
            if pgn_game is not None:
                flush_comments(current_node, pending_comments, pgn_game)
                games.append(pgn_game)
                pending_comments = []
                pgn_game = None

    return games


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "iron_english_annotated.pgn"

    print(f"Reading {EPUB_PATH} ...")
    games = extract_games(EPUB_PATH)
    print(f"Extracted {len(games)} games.")

    with open(output_path, "w", encoding="utf-8") as out:
        for i, game in enumerate(games):
            exporter = chess.pgn.StringExporter(
                headers=True, variations=False, comments=True
            )
            out.write(game.accept(exporter))
            out.write("\n\n")

    print(f"Written to {output_path}")

    # Print a brief summary
    for i, game in enumerate(games, 1):
        h = game.headers
        result = h.get("Result", "*")
        moves = sum(1 for _ in game.mainline())
        print(
            f"  {i:2}. {h.get('Annotator',''):<8}  "
            f"{h.get('White','?')} vs {h.get('Black','?')}  "
            f"({h.get('Event','?')}, {h.get('Date','?')[:4]})  "
            f"{moves} moves  {result}"
        )


if __name__ == "__main__":
    main()
