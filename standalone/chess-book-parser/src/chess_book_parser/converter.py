#!/usr/bin/env python3
"""
Convert chess books (PDF/EPUB) to a PGN file with all chapters and comments extracted.
"""

import argparse
import re
from pathlib import Path
from typing import List, Dict

MAX_COMMENT_LENGTH = 3000

try:
    import chess
    import chess.pgn
    from PyPDF2 import PdfReader
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    DEPS_AVAILABLE = True
except ImportError as e:
    DEPS_AVAILABLE = False
    MISSING_DEP = str(e)


class BookParser:
    """Parse chess books from various formats."""

    @staticmethod
    def parse_pdf(pdf_path: str) -> str:
        """Extract text from PDF file."""
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"File not found: {pdf_path}")

        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text

    @staticmethod
    def parse_epub(epub_path: str) -> str:
        """Extract text from EPUB file following the reading order (spine)."""
        if not Path(epub_path).exists():
            raise FileNotFoundError(f"File not found: {epub_path}")

        book = epub.read_epub(epub_path)
        text = ""

        # Follow the spine to maintain correct reading order
        for item_id, linear in book.spine:
            item = book.get_item_with_id(item_id)
            if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                text += soup.get_text() + "\n\n"

        if not text.strip():
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    text += soup.get_text() + "\n\n"

        return text

    @staticmethod
    def extract_chapters(text: str, min_content_length: int = 20) -> List[Dict]:
        """Split text into logical chapters based on common headers."""
        patterns = [
            r'(?:^|\n)Chapter\s+(?:\d+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen|Fifteen)[^\n]*',
            r'(?:^|\n)CHAPTER\s+(?:\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN)[^\n]*',
            r'(?:^|\n)Part\s+(?:[IVX\d]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)[^\n]*',
            r'(?:^|\n)\d+\.\s+[A-Z][^\n]{5,100}',
            r'(?:^|\n)#{1,3}\s+[^\n]+',
        ]

        combined_pattern = f"(?:{'|'.join(patterns)})"
        matches = list(re.finditer(combined_pattern, text, flags=re.MULTILINE))

        if not matches:
            return [{'title': 'Full Book', 'content': text}]

        chapters = []
        intro_text = text[:matches[0].start()].strip()
        if len(intro_text) > 100:
            chapters.append({'title': 'Introduction', 'content': intro_text})

        for i in range(len(matches)):
            start_pos = matches[i].start()
            header_end = matches[i].end()
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(text)
            title = text[start_pos:header_end].strip()
            content = text[header_end:end_pos].strip()
            if len(content) >= min_content_length:
                chapters.append({'title': title, 'content': content})

        return chapters


class NotationParser:
    """
    Parse chess notation using Stack-Based Tree Builder with Lookahead.

    Handles:
    - Analysis branches that jump back in move numbers
    - Ambiguous moves legal on multiple branches
    - Sticky notation (6.Nge2, 11...Ne8)
    """

    @staticmethod
    def extract_lines_from_chapter(text: str, chapter_title: str) -> List[Dict]:
        """GAME SLICER: Split chapter into Introduction + Game segments."""
        game_marker_pattern = r'Game\s+(\d+)'
        matches = list(re.finditer(game_marker_pattern, text, flags=re.IGNORECASE))

        if not matches:
            return [{'title': chapter_title, 'pgn': NotationParser.text_to_pgn(text, chapter_title)}]

        results = []
        intro_text = text[:matches[0].start()].strip()
        if intro_text:
            results.append({
                'title': f"{chapter_title} - Introduction",
                'pgn': NotationParser.text_to_pgn(intro_text, f"{chapter_title} - Introduction")
            })

        for i, match in enumerate(matches):
            game_num = match.group(1)
            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            segment_text = text[start_pos:end_pos].strip()
            title = f"{chapter_title} - Game {game_num}"
            results.append({'title': title, 'pgn': NotationParser.text_to_pgn(segment_text, title)})

        return results

    @staticmethod
    def text_to_pgn(text: str, chapter_title: str) -> str:
        """
        Stack-Based Tree Builder with Lookahead.

        Algorithm:
        1. Tokenize text into Move tokens and Text tokens (Sticky Regex)
        2. For each Move token, find ALL valid parent nodes in the tree
        3. Use 1-step lookahead to disambiguate when multiple parents valid
        4. Attach move to correct parent, building proper variation tree
        """

        # =====================================================
        # TOKEN CLASSES
        # =====================================================
        class MoveToken:
            __slots__ = ['move_num', 'is_black', 'san', 'original']
            def __init__(self, move_num: int, is_black: bool, san: str, original: str):
                self.move_num = move_num
                self.is_black = is_black
                self.san = san
                self.original = original

        class TextToken:
            __slots__ = ['text']
            def __init__(self, text: str):
                self.text = text

        class VariationStartToken:
            __slots__ = []

        class VariationEndToken:
            __slots__ = []

        # =====================================================
        # TOKENIZER (Sticky Regex)
        # =====================================================
        def tokenize(raw_text: str) -> list:
            """Convert text to list of MoveToken and TextToken."""
            tokens = []

            # Sticky pattern: handles "6.Nge2", "11...Ne8", "7.0-0"
            explicit_move = re.compile(
                r'(\d+)(\.{1,3})\s*'
                r'([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O|0-0-0|0-0)'
                r'([!?]*)'
            )

            # Raw SAN for implicit Black moves: "1.e4 e5"
            raw_san = re.compile(
                r'([KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|'
                r'[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?|'
                r'[a-h][1-8](?:=[QRBN])?[+#]?|'
                r'O-O-O|O-O|0-0-0|0-0)([!?]*)'
            )

            pos = 0
            last_move_num = 0
            last_was_white = False

            while pos < len(raw_text):
                # Variation markers take highest priority
                if raw_text[pos] == '(':
                    tokens.append(VariationStartToken())
                    pos += 1
                    last_was_white = False
                    continue
                if raw_text[pos] == ')':
                    tokens.append(VariationEndToken())
                    pos += 1
                    last_was_white = False
                    continue

                # Try explicit move first
                m = explicit_move.match(raw_text, pos)
                if m:
                    move_num = int(m.group(1))
                    dots = m.group(2)
                    san = m.group(3).replace('0-0-0', 'O-O-O').replace('0-0', 'O-O')
                    ann = m.group(4) or ''
                    is_black = len(dots) > 1

                    tokens.append(MoveToken(move_num, is_black, san + ann, m.group(0)))
                    last_move_num = move_num
                    last_was_white = not is_black
                    pos = m.end()
                    continue

                # Try raw SAN (implicit Black after White)
                if last_was_white:
                    ws_match = re.match(r'\s{0,10}', raw_text[pos:])
                    ws_end = pos + (ws_match.end() if ws_match else 0)
                    if not re.match(r'\d+\.', raw_text[ws_end:]):
                        san_m = raw_san.match(raw_text, ws_end)
                        if san_m:
                            san = san_m.group(1).replace('0-0-0', 'O-O-O').replace('0-0', 'O-O')
                            ann = san_m.group(2) or ''
                            tokens.append(MoveToken(last_move_num, True, san + ann, san_m.group(0)))
                            last_was_white = False
                            pos = san_m.end()
                            continue

                # No move - collect text until next move or variation marker
                next_move = explicit_move.search(raw_text, pos)
                paren_search = re.search(r'[()]', raw_text[pos:])

                if paren_search:
                    paren_abs = pos + paren_search.start()
                    stop = min(next_move.start(), paren_abs) if next_move else paren_abs
                elif next_move:
                    stop = next_move.start()
                else:
                    text_content = raw_text[pos:]
                    if text_content.strip():
                        tokens.append(TextToken(text_content.strip()))
                    break

                text_content = raw_text[pos:stop]
                if text_content.strip():
                    tokens.append(TextToken(text_content.strip()))
                pos = stop
                last_was_white = False

            return tokens

        # =====================================================
        # BUILD THE TREE
        # =====================================================
        game = chess.pgn.Game()
        game.headers["Event"] = chapter_title
        game.headers["Site"] = "Chess Book"
        game.headers["White"] = "Study"
        game.headers["Black"] = "Analysis"
        game.headers["Result"] = "*"

        # Try to extract player names
        header_match = re.search(r'Game\s+\d+\s+([A-Za-z]+)\s*[-–]\s*([A-Za-z]+)', text)
        if header_match:
            game.headers["White"] = header_match.group(1).strip()
            game.headers["Black"] = header_match.group(2).strip()

        # Node registry: all nodes for finding valid parents
        node_registry: List[chess.pgn.GameNode] = [game]
        main_line_leaf = game
        current_node = game

        # Tokenize
        tokens = tokenize(text)

        # Variation stack: each entry saves (current_node, main_line_leaf) at the
        # point where '(' was encountered so ')' can restore the context exactly.
        variation_stack = []

        # Process each token
        for token_idx, token in enumerate(tokens):
            if isinstance(token, VariationStartToken):
                variation_stack.append((current_node, main_line_leaf))
                continue

            if isinstance(token, VariationEndToken):
                if variation_stack:
                    current_node, main_line_leaf = variation_stack.pop()
                continue

            if isinstance(token, TextToken):
                comment = re.sub(r'\s+', ' ', token.text)
                comment = comment.replace('{', '(').replace('}', ')')
                if len(comment) < MAX_COMMENT_LENGTH:
                    current_node.comment = (current_node.comment + " " + comment).strip()

            elif isinstance(token, MoveToken):
                san_clean = re.sub(r'[!?]+$', '', token.san)
                expected_turn = chess.BLACK if token.is_black else chess.WHITE

                # =====================================================
                # STEP 1: Find ALL valid parent nodes
                # =====================================================
                valid_parents = []
                for node in node_registry:
                    board = node.board()
                    if board.fullmove_number == token.move_num and board.turn == expected_turn:
                        try:
                            move = board.parse_san(san_clean)
                            valid_parents.append((node, move))
                        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
                            pass

                if len(valid_parents) == 0:
                    continue

                elif len(valid_parents) == 1:
                    parent_node, move = valid_parents[0]

                else:
                    # =====================================================
                    # STEP 2: Disambiguate - Prefer Current Branch
                    # =====================================================
                    current_branch_match = None
                    for pnode, pmove in valid_parents:
                        if pnode == current_node:
                            current_branch_match = (pnode, pmove)
                            break

                    if current_branch_match:
                        parent_node, move = current_branch_match
                    else:
                        # Use lookahead to disambiguate
                        next_move_token = None
                        for future_idx in range(token_idx + 1, len(tokens)):
                            if isinstance(tokens[future_idx], MoveToken):
                                next_move_token = tokens[future_idx]
                                break

                        chosen = None
                        if next_move_token:
                            next_turn = chess.BLACK if next_move_token.is_black else chess.WHITE
                            next_san = re.sub(r'[!?]+$', '', next_move_token.san)

                            # First check main_line_leaf
                            for pnode, pmove in valid_parents:
                                if pnode == main_line_leaf:
                                    test_board = pnode.board().copy()
                                    test_board.push(pmove)
                                    if test_board.fullmove_number == next_move_token.move_num and test_board.turn == next_turn:
                                        try:
                                            test_board.parse_san(next_san)
                                            chosen = (pnode, pmove)
                                            break
                                        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError, ValueError):
                                            pass

                            # Then check others
                            if not chosen:
                                for pnode, pmove in valid_parents:
                                    test_board = pnode.board().copy()
                                    test_board.push(pmove)
                                    if test_board.fullmove_number == next_move_token.move_num and test_board.turn == next_turn:
                                        try:
                                            test_board.parse_san(next_san)
                                            chosen = (pnode, pmove)
                                            break
                                        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError, ValueError):
                                            pass

                        if chosen:
                            parent_node, move = chosen
                        else:
                            parent_node, move = valid_parents[0]
                            for pnode, pmove in valid_parents:
                                if pnode == main_line_leaf:
                                    parent_node, move = pnode, pmove
                                    break

                # =====================================================
                # STEP 3: Attach Move
                # =====================================================
                existing = None
                for var in parent_node.variations:
                    if var.move == move:
                        existing = var
                        break

                if existing:
                    new_node = existing
                else:
                    new_node = parent_node.add_variation(move)
                    node_registry.append(new_node)

                if parent_node == main_line_leaf:
                    main_line_leaf = new_node

                current_node = new_node

        return str(game)

    @staticmethod
    def extract_games(text: str) -> List[str]:
        """Legacy helper."""
        return [NotationParser.text_to_pgn(text, "Extracted Game")]


def main():
    parser = argparse.ArgumentParser(description='Convert chess books to PGN')
    parser.add_argument('--pdf', help='Path to PDF file')
    parser.add_argument('--epub', help='Path to EPUB file')
    parser.add_argument('--output', help='Output PGN file path (default: <book_name>.pgn)')
    parser.add_argument('--book-name', help='Name for the book (used in chapter headers)')
    parser.add_argument('--dry-run', action='store_true', help='Parse only, print first 5 chapters')

    args = parser.parse_args()
    if not (args.pdf or args.epub):
        parser.error("Provide --pdf or --epub")

    book_path = args.pdf or args.epub
    book_name = args.book_name or Path(book_path).stem

    # Strategy selection: use HTML-aware parser for structured EPUBs
    if args.epub:
        from chess_book_parser.parsers.epub_structured import has_movetext_data, parse_structured_epub

        if has_movetext_data(args.epub):
            return _run_structured_epub(args, book_name)

    # Fallback: text-based parsing for PDFs and unstructured EPUBs
    return _run_text_based(args, book_name)


def _run_structured_epub(args, book_name: str) -> int:
    """Parse structured EPUB using HTML-aware parser."""
    from chess_book_parser.parsers.epub_structured import parse_structured_epub

    print("Parsing structured EPUB (HTML-aware parser)...")
    games = parse_structured_epub(args.epub)
    print(f"Found {len(games)} games.")

    if args.dry_run:
        for i, game in enumerate(games[:5], 1):
            pgn = str(game)
            event = game.headers.get("Event", "?")
            white = game.headers.get("White", "?")
            black = game.headers.get("Black", "?")
            print(f"\n[{i}] {event}")
            print(f"    {white} vs {black}")
            print(f"    {pgn[pgn.index(chr(10)*2)+2:][:400]}...")
        return 0

    output_path = args.output or f"{book_name}.pgn"
    print(f"\nWriting {len(games)} games to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, game in enumerate(games):
            # Prepend book name to Event header, respecting 100-char Lichess limit
            event = game.headers.get("Event", "?")
            game.headers["Event"] = f"{book_name} - {event}"[:100]

            f.write(str(game))
            f.write("\n\n")
            print(f"   [{i+1}/{len(games)}] {game.headers['Event']}")

    print(f"\nDone. PGN written to {output_path}")
    return 0


def _run_text_based(args, book_name: str) -> int:
    """Parse book using text-based fallback parser."""
    print("Parsing book (text-based parser)...")
    text = BookParser.parse_pdf(args.pdf) if args.pdf else BookParser.parse_epub(args.epub)

    print("\nExtracting chapters and games...")
    raw_chapters = BookParser.extract_chapters(text)
    chapters = []
    for rc in raw_chapters:
        chapters.extend(NotationParser.extract_lines_from_chapter(rc['content'], rc['title']))

    if args.dry_run:
        for i, ch in enumerate(chapters[:5], 1):
            print(f"\n[{i}] {ch['title']}\n{ch['pgn'][:500]}...")
        return 0

    output_path = args.output or f"{book_name}.pgn"
    print(f"\nWriting {len(chapters)} chapters to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, chapter in enumerate(chapters):
            name = f"{book_name} - {chapter['title']}"[:100]
            pgn = chapter['pgn'].replace(
                f'[Event "{chapter["title"]}"]',
                f'[Event "{name}"]',
                1
            )
            f.write(pgn)
            f.write("\n\n")
            print(f"   [{i+1}/{len(chapters)}] {name}")

    print(f"\nDone. PGN written to {output_path}")
    return 0


if __name__ == '__main__':
    exit(main())
