"""HTML-aware parser for structured chess EPUBs.

Extracts MOVETEXT hidden inputs, game headers, and commentary from
EPUB HTML files that contain structured chess data (e.g., Everyman Chess format).
"""

import re
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

import chess.pgn
from bs4 import BeautifulSoup, NavigableString

from chess_book_parser.parsers.movetext import parse_movetext


def has_movetext_data(epub_path: str) -> bool:
    """Check if an EPUB contains structured MOVETEXT data."""
    try:
        with zipfile.ZipFile(epub_path) as zf:
            for name in zf.namelist():
                if not name.endswith(".html"):
                    continue
                html = zf.read(name).decode("utf-8", errors="replace")
                # Look for a non-empty MOVETEXT input
                if re.search(
                    r'id="MOVETEXT\d+"[^>]*value="root\s+\d', html
                ):
                    return True
        return False
    except (zipfile.BadZipFile, OSError):
        return False


def parse_structured_epub(epub_path: str) -> List[chess.pgn.Game]:
    """Parse a structured EPUB into a list of annotated PGN games.

    Args:
        epub_path: Path to the EPUB file.

    Returns:
        List of chess.pgn.Game objects with headers and commentary.
    """
    chapters = _get_chapter_groups(epub_path)
    all_games = []

    for chapter_name, html_bytes_list in chapters:
        # Merge split files into a single soup for processing
        merged_soup = _merge_html_files(html_bytes_list)
        games = _extract_games_from_soup(merged_soup, chapter_name)
        all_games.extend(games)

    return all_games


def _get_chapter_groups(
    epub_path: str,
) -> List[Tuple[str, List[bytes]]]:
    """Read EPUB and return chapter groups with merged split files.

    Groups split files (e.g., Chapter 3 split_000, split_001) together
    so they can be processed as a single unit. This is necessary because
    game content can span split file boundaries.

    Returns:
        List of (chapter_name, [html_bytes, ...]) tuples.
    """
    with zipfile.ZipFile(epub_path) as zf:
        manifest = _parse_opf_manifest(zf)
        spine_ids = _parse_opf_spine(zf)

        ordered_files = []
        for item_id in spine_ids:
            href = manifest.get(item_id)
            if href and href.endswith(".html"):
                decoded = href.replace("%20", " ")
                if decoded in zf.namelist():
                    ordered_files.append(decoded)

        # Group files by chapter prefix (split files share a prefix)
        chapters: List[Tuple[str, List[bytes]]] = []
        current_chapter_name = None
        current_group: List[bytes] = []
        current_prefix = None

        for filename in ordered_files:
            if _should_skip_file(filename):
                continue

            html_bytes = zf.read(filename)

            # Detect chapter name
            chapter_name = _detect_chapter_name(html_bytes, filename, current_chapter_name)
            if chapter_name:
                current_chapter_name = chapter_name

            if not _has_real_movetext(html_bytes):
                continue

            # Determine the chapter prefix for grouping split files
            prefix = _get_chapter_prefix(filename)

            if prefix != current_prefix and current_group:
                # New chapter group - save the previous one
                chapters.append((chapters_name_for_group, current_group))
                current_group = []

            if prefix != current_prefix:
                chapters_name_for_group = current_chapter_name or "Unknown Chapter"

            current_prefix = prefix
            current_group.append(html_bytes)

        # Don't forget the last group
        if current_group:
            chapters.append((chapters_name_for_group, current_group))

        return chapters


def _get_chapter_prefix(filename: str) -> str:
    """Get the chapter prefix for grouping split files.

    '8_Chapter 3_converted_split_000.html' -> '8_Chapter 3'
    '6_Chapter 1_converted.html' -> '6_Chapter 1'
    """
    # Remove split suffix and extension
    base = re.sub(r"_converted(_split_\d+)?\.html$", "", filename)
    return base


def _merge_html_files(html_bytes_list: List[bytes]) -> BeautifulSoup:
    """Merge multiple HTML files into a single BeautifulSoup for processing.

    Combines all hidden inputs and body content from split files into one
    document, preserving reading order.
    """
    if len(html_bytes_list) == 1:
        return BeautifulSoup(html_bytes_list[0], "html.parser")

    # Parse all files and merge their body content
    merged_inputs = []
    merged_body_elements = []

    for html_bytes in html_bytes_list:
        soup = BeautifulSoup(html_bytes, "html.parser")

        # Collect hidden inputs (MOVETEXTs and FENs)
        for inp in soup.find_all("input", type="hidden"):
            merged_inputs.append(str(inp))

        # Collect all body-level elements (paragraphs, etc.)
        body = soup.find("body")
        if body:
            for child in body.children:
                if hasattr(child, "name") and child.name:
                    merged_body_elements.append(str(child))
        else:
            # No body tag - collect all p, div, input elements
            for elem in soup.find_all(["p", "div", "input"]):
                merged_body_elements.append(str(elem))

    # Build merged HTML
    merged_html = "<html><body>"
    merged_html += "\n".join(merged_inputs)
    merged_html += "\n".join(merged_body_elements)
    merged_html += "</body></html>"

    return BeautifulSoup(merged_html, "html.parser")


def _parse_opf_manifest(zf: zipfile.ZipFile) -> Dict[str, str]:
    """Parse the OPF manifest to get id -> href mapping."""
    opf_content = zf.read("content.opf").decode("utf-8")
    manifest = {}
    for match in re.finditer(
        r'<item\s+id="([^"]+)"\s+href="([^"]+)"', opf_content
    ):
        manifest[match.group(1)] = match.group(2)
    return manifest


def _parse_opf_spine(zf: zipfile.ZipFile) -> List[str]:
    """Parse the OPF spine to get reading order."""
    opf_content = zf.read("content.opf").decode("utf-8")
    return re.findall(r'<itemref\s+idref="([^"]+)"', opf_content)


def _should_skip_file(filename: str) -> bool:
    """Check if a file should be skipped (front matter, indices)."""
    lower = filename.lower()
    skip_patterns = [
        "contents_converted",
        "title page_converted",
        "about the authors_converted",
        "preface_converted",
        "index of variations_converted",
        "index of complete games_converted",
        "titlepage",
    ]
    return any(pat in lower for pat in skip_patterns)


def _has_real_movetext(html_bytes: bytes) -> bool:
    """Check if HTML contains at least one non-empty MOVETEXT."""
    html = html_bytes.decode("utf-8", errors="replace")
    for match in re.finditer(r'value="(root\s*[^"]*)"', html):
        value = match.group(1).strip()
        if value and value != "root" and len(value) > 5:
            return True
    return False


def _detect_chapter_name(
    html_bytes: bytes, filename: str, fallback: Optional[str]
) -> Optional[str]:
    """Extract chapter name from HTML head elements or filename."""
    soup = BeautifulSoup(html_bytes, "html.parser")

    # Look for <p class="head"> elements (chapter titles)
    heads = soup.find_all("p", class_="head")
    parts = []
    for h in heads:
        text = h.get_text(strip=True)
        if text:
            parts.append(text)

    if parts:
        return " - ".join(parts)

    # For split files, carry forward the chapter name from the first split
    if "_split_" in filename and fallback:
        return fallback

    # Extract from filename as last resort
    match = re.search(r"Chapter\s+(\d+)", filename, re.IGNORECASE)
    if match:
        return f"Chapter {match.group(1)}"

    # Check for Introduction
    if "introduction" in filename.lower():
        return "Introduction"

    return fallback


def _extract_games_from_soup(
    soup: BeautifulSoup, chapter_name: str
) -> List[chess.pgn.Game]:
    """Process parsed HTML: extract MOVETEXTs, game headers, commentary.

    Returns:
        List of chess.pgn.Game objects with headers and comments set.
    """
    # Extract all MOVETEXTs and FENs
    movetexts = _extract_movetexts(soup)
    fens = _extract_fens(soup)

    if not movetexts:
        return []

    # Extract game headers (game_index -> header info)
    game_headers = _extract_game_headers(soup)

    games = []
    for idx, movetext_value in sorted(movetexts.items()):
        fen = fens.get(idx)
        game, mapping = parse_movetext(movetext_value, fen)
        if game is None:
            continue

        # Set PGN headers
        header = game_headers.get(idx)
        _set_game_headers(game, header, chapter_name, idx)

        # Extract and merge commentary
        comments = _extract_commentary(soup, idx)
        _merge_comments(game, mapping, comments)

        # Extract game result from HTML
        result = _extract_result(soup, idx)
        if result:
            game.headers["Result"] = result

        games.append(game)

    return games


def _extract_movetexts(soup: BeautifulSoup) -> Dict[int, str]:
    """Extract MOVETEXT hidden inputs: {index: value}."""
    result = {}
    for inp in soup.find_all("input", type="hidden"):
        id_attr = inp.get("id", "")
        match = re.match(r"MOVETEXT(\d+)", id_attr)
        if match:
            value = inp.get("value", "").strip()
            if value and value != "root" and len(value) > 5:
                result[int(match.group(1))] = value
    return result


def _extract_fens(soup: BeautifulSoup) -> Dict[int, str]:
    """Extract FEN hidden inputs: {index: fen_string}."""
    result = {}
    for inp in soup.find_all("input", type="hidden"):
        id_attr = inp.get("id", "")
        match = re.match(r"FEN(\d+)", id_attr)
        if match:
            value = inp.get("value", "").strip()
            if value:
                result[int(match.group(1))] = value
    return result


def _extract_game_headers(soup: BeautifulSoup) -> Dict[int, Dict]:
    """Parse <p class="game"> elements into game header dicts.

    Maps each game header to its MOVETEXT index by finding the first
    gXmYvZ span that follows it in document order. This handles the fact
    that game anchor numbering (game1, game2...) is global across the book,
    while gX indices are per-file matching MOVETEXT indices.

    Returns:
        {movetext_index: {"number": "Game N", "white": "...", "black": "...", "event": "..."}}
    """
    headers = {}

    # Walk all paragraphs in order to map game headers to gX indices.
    # Only check bold paragraphs (mainline moves) since normal1 paragraphs
    # between a game header and first bold may reference the previous game.
    pending_header = None
    for p in soup.find_all("p"):
        classes = p.get("class", [])

        if "game" in classes:
            pending_header = _parse_game_header_element(p)
            continue

        if pending_header is not None and "bold" in classes:
            spans = p.find_all("span", attrs={"name": re.compile(r"^g\d+m")})
            for span in spans:
                name = span.get("name", "")
                match = re.match(r"g(\d+)m", name)
                if match:
                    game_idx = int(match.group(1))
                    headers[game_idx] = pending_header
                    pending_header = None
                    break

    return headers


def _parse_game_header_element(game_p) -> Dict:
    """Parse a single <p class="game"> element into a header dict."""
    info = {"number": "", "white": "Study", "black": "Analysis", "event": ""}

    # Game number from italic span
    italic = game_p.find("span", class_="italic")
    if italic:
        info["number"] = italic.get_text(strip=True)

    # Player names from bold1 span
    bold1 = game_p.find("span", class_="bold1")
    if bold1:
        players_text = bold1.get_text(strip=True)
        # Split on dash variants: -, –, —
        parts = re.split(r"\s*[-–—]\s*", players_text, maxsplit=1)
        if len(parts) == 2:
            info["white"] = parts[0].strip()
            info["black"] = parts[1].strip()
        elif parts:
            info["white"] = parts[0].strip()

    # Event info: remaining text after bold1 and italic
    full_text = game_p.get_text(strip=True)
    event_text = full_text
    if info["number"]:
        event_text = event_text.replace(info["number"], "", 1)
    if bold1:
        event_text = event_text.replace(bold1.get_text(strip=True), "", 1)
    info["event"] = event_text.strip()

    return info


def _set_game_headers(
    game: chess.pgn.Game,
    header: Optional[Dict],
    chapter_name: str,
    movetext_idx: int,
) -> None:
    """Set PGN headers on a game."""
    if header:
        game.headers["White"] = header.get("white", "Study")
        game.headers["Black"] = header.get("black", "Analysis")
        event_parts = [chapter_name]
        if header.get("number"):
            event_parts.append(header["number"])
        game.headers["Event"] = " - ".join(event_parts)
        if header.get("event"):
            game.headers["Site"] = header["event"]
        else:
            game.headers["Site"] = "Chess Book"
    else:
        game.headers["White"] = "Study"
        game.headers["Black"] = "Analysis"
        game.headers["Event"] = f"{chapter_name} - Line {movetext_idx + 1}"
        game.headers["Site"] = "Chess Book"

    game.headers["Result"] = "*"


def _extract_commentary(
    soup: BeautifulSoup, game_idx: int
) -> Dict[Optional[Tuple[int, int]], str]:
    """Extract commentary text for a specific game, keyed by (m, v) position.

    Walks all paragraphs in document order, tracking which game's moves
    are being referenced. Commentary text is associated with the last
    move reference encountered.

    Args:
        soup: Parsed HTML.
        game_idx: The game index (0-based, matching MOVETEXT index).

    Returns:
        Dict mapping (m, v) tuples to commentary text. Key None means
        commentary before any moves (game-level comment).
    """
    comments: Dict[Optional[Tuple[int, int]], str] = {}
    game_prefix = f"g{game_idx}m"
    last_move_ref: Optional[Tuple[int, int]] = None
    in_game_section = False

    for p in soup.find_all("p"):
        classes = p.get("class", [])

        # Check if this paragraph references our game
        spans_in_p = p.find_all("span", attrs={"name": re.compile(f"^g{game_idx}m")})

        # Track if we're in the right game section via bold paragraphs
        if "bold" in classes and spans_in_p:
            in_game_section = True
            # Update last_move_ref to the last move span in this bold paragraph
            for span in spans_in_p:
                mv = _parse_mv_from_span(span, game_idx)
                if mv is not None:
                    last_move_ref = mv
            continue

        # If we see a bold paragraph for a DIFFERENT game, we've left our section
        if "bold" in classes and not spans_in_p:
            other_game_spans = p.find_all("span", attrs={"name": re.compile(r"^g\d+m")})
            if other_game_spans and in_game_section:
                # Check if these are for a different game
                for span in other_game_spans:
                    name = span.get("name", "")
                    if name.startswith("g") and not name.startswith(f"g{game_idx}m"):
                        in_game_section = False
                        break

        # Process commentary paragraphs
        if "normal1" in classes and in_game_section:
            _process_commentary_paragraph(p, game_idx, last_move_ref, comments)
            # Update last_move_ref from this paragraph's spans
            for span in spans_in_p:
                mv = _parse_mv_from_span(span, game_idx)
                if mv is not None:
                    last_move_ref = mv

    return comments


def _parse_mv_from_span(
    span, game_idx: int
) -> Optional[Tuple[int, int]]:
    """Parse a (m, v) tuple from a span's name attribute."""
    name = span.get("name", "")
    match = re.match(rf"g{game_idx}m(\d+)v(\d+)", name)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


def _process_commentary_paragraph(
    p,
    game_idx: int,
    initial_last_ref: Optional[Tuple[int, int]],
    comments: Dict[Optional[Tuple[int, int]], str],
) -> None:
    """Walk a normal1 paragraph's children, extracting text segments between move refs.

    Text before the first move span -> associated with initial_last_ref.
    Text after a move span -> associated with that span's (m, v).
    """
    current_ref = initial_last_ref

    for child in p.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                _append_comment(comments, current_ref, text)

        elif child.name == "span":
            # Check if this is a move reference for our game
            mv = _parse_mv_from_span(child, game_idx)
            if mv is not None:
                current_ref = mv
            else:
                # Not a move ref - might be a chess glyph or other span
                # Check for nested move spans
                inner = child.find("span", attrs={"name": re.compile(rf"^g{game_idx}m")})
                if inner:
                    mv = _parse_mv_from_span(inner, game_idx)
                    if mv is not None:
                        current_ref = mv
                else:
                    # Regular text content (e.g., chess glyph)
                    text = child.get_text().strip()
                    if text and "chess" not in (child.get("class") or []):
                        _append_comment(comments, current_ref, text)

        elif child.name == "br":
            pass
        else:
            # Other elements - extract text
            text = child.get_text().strip()
            if text:
                _append_comment(comments, current_ref, text)


def _append_comment(
    comments: Dict[Optional[Tuple[int, int]], str],
    key: Optional[Tuple[int, int]],
    text: str,
) -> None:
    """Append text to a comment entry, normalizing whitespace."""
    text = text.strip()
    if not text:
        return
    # Sanitize curly braces with square brackets for PGN compatibility
    text = text.replace("{", "[").replace("}", "]")
    # Replace a)/b)/c) list markers with a./b./c.
    text = re.sub(r"\b([a-z])\)", r"\1.", text)
    # Skip pure punctuation noise (lone parens, brackets, etc.)
    if re.match(r"^[()[\];,.\s]+$", text):
        return
    if key in comments:
        comments[key] = comments[key] + " " + text
    else:
        comments[key] = text


def _clean_comment_text(text: str) -> str:
    """Clean up assembled commentary text for PGN output."""
    # Strip unmatched leading (
    while text.startswith("(") and text.count("(") > text.count(")"):
        text = text[1:].lstrip()
    # Strip unmatched trailing )
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1].rstrip()
    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _merge_comments(
    game: chess.pgn.Game,
    mapping: Dict[Tuple[int, int], chess.pgn.GameNode],
    comments: Dict[Optional[Tuple[int, int]], str],
) -> None:
    """Insert commentary into game tree nodes using the (m,v)->node mapping."""
    for key, text in comments.items():
        text = _clean_comment_text(text)
        if not text:
            continue
        if key is None:
            # Game-level comment (before any moves)
            game.comment = _join_comment(game.comment, text)
        else:
            node = mapping.get(key)
            if node is not None:
                node.comment = _join_comment(node.comment, text)


def _extract_result(soup: BeautifulSoup, game_idx: int) -> Optional[str]:
    """Extract game result from HTML bold paragraphs.

    Scans bold paragraphs for this game looking for result tokens
    (1-0, 0-1, ½-½) appearing as text after the last move span.

    Returns:
        PGN result string ("1-0", "0-1", "1/2-1/2") or None.
    """
    result_pattern = re.compile(r"(1-0|0-1|½-½|1/2-1/2)")
    last_result = None

    for p in soup.find_all("p", class_="bold"):
        spans = p.find_all(
            "span", attrs={"name": re.compile(rf"^g{game_idx}m")}
        )
        if not spans:
            continue

        # Look for result tokens in text nodes (outside spans)
        for child in p.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                m = result_pattern.search(text)
                if m:
                    result = m.group(1)
                    if result == "½-½":
                        result = "1/2-1/2"
                    last_result = result

    return last_result


def _join_comment(existing: str, new: str) -> str:
    """Join existing and new comment text."""
    if existing:
        return existing + " " + new
    return new
