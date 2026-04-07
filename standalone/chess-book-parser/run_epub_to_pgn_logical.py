#!/usr/bin/env python3
"""
Extract complete annotated games from Logical Chess: Move by Move EPUB and output as PGN.

Each game is in a separate HTML file. The structure is:
  <h2>      Game N / White – Black / Location Year / Opening
  <p class="center">  One chess move (piece images used instead of piece letters)
  <p>        Commentary explaining that move

Usage:
    python run_epub_to_pgn_logical.py [output.pgn]
    (defaults to logical_chess_annotated.pgn)
"""

import re
import sys
import zipfile

import chess
import chess.pgn
from bs4 import BeautifulSoup

EPUB_PATH = "books/logical_chess.epub"

# Files containing games (one game per file, text00005 – text00039)
GAME_FILES = [f"OEBPS/text000{i:02d}.html" for i in range(5, 42)]

# Piece images found in the book.  We use these as ordering hints when
# trying to construct a legal SAN.  python-chess is the ultimate arbiter.
IMAGE_PIECE_HINTS = {
    "Image00003.jpg": "N",   # Knight (both colours)
    "Image00004.jpg": "R",   # Rook (white / bright)
    "Image00005.jpg": "Q",   # Queen or black bishop – let chess decide
    "Image00006.jpg": "R",   # Rook (black / dark)
    "Image00007.jpg": "B",   # Bishop (white / bright)
}

# When a hint piece is illegal, try pieces in this order before giving up.
FALLBACK_ORDER = ["Q", "N", "B", "R", "K", ""]   # "" = pawn

NAG_MAP = {
    "!":  chess.pgn.NAG_GOOD_MOVE,
    "?":  chess.pgn.NAG_MISTAKE,
    "!!": chess.pgn.NAG_BRILLIANT_MOVE,
    "??": chess.pgn.NAG_BLUNDER,
    "!?": chess.pgn.NAG_SPECULATIVE_MOVE,
    "?!": chess.pgn.NAG_DUBIOUS_MOVE,
}

RESULT_RE = re.compile(r"^\s*(1-0|0-1|1/2-1/2|\*)\s*$")
CASTLING_RE = re.compile(r"[O0]-[O0]-[O0]|[O0]-[O0]")
ANNOTATION_RE = re.compile(r"[!?]{1,2}$")


def parse_game_header(h2_tag):
    """
    Parse an h2 like:
        Game 1
        von Scheve – Teichmann
        Berlin 1907
        Giuoco Piano
    Returns dict with game_num, white, black, event, year, opening.
    """
    raw = h2_tag.get_text("\n").strip()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    game_num = ""
    white = black = "?"
    event = year = opening = ""

    for line in lines:
        if line.startswith("Game "):
            game_num = line
        elif re.search(r"\d{4}", line):
            # Location + year: e.g. "Berlin 1907" or "Weston-super-Mare 1924"
            m = re.search(r"\d{4}", line)
            year = m.group()
            event = line[: m.start()].strip().rstrip(",").rstrip()
        elif "–" in line or " - " in line:
            # Player line uses em-dash or " - " (spaced hyphen).
            # Avoid splitting on hyphenated names like "Nimzo-Indian".
            sep = "–" if "–" in line else " - "
            parts = line.split(sep, 1)
            white = parts[0].strip()
            black = parts[1].strip() if len(parts) > 1 else "?"
        else:
            opening = line

    return {
        "game_num": game_num,
        "white": white,
        "black": black,
        "event": event or "?",
        "year": year or "????",
        "opening": opening,
    }


def clean_move_text(raw: str) -> str:
    """Strip (D) / (d) diagram markers, CR/LF, and collapse whitespace."""
    t = re.sub(r"\(D\)", "", raw, flags=re.IGNORECASE)
    t = t.replace("\r", " ").replace("\n", " ")
    return " ".join(t.split()).strip()


def extract_move_info(p_tag):
    """
    From a <p class="center"> tag, return:
        (is_black: bool, raw_fragment: str, piece_hint: str | None)

    raw_fragment is the move text minus the move number and "..." prefix,
    e.g. "f3!", "xh3+", "g8", "0-0", "1-0", etc.
    piece_hint is the piece letter implied by the <img> tag, or None.
    """
    text = clean_move_text(p_tag.get_text())

    # Game result?
    if RESULT_RE.match(text):
        return None, text.strip(), None

    # Castling shorthand  (e.g. "5  0-0" or "12   0-0-0")
    castling_m = CASTLING_RE.search(text)
    if castling_m:
        is_black = "..." in text
        fragment = castling_m.group().replace("0", "O")  # normalise digit → letter
        ann_m = ANNOTATION_RE.search(text[castling_m.end():])
        if ann_m:
            fragment += ann_m.group()
        return is_black, fragment, None

    # Piece hint from the first <img> inside the paragraph
    piece_hint = None
    img = p_tag.find("img")
    if img:
        src = img.get("src", "")
        piece_hint = IMAGE_PIECE_HINTS.get(src)

    # is_black determined by presence of "..."
    is_black = "..." in text

    # Strip leading move-number prefix.  Patterns:
    #   "12   ...  xh3+"   →  "xh3+"
    #   "2   f3!"          →  "f3!"
    #   "17  h1 (D)"       →  "h1"    (already cleaned, just "h1")
    # Remove <number> <dots?> from start, leaving just the square/capture part.
    text = re.sub(r"^\d+\s*\.{0,3}\s*", "", text).strip()

    return is_black, text, piece_hint


def _fix_ocr(text: str) -> str:
    """Fix common OCR digitisation errors in move text.
    Lowercase 'l' is often scanned as digit '1' in square coordinates."""
    # e.g. "hl" → "h1",  "Rxhl" → "Rxh1"
    return re.sub(r"([a-hA-H])l\b", lambda m: m.group(1) + "1", text)


def build_san(fragment: str, board: chess.Board, piece_hint: str | None) -> str | None:
    """
    Given a raw move fragment (e.g. "f3!", "xh3+", "g8", "e4"),
    find a legal SAN by trying piece prefixes.  Returns the SAN string
    (without annotation suffix — those are added via NAG by the caller)
    or None if nothing works.
    """
    # Fix OCR artefacts then strip annotation so parse_san gets clean SAN.
    fragment = _fix_ocr(fragment)
    ann_m = ANNOTATION_RE.search(fragment)
    core = fragment[: ann_m.start()] if ann_m else fragment

    # Build the ordered list of piece prefixes to try.
    # In figurine notation, ALL piece moves carry an image; moves without
    # an image are almost always pawn moves.  Reflect that priority here.
    if piece_hint is None:
        # No image → pawn first, then pieces as fallback
        hints_to_try = [""] + [p for p in FALLBACK_ORDER if p != ""]
    else:
        hints_to_try = [piece_hint]
        for p in FALLBACK_ORDER:
            if p not in hints_to_try:
                hints_to_try.append(p)

    for piece in hints_to_try:
        candidate = piece + core           # pure SAN, no annotation
        try:
            board.parse_san(candidate)
            return candidate              # legal — done
        except Exception:
            pass

    return None


def extract_games(epub_path: str):
    """Extract all annotated games from the EPUB as chess.pgn.Game objects."""
    games = []

    with zipfile.ZipFile(epub_path) as z:
        available = set(z.namelist())

        for game_file in GAME_FILES:
            if game_file not in available:
                continue

            with z.open(game_file) as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            # Game header
            h2 = soup.find("h2")
            if h2 is None:
                continue   # not a game chapter

            header = parse_game_header(h2)
            if not header["white"] or header["white"] == "?":
                continue   # skip non-game files

            pgn_game = chess.pgn.Game()
            pgn_game.headers["Event"] = header["event"]
            pgn_game.headers["Site"] = "?"
            pgn_game.headers["Date"] = f"{header['year']}.??.??"
            pgn_game.headers["Round"] = "?"
            pgn_game.headers["White"] = header["white"]
            pgn_game.headers["Black"] = header["black"]
            pgn_game.headers["Result"] = "*"
            pgn_game.headers["Annotator"] = header["game_num"]
            if header["opening"]:
                pgn_game.headers["Opening"] = header["opening"]

            board = chess.Board()
            node = pgn_game
            pending_comments = []   # commentary collected before next move

            # Any paragraphs before the first <p class="center"> are a game intro
            intro_paragraphs = []

            for p in soup.find_all("p"):
                cls = p.get("class", [])

                if "center" in cls:
                    # --- MOVE PARAGRAPH ---
                    is_black, fragment, piece_hint = extract_move_info(p)

                    # Collect intro text (before move 1) as a game-level comment
                    if intro_paragraphs and node is pgn_game:
                        pgn_game.comment = " ".join(intro_paragraphs)
                        intro_paragraphs = []

                    # Game result token?
                    if RESULT_RE.match(fragment):
                        pgn_game.headers["Result"] = fragment.strip()
                        # Flush any remaining comments onto last move node
                        if pending_comments and node is not pgn_game:
                            node.comment = " ".join(pending_comments)
                            pending_comments = []
                        continue

                    # Flush accumulated commentary onto the PREVIOUS move node
                    if pending_comments and node is not pgn_game:
                        node.comment = " ".join(pending_comments)
                        pending_comments = []

                    # Build SAN and push onto board + game tree
                    san = build_san(fragment, board, piece_hint)
                    if san is None:
                        # Can't parse — skip silently (usually stray text)
                        continue

                    try:
                        move = board.parse_san(san)
                        board.push(move)
                        node = node.add_variation(move)
                        # Carry over annotation as NAG
                        ann_m = ANNOTATION_RE.search(_fix_ocr(fragment))
                        if ann_m:
                            nag = NAG_MAP.get(ann_m.group())
                            if nag is not None:
                                node.nags.add(nag)
                    except Exception:
                        pass   # move already validated in build_san; shouldn't happen

                else:
                    # --- COMMENTARY PARAGRAPH ---
                    text = " ".join(p.get_text().split()).strip()
                    if not text:
                        continue

                    if node is pgn_game:
                        # Still in the intro — collect separately
                        intro_paragraphs.append(text)
                    else:
                        pending_comments.append(text)

            # Flush final comments
            if pending_comments and node is not pgn_game:
                node.comment = " ".join(pending_comments)

            games.append(pgn_game)

    return games


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "logical_chess_annotated.pgn"

    print(f"Reading {EPUB_PATH} …")
    games = extract_games(EPUB_PATH)
    print(f"Extracted {len(games)} games.")

    with open(output_path, "w", encoding="utf-8") as out:
        for game in games:
            exporter = chess.pgn.StringExporter(
                headers=True, variations=False, comments=True
            )
            out.write(game.accept(exporter))
            out.write("\n\n")

    print(f"Written to {output_path}\n")

    for i, game in enumerate(games, 1):
        h = game.headers
        plies = sum(1 for _ in game.mainline())
        print(
            f"  {i:2}. {h.get('Annotator',''):<8}  "
            f"{h.get('White','?')} vs {h.get('Black','?')}  "
            f"({h.get('Event','?')}, {h.get('Date','?')[:4]})  "
            f"{plies} plies  {h.get('Result','*')}"
        )


if __name__ == "__main__":
    main()
