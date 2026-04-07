#!/usr/bin/env python3
"""
Faithful Python port of the PGN parser + ChessBoard from docs/books.html.

This deliberately does NOT use python-chess — it replicates the exact geometric
move validation logic from the web app, so we can find games that would break
when displayed in the browser.

Usage:
    python scripts/simulate_webapp_parser.py <pgn_file> [--verbose] [--json]
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from copy import deepcopy


# ---------------------------------------------------------------------------
# Constants (matching books.html)
# ---------------------------------------------------------------------------
FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
RANKS = ['1', '2', '3', '4', '5', '6', '7', '8']


def get_square_coords(sq: str) -> tuple[int, int]:
    """Convert algebraic square to (row, col). row 0 = rank 8 (Black's back rank)."""
    return 7 - RANKS.index(sq[1]), FILES.index(sq[0])


# ---------------------------------------------------------------------------
# ChessBoard — geometric move validation (port of JS ChessBoard)
# ---------------------------------------------------------------------------
class ChessBoard:
    def __init__(self):
        self.board = [[None]*8 for _ in range(8)]
        self.turn = 'w'
        self.castling = {'w': {'K': True, 'Q': True}, 'b': {'k': True, 'q': True}}
        self.en_passant = None
        self.reset()

    def reset(self):
        self.board = [[None]*8 for _ in range(8)]
        for c, p in enumerate('rnbqkbnr'):
            self.board[0][c] = {'color': 'b', 'type': p.upper()}
        for c in range(8):
            self.board[1][c] = {'color': 'b', 'type': 'P'}
            self.board[6][c] = {'color': 'w', 'type': 'P'}
        for c, p in enumerate('RNBQKBNR'):
            self.board[7][c] = {'color': 'w', 'type': p}
        self.turn = 'w'
        self.castling = {'w': {'K': True, 'Q': True}, 'b': {'k': True, 'q': True}}
        self.en_passant = None

    def clone(self):
        nb = ChessBoard.__new__(ChessBoard)
        nb.board = [[deepcopy(cell) for cell in row] for row in self.board]
        nb.turn = self.turn
        nb.castling = deepcopy(self.castling)
        nb.en_passant = self.en_passant
        return nb

    def get_piece(self, row, col):
        if row < 0 or row > 7 or col < 0 or col > 7:
            return None
        return self.board[row][col]

    def is_path_clear(self, r1, c1, r2, c2):
        dr = (1 if r2 > r1 else -1) if r2 != r1 else 0
        dc = (1 if c2 > c1 else -1) if c2 != c1 else 0
        r, c = r1 + dr, c1 + dc
        while r != r2 or c != c2:
            if self.board[r][c] is not None:
                return False
            r += dr
            c += dc
        return True

    def can_move(self, r1, c1, r2, c2, piece_type, is_capture):
        dr = r2 - r1
        dc = c2 - c1
        abs_dr = abs(dr)
        abs_dc = abs(dc)
        t = piece_type.upper()

        if t == 'N':
            return (abs_dr == 2 and abs_dc == 1) or (abs_dr == 1 and abs_dc == 2)
        if t == 'B':
            return abs_dr == abs_dc and self.is_path_clear(r1, c1, r2, c2)
        if t == 'R':
            return (dr == 0 or dc == 0) and self.is_path_clear(r1, c1, r2, c2)
        if t == 'Q':
            return (dr == 0 or dc == 0 or abs_dr == abs_dc) and self.is_path_clear(r1, c1, r2, c2)
        if t == 'K':
            return abs_dr <= 1 and abs_dc <= 1
        if t == 'P':
            direction = -1 if self.turn == 'w' else 1
            start_row = 6 if self.turn == 'w' else 1
            if c1 == c2:
                if is_capture:
                    return False
                if dr == direction:
                    return True
                if r1 == start_row and dr == 2 * direction and self.is_path_clear(r1, c1, r2, c2):
                    return True
                return False
            return is_capture and abs(c1 - c2) == 1 and dr == direction
        return False

    def make_move(self, move_san: str):
        """Attempt to make a move. Returns the move dict or None on failure."""
        clean = re.sub(r'[+#?!]', '', move_san)
        move = None

        if clean in ('O-O', '0-0'):
            row = 7 if self.turn == 'w' else 0
            move = {
                'from': (row, 4), 'to': (row, 6), 'is_castle': True,
                'rook_from': (row, 7), 'rook_to': (row, 5),
            }
        elif clean in ('O-O-O', '0-0-0'):
            row = 7 if self.turn == 'w' else 0
            move = {
                'from': (row, 4), 'to': (row, 2), 'is_castle': True,
                'rook_from': (row, 0), 'rook_to': (row, 3),
            }
        else:
            m = re.match(r'([KQRBN])?([a-h])?([1-8])?x?([a-h][1-8])(=[QRBN])?', clean)
            if not m:
                return None

            piece_type = m.group(1) or 'P'
            from_file = m.group(2)
            from_rank = m.group(3)
            dest = m.group(4)
            promotion = m.group(5)[1] if m.group(5) else None

            to_row, to_col = get_square_coords(dest)

            candidates = []
            for r in range(8):
                for c in range(8):
                    p = self.board[r][c]
                    if p and p['color'] == self.turn and p['type'] == piece_type:
                        if from_file and FILES[c] != from_file:
                            continue
                        if from_rank and RANKS[7 - r] != from_rank:
                            continue
                        candidates.append((r, c))

            if not candidates:
                return None

            from_sq = None
            if len(candidates) == 1:
                from_sq = candidates[0]
            else:
                is_capture = 'x' in clean
                legal = [
                    (r, c) for r, c in candidates
                    if self.can_move(r, c, to_row, to_col, piece_type, is_capture)
                ]
                from_sq = legal[0] if legal else candidates[0]

            move = {
                'from': from_sq, 'to': (to_row, to_col),
                'promotion': promotion, 'is_castle': False,
            }

        if move is None:
            return None

        fr, fc = move['from']
        tr, tc = move['to']
        p = self.board[fr][fc]
        if p is None:
            return None

        self.board[tr][tc] = p
        self.board[fr][fc] = None

        if move.get('is_castle'):
            rfr, rfc = move['rook_from']
            rtr, rtc = move['rook_to']
            rook = self.board[rfr][rfc]
            self.board[rtr][rtc] = rook
            self.board[rfr][rfc] = None

        if move.get('promotion'):
            self.board[tr][tc] = {'color': self.turn, 'type': move['promotion']}

        self.turn = 'b' if self.turn == 'w' else 'w'
        return move


# ---------------------------------------------------------------------------
# PGNParser — port of JS PGNParser from books.html
# ---------------------------------------------------------------------------
class PGNParser:
    @staticmethod
    def parse(pgn_text: str) -> list[dict]:
        games = []
        text = pgn_text.replace('\r\n', '\n')
        lines = text.split('\n')
        current_game = {'headers': {}, 'raw_moves': ''}
        in_moves = False

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('['):
                if in_moves:
                    games.append(PGNParser.process_game(current_game))
                    current_game = {'headers': {}, 'raw_moves': ''}
                    in_moves = False
                m = re.match(r'^\[(\w+)\s+"(.*)"\]$', line)
                if m:
                    current_game['headers'][m.group(1)] = m.group(2)
            else:
                in_moves = True
                current_game['raw_moves'] += ' ' + line

        if current_game['raw_moves']:
            games.append(PGNParser.process_game(current_game))
        return games

    @staticmethod
    def process_game(game_data: dict) -> dict:
        raw = game_data['raw_moves']
        raw = PGNParser.sanitize(raw)
        raw = re.sub(r'\s*(1-0|0-1|1/2-1/2|\*)\s*$', '', raw)
        tokens = PGNParser.tokenize(raw)
        return {'headers': game_data['headers'], 'moves': PGNParser.build_move_tree(tokens)}

    @staticmethod
    def sanitize(text: str) -> str:
        text = re.sub(r'(\d+\.)([^\s.])', r'\1 \2', text)
        text = re.sub(r'(\d+\.\.\.)([^\s])', r'\1 \2', text)
        return text

    @staticmethod
    def tokenize(text: str) -> list[dict]:
        tokens = []
        current = ''
        in_comment = False

        for ch in text:
            if in_comment:
                if ch == '}':
                    tokens.append({'type': 'comment', 'value': current.strip()})
                    current = ''
                    in_comment = False
                else:
                    current += ch
                continue

            if ch == '{':
                if current.strip():
                    tokens.append({'type': 'text', 'value': current.strip()})
                current = ''
                in_comment = True
                continue

            if ch in ('(', ')'):
                if current.strip():
                    tokens.append({'type': 'text', 'value': current.strip()})
                tokens.append({'type': 'symbol', 'value': ch})
                current = ''
                continue

            current += ch

        if current.strip():
            tokens.append({'type': 'text', 'value': current.strip()})

        final_tokens = []
        for t in tokens:
            if t['type'] != 'text':
                final_tokens.append(t)
                continue
            for part in t['value'].split():
                if not part:
                    continue
                if re.match(r'^\d+\.+$', part):
                    continue
                final_tokens.append({'type': 'move', 'value': part})

        return final_tokens

    @staticmethod
    def build_move_tree(tokens: list[dict]) -> list[dict]:
        root = []
        stack = [root]
        current_list = root

        for token in tokens:
            if token['type'] == 'symbol' and token['value'] == '(':
                if not current_list:
                    continue
                last_move = current_list[-1]
                if 'variations' not in last_move:
                    last_move['variations'] = []
                new_var = []
                last_move['variations'].append(new_var)
                stack.append(current_list)
                current_list = new_var
            elif token['type'] == 'symbol' and token['value'] == ')':
                if len(stack) > 1:
                    stack.pop()
                    current_list = stack[-1]
            elif token['type'] == 'comment':
                if current_list:
                    last = current_list[-1]
                    if 'comments' not in last:
                        last['comments'] = []
                    last['comments'].append(token['value'])
            elif token['type'] == 'move':
                san = re.sub(r'^\d+\.+', '', token['value'])
                if not san:
                    continue
                current_list.append({'san': san, 'comments': [], 'variations': []})

        return root


# ---------------------------------------------------------------------------
# Simulation: play each game through the web app board
# ---------------------------------------------------------------------------
NAG_RE = re.compile(r'^\$\d+$')


@dataclass
class GameSimResult:
    index: int
    white: str
    black: str
    event: str
    total_moves: int
    successful_moves: int
    nag_count: int = 0
    failures: list = field(default_factory=list)
    nag_failures: list = field(default_factory=list)
    success: bool = True          # True if no non-NAG failures
    has_nags: bool = False


def simulate_game(parsed_game: dict, idx: int) -> GameSimResult:
    """Play a parsed game's main line through ChessBoard, reporting failures."""
    headers = parsed_game['headers']
    result = GameSimResult(
        index=idx,
        white=headers.get('White', '?'),
        black=headers.get('Black', '?'),
        event=headers.get('Event', '?'),
        total_moves=0,
        successful_moves=0,
    )

    board = ChessBoard()
    moves = parsed_game['moves']

    def play_mainline(move_list, board, depth=0):
        for mi, move_node in enumerate(move_list):
            san = move_node['san']
            result.total_moves += 1

            # Detect NAG tokens ($1, $2, etc.) — valid PGN but web app can't handle them
            if NAG_RE.match(san):
                result.nag_count += 1
                result.has_nags = True
                result.nag_failures.append({
                    'move_index': result.total_moves,
                    'san': san,
                    'turn': 'b' if board.turn == 'b' else 'w',
                    'depth': depth,
                })
                continue

            move = board.make_move(san)
            if move is None:
                result.failures.append({
                    'move_index': result.total_moves,
                    'san': san,
                    'turn': 'b' if board.turn == 'b' else 'w',
                    'depth': depth,
                })
                result.success = False
            else:
                result.successful_moves += 1

    play_mainline(moves, board)
    return result


def simulate_all(pgn_path: str, verbose: bool = False) -> dict:
    """Parse and simulate all games in the PGN file."""
    text = Path(pgn_path).read_text(encoding='utf-8')
    parsed_games = PGNParser.parse(text)

    results = []
    total_pass = 0
    total_fail = 0
    total_nag_games = 0
    total_nag_tokens = 0

    for idx, game in enumerate(parsed_games):
        sim = simulate_game(game, idx)
        results.append(sim)
        if sim.success:
            total_pass += 1
        else:
            total_fail += 1
        if sim.has_nags:
            total_nag_games += 1
            total_nag_tokens += sim.nag_count

    return {
        'file': pgn_path,
        'total_games': len(parsed_games),
        'passed': total_pass,
        'failed': total_fail,
        'pass_rate': f"{total_pass/len(parsed_games)*100:.1f}%" if parsed_games else "N/A",
        'games_with_nags': total_nag_games,
        'total_nag_tokens': total_nag_tokens,
        'games': [asdict(r) for r in results],
    }


def print_summary(result: dict, verbose: bool = False):
    """Print human-readable simulation report."""
    print(f"\n{'='*70}")
    print(f"Web App Parser Simulation: {result['file']}")
    print(f"{'='*70}")
    print(f"Games: {result['total_games']}  |  Passed: {result['passed']}  |  "
          f"Failed: {result['failed']}  |  Rate: {result['pass_rate']}")
    print(f"NAG issues: {result['games_with_nags']} games with {result['total_nag_tokens']} "
          f"NAG tokens (web app treats as invalid moves)")
    print()

    # Report real move failures (non-NAG)
    failed_games = [g for g in result['games'] if not g['success']]
    if failed_games:
        print(f"--- MOVE FAILURES ({len(failed_games)} games) ---")
        for g in failed_games:
            print(f"\nGame {g['index']}: {g['white']} vs {g['black']} ({g['event']})")
            print(f"  Moves: {g['total_moves']} total, {g['successful_moves']} ok, "
                  f"{len(g['failures'])} failed, {g['nag_count']} NAGs skipped")
            for f in g['failures'][:10]:
                print(f"  ✗ Move {f['move_index']}: {f['san']} "
                      f"({'White' if f['turn'] == 'w' else 'Black'} to play)")
            if len(g['failures']) > 10:
                print(f"  ... and {len(g['failures']) - 10} more failures")
    else:
        print("All games' actual moves parsed successfully by the web app logic!")

    # Report NAG-only games (no real failures, just NAGs)
    nag_only_games = [g for g in result['games'] if g['success'] and g['has_nags']]
    if nag_only_games:
        print(f"\n--- NAG-ONLY ISSUES ({len(nag_only_games)} games, moves parse OK) ---")
        for g in nag_only_games[:5]:
            print(f"  Game {g['index']}: {g['white']} vs {g['black']} — "
                  f"{g['nag_count']} NAG tokens")
        if len(nag_only_games) > 5:
            print(f"  ... and {len(nag_only_games) - 5} more")

    if verbose:
        print(f"\n--- ALL GAMES ---")
        print(f"{'#':>4} {'White':<20} {'Black':<20} {'Moves':>5} {'OK':>4} {'Fail':>4} {'Status':>8}")
        print("-" * 70)
        for g in result['games']:
            status = "PASS" if g['success'] else "FAIL"
            print(f"{g['index']:>4} {g['white']:<20} {g['black']:<20} "
                  f"{g['total_moves']:>5} {g['successful_moves']:>4} "
                  f"{len(g['failures']):>4} {status:>8}")


def main():
    parser = argparse.ArgumentParser(description="Simulate web app PGN parser")
    parser.add_argument("pgn_file", help="Path to PGN file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all games")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--json-file", help="Write JSON report to file")
    args = parser.parse_args()

    result = simulate_all(args.pgn_file, args.verbose)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result, args.verbose)

    if args.json_file:
        Path(args.json_file).write_text(json.dumps(result, indent=2))
        print(f"\nJSON report written to {args.json_file}")

    sys.exit(1 if result['failed'] > 0 else 0)


if __name__ == "__main__":
    main()
