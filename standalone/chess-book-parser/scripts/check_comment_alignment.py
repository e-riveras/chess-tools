#!/usr/bin/env python3
"""
Check comment-move semantic alignment in annotated PGN files.

Phase A (heuristics): Extract piece/move references from comments, check if they
  match the attached move or nearby moves (±2 plies).
Phase B (LLM): For flagged comments, ask Gemini whether the commentary makes
  sense for the board position.

Usage:
    python scripts/check_comment_alignment.py <pgn_file> [--llm] [--json]
"""

import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import chess
import chess.pgn

# ---------------------------------------------------------------------------
# Piece / SAN extraction from comment text
# ---------------------------------------------------------------------------
PIECE_WORDS = {
    'queen': 'Q', 'queens': 'Q',
    'king': 'K', 'kings': 'K',
    'rook': 'R', 'rooks': 'R',
    'bishop': 'B', 'bishops': 'B',
    'knight': 'N', 'knights': 'N',
    'pawn': 'P', 'pawns': 'P',
}

# Matches SAN-like references in prose: "Nf3", "15.Ba3", "exd5", "O-O"
SAN_REF_RE = re.compile(
    r'(?:(\d+)\.\s*)?'
    r'(O-O-O|O-O|0-0-0|0-0|'
    r'[NBRQK][a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQK])?|'
    r'[a-h]x[a-h][1-8](?:=[NBRQK])?|'
    r'[a-h][1-8](?:=[NBRQK])?)'
    r'[+#]?'
)

# Matches move number references like "15.Ba3" or "15..."
MOVE_NUM_SAN_RE = re.compile(
    r'(\d+)\.(\.\.)?'
    r'(O-O-O|O-O|0-0-0|0-0|'
    r'[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQK])?)'
    r'[+#!?]*'
)


def normalize_san(san: str) -> str:
    """Normalize castling notation."""
    s = san.replace('\u2013', '-').replace('\u2014', '-')
    s = s.replace('0-0-0', 'O-O-O').replace('0-0', 'O-O')
    return s


@dataclass
class CommentAnalysis:
    game_index: int
    ply: int                    # ply number of the move this comment is attached to
    move_san: str               # the actual move played
    comment_excerpt: str        # first 100 chars
    piece_refs: list            # pieces mentioned in comment
    san_refs: list              # SAN moves mentioned
    numbered_refs: list         # "15.Ba3" style references
    flag: str = ""              # "ok", "possible_misalign", "likely_misalign"
    flag_reason: str = ""
    llm_verdict: str = ""       # filled by Phase B


def extract_refs(comment: str) -> tuple[list[str], list[str], list[tuple[int, str]]]:
    """Extract piece names, SAN references, and numbered move references from comment."""
    lower = comment.lower()

    # Piece word mentions
    piece_refs = []
    for word, piece in PIECE_WORDS.items():
        if word in lower:
            piece_refs.append(piece)

    # SAN references
    san_refs = []
    for m in SAN_REF_RE.finditer(comment):
        san = normalize_san(m.group(2))
        if len(san) >= 2:
            san_refs.append(san)

    # Numbered move references "15.Ba3"
    numbered_refs = []
    for m in MOVE_NUM_SAN_RE.finditer(comment):
        move_num = int(m.group(1))
        san = normalize_san(m.group(3))
        numbered_refs.append((move_num, san))

    return list(set(piece_refs)), list(set(san_refs)), numbered_refs


def get_move_piece(board: chess.Board, move: chess.Move) -> str:
    """Get the piece type letter for a move."""
    piece = board.piece_at(move.from_square)
    if piece is None:
        return '?'
    return piece.symbol().upper()


def get_nearby_pieces(game_node, window: int = 2) -> set[str]:
    """Get piece types involved in nearby moves (±window plies)."""
    pieces = set()

    # Go backward
    node = game_node
    for _ in range(window):
        if node.parent is None or node.parent.move is None:
            break
        node = node.parent
        piece = node.board().piece_at(node.move.from_square)
        if piece:
            pieces.add(piece.symbol().upper())

    # Go forward
    node = game_node
    for _ in range(window):
        if not node.variations:
            break
        next_node = node.variations[0]
        piece = node.board().piece_at(next_node.move.from_square)
        if piece:
            pieces.add(piece.symbol().upper())
        node = next_node

    return pieces


# ---------------------------------------------------------------------------
# Phase A: Heuristic alignment check
# ---------------------------------------------------------------------------
def check_numbered_ref(ply: int, move_num: int, is_black: bool) -> bool:
    """Check if a numbered move reference is approximately correct for this ply."""
    # ply 1 = move 1 white, ply 2 = move 1 black, ply 3 = move 2 white...
    expected_num = (ply + 1) // 2
    # Allow ±3 move numbers tolerance (commentary often references future/past)
    return abs(expected_num - move_num) <= 3


def analyze_comment(game_index: int, node, ply: int) -> CommentAnalysis | None:
    """Analyze a single comment for alignment issues."""
    comment = node.comment
    if not comment or len(comment.strip()) < 10:
        return None

    board = node.parent.board() if node.parent else node.board()
    move_san = board.san(node.move) if node.move else "?"
    move_piece = get_move_piece(board, node.move) if node.move else "?"

    piece_refs, san_refs, numbered_refs = extract_refs(comment)

    analysis = CommentAnalysis(
        game_index=game_index,
        ply=ply,
        move_san=move_san,
        comment_excerpt=comment[:100].replace('\n', ' '),
        piece_refs=piece_refs,
        san_refs=san_refs,
        numbered_refs=[(n, s) for n, s in numbered_refs],
    )

    # Check numbered references
    is_black = (ply % 2 == 0)
    bad_numbered = []
    for move_num, san in numbered_refs:
        if not check_numbered_ref(ply, move_num, is_black):
            bad_numbered.append(f"{move_num}.{san}")

    if bad_numbered and len(bad_numbered) > len(numbered_refs) / 2:
        analysis.flag = "possible_misalign"
        analysis.flag_reason = f"Numbered refs far from current ply {ply}: {bad_numbered}"

    # Check piece references against nearby moves
    if piece_refs and node.move:
        nearby = get_nearby_pieces(node)
        nearby.add(move_piece)
        # If comment mentions pieces not seen in nearby moves, might be misaligned
        unmatched = [p for p in piece_refs if p not in nearby and p != 'P']
        if unmatched and len(unmatched) > len(piece_refs) / 2:
            if not analysis.flag:
                analysis.flag = "possible_misalign"
                analysis.flag_reason = f"Pieces {unmatched} not found in nearby moves"

    if not analysis.flag:
        analysis.flag = "ok"

    return analysis


# ---------------------------------------------------------------------------
# Phase B: LLM alignment check (Gemini)
# ---------------------------------------------------------------------------
def llm_check_comments(flagged: list[CommentAnalysis], game_nodes: dict):
    """Call Gemini API to verify flagged comments. Modifies flagged in-place."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Warning: GEMINI_API_KEY not set, skipping LLM phase", file=sys.stderr)
        return

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
    except ImportError:
        print("Warning: google-generativeai not installed, skipping LLM phase", file=sys.stderr)
        return

    for analysis in flagged:
        key = (analysis.game_index, analysis.ply)
        node = game_nodes.get(key)
        if not node:
            continue

        board = node.parent.board() if node.parent else node.board()
        fen = board.fen()
        comment = node.comment

        # Get last 3 moves for context
        recent_moves = []
        n = node
        for _ in range(3):
            if n.parent and n.move:
                recent_moves.insert(0, n.parent.board().san(n.move))
                n = n.parent

        prompt = f"""You are a chess expert reviewing annotated game commentary.

Board position (FEN): {fen}
Last moves leading here: {' '.join(recent_moves)}
Move just played: {analysis.move_san}
Comment attached to this move:
"{comment[:500]}"

Does this commentary make sense for this board position and the move just played?
Answer briefly: "ALIGNED" if the commentary fits, or "MISALIGNED: <reason>" if not.
If misaligned, suggest which move number it likely belongs to."""

        try:
            response = model.generate_content(prompt)
            analysis.llm_verdict = response.text.strip()[:200]
        except Exception as e:
            analysis.llm_verdict = f"LLM error: {e}"


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def analyze_pgn(pgn_path: str, use_llm: bool = False) -> dict:
    """Run alignment analysis on all games in PGN file."""
    text = Path(pgn_path).read_text(encoding='utf-8')
    reader = io.StringIO(text)

    all_analyses = []
    game_nodes = {}  # (game_idx, ply) -> node, for LLM phase
    game_idx = 0

    while True:
        try:
            game = chess.pgn.read_game(reader)
            if game is None:
                break
        except Exception:
            game_idx += 1
            continue

        # Walk mainline
        ply = 0
        # Check root comment
        if game.comment and len(game.comment.strip()) > 10:
            analysis = CommentAnalysis(
                game_index=game_idx,
                ply=0,
                move_san="(start)",
                comment_excerpt=game.comment[:100].replace('\n', ' '),
                piece_refs=extract_refs(game.comment)[0],
                san_refs=extract_refs(game.comment)[1],
                numbered_refs=[(n, s) for n, s in extract_refs(game.comment)[2]],
                flag="ok",  # Root comments are introductory, rarely misaligned
            )
            all_analyses.append(analysis)

        for node in game.mainline():
            ply += 1
            if node.comment:
                analysis = analyze_comment(game_idx, node, ply)
                if analysis:
                    all_analyses.append(analysis)
                    if analysis.flag != "ok":
                        game_nodes[(game_idx, ply)] = node

        game_idx += 1

    # Phase B: LLM check on flagged comments
    flagged = [a for a in all_analyses if a.flag != "ok"]
    if use_llm and flagged:
        print(f"Running LLM check on {len(flagged)} flagged comments...", file=sys.stderr)
        llm_check_comments(flagged, game_nodes)

    # Summary
    ok_count = sum(1 for a in all_analyses if a.flag == "ok")
    flagged_count = len(flagged)

    return {
        'file': pgn_path,
        'total_comments_analyzed': len(all_analyses),
        'aligned': ok_count,
        'flagged': flagged_count,
        'flag_breakdown': {
            'possible_misalign': sum(1 for a in all_analyses if a.flag == 'possible_misalign'),
            'likely_misalign': sum(1 for a in all_analyses if a.flag == 'likely_misalign'),
        },
        'analyses': [asdict(a) for a in all_analyses if a.flag != "ok"],
        'all_analyses_count': len(all_analyses),
    }


def print_summary(result: dict):
    """Print human-readable alignment report."""
    print(f"\n{'='*70}")
    print(f"Comment Alignment Report: {result['file']}")
    print(f"{'='*70}")
    print(f"Comments analyzed: {result['total_comments_analyzed']}")
    print(f"Aligned: {result['aligned']}  |  Flagged: {result['flagged']}")
    if result['flag_breakdown']:
        print(f"Breakdown: {result['flag_breakdown']}")
    print()

    if result['analyses']:
        print(f"--- FLAGGED COMMENTS ({len(result['analyses'])}) ---")
        for a in result['analyses']:
            print(f"\nGame {a['game_index']}, ply {a['ply']} ({a['move_san']}):")
            print(f"  Flag: {a['flag']} — {a['flag_reason']}")
            print(f"  Comment: {a['comment_excerpt']}...")
            if a.get('san_refs'):
                print(f"  SAN refs: {a['san_refs']}")
            if a.get('numbered_refs'):
                print(f"  Numbered refs: {a['numbered_refs']}")
            if a.get('llm_verdict'):
                print(f"  LLM: {a['llm_verdict']}")
    else:
        print("No misaligned comments detected!")


def main():
    parser = argparse.ArgumentParser(description="Check comment-move alignment in PGN")
    parser.add_argument("pgn_file", help="Path to PGN file")
    parser.add_argument("--llm", action="store_true", help="Enable LLM verification of flagged comments")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--json-file", help="Write JSON report to file")
    args = parser.parse_args()

    result = analyze_pgn(args.pgn_file, use_llm=args.llm)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)

    if args.json_file:
        Path(args.json_file).write_text(json.dumps(result, indent=2))
        print(f"\nJSON report written to {args.json_file}")

    sys.exit(1 if result['flagged'] > 0 else 0)


if __name__ == "__main__":
    main()
