# Chess book parser — session context (historical)

**Current location:** EPUB/PDF → PGN and optional Lichess upload live in `standalone/chess-book-parser/` in this repo (you can copy that tree to its own git repo, e.g. `~/git/chess-book-parser`). The discussion below refers to `chess_book_parser.converter.NotationParser` and related code in that package.

---

## Project Overview (archived notes)
Converting chess books (EPUB/PDF) to Lichess studies using Python. Implementation was originally under `chess_tools.study`; it has been extracted as above.

## Current Problem
The parser fails when chess books have:
1. **Analysis Branches** - Text discusses deep variations then jumps back to main line (e.g., move 12 back to move 9)
2. **Ambiguity Conflicts** - Same move is legal on multiple branches, parser picks wrong one

## Example Problem Text
```
1.c4 Nf6 2.Nc3 d6 3.g3 g6
Instead, 3...e5 4.Bg2 Nbd7 5.d3 Be7 would resemble the Old Indian.
4.Bg2 Bg7 5.e4 e5 6.Nge2 Nc6 7.0-0 0-0...
```

**Issue**: The variation `3...e5 4.Bg2 Nbd7 5.d3 Be7` appears BEFORE the main line continuation `4.Bg2 Bg7 5.e4`. Parser incorrectly puts `Nbd7` on main line instead of `Bg7`.

## Architecture Attempted: Stack-Based Tree Builder with Lookahead

### Current Implementation (in `chess_book_parser.converter`, formerly book_to_study)
1. **Tokenizer**: Converts text to MoveToken and TextToken list
   - Handles sticky notation: `6.Nge2`, `11...Ne8`, `7.0-0` (no spaces)
   - Captures implicit Black moves: `1.e4 e5` format

2. **Tree Builder**:
   - Maintains `node_registry` of all created nodes
   - For each move, finds ALL valid parent nodes
   - Uses lookahead to disambiguate multiple valid parents
   - Tracks `main_line_leaf` and `current_node`

### The Core Bug
When tokenized, variation moves appear BEFORE main line continuation:
```
Tokens: [1.c4, Nf6, 2.Nc3, d6, 3.g3, g6, TEXT, 3...e5, 4.Bg2, Nbd7, 5.d3, Be7, TEXT, 4.Bg2, Bg7, 5.e4...]
```

When processing first `4.Bg2`, valid parents are:
- After `3...g6` (main line)
- After `3...e5` (variation)

Lookahead to `Nbd7` is legal on BOTH (knight b8 can go to d7 in either position), so parser picks wrong branch.

## Proposed Solutions (Not Yet Implemented)

### Option 1: Context-Aware Disambiguation
When multiple valid parents exist:
1. Check immediate text context for variation markers ("Instead", "After", "would", "should")
2. If current token follows variation markers, attach to variation branch
3. Otherwise prefer main line

### Option 2: Two-Pass Approach
1. First pass: Build main line only (skip tokens in "commentary context")
2. Second pass: Add variations where they branch from main line

### Option 3: Track "Expected Next Move"
1. After each main line move, track what move number/color we expect next
2. Only moves matching expectation go on main line
3. Non-matching moves become variations

## Key Code Sections

### Tokenizer Pattern (Sticky Regex)
```python
explicit_move = re.compile(
    r'(\d+)(\.{1,3})\s*'
    r'([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O|0-0-0|0-0)'
    r'([!?]*)'
)
```

### Valid Parent Search
```python
for node in node_registry:
    board = node.board()
    if board.fullmove_number == token.move_num and board.turn == expected_turn:
        try:
            move = board.parse_san(san_clean)
            valid_parents.append((node, move))
        except:
            pass
```

## Test Files
- Test EPUB: place under `samples/` in the book-parser package (e.g. Iron English sample)
- Unit tests: `standalone/chess-book-parser/tests/test_book_to_study.py` (and related)
- Lichess Study ID: `zz3KrBvL` (example)

## Commands
```bash
cd standalone/chess-book-parser   # or ~/git/chess-book-parser
export PYTHONPATH=src
pytest tests/test_book_to_study.py -v

# Dry run (parse only)
python run_book_to_study.py --epub path/to/book.epub --dry-run
```

## Environment
- Lichess API token in `.env` as `LICHESS_TOKEN`
- Saved study ID in `~/.chess_transfer_config.json`

## What Works
- EPUB/PDF parsing
- Chapter extraction (by "Chapter N" markers)
- Game slicing (by "Game N" markers)
- Sticky regex (handles no-space notation)
- Stack-Based Tree Builder with Lookahead
- Main line vs variation disambiguation (current branch preference)
- Lichess upload with rate limiting
- All 15 unit tests passing

## Fixed (Previously Broken)
- Main line vs variation disambiguation when variation text appears before main line continuation
- Solution: "Current branch preference" - when multiple valid parents exist, prefer continuing on current branch (`current_node`), with lookahead disambiguation as fallback
