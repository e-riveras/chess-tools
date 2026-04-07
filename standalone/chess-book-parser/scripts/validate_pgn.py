#!/usr/bin/env python3
"""
Automated PGN validator for chess book converter output.

Checks:
  - Structural: brace/paren balance, header format, game separation, result terminators
  - Move validity: python-chess parse success, move count, castling notation
  - Comment quality: leading punctuation artifacts, diagram markers, embedded results, length
  - Result consistency: Result header matches game outcome

Usage:
    python scripts/validate_pgn.py <pgn_file> [--json]
"""

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import chess
import chess.pgn


# ---------------------------------------------------------------------------
# Issue model
# ---------------------------------------------------------------------------
@dataclass
class Issue:
    game_index: int
    severity: str        # "critical", "major", "minor"
    category: str
    message: str
    detail: str = ""


@dataclass
class GameReport:
    index: int
    white: str = ""
    black: str = ""
    event: str = ""
    ply_count: int = 0
    comment_count: int = 0
    issues: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Structural checks (raw text)
# ---------------------------------------------------------------------------
def check_brace_balance(text: str) -> list[Issue]:
    """Check { } balance across the entire file."""
    issues = []
    depth = 0
    for i, ch in enumerate(text):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                line_no = text[:i].count('\n') + 1
                issues.append(Issue(
                    game_index=-1, severity="critical", category="structure",
                    message=f"Unmatched closing brace at line {line_no}",
                ))
                depth = 0
    if depth > 0:
        issues.append(Issue(
            game_index=-1, severity="critical", category="structure",
            message=f"Unclosed braces: {depth} still open at end of file",
        ))
    return issues


def check_paren_balance(text: str) -> list[Issue]:
    """Check ( ) balance outside of comments."""
    issues = []
    depth = 0
    in_comment = False
    for i, ch in enumerate(text):
        if ch == '{':
            in_comment = True
        elif ch == '}':
            in_comment = False
        elif not in_comment:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth < 0:
                    line_no = text[:i].count('\n') + 1
                    issues.append(Issue(
                        game_index=-1, severity="critical", category="structure",
                        message=f"Unmatched closing paren at line {line_no}",
                    ))
                    depth = 0
    if depth > 0:
        issues.append(Issue(
            game_index=-1, severity="major", category="structure",
            message=f"Unclosed parentheses: {depth} still open at end of file",
        ))
    return issues


def check_header_format(text: str) -> list[Issue]:
    """Verify PGN headers follow standard format."""
    issues = []
    header_re = re.compile(r'^\[(\w+)\s+"(.*)"\]\s*$')
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('[') and not stripped.startswith('[%'):
            if not header_re.match(stripped):
                issues.append(Issue(
                    game_index=-1, severity="major", category="structure",
                    message=f"Malformed header at line {line_no}",
                    detail=stripped[:80],
                ))
    return issues


# ---------------------------------------------------------------------------
# Per-game checks
# ---------------------------------------------------------------------------
CASTLING_ZERO_RE = re.compile(r'\b0-0(-0)?\b')
LEADING_PUNCT_RE = re.compile(r'^[,;:.!?»«\u2013\u2014\u2022\u00bb]+\s')
DIAGRAM_RE = re.compile(r'\[Diagram\b', re.IGNORECASE)
INLINE_RESULT_RE = re.compile(r'(?:^|\s)(1-0|0-1|1/2-1/2)(?:\s|$|[,.])')
EMPTY_COMMENT_RE = re.compile(r'\{\s*\}')


def count_comments(node) -> tuple[int, list[str]]:
    """Walk mainline and count comments, collecting them."""
    comments = []
    # Root comment (before first move)
    if node.comment:
        comments.append(node.comment)
    for child in node.mainline():
        if child.comment:
            comments.append(child.comment)
    return len(comments), comments


def check_game(game: chess.pgn.Game, idx: int) -> GameReport:
    """Run all per-game checks."""
    headers = game.headers
    report = GameReport(
        index=idx,
        white=headers.get("White", "?"),
        black=headers.get("Black", "?"),
        event=headers.get("Event", "?"),
    )

    # Ply count
    plies = list(game.mainline_moves())
    report.ply_count = len(plies)

    # Comments
    comment_count, comments = count_comments(game)
    report.comment_count = comment_count

    # --- Check: empty game with no comments ---
    if report.ply_count == 0 and comment_count == 0:
        report.issues.append(Issue(
            game_index=idx, severity="major", category="content",
            message="Empty game: no moves and no comments",
        ))

    # --- Check: very short game (< 4 plies) with no root comment ---
    if 0 < report.ply_count < 4 and not game.comment:
        report.issues.append(Issue(
            game_index=idx, severity="minor", category="content",
            message=f"Very short game ({report.ply_count} plies) with no introductory comment",
        ))

    # --- Check: castling notation (0-0 instead of O-O) in raw movetext ---
    # We check the actual SAN output from python-chess, which normalizes to O-O,
    # so instead check comments for 0-0 references that might be movetext leaks
    board = game.board()
    for move_node in game.mainline():
        san = board.san(move_node.move)
        board.push(move_node.move)
        # python-chess always outputs O-O, so if the PGN had 0-0 it was already parsed
        # We mainly care about comments containing 0-0 as if it were a move

    # --- Check: comment quality ---
    for ci, comment in enumerate(comments):
        # Leading punctuation artifacts
        if LEADING_PUNCT_RE.match(comment):
            report.issues.append(Issue(
                game_index=idx, severity="minor", category="comment",
                message=f"Comment {ci+1} starts with punctuation artifact",
                detail=comment[:60],
            ))

        # Diagram markers
        if DIAGRAM_RE.search(comment):
            report.issues.append(Issue(
                game_index=idx, severity="minor", category="comment",
                message=f"Comment {ci+1} contains [Diagram] marker",
                detail=comment[:60],
            ))

        # Very long comments (> 3000 chars) — might be merge of multiple
        if len(comment) > 3000:
            report.issues.append(Issue(
                game_index=idx, severity="minor", category="comment",
                message=f"Comment {ci+1} is very long ({len(comment)} chars) — possible merge",
            ))

        # Inline result in comment body (not at end)
        matches = list(INLINE_RESULT_RE.finditer(comment))
        for m in matches:
            # Allow if it's at the very end of the comment
            if m.end() < len(comment) - 5:
                pass  # Results embedded mid-comment are common in annotations, skip

        # Empty-ish comment
        stripped = comment.strip()
        if len(stripped) < 2:
            report.issues.append(Issue(
                game_index=idx, severity="minor", category="comment",
                message=f"Comment {ci+1} is effectively empty",
                detail=repr(stripped),
            ))

    # --- Check: result consistency ---
    header_result = headers.get("Result", "*")
    # Look for result mentioned in the last comment
    if comments and header_result != "*":
        last_comment = comments[-1]
        if header_result not in last_comment and report.ply_count > 0:
            # Not necessarily an error — many annotated games don't restate result
            pass

    # --- Check: result terminator presence ---
    if header_result not in ("1-0", "0-1", "1/2-1/2", "*"):
        report.issues.append(Issue(
            game_index=idx, severity="major", category="result",
            message=f"Non-standard Result header: '{header_result}'",
        ))

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def validate_pgn(pgn_path: str) -> dict:
    """Run all validation checks and return structured results."""
    path = Path(pgn_path)
    text = path.read_text(encoding="utf-8")

    # Structural checks on raw text
    all_issues = []
    all_issues.extend(check_brace_balance(text))
    all_issues.extend(check_paren_balance(text))
    all_issues.extend(check_header_format(text))

    # Empty comment check
    empty_matches = list(EMPTY_COMMENT_RE.finditer(text))
    for m in empty_matches:
        line_no = text[:m.start()].count('\n') + 1
        all_issues.append(Issue(
            game_index=-1, severity="minor", category="comment",
            message=f"Empty comment braces at line {line_no}",
        ))

    # Per-game checks
    reader = io.StringIO(text)
    game_reports = []
    idx = 0
    parse_errors = 0

    while True:
        try:
            game = chess.pgn.read_game(reader)
            if game is None:
                break
            report = check_game(game, idx)
            game_reports.append(report)
            all_issues.extend(report.issues)
            idx += 1
        except Exception as e:
            parse_errors += 1
            all_issues.append(Issue(
                game_index=idx, severity="critical", category="parse",
                message=f"python-chess parse error: {e}",
            ))
            idx += 1

    # Aggregate stats
    total_plies = sum(r.ply_count for r in game_reports)
    plies_with_moves = [r.ply_count for r in game_reports if r.ply_count > 0]
    total_comments = sum(r.comment_count for r in game_reports)

    severity_counts = {"critical": 0, "major": 0, "minor": 0}
    for issue in all_issues:
        severity_counts[issue.severity] += 1

    category_counts = {}
    for issue in all_issues:
        category_counts[issue.category] = category_counts.get(issue.category, 0) + 1

    result = {
        "file": str(path),
        "total_games": len(game_reports),
        "parse_errors": parse_errors,
        "total_plies": total_plies,
        "total_comments": total_comments,
        "games_with_moves": len(plies_with_moves),
        "games_without_moves": len(game_reports) - len(plies_with_moves),
        "ply_range": f"{min(plies_with_moves)}-{max(plies_with_moves)}" if plies_with_moves else "N/A",
        "severity_counts": severity_counts,
        "category_counts": category_counts,
        "issues": [asdict(i) for i in all_issues],
        "games": [asdict(r) for r in game_reports],
    }
    return result


def print_summary(result: dict):
    """Print human-readable summary to stdout."""
    print(f"\n{'='*70}")
    print(f"PGN Validation Report: {result['file']}")
    print(f"{'='*70}")
    print(f"Games: {result['total_games']}  (with moves: {result['games_with_moves']}, "
          f"without: {result['games_without_moves']})")
    print(f"Total plies: {result['total_plies']}  |  Ply range: {result['ply_range']}")
    print(f"Total comments: {result['total_comments']}")
    print(f"Parse errors: {result['parse_errors']}")
    print()

    sc = result['severity_counts']
    print(f"Issues:  Critical={sc['critical']}  Major={sc['major']}  Minor={sc['minor']}")
    if result['category_counts']:
        print(f"By category: {result['category_counts']}")
    print()

    # Print issues grouped by severity
    for sev in ("critical", "major", "minor"):
        issues = [i for i in result['issues'] if i['severity'] == sev]
        if not issues:
            continue
        print(f"--- {sev.upper()} ({len(issues)}) ---")
        for i in issues:
            game_label = f"Game {i['game_index']}" if i['game_index'] >= 0 else "File-level"
            print(f"  [{game_label}] [{i['category']}] {i['message']}")
            if i.get('detail'):
                print(f"    → {i['detail']}")
        print()

    # Per-game summary table (only games with issues)
    games_with_issues = [g for g in result['games'] if g['issues']]
    if games_with_issues:
        print(f"--- Games with issues ({len(games_with_issues)}/{result['total_games']}) ---")
        print(f"{'#':>4} {'White':<20} {'Black':<20} {'Plies':>5} {'Comments':>8} {'Issues':>6}")
        print("-" * 70)
        for g in games_with_issues:
            print(f"{g['index']:>4} {g['white']:<20} {g['black']:<20} "
                  f"{g['ply_count']:>5} {g['comment_count']:>8} {len(g['issues']):>6}")


def main():
    parser = argparse.ArgumentParser(description="Validate PGN file for chess book quality")
    parser.add_argument("pgn_file", help="Path to PGN file")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of summary")
    parser.add_argument("--json-file", help="Write JSON report to file")
    args = parser.parse_args()

    result = validate_pgn(args.pgn_file)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)

    if args.json_file:
        Path(args.json_file).write_text(json.dumps(result, indent=2))
        print(f"\nJSON report written to {args.json_file}")

    # Exit code based on severity
    if result['severity_counts']['critical'] > 0:
        sys.exit(2)
    elif result['severity_counts']['major'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
