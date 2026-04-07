#!/usr/bin/env python3
"""
Entry point for book-to-study conversion.

Usage:
    python run_book_to_study.py --pdf book.pdf --study-id abc123
    python run_book_to_study.py --epub book.epub --study-id abc123

See python run_book_to_study.py --help for all options.
"""

from chess_book_parser.converter import main

if __name__ == "__main__":
    exit(main())
