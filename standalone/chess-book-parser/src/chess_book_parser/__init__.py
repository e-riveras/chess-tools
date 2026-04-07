"""Chess book → PGN conversion (EPUB/PDF, structured Everyman-style EPUBs, per-book runners)."""

from chess_book_parser.converter import BookParser, NotationParser, main
from chess_book_parser.parsers.epub_structured import has_movetext_data, parse_structured_epub
from chess_book_parser.parsers.movetext import parse_movetext

__all__ = [
    "BookParser",
    "NotationParser",
    "main",
    "has_movetext_data",
    "parse_structured_epub",
    "parse_movetext",
]
