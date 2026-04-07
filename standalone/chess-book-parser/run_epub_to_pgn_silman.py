#!/usr/bin/env python3
"""
Extract annotated games from "How to Reassess Your Chess" (4th ed.) by Jeremy Silman
and output as an annotated PGN.

The EPUB structure (Calibre-converted, Siles Press edition) uses:
  <h6 class="article">   — game header: "White - Black, Event Location Year"
  <p class="firstline-indent-basic-text*"> with <span class="bold"> — main-line moves
  <div class="nobreak">   — diagrams (images); anonymous ones don't break the game
  <p class="caption1">   with <span class="bold"> containing player names — diagram positions

Strategy:
  • Complete games (h6.article) are extracted with move validation via python-chess.
  • Diagram-only positions (caption1 with player names) are recorded as PGN games
    with a [FEN "?"] tag and their commentary, but moves are NOT validated (no FEN).
  • Only the main line is extracted (variations in commentary are skipped).

Usage:
    python run_epub_to_pgn_silman.py [output.pgn]
    (defaults to silman_reassess_annotated.pgn)
"""

import re
import sys
import zipfile
import glob

import chess
import chess.pgn
from bs4 import BeautifulSoup, NavigableString

# ── Configuration ─────────────────────────────────────────────────────────────

EPUB_PATH = glob.glob("books/*reassess*.epub")[0]

# HTML files in reading order (from content.opf or toc.ncx)
# We'll enumerate text/part*.html in sorted order; skip front matter
SKIP_PARTS = {f"text/part{i:04d}.html" for i in range(7)}  # 0000–0006 = front matter
# Also skip test/answer sections (part0063 onwards are tests) — keep all for completeness

NAG_MAP = {
    "!":  chess.pgn.NAG_GOOD_MOVE,
    "?":  chess.pgn.NAG_MISTAKE,
    "!!": chess.pgn.NAG_BRILLIANT_MOVE,
    "??": chess.pgn.NAG_BLUNDER,
    "!?": chess.pgn.NAG_SPECULATIVE_MOVE,
    "?!": chess.pgn.NAG_DUBIOUS_MOVE,
}

# Matches individual move tokens with optional annotation suffix.
# Handles en-dash (–) in castling notation (common in Calibre EPUB conversions).
MOVE_TOKEN_RE = re.compile(
    r"(?:\d+\.{1,3}\s*)?"                                            # optional move number
    r"("                                                               # capture: SAN base
    r"[O0][-\u2013][O0][-\u2013][O0][+#]?"                          #   queenside castling (- or –)
    r"|[O0][-\u2013][O0][+#]?"                                       #   kingside castling (- or –)
    r"|[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQK])?[+#]?"        #   regular move
    r")"
    r"([!?]{0,2})"                                                    # annotation suffix
)

RESULT_RE = re.compile(r"(?:^|\s)(1-0|0-1|1/2-1/2|\*)\s*$")

# ── Header parsing ─────────────────────────────────────────────────────────────

# Player separator: " - ", " – ", " vs. ", " vs " (all variants seen in book)
PLAYER_SEP = re.compile(r"\s+(?:[-–]|vs\.?)\s+")
# Use LAST 4-digit number >= 1800 as year (avoids rating numbers like 1539)
YEAR_RE = re.compile(r"(?<!\()\b(1[89]\d{2}|20[012]\d)\b")


def parse_game_header(raw: str) -> dict:
    """Parse 'White - Black, Event Location Year' from h6.article text."""
    raw = raw.strip()
    white = black = "?"
    event = "?"
    year = "????"

    # Split on first player separator
    sep_m = PLAYER_SEP.search(raw)
    if sep_m:
        white = raw[: sep_m.start()].strip()
        rest = raw[sep_m.end():]
        # rest = "Black, Event Location Year"
        # Find LAST plausible year (>= 1800, not after a parenthesis = not a rating)
        all_years = list(YEAR_RE.finditer(rest))
        if all_years:
            year_m = all_years[-1]   # use rightmost plausible year
            year = year_m.group(1)
            before_year = rest[: year_m.start()].strip().rstrip(",")
            # Remove any rating annotations like "(1539)" from before_year
            before_year = re.sub(r"\s*\(\d{3,4}\)", "", before_year).strip()
            # Split before_year into Black and Event
            comma = before_year.find(",")
            if comma >= 0:
                black = before_year[:comma].strip()
                event = before_year[comma + 1:].strip()
            else:
                black = before_year.strip()
                event = "?"
        else:
            # No year found — strip rating from rest and use as Black
            rest_clean = re.sub(r"\s*\(\d{3,4}\)", "", rest).strip()
            comma = rest_clean.find(",")
            if comma >= 0:
                black = rest_clean[:comma].strip()
                event = rest_clean[comma + 1:].strip()
            else:
                black = rest_clean.strip()
    else:
        event = raw

    return {"white": white, "black": black, "event": event, "year": year}


def parse_diagram_caption(caption_text: str) -> dict:
    """
    Parse diagram captions like:
      "A. Bisguier - R. Fischer, Bled 1961Black to move"
      "Nguyen Anh Dung - J. Waitzkin, Szeged 1994White to move"
    Returns header dict plus 'side_to_move'.
    """
    # Strip trailing "White/Black to move"
    side = "*"
    cap = caption_text
    m = re.search(r"(White|Black)\s+to\s+move", cap, re.IGNORECASE)
    if m:
        side = "w" if m.group(1).lower() == "white" else "b"
        cap = cap[: m.start()].strip()
    hdr = parse_game_header(cap)
    hdr["side_to_move"] = side
    return hdr


# ── SAN normalisation & move application ──────────────────────────────────────

def _normalise(san: str) -> str:
    # Normalise digit zeros and en-dashes to letter-O with hyphens for python-chess
    s = san.replace("\u2013", "-")   # en-dash → hyphen
    s = s.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    return s


def apply_moves(text: str, board: chess.Board, node: chess.pgn.GameNode):
    """
    Parse all move tokens from text and push them onto board/node.
    Returns (final_node, result_str_or_None).
    """
    result = None
    rm = RESULT_RE.search(text)
    if rm:
        result = rm.group(1)
        text = text[: rm.start()]

    for m in MOVE_TOKEN_RE.finditer(text):
        san, ann = m.group(1), m.group(2)
        if not san:
            continue
        try:
            move = board.parse_san(_normalise(san))
            board.push(move)
            node = node.add_variation(move)
            if ann in NAG_MAP:
                node.nags.add(NAG_MAP[ann])
        except Exception:
            pass  # skip illegal/unparseable tokens

    return node, result


# ── Paragraph text extraction ──────────────────────────────────────────────────

def para_text(p_tag) -> str:
    """Return clean prose text of a paragraph (strip excess whitespace)."""
    return " ".join(p_tag.get_text().split()).strip()


def para_bold_spans(p_tag) -> list[str]:
    """Return text from all <span class='bold'> inside the paragraph."""
    return [" ".join(s.get_text().split()) for s in p_tag.find_all("span", class_="bold")]


# ── Main extraction ────────────────────────────────────────────────────────────

COMPLETE_GAME_CLASSES = re.compile(
    r"firstline-indent-basic-text|firstline-indent-basic-text-sa|"
    r"firstline-indent-basic-text-sb|firstline-indent-basic-text-sb1|"
    r"firstline-indent-basic-text-sa1"
)

COMMENTARY_CLASSES = re.compile(
    r"firstline-indent-basic-text|list|float-text|nobreak|para-title|"
    r"chapter-quote|chapter-descripton"
)


def is_player_caption(bold_text: str) -> bool:
    """Return True if the bold text from a caption1 includes player names (not just to-move)."""
    if re.search(r"(White|Black)\s+to\s+move", bold_text, re.IGNORECASE) and not PLAYER_SEP.search(bold_text):
        return False
    return bool(PLAYER_SEP.search(bold_text) and YEAR_RE.search(bold_text))


def extract_games(epub_path: str):
    """
    Returns a list of (pgn_game, game_type) tuples where game_type is
    'complete' or 'diagram'.
    """
    all_games = []

    with zipfile.ZipFile(epub_path) as z:
        html_files = sorted(
            name for name in z.namelist()
            if name.startswith("text/part") and name.endswith(".html")
        )

        for fname in html_files:
            if fname in SKIP_PARTS:
                continue

            with z.open(fname) as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            # ── State ──
            pgn_game = None           # current game being built
            board = None
            node = None
            pending_comment = []     # commentary paragraphs before next move
            game_type = "complete"
            result = None
            post_game_comment = []   # inter-game text after result, to be next game's root comment
            game_ended = False       # True after game result token found

            def save_game():
                """Flush pending comment, append game to list."""
                nonlocal pgn_game, pending_comment, game_ended
                if pgn_game is None:
                    return
                if pending_comment and node is not pgn_game:
                    node.comment = " ".join(pending_comment) if not node.comment else node.comment + " " + " ".join(pending_comment)
                elif pending_comment:
                    pgn_game.comment = " ".join(pending_comment) if not pgn_game.comment else pgn_game.comment + " " + " ".join(pending_comment)
                pending_comment = []
                all_games.append((pgn_game, game_type))
                pgn_game = None
                game_ended = False

            def start_complete_game(hdr):
                nonlocal pgn_game, board, node, pending_comment, post_game_comment, game_type, result, game_ended
                save_game()
                pgn_game = chess.pgn.Game()
                pgn_game.headers["Event"] = hdr["event"] or "?"
                pgn_game.headers["Site"] = "?"
                pgn_game.headers["Date"] = f"{hdr['year']}.??.??"
                pgn_game.headers["Round"] = "?"
                pgn_game.headers["White"] = hdr["white"]
                pgn_game.headers["Black"] = hdr["black"]
                pgn_game.headers["Result"] = "*"
                pgn_game.headers["Annotator"] = "J. Silman"
                # Intro text (pre-game prose + inter-game text) becomes the root comment
                intro = pending_comment + post_game_comment
                if intro:
                    pgn_game.comment = " ".join(intro)
                pending_comment = []
                post_game_comment = []
                board = chess.Board()
                node = pgn_game
                game_type = "complete"
                result = None
                game_ended = False

            def start_diagram_game(hdr):
                nonlocal pgn_game, board, node, pending_comment, post_game_comment, game_type, result, game_ended
                save_game()
                pgn_game = chess.pgn.Game()
                pgn_game.headers["Event"] = hdr["event"] or "?"
                pgn_game.headers["Site"] = "?"
                pgn_game.headers["Date"] = f"{hdr['year']}.??.??"
                pgn_game.headers["Round"] = "?"
                pgn_game.headers["White"] = hdr["white"]
                pgn_game.headers["Black"] = hdr["black"]
                pgn_game.headers["Result"] = "*"
                pgn_game.headers["Annotator"] = "J. Silman"
                # Intro text becomes root comment; add diagram marker
                intro = pending_comment + post_game_comment
                diagram_marker = "[Position from diagram — moves are illustrative only]"
                pgn_game.comment = (" ".join(intro) + " " + diagram_marker).strip() if intro else diagram_marker
                pending_comment = []
                post_game_comment = []
                board = None
                node = pgn_game
                game_type = "diagram"
                result = None
                game_ended = False

            def add_comment(text):
                nonlocal pending_comment, post_game_comment
                if text:
                    # Strip leading result/punctuation artifacts
                    text = re.sub(r'^[,;:.]+\s*(?:1-0|0-1|1/2-1/2)\.?\s*', '', text)
                    text = re.sub(r'^[,;:.»«\u2013\u2014]+\s*', '', text)
                    # Strip standalone results
                    if re.match(r'^(?:1-0|0-1|1/2-1/2)\.?\s*$', text):
                        text = ''
                    if text.strip():
                        if game_ended:
                            post_game_comment.append(text.strip())
                        else:
                            pending_comment.append(text.strip())

            def flush_comment_to_node(n):
                nonlocal pending_comment
                if not pending_comment:
                    return
                txt = " ".join(pending_comment)
                pending_comment = []
                if n is pgn_game:
                    if pgn_game.comment:
                        pgn_game.comment += " " + txt
                    else:
                        pgn_game.comment = txt
                else:
                    if n.comment:
                        n.comment += " " + txt
                    else:
                        n.comment = txt

            # ── Walk document elements ──
            for tag in soup.find_all(
                ["h6", "h2", "h3", "p", "div"],
                recursive=True
            ):
                cls = tag.get("class", [])

                # ── Game header (complete game) ──
                if tag.name == "h6" and "article" in cls:
                    raw = tag.get_text(" ").strip()
                    if raw:
                        hdr = parse_game_header(raw)
                        start_complete_game(hdr)
                    continue

                # ── Diagram div ──
                if tag.name == "div" and "nobreak" in cls:
                    # Extract caption text
                    cap_tag = tag.find("p", class_="caption1")
                    if cap_tag:
                        cap_bold = cap_tag.find("span", class_="bold")
                        cap_text = cap_bold.get_text(" ").strip() if cap_bold else cap_tag.get_text().strip()
                        if is_player_caption(cap_text):
                            # Diagram position game - save current, start diagram
                            hdr = parse_diagram_caption(cap_text)
                            start_diagram_game(hdr)
                        else:
                            # Anonymous diagram - just add "[Diagram]" to commentary
                            add_comment("[Diagram]")
                    else:
                        # Diagram without caption
                        diag_num = tag.find("p", class_=re.compile("diagram-number"))
                        if diag_num:
                            add_comment(f"[{diag_num.get_text().strip()}]")
                    continue

                # Skip sub-tags of nobreak (already processed above)
                if tag.parent and "nobreak" in (tag.parent.get("class") or []):
                    continue

                # ── Main content paragraphs ──
                if tag.name != "p":
                    # h2/h3 chapter headings - add as commentary context
                    if tag.name in ("h2", "h3"):
                        txt = tag.get_text().strip()
                        if txt and pgn_game is not None:
                            add_comment(f"[{txt}]")
                    continue

                # Paragraph: check class
                cls_str = cls[0] if cls else ""

                # Caption paragraphs are handled inside nobreak div above
                if "caption1" in cls or "diagram-number" in cls_str:
                    continue

                # Float titles (rule boxes)
                if "float-title" in cls:
                    continue  # skip the label
                if "float-text" in cls:
                    txt = para_text(tag)
                    if txt and pgn_game is not None:
                        add_comment(f"[Rule: {txt}]")
                    continue

                # Main content paragraphs (moves + commentary)
                if COMPLETE_GAME_CLASSES.search(cls_str) or any(
                    c in cls for c in ["list", "list-1-last", "list-1-l"]
                ):
                    if pgn_game is None:
                        # No game active: collect prose as pre-game intro text
                        text = para_text(tag)
                        if text:
                            add_comment(text)
                        continue

                    # If a result was already found in a previous bold span, any
                    # new paragraph's content belongs to the next game.
                    if result and not game_ended:
                        game_ended = True

                    bold_spans = para_bold_spans(tag)
                    plain_text = para_text(tag)

                    if not bold_spans:
                        # Pure commentary
                        add_comment(plain_text)
                        continue

                    # Has bold content = moves — walk children in order so inline
                    # commentary between bold spans attaches to the preceding move.
                    flush_comment_to_node(node)

                    last_move_node = node  # node before any moves in this paragraph
                    inline_parts = []      # text accumulated since last game-move bold span
                    paren_depth = 0        # parenthesis nesting (commentary refs are inside parens)

                    for child in tag.children:
                        if isinstance(child, NavigableString):
                            t = str(child)
                            t_strip = t.strip()
                            if t_strip:
                                inline_parts.append(t_strip)
                            paren_depth += t.count('(') - t.count(')')
                            paren_depth = max(0, paren_depth)
                        elif child.name == "span" and "bold" in (child.get("class") or []):
                            bspan = " ".join(child.get_text().split())
                            if not bspan:
                                continue
                            if paren_depth > 0:
                                # Inside parentheses — move reference in commentary, not a game move
                                inline_parts.append(bspan)
                            else:
                                # Game moves — flush accumulated inline commentary to preceding node
                                if inline_parts:
                                    txt = " ".join(inline_parts).strip()
                                    txt = re.sub(r'^[,;:.]+\s*(?:1-0|0-1|1/2-1/2)\.?\s*', '', txt)
                                    txt = re.sub(r'^[,;:.»«\u2013\u2014]+\s*', '', txt)
                                    if re.match(r'^(?:1-0|0-1|1/2-1/2)\.?\s*$', txt):
                                        txt = ''
                                    if txt.strip():
                                        if last_move_node is pgn_game:
                                            pgn_game.comment = ((pgn_game.comment + " " + txt) if pgn_game.comment else txt).strip()
                                        else:
                                            last_move_node.comment = ((last_move_node.comment + " " + txt) if last_move_node.comment else txt).strip()
                                    inline_parts = []

                                if board is not None:
                                    node, r = apply_moves(bspan, board, node)
                                    if r:
                                        result = r
                                        game_ended = True
                                else:
                                    # Diagram game: store as text in comment
                                    if node is pgn_game:
                                        pgn_game.comment = ((pgn_game.comment + " " + bspan) if pgn_game.comment else bspan).strip()
                                    else:
                                        node.comment = ((node.comment + " " + bspan) if node.comment else bspan).strip()
                                last_move_node = node
                        else:
                            # Other spans (italic, etc.) — include text as commentary
                            t = child.get_text().strip()
                            if t:
                                inline_parts.append(t)

                    # Remaining text after the last bold span goes to pending_comment
                    if inline_parts:
                        commentary = " ".join(inline_parts).strip()
                        if commentary:
                            add_comment(commentary)

            # End of file: save last game
            save_game()

    # Set final result headers
    for pgn_game, _ in all_games:
        if pgn_game.headers.get("Result") == "*":
            # Try to infer from mainline
            pass  # leave as * if unknown

    return all_games


# ── Output ────────────────────────────────────────────────────────────────────

def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "silman_reassess_annotated.pgn"

    print(f"Reading {EPUB_PATH} ...")
    games = extract_games(EPUB_PATH)

    # Remove empty games (section headers with no content)
    before = len(games)
    games = [(g, t) for g, t in games
             if sum(1 for _ in g.mainline()) > 0 or g.comment]
    if before != len(games):
        print(f"Filtered out {before - len(games)} empty games.")

    # Strip NAGs — the web app parser can't handle $N tokens.
    # Prepend NAG symbol to existing comment; drop NAGs with no comment.
    NAG_TO_SYMBOL = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!"}
    nag_count = 0
    for pgn_game, gtype in games:
        for move_node in pgn_game.mainline():
            if move_node.nags:
                if move_node.comment:
                    symbols = "".join(NAG_TO_SYMBOL.get(n, "") for n in sorted(move_node.nags))
                    if symbols:
                        move_node.comment = f"({symbols}) {move_node.comment}"
                        nag_count += 1
                move_node.nags.clear()
    if nag_count:
        print(f"Converted {nag_count} NAG annotations to inline comment symbols.")

    complete = sum(1 for _, t in games if t == "complete")
    diagrams = sum(1 for _, t in games if t == "diagram")
    print(f"Extracted {len(games)} games ({complete} complete, {diagrams} diagram positions).")

    with open(output_path, "w", encoding="utf-8") as out:
        for pgn_game, gtype in games:
            exporter = chess.pgn.StringExporter(
                headers=True, variations=False, comments=True
            )
            out.write(pgn_game.accept(exporter))
            out.write("\n\n")

    print(f"Written to {output_path}\n")

    # Summary
    for i, (pgn_game, gtype) in enumerate(games, 1):
        h = pgn_game.headers
        plies = sum(1 for _ in pgn_game.mainline())
        print(
            f"  {i:3}. [{gtype[:4].upper()}] "
            f"{h.get('White','?')} vs {h.get('Black','?')}  "
            f"({h.get('Event','?')}, {h.get('Date','?')[:4]})  "
            f"{plies} plies  {h.get('Result','*')}"
        )


if __name__ == "__main__":
    main()
