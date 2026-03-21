# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Chess.com to Lichess sync and analysis system. Automatically imports games from Chess.com to Lichess, generates AI-powered HTML analysis reports, maintains a cumulative blunder drills database, and converts chess books (EPUB/PDF) into study PGNs.

## Commands

### Testing
```bash
export PYTHONPATH=$(pwd)/src:$(pwd)
pytest --cov=src --cov-report=term-missing tests/
```

### Running Locally
```bash
# Full sync pipeline (imports Chess.com games → Lichess, analyzes latest game)
python run_sync.py

# Standalone analysis (analyzes a PGN file or fetches latest Lichess game)
python run_analysis.py [pgn_file]

# Retroactively extract blunder drills from all Lichess games
python run_extract_drills.py [--limit N] [--dry-run]

# Convert a chess book (EPUB or PDF) to annotated PGN
python run_book_to_study.py --epub books/my_book.epub --output my_book.pgn
python run_book_to_study.py --pdf   books/my_book.pdf  --output my_book.pgn --dry-run
```

### Dependencies
```bash
pip install -r requirements.txt
```

## Architecture

### Package Structure

```
src/
├── chess_tools/            # Main package (src/chess_tools/__init__.py)
│   ├── lib/                # Shared library
│   │   ├── models.py       # CrucialMoment dataclass
│   │   ├── utils.py        # Logging, env vars, path helpers
│   │   ├── api/
│   │   │   ├── lichess.py  # Lichess API client (berserk wrapper)
│   │   │   └── chesscom.py # Chess.com public API client
│   │   └── data/
│   │       └── history.py  # Import history JSON (data/history.json)
│   ├── analysis/           # Analysis pipeline
│   │   ├── engine.py       # ChessAnalyzer + classify_moment/classify_tactic
│   │   ├── narrator.py     # LLM narration (abstract base + Gemini + Mock)
│   │   ├── report.py       # HTML + Markdown report generation
│   │   ├── pipeline.py     # run_analysis_pipeline() orchestrator
│   │   ├── drills.py       # Blunder drill JSON management
│   │   └── history.py      # Cross-game analysis history
│   ├── study/              # Book study tools
│   │   ├── converter.py    # BookParser + NotationParser (PDF/EPUB → PGN)
│   │   └── parsers/
│   │       ├── epub_structured.py  # HTML-aware EPUB parser
│   │       ├── movetext.py         # Move-text tokenizer helpers
│   │       └── pgn_sanitizer.py    # PGN cleanup utilities
│   └── transfer/
│       └── sync.py         # run_sync_pipeline() orchestrator
└── tactics.py              # TacticClassifier (geometry-only motif detector)
                            # NOTE: lives at src/tactics.py, imported as `tactics`
```

### Three Main Pipelines

1. **Sync Pipeline** (`run_sync.py` → `src/chess_tools/transfer/sync.py`)
   - Fetches Chess.com archives → imports new rapid/blitz games to Lichess
   - Only analyzes games with `time_class` in `{"rapid", "blitz"}`
   - Triggers analysis for the newest unanalyzed game
   - Maintains import state in `data/history.json`

2. **Analysis Pipeline** (`run_analysis.py` → `src/chess_tools/analysis/pipeline.py`)
   - Runs Stockfish engine analysis to find blunders, missed chances, and missed mates
   - Gets LLM explanations via Google Gemini (`gemini-2.0-flash`)
   - Generates HTML reports with eval charts and SVG board diagrams in `docs/analysis/`
   - Also generates markdown reports in `analysis/`
   - Updates `docs/drills/drills.json` and `docs/analysis/history.json`

3. **Book Study Pipeline** (`run_book_to_study.py` → `src/chess_tools/study/converter.py`)
   - Parses chess books from EPUB or PDF
   - Uses structured HTML parser for EPUBs with embedded movetext data
   - Falls back to text-based Stack-Based Tree Builder for PDFs / plain EPUBs
   - Outputs annotated PGN files suitable for Lichess study import

### Key Modules

- `src/chess_tools/lib/models.py` — `CrucialMoment` dataclass with `to_drill_dict()`
- `src/chess_tools/analysis/engine.py` — `ChessAnalyzer` context manager; `classify_moment()` returning `(moment_type, severity)`; `classify_tactic()` for legacy tactic labels
- `src/chess_tools/analysis/narrator.py` — `AnalysisNarrator` abstract base; `GoogleGeminiNarrator`; `MockNarrator` (no API key needed)
- `src/chess_tools/analysis/report.py` — `generate_html_report()`, `generate_markdown_report()`, `regenerate_index_page()`
- `src/chess_tools/analysis/drills.py` — `update_drills_json()` appends blunders (deduplicated by id)
- `src/chess_tools/analysis/history.py` — `load/update/save_analysis_history()`, `format_history_for_prompt()`; keeps last 20 games
- `src/chess_tools/lib/data/history.py` — `load_history()` / `save_history()` for import state
- `src/chess_tools/lib/utils.py` — `get_repo_root()` (git root), `get_project_root()` (src/../), `check_env_var()`
- `src/tactics.py` — `TacticClassifier` using geometric motif detection (fork, pin, skewer, discovered attack, overloaded defender, back-rank weakness); returns `TacticEvent` dataclass list

### Data Flow

```
Chess.com archives → Import to Lichess → Stockfish analysis → LLM narration → HTML report
                                                           ↓
                                              docs/drills/drills.json (blunders only)
                                              docs/analysis/history.json (cross-game patterns)
```

State files:
- `data/history.json` — imported Chess.com game IDs + last analyzed game ID
- `docs/analysis/history.json` — cross-game tactic pattern history (last 20 games)
- `docs/drills/drills.json` — cumulative blunder drill entries (deduplicated)

### Output (GitHub Pages)

```
docs/
├── index.html              # Main dashboard
├── books.html              # Book study page
├── analysis/
│   ├── index.html          # Analysis reports index (auto-regenerated)
│   ├── history.json        # Cross-game tactic history
│   └── YYYY-MM-DD_White_vs_Black.html  # Per-game HTML reports
└── drills/
    ├── index.html          # Interactive blunder drill page
    └── drills.json         # All drill entries
```

## Environment Variables

Required in `.env` (see `.env.example`):
- `STOCKFISH_PATH` — Path to Stockfish binary
- `GEMINI_API_KEY` — Google Gemini API key
- `LICHESS_TOKEN` — Lichess API token (username derived automatically via `/api/account`)
- `CHESSCOM_USERNAME` — Chess.com username (defaults to `erivera90` in sync)

Optional:
- `ANALYSIS_TIME_LIMIT` — Engine analysis time per position (default: 0.1s)
- `LOG_LEVEL` — Logging level (default: `INFO`)

## PYTHONPATH Requirement

Both `src/` and the repo root must be on `PYTHONPATH` because `src/tactics.py` is at the
repo root level and imported as `from tactics import TacticClassifier`:

```bash
export PYTHONPATH=$(pwd)/src:$(pwd)
```

The GitHub Actions workflows set this via:
```yaml
PYTHONPATH: ${{ github.workspace }}/src:${{ github.workspace }}
```

## Key Patterns & Conventions

### Moment Classification
`classify_moment()` in `engine.py` returns `(moment_type, severity)` or `None`:
- `moment_type`: `"blunder"` | `"missed_chance"` | `"missed_mate"`
- `severity`: `"critical"` (≥500cp) | `"major"` (≥300cp) | `"minor"`
- **Smart filter**: positions decided before AND after (|cp| > 500, same sign) are skipped

### Tactic Classification (Two Layers)
1. **Legacy `classify_tactic()`** — string label from deterministic geometry (priority order: forced_mate → back_rank_mate → skewer → pin → discovered_attack → hanging_piece → hanging_pawn → losing_exchange → fork → trapped_piece → positional)
2. **`TacticClassifier`** (in `src/tactics.py`) — geometric motif events (`TacticEvent` dataclasses); overrides legacy label only when legacy returns `"positional"` or `"unknown"`

### Context Manager for Engine
```python
with ChessAnalyzer(stockfish_path, time_limit=0.1) as analyzer:
    moments, metadata, move_evals = analyzer.analyze_game(pgn_text, hero_username="myuser")
```

### Narrator Abstraction
`AnalysisNarrator` is abstract. `MockNarrator` is used for testing without API keys (auto-selected when `GEMINI_API_KEY` is absent). The Gemini narrator uses different prompts for blunders vs missed chances.

### Drills System
Only blunders (`moment_type == "blunder"`) are added to drills. Each entry has a unique `id` of `{game_id}_{half_move_number}`. The `fen_after` and `refutation_move_uci` fields support a "Punish" drill mode. The `punished` field records whether the opponent found the refutation.

### Rate Limiting
- Lichess import: 6s delay between new imports, 1s delay for duplicates
- 429 errors from Lichess trigger backoff retry in the API client

## GitHub Actions Workflows

- **`ci.yml`** — runs on push/PR to `main`: installs deps, runs pytest with coverage
- **`sync.yml`** — runs hourly (cron `0 * * * *`) and on push to `main`: installs Stockfish, runs `run_sync.py`, commits `data/history.json`, `docs/analysis/`, and `docs/drills/drills.json`

## Testing

```bash
export PYTHONPATH=$(pwd)/src:$(pwd)
pytest tests/                           # run all tests
pytest tests/test_analysis_history.py  # specific module
pytest --cov=src --cov-report=term-missing tests/  # with coverage
```

Test files mirror the source structure:
- `tests/test_pipelines.py` — sync/analysis pipeline integration tests
- `tests/test_report.py` — HTML/markdown report generation
- `tests/test_drills.py` — drill JSON management
- `tests/test_analysis_history.py` — cross-game history
- `tests/test_tactic_classifier.py` — TacticClassifier motif detection
- `tests/test_book_to_study.py` — book parsing / PGN conversion
- `tests/test_epub_structured.py` — structured EPUB parser
- `tests/test_pgn_sanitizer.py` — PGN sanitizer utilities
