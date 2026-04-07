# Claude Code Implementation Brief

> **Note:** The book-to-PGN / Lichess-study feature described here was implemented and then **split out** to `standalone/chess-book-parser/`. Do not add it back into the main `chess_transfer` sync/analysis codebase; extend the standalone package instead.

## Quick Start Prompt for Claude Code

Copy and paste this to Claude Code:

```
I want to extend my chess_transfer repository (https://github.com/e-riveras/chess_transfer) 
with a new feature that converts chess books (PDF/EPUB) into Lichess study chapters.

The implementation should:
1. Add a new module chess_transfer/book_to_study.py
2. Parse PDF/EPUB files to extract chess notation and commentary
3. Convert extracted content to PGN format
4. Upload chapters to an existing Lichess study using the berserk API

I have these reference files:
- requirements.txt (dependencies needed)
- book_to_study_integrated.py (complete reference implementation)
- CLAUDE_CODE_INSTRUCTIONS.md (detailed specifications)

Please implement this following the CLI-based pattern of my existing chess_transfer repo.
The tool should be runnable as: python -m chess_transfer.book_to_study --pdf mybook.pdf
```

---

## Implementation Checklist

### Phase 1: Setup (5 minutes)
- [ ] Review existing chess_transfer repo structure
- [ ] Create chess_transfer/book_to_study.py
- [ ] Update requirements.txt with new dependencies
- [ ] Install dependencies: pip install -r requirements.txt

### Phase 2: Core Implementation (30 minutes)
- [ ] Implement BookParser class
  - [ ] parse_pdf() method
  - [ ] parse_epub() method
  - [ ] extract_chapters() method
- [ ] Implement NotationParser class
  - [ ] extract_games() method
  - [ ] text_to_pgn() method
- [ ] Implement LichessStudyUploader class
  - [ ] Initialize berserk client
  - [ ] add_chapters() method
- [ ] Implement ConfigManager class
  - [ ] Save/load config from ~/.chess_transfer_config.json
  - [ ] get_study_id() and set_study_id() methods

### Phase 3: CLI Integration (15 minutes)
- [ ] Implement main() function with argparse
- [ ] Add command-line arguments (--pdf, --epub, --study-id, etc.)
- [ ] Add progress output (print statements)
- [ ] Error handling and validation

### Phase 4: Testing (20 minutes)
- [ ] Create tests/test_book_to_study.py
- [ ] Test PDF parsing
- [ ] Test PGN conversion
- [ ] Test config save/load
- [ ] Test error handling

### Phase 5: Documentation (10 minutes)
- [ ] Add usage examples to README
- [ ] Add docstrings to all functions
- [ ] Update requirements.txt if needed

---

## Key Implementation Details

### 1. Parsing Chess Notation
Use regex pattern to extract moves:
```python
move_pattern = r'(?:\d+\.+\s*)?(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?|O-O-O|O-O)'
```

### 2. Handling Variations
Detect parentheses for variations:
```python
variation_pattern = r'\(([^)]+)\)'
```

### 3. Preserving Comments
Extract comments in curly braces:
```python
comment_pattern = r'\{([^}]+)\}'
```

### 4. PGN Format
Standard PGN structure:
```
[Event "Chapter Title"]
[Site "Chess Book"]

{Introduction comment}

1. e4 e5 2. Nf3 {Comment} Nc6 (2... Nf6 {Variation comment}) 3. Bc4
```

### 5. Berserk API Usage
```python
import berserk

session = berserk.TokenSession(api_token)
client = berserk.Client(session=session)

client.studies.create_chapter(
    study_id=study_id,
    name=chapter_name,
    pgn=pgn_string,
    orientation='white'
)
```

---

## Testing Approach

### Unit Tests
```python
def test_parse_pdf():
    # Test with sample PDF
    text = BookParser.parse_pdf('test_book.pdf')
    assert len(text) > 0

def test_text_to_pgn():
    sample_text = "1.e4 e5 2.Nf3 {Developing} Nc6"
    pgn = NotationParser.text_to_pgn(sample_text, "Test Chapter")
    assert "[Event" in pgn
    assert "1. e4 e5" in pgn
```

### Integration Test
```python
# Don't hit real API in tests - use mocking
from unittest.mock import Mock, patch

@patch('berserk.Client')
def test_upload_chapters(mock_client):
    uploader = LichessStudyUploader("fake_token")
    chapters = [{'title': 'Ch1', 'pgn': '1.e4 e5'}]
    uploader.add_chapters("study123", chapters, "Test Book")
    mock_client.studies.create_chapter.assert_called()
```

---

## Example Code Structure

```python
#!/usr/bin/env python3
"""
chess_transfer/book_to_study.py

Convert chess books (PDF/EPUB) to Lichess study chapters.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Optional

try:
    import berserk
    import chess.pgn
    from PyPDF2 import PdfReader
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False


class BookParser:
    """Parse chess books from various formats"""
    
    @staticmethod
    def parse_pdf(pdf_path: str) -> str:
        """Extract text from PDF file"""
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
        return text
    
    # ... more methods


class NotationParser:
    """Parse chess notation from text"""
    
    @staticmethod
    def text_to_pgn(text: str, chapter_title: str) -> str:
        """Convert book text to PGN format"""
        # Implementation here
        pass


class LichessStudyUploader:
    """Upload chapters to Lichess study"""
    
    def __init__(self, api_token: str):
        """Initialize with API token"""
        # Implementation here
        pass


class ConfigManager:
    """Manage configuration"""
    
    CONFIG_FILE = Path.home() / ".chess_transfer_config.json"
    
    @classmethod
    def get_study_id(cls) -> Optional[str]:
        """Load saved study ID"""
        # Implementation here
        pass


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Convert chess books to Lichess study chapters'
    )
    # Add arguments
    # Implement workflow
    pass


if __name__ == '__main__':
    main()
```

---

## Expected Output

### Successful Run
```
$ python -m chess_transfer.book_to_study --pdf italian_game.pdf

📖 Parsing book...
   Extracted 45,231 characters

📑 Extracting chapters...
   Found 8 chapters

♟️  Converting to PGN...

📚 Uploading 'italian_game' to study xyz123
   Creating 8 chapters...

   ✓ [1/8] italian_game - Chapter 1: Introduction
   ✓ [2/8] italian_game - Chapter 2: Main Line 4.c3
   ✓ [3/8] italian_game - Chapter 3: Two Knights Defense
   ✓ [4/8] italian_game - Chapter 4: Fried Liver Attack
   ✓ [5/8] italian_game - Chapter 5: Giuoco Piano
   ✓ [6/8] italian_game - Chapter 6: Evans Gambit
   ✓ [7/8] italian_game - Chapter 7: Modern Variations
   ✓ [8/8] italian_game - Chapter 8: Complete Repertoire

✓ Done! View at: https://lichess.org/study/xyz123
```

### Error Handling
```
$ python -m chess_transfer.book_to_study --pdf missing.pdf

❌ Error: File not found: missing.pdf
```

```
$ python -m chess_transfer.book_to_study --pdf book.pdf

❌ No study ID provided

Setup:
1. Create study at https://lichess.org/study
2. Copy study ID from URL
3. Run: python -m chess_transfer.book_to_study --study-id YOUR_ID --save-study
```

---

## Final Deliverables

After implementation, you should have:

1. **chess_transfer/book_to_study.py** - Main implementation
2. **requirements.txt** - Updated with dependencies
3. **tests/test_book_to_study.py** - Test suite
4. **README.md** - Updated with usage examples

And be able to run:
```bash
# Install
pip install -r requirements.txt

# Use
python -m chess_transfer.book_to_study --pdf mybook.pdf --study-id xyz123
```

---

## Tips for Claude Code

1. **Start Simple**: Implement basic PDF parsing first, then add EPUB
2. **Test Early**: Test PDF extraction with a sample file before building the whole pipeline
3. **Mock API Calls**: Use mocking for tests to avoid hitting Lichess API
4. **Handle Edge Cases**: PDFs can have weird formatting, handle gracefully
5. **Progress Output**: Users want to see what's happening (use print statements)
6. **Config File**: Save study ID so users don't have to enter it every time

---

## Questions to Ask During Implementation

- Should we support batch processing (multiple PDFs at once)?
- How should we handle books with diagrams (FEN detection)?
- Should we add a dry-run mode to preview chapters before uploading?
- Do we want a --force flag to re-upload and replace existing chapters?
- Should we support .cbh (ChessBase) format?

---

## Reference Files Provided

1. **requirements.txt** - All dependencies needed
2. **book_to_study_integrated.py** - Complete working implementation
3. **CLAUDE_CODE_INSTRUCTIONS.md** - Detailed specifications
4. **QUICK_INTEGRATION.md** - Integration guide
5. **IMPLEMENTATION_GUIDE.md** - Architecture decisions

Use these as reference but feel free to improve the implementation!
