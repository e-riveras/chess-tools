# chess-book-parser

Extract annotated games and commentary from chess EPUBs/PDFs into PGN. Includes:

- **Library** (`chess_book_parser`): text-based parsing, structured Everyman-style EPUB parsing (MOVETEXT), movetext helpers, PGN sanitization.
- **CLI**: `run_book_to_study.py` for generic `--pdf` / `--epub` → PGN; book-specific runners (`run_epub_to_pgn*.py`) for editions with fixed HTML layout.
- **Scripts**: `scripts/validate_pgn.py`, alignment checks, parser simulation.
- **Optional**: `book_to_study_integrated.py` uploads chapters to a Lichess study (needs `LICHESS_TOKEN` and `pip install -e ".[lichess]"`).

## Setup

```bash
cd chess-book-parser
python -m venv .venv && source .venv/bin/activate
pip install -e .
# or: pip install -r requirements.txt && export PYTHONPATH=src
```

Place sample EPUBs under `samples/` if you want full `test_epub_structured` coverage (tests skip when the file is missing).

## Usage

```bash
python run_book_to_study.py --epub path/to/book.epub --output out.pgn
python run_epub_to_pgn_silman.py out.pgn   # Silman 4th ed.; expects books/*reassess*.epub
pytest
```

## Origin

Extracted from a Chess.com–Lichess sync/analysis project as a standalone package. While it lives under `standalone/chess-book-parser` in that repo, copy or move this folder out and `git init` when you want a separate repository.
