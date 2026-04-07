# Detecting chess tactical motifs with python-chess

**A complete architectural guide with working code for fork, pin, skewer, discovered attack, overloaded defender, and back-rank mate detection—integrated into a PGN analysis pipeline that outputs annotated PGN, JSON, and HTML reports.**

The cleanest approach to building a tactical motif detector in Python combines python-chess's native bitboard attack maps with ray-based linear piece analysis, validated by Stockfish evaluation deltas. The python-chess library exposes `board.attacks()`, `board.attackers()`, `board.pin()`, and `SquareSet` bitboard operations that make geometric tactic detection possible at **~5ms per position** without engine calls—engine validation then confirms whether a detected pattern actually wins material. The Lichess puzzler tagger (`ornicar/lichess-puzzler/tagger/cook.py`) is the gold-standard open-source reference, tagging 40+ themes across millions of puzzles using exactly this two-phase pattern: fast geometric detection first, engine confirmation second.

---

## The board traversal primitives that make everything possible

python-chess represents board state internally as **64-bit integer bitboards** for each piece type and color. The library exposes these through a clean Python API, but understanding the bitboard layer is essential for efficient motif detection.

**Attack maps** are the foundation. `board.attacks(square)` returns a `SquareSet` (bitboard wrapper) of all squares a piece attacks from a given square, accounting for current board occupancy (blocking pieces). `board.attackers(color, square)` returns all pieces of a color attacking a target square. Both methods handle sliding pieces correctly—a rook on a1 won't "attack" a8 if there's a piece on a5 blocking it.

```python
import chess

board = chess.Board("r1bqkb1r/pppp1ppp/2n5/4p2Q/2B1n3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 4")

# What does the queen on h5 attack?
queen_attacks = board.attacks(chess.H5)  # SquareSet of attacked squares

# Who attacks f7?
f7_attackers = board.attackers(chess.WHITE, chess.F7)  # {H5, C4} — queen and bishop

# SquareSet supports set operations
enemy_pieces = chess.SquareSet(board.occupied_co[chess.BLACK])
attacked_enemies = queen_attacks & enemy_pieces  # Intersection: enemy pieces under attack
```

**Pin detection** is built-in. `board.is_pinned(color, square)` returns whether a piece is absolutely pinned to its king. `board.pin(color, square)` returns the full ray mask of the pin—the line from pinner through pinned piece to king. If no pin exists, it returns `BB_ALL` (all 64 squares set). The internal `pin_mask()` method iterates over file/rank/diagonal attack tables, finds enemy sliders ("snipers") on the same ray as the king, and checks whether exactly one piece sits between sniper and king.

**Ray and between functions** are critical for pin/skewer/discovered attack logic. `chess.SquareSet.ray(a, b)` returns all squares on the rank, file, or diagonal containing both squares. `chess.SquareSet.between(a, b)` returns squares strictly between two squares on a ray. The pre-computed attack tables `BB_DIAG_ATTACKS`, `BB_RANK_ATTACKS`, `BB_FILE_ATTACKS` enable O(1) sliding piece attack lookups given an occupancy mask.

```python
# Ray from e2 to b5 returns the full diagonal a6-f1
full_ray = chess.SquareSet.ray(chess.E2, chess.B5)

# Squares between e1 and e8 (the e-file interior)
between = chess.SquareSet.between(chess.E1, chess.E8)

# Step-attack tables for non-sliding pieces
knight_attacks = chess.BB_KNIGHT_ATTACKS[chess.E5]  # Raw bitboard int
king_escapes = chess.BB_KING_ATTACKS[chess.G1]
pawn_attacks = chess.BB_PAWN_ATTACKS[chess.WHITE][chess.E4]
```

**Piece accessors** provide fast bitboard-level access: `board.pieces(chess.KNIGHT, chess.WHITE)` returns a `SquareSet`, `board.piece_type_at(square)` returns the piece type integer (1–6), and `board.occupied_co[color]` gives the occupancy bitboard for a color. The piece-type bitboards `board.pawns`, `board.knights`, `board.bishops`, `board.rooks`, `board.queens`, `board.kings` enable direct bitwise operations without iteration.

---

## The TacticClassifier architecture and piece value logic

The core architectural pattern is a `TacticClassifier` class that takes a `chess.Board` plus a `chess.Move`, applies the move, runs each detector, and returns a list of `TacticEvent` dataclass instances. Each detector is a pure function operating on board state—no engine required for the geometric phase.

```python
import chess
import chess.pgn
import chess.svg
import chess.engine
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum

class MotifType(str, Enum):
    FORK = "fork"
    PIN = "pin"
    SKEWER = "skewer"
    DISCOVERED_ATTACK = "discovered_attack"
    OVERLOADED_DEFENDER = "overloaded_defender"
    BACK_RANK_WEAKNESS = "back_rank_weakness"

PIECE_VALUES = {
    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000,
}

@dataclass
class TacticEvent:
    motif: MotifType
    fen_before: str
    fen_after: str
    move_uci: str
    move_san: str
    attacker_square: int
    target_squares: list[int]
    involved_pieces: list[dict]
    severity_score: float = 0.0
    cp_swing: Optional[int] = None
    description: str = ""

class TacticClassifier:
    """Detects tactical motifs from a board position and move."""

    def __init__(self, engine_path: Optional[str] = None, engine_depth: int = 18):
        self.engine = None
        self.engine_depth = engine_depth
        if engine_path:
            self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)

    def classify(self, board: chess.Board, move: chess.Move) -> list[TacticEvent]:
        """Run all detectors on board + move. Returns list of detected tactics."""
        fen_before = board.fen()
        san = board.san(move)
        uci = move.uci()
        events = []

        # Run each geometric detector
        events.extend(self._detect_forks(board, move, fen_before, san, uci))
        events.extend(self._detect_pins_and_skewers(board, move, fen_before, san, uci))
        events.extend(self._detect_discovered_attacks(board, move, fen_before, san, uci))
        events.extend(self._detect_overloaded_defenders(board, move, fen_before, san, uci))
        events.extend(self._detect_back_rank(board, move, fen_before, san, uci))

        # Engine validation phase (optional but recommended)
        if self.engine:
            events = self._validate_with_engine(board, move, events)

        return events

    def close(self):
        if self.engine:
            self.engine.quit()
```

The **piece value comparison** logic is the key differentiator between pins and skewers. A pin has a less-valuable piece in front shielding a more-valuable piece behind; a skewer has a more-valuable piece in front forced to move, exposing a less-valuable piece behind. The `PIECE_VALUES` dict uses centipawn-scale values (**100** for pawn, **320/330** for knight/bishop, **500** for rook, **900** for queen, **20000** for king) rather than the simple 1/3/5/9 scale, which allows finer distinctions (bishop slightly above knight).

---

## Fork detection: post-move attack set intersection

A fork exists when a single piece attacks **two or more enemy pieces of sufficient value** simultaneously. The algorithm: after making the move, compute the attack set of the moved piece, intersect with enemy-occupied squares, filter by value thresholds, and verify the fork is non-trivially profitable.

```python
def _detect_forks(self, board, move, fen_before, san, uci):
    events = []
    color = board.turn
    enemy = not color

    board.push(move)
    to_sq = move.to_square
    piece = board.piece_at(to_sq)
    if piece is None:
        board.pop()
        return events

    attack_set = board.attacks(to_sq)
    attacked_enemies = []

    for sq in attack_set:
        target = board.piece_at(sq)
        if target and target.color == enemy:
            attacked_enemies.append((sq, target))

    # Fork requires 2+ valuable targets
    if len(attacked_enemies) >= 2:
        # Filter: targets must be worth at least as much as the forking piece
        # (exception: any fork involving the king counts)
        forker_val = PIECE_VALUES[piece.piece_type]
        valuable = [
            (sq, t) for sq, t in attacked_enemies
            if t.piece_type == chess.KING or PIECE_VALUES[t.piece_type] >= forker_val
        ]

        if len(valuable) >= 2:
            # False-positive check: is the forking piece immediately capturable
            # for a net loss?
            recapturers = board.attackers(enemy, to_sq)
            net_gain = sum(PIECE_VALUES[t.piece_type] for _, t in valuable)
            if recapturers:
                # If recaptured, we only win the lesser target minus our piece
                min_target_val = min(PIECE_VALUES[t.piece_type] for _, t in valuable)
                net_gain = min_target_val  # Best case: capture biggest, lose forker

            if net_gain > forker_val * 0.5:  # Fork must be profitable
                fen_after = board.fen()
                severity = min(1.0, net_gain / 900)  # Normalize to 0-1

                events.append(TacticEvent(
                    motif=MotifType.FORK,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_uci=uci,
                    move_san=san,
                    attacker_square=to_sq,
                    target_squares=[sq for sq, _ in valuable],
                    involved_pieces=[
                        {"square": chess.square_name(to_sq), "piece": piece.symbol(),
                         "role": "forker"},
                        *[{"square": chess.square_name(sq), "piece": t.symbol(),
                           "role": "target"} for sq, t in valuable]
                    ],
                    severity_score=severity,
                    description=f"{piece.symbol()} fork on {chess.square_name(to_sq)} "
                                f"attacking {', '.join(chess.square_name(sq) for sq, _ in valuable)}"
                ))

    board.pop()
    return events
```

**Knight forks** are the most reliable because knights can't be blocked—only captured or evaded. Queen forks are common but often false positives since the queen can be chased. A fork that simultaneously gives **check** is the most forcing and should receive a **1.5× severity multiplier** since the opponent must address the check first, guaranteeing one target is captured.

---

## Pin and skewer detection through ray casting

Pins and skewers share identical geometric detection logic—the difference is purely **value ordering** along the ray. The algorithm iterates over the moving side's sliding pieces (bishops, rooks, queens), casts rays in each valid direction, collects the first two enemy pieces on each ray, and classifies based on relative piece values.

```python
def _detect_pins_and_skewers(self, board, move, fen_before, san, uci):
    events = []
    color = board.turn

    board.push(move)
    enemy = board.turn  # After push, it's the opponent's turn

    # Check all friendly sliders for pin/skewer patterns
    sliders = (
        (board.bishops | board.queens) & board.occupied_co[not enemy],
        (board.rooks | board.queens) & board.occupied_co[not enemy],
    )
    slider_types = ("diagonal", "linear")

    for slider_mask, ray_type in zip(sliders, slider_types):
        for slider_sq in chess.scan_reversed(slider_mask):
            slider = board.piece_at(slider_sq)
            attacks = board.attacks(slider_sq)

            # For each enemy piece this slider attacks
            for first_sq in attacks:
                first_piece = board.piece_at(first_sq)
                if not first_piece or first_piece.color != enemy:
                    continue

                # Cast ray THROUGH the first piece to find a second piece behind
                ray = chess.SquareSet.ray(slider_sq, first_sq)
                if not ray:
                    continue

                # Walk along the ray past first_piece looking for second piece
                second_sq = None
                second_piece = None

                # Get direction: squares on ray beyond first_piece, away from slider
                beyond = ray & ~chess.SquareSet.between(slider_sq, first_sq)
                beyond.discard(slider_sq)
                beyond.discard(first_sq)

                # Walk outward from first_sq
                for candidate in _squares_along_ray_from(first_sq, slider_sq, ray):
                    occ = board.piece_at(candidate)
                    if occ:
                        if occ.color == enemy:
                            second_sq = candidate
                            second_piece = occ
                        break  # Stop at first piece (friend or foe) beyond

                if second_piece is None:
                    continue

                front_val = PIECE_VALUES[first_piece.piece_type]
                back_val = PIECE_VALUES[second_piece.piece_type]

                # PIN: front piece is LESS valuable than back piece
                # SKEWER: front piece is MORE valuable than back piece
                if back_val > front_val:
                    motif = MotifType.PIN
                    desc = (f"{slider.symbol()} on {chess.square_name(slider_sq)} "
                            f"pins {first_piece.symbol()} on {chess.square_name(first_sq)} "
                            f"against {second_piece.symbol()} on {chess.square_name(second_sq)}")
                    severity = min(1.0, front_val / back_val)
                elif front_val > back_val or first_piece.piece_type == chess.KING:
                    motif = MotifType.SKEWER
                    desc = (f"{slider.symbol()} on {chess.square_name(slider_sq)} "
                            f"skewers {first_piece.symbol()} on {chess.square_name(first_sq)} "
                            f"winning {second_piece.symbol()} on {chess.square_name(second_sq)}")
                    severity = min(1.0, back_val / 900)
                else:
                    continue  # Equal value—not a clear pin or skewer

                fen_after = board.fen()
                events.append(TacticEvent(
                    motif=motif,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_uci=uci,
                    move_san=san,
                    attacker_square=slider_sq,
                    target_squares=[first_sq, second_sq],
                    involved_pieces=[
                        {"square": chess.square_name(slider_sq),
                         "piece": slider.symbol(), "role": "pinner"},
                        {"square": chess.square_name(first_sq),
                         "piece": first_piece.symbol(), "role": "front"},
                        {"square": chess.square_name(second_sq),
                         "piece": second_piece.symbol(), "role": "behind"},
                    ],
                    severity_score=severity,
                    description=desc,
                ))

    board.pop()
    return events


def _squares_along_ray_from(origin, reference, ray):
    """Yield squares along `ray` starting from `origin`, moving away from `reference`."""
    direction = origin - reference
    # Determine step direction from the rank/file deltas
    rank_delta = chess.square_rank(origin) - chess.square_rank(reference)
    file_delta = chess.square_file(origin) - chess.square_file(reference)
    rank_step = (1 if rank_delta > 0 else -1) if rank_delta != 0 else 0
    file_step = (1 if file_delta > 0 else -1) if file_delta != 0 else 0

    r, f = chess.square_rank(origin) + rank_step, chess.square_file(origin) + file_step
    while 0 <= r <= 7 and 0 <= f <= 7:
        sq = chess.square(f, r)
        if sq in ray:
            yield sq
        r += rank_step
        f += file_step
```

The key reliability insight: **absolute pins** (pinned to the king) can leverage `board.is_pinned(color, square)` for fast detection, but **relative pins** (pinned to a queen or rook) require the ray-casting approach above. For the pin/skewer distinction, the rule is deterministic: `PIECE_VALUES[front] < PIECE_VALUES[back]` → pin; `PIECE_VALUES[front] > PIECE_VALUES[back]` → skewer. A king-first alignment is always a skewer (the king must move, exposing whatever is behind).

---

## Discovered attacks via before/after attack map diffing

A discovered attack occurs when moving one piece **unblocks a ray**, revealing an attack from a friendly sliding piece behind it onto an enemy target. The detection algorithm compares attack maps before and after the move to identify newly created attack lines from pieces other than the one that moved.

```python
def _detect_discovered_attacks(self, board, move, fen_before, san, uci):
    events = []
    color = board.turn
    from_sq = move.from_square

    # Snapshot: attacks of all friendly sliders BEFORE the move
    friendly_sliders = (
        (board.rooks | board.bishops | board.queens) & board.occupied_co[color]
    )
    # Remove the moving piece from slider set (we care about pieces BEHIND it)
    friendly_sliders &= ~chess.BB_SQUARES[from_sq]

    before_attacks = {}
    for sq in chess.SquareSet(friendly_sliders):
        before_attacks[sq] = board.attacks(sq)

    # Make the move
    board.push(move)
    enemy = board.turn  # Now opponent's turn

    # Check which friendly sliders gained new attack targets
    for sq in chess.SquareSet(friendly_sliders):
        if board.piece_at(sq) is None:
            continue  # Piece was captured or moved (shouldn't happen for behind piece)
        after = board.attacks(sq)
        newly_attacked = after & ~before_attacks.get(sq, chess.SquareSet())

        # Filter to enemy pieces that are now newly attacked
        new_targets = newly_attacked & board.occupied_co[enemy]
        for target_sq in new_targets:
            target = board.piece_at(target_sq)
            if target is None:
                continue
            slider = board.piece_at(sq)
            is_check = target.piece_type == chess.KING

            severity = 0.7 if is_check else min(1.0, PIECE_VALUES[target.piece_type] / 900)
            if is_check:
                severity = 0.9  # Discovered check is very forcing

            fen_after = board.fen()
            events.append(TacticEvent(
                motif=MotifType.DISCOVERED_ATTACK,
                fen_before=fen_before,
                fen_after=fen_after,
                move_uci=uci,
                move_san=san,
                attacker_square=sq,
                target_squares=[target_sq],
                involved_pieces=[
                    {"square": chess.square_name(from_sq),
                     "piece": board.piece_at(move.to_square).symbol() if board.piece_at(move.to_square) else "?",
                     "role": "unmasker"},
                    {"square": chess.square_name(sq),
                     "piece": slider.symbol(), "role": "revealed_attacker"},
                    {"square": chess.square_name(target_sq),
                     "piece": target.symbol(), "role": "target"},
                ],
                severity_score=severity,
                description=f"Discovered {'check' if is_check else 'attack'}: "
                            f"{slider.symbol()} on {chess.square_name(sq)} "
                            f"now attacks {target.symbol()} on {chess.square_name(target_sq)}",
            ))

    board.pop()
    return events
```

An efficient alternative uses python-chess's **x-ray attack** pattern from the official examples (`examples/xray_attacks.py`). Instead of computing full before/after diffs, you modify the occupancy bitboard by removing the moving piece, recompute sliding attacks with the modified occupancy, and intersect with friendly sliders. This runs in constant time per ray direction. **Discovered checks** are the most forcing variant and should always receive high severity—the opponent must respond to check, giving the moving piece a free tempo.

---

## Overloaded defenders: mapping defensive obligations

An overloaded piece is one defending **two or more critical squares or pieces**—force it to address one obligation, and the others collapse. Detection maps each enemy piece to its defensive duties, then identifies pieces with multiple critical obligations.

```python
def _detect_overloaded_defenders(self, board, move, fen_before, san, uci):
    events = []
    color = board.turn
    enemy = not color

    board.push(move)
    active = board.turn  # Opponent is now active (the "defending" side)

    # Map each defending piece to the critical things it guards
    for def_sq in board.pieces(active):  # Iterate all opponent pieces
        defender = board.piece_at(def_sq)
        if defender is None:
            continue

        defense_set = board.attacks(def_sq)
        obligations = []

        for guarded_sq in defense_set:
            guarded = board.piece_at(guarded_sq)
            # Obligation 1: defending a friendly piece under attack
            if guarded and guarded.color == active:
                attackers = board.attackers(not active, guarded_sq)
                if attackers:
                    # Is this defender the SOLE defender?
                    all_defenders = board.attackers(active, guarded_sq)
                    all_defenders.discard(def_sq)  # Remove self
                    if not all_defenders:
                        obligations.append({
                            "type": "sole_defender",
                            "square": guarded_sq,
                            "piece": guarded.symbol(),
                            "value": PIECE_VALUES[guarded.piece_type],
                        })

        # Overloaded = 2+ sole-defender obligations
        if len(obligations) >= 2:
            total_at_risk = sum(o["value"] for o in obligations)
            severity = min(1.0, total_at_risk / 1200)

            fen_after = board.fen()
            events.append(TacticEvent(
                motif=MotifType.OVERLOADED_DEFENDER,
                fen_before=fen_before,
                fen_after=fen_after,
                move_uci=move.uci(),
                move_san=board.san(move) if board.move_stack else san,
                attacker_square=def_sq,
                target_squares=[o["square"] for o in obligations],
                involved_pieces=[
                    {"square": chess.square_name(def_sq),
                     "piece": defender.symbol(), "role": "overloaded"},
                    *[{"square": chess.square_name(o["square"]),
                       "piece": o["piece"], "role": "obligation"} for o in obligations]
                ],
                severity_score=severity,
                description=f"{defender.symbol()} on {chess.square_name(def_sq)} "
                            f"is overloaded defending {len(obligations)} pieces",
            ))

    board.pop()
    return events
```

The main false-positive risk with overloading is **zwischenzug**—the "overloaded" defender may have an in-between move that changes the calculus entirely. Engine validation is particularly important for this motif.

---

## Back-rank weakness: king shelter plus heavy piece reachability

Back-rank mate vulnerability exists when the king sits on the **back rank** (rank 1 for White, rank 8 for Black), all escape squares one rank forward are blocked by friendly pieces or controlled by the enemy, and the opponent has heavy pieces (rook/queen) that can reach the back rank.

```python
def _detect_back_rank(self, board, move, fen_before, san, uci):
    events = []
    color = board.turn

    board.push(move)
    enemy = board.turn  # Defending side
    back_rank = 0 if enemy == chess.WHITE else 7
    escape_rank = 1 if enemy == chess.WHITE else 6

    king_sq = board.king(enemy)
    if king_sq is None or chess.square_rank(king_sq) != back_rank:
        board.pop()
        return events

    king_file = chess.square_file(king_sq)

    # Check if all escape squares (one rank forward) are blocked
    all_blocked = True
    for df in [-1, 0, 1]:
        f = king_file + df
        if not (0 <= f <= 7):
            continue
        escape_sq = chess.square(f, escape_rank)
        piece_on_escape = board.piece_at(escape_sq)
        # Blocked if: own piece sits there, OR enemy controls it
        is_blocked = (
            (piece_on_escape is not None and piece_on_escape.color == enemy) or
            board.is_attacked_by(not enemy, escape_sq)
        )
        if not is_blocked:
            all_blocked = False
            break

    if not all_blocked:
        board.pop()
        return events

    # Check if attacking side has heavy pieces that threaten the back rank
    heavy = (board.rooks | board.queens) & board.occupied_co[not enemy]
    back_rank_mask = chess.BB_RANK_1 if enemy == chess.WHITE else chess.BB_RANK_8

    for attacker_sq in chess.SquareSet(heavy):
        attacker = board.piece_at(attacker_sq)
        attacker_attacks = board.attacks(attacker_sq)
        # Does this heavy piece attack any square on the back rank?
        if int(attacker_attacks) & back_rank_mask:
            fen_after = board.fen()
            events.append(TacticEvent(
                motif=MotifType.BACK_RANK_WEAKNESS,
                fen_before=fen_before,
                fen_after=fen_after,
                move_uci=move.uci(),
                move_san=san,
                attacker_square=attacker_sq,
                target_squares=[king_sq],
                involved_pieces=[
                    {"square": chess.square_name(attacker_sq),
                     "piece": attacker.symbol(), "role": "threat"},
                    {"square": chess.square_name(king_sq),
                     "piece": "k" if enemy == chess.BLACK else "K", "role": "target"},
                ],
                severity_score=0.85,
                description=f"Back-rank weakness: {attacker.symbol()} on "
                            f"{chess.square_name(attacker_sq)} threatens mate",
            ))
            break  # One threat is enough to flag

    board.pop()
    return events
```

A king with **luft** (an escape square created by advancing g6/h6 or g3/h3) is not vulnerable. The check must verify that no escape exists in any of the three forward squares. Severity should be **critical** when a forcing mate sequence exists, **moderate** when it's just a positional weakness that constrains the defender.

---

## Engine validation turns geometry into ground truth

Geometric detection produces candidates; **Stockfish evaluation** confirms them. The validation phase evaluates the position before and after the tactical move. A cp swing of **>100** indicates a real mistake was punished; **>300** indicates a blunder-level tactic.

```python
def _validate_with_engine(self, board, move, events):
    """Filter events by engine evaluation delta to remove false positives."""
    limit = chess.engine.Limit(depth=self.engine_depth)

    info_before = self.engine.analyse(board, limit)
    score_before = info_before["score"].relative.score(mate_score=10000)

    board.push(move)
    info_after = self.engine.analyse(board, limit)
    score_after = -info_after["score"].relative.score(mate_score=10000)
    board.pop()

    cp_swing = score_after - score_before

    # Thresholds per motif type
    thresholds = {
        MotifType.FORK: 150,
        MotifType.PIN: 80,
        MotifType.SKEWER: 150,
        MotifType.DISCOVERED_ATTACK: 100,
        MotifType.OVERLOADED_DEFENDER: 100,
        MotifType.BACK_RANK_WEAKNESS: 200,
    }

    validated = []
    for event in events:
        threshold = thresholds.get(event.motif, 100)
        if abs(cp_swing) >= threshold:
            event.cp_swing = int(cp_swing)
            validated.append(event)

    return validated
```

**MultiPV analysis** provides richer context. By requesting `multipv=3`, you get the top 3 engine lines and can verify the tactical move is actually the engine's top choice. If the detected tactic corresponds to the best move and the second-best move is significantly worse, that's strong confirmation. The cp loss thresholds used by Lichess and Chess.com align well: **50–100 cp** for inaccuracy (`?!`), **100–300 cp** for mistake (`?`), **300+ cp** for blunder (`??`).

---

## The full PGN analysis pipeline with triple-format output

The pipeline reads PGN files, iterates through every move, runs the classifier, and produces all three output formats simultaneously. Here's the complete integration:

```python
class TacticalAnalysisPipeline:
    """Full pipeline: PGN in → annotated PGN + JSON + HTML out."""

    def __init__(self, engine_path: Optional[str] = None):
        self.classifier = TacticClassifier(engine_path=engine_path)
        self.all_events: list[TacticEvent] = []

    def analyze_pgn(self, pgn_path: str) -> list[TacticEvent]:
        """Parse PGN, classify every position, collect events."""
        with open(pgn_path) as pgn_file:
            while True:
                game = chess.pgn.read_game(pgn_file)
                if game is None:
                    break
                self._analyze_game(game)
        return self.all_events

    def _analyze_game(self, game: chess.pgn.Game):
        board = game.board()
        node = game

        for next_node in game.mainline():
            move = next_node.move
            events = self.classifier.classify(board, move)

            # Annotate the PGN node with detected tactics
            for event in events:
                # Add NAG based on severity
                if event.severity_score > 0.8:
                    next_node.nags.add(chess.pgn.NAG_BRILLIANT_MOVE)
                elif event.severity_score > 0.5:
                    next_node.nags.add(chess.pgn.NAG_GOOD_MOVE)

                # Add descriptive comment
                existing = next_node.comment
                tactic_comment = f"[{event.motif.value}] {event.description}"
                next_node.comment = f"{existing} {tactic_comment}".strip()

                # Store engine eval if available
                if event.cp_swing is not None:
                    score = chess.engine.PovScore(
                        chess.engine.Cp(event.cp_swing), chess.WHITE
                    )
                    next_node.set_eval(score)

            self.all_events.extend(events)
            board.push(move)

        return game

    def export_pgn(self, game: chess.pgn.Game, output_path: str):
        """Write annotated PGN with NAGs and comments."""
        with open(output_path, "w") as f:
            exporter = chess.pgn.FileExporter(f)
            game.accept(exporter)

    def export_json(self, output_path: str):
        """Write JSON array of all tactic events."""
        data = [asdict(e) for e in self.all_events]
        # Convert enum values to strings
        for d in data:
            d["motif"] = d["motif"]  # Already a string via str enum
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

    def export_html(self, output_path: str):
        """Generate HTML report with embedded SVG board diagrams."""
        html = ['<!DOCTYPE html><html><head>',
                '<meta charset="utf-8">',
                '<title>Tactical Analysis Report</title>',
                '<style>',
                'body{font-family:system-ui;max-width:1100px;margin:0 auto;padding:24px}',
                '.tactic{border:1px solid #ddd;border-radius:8px;padding:20px;margin:20px 0}',
                '.tactic h3{margin-top:0}',
                '.board-and-info{display:flex;gap:24px;align-items:flex-start}',
                'table{border-collapse:collapse}td{padding:4px 10px}',
                '.high{border-left:4px solid #e74c3c}',
                '.med{border-left:4px solid #e67e22}',
                '.low{border-left:4px solid #3498db}',
                '</style></head><body>',
                '<h1>♟ Tactical Analysis Report</h1>',
                f'<p>{len(self.all_events)} tactics detected</p>']

        for i, event in enumerate(self.all_events):
            board = chess.Board(event.fen_before)
            move = chess.Move.from_uci(event.move_uci)

            # Build arrows for the tactic
            arrows = [chess.svg.Arrow(move.from_square, move.to_square, color="blue")]
            for sq in event.target_squares:
                arrows.append(chess.svg.Arrow(event.attacker_square, sq, color="red"))

            svg = chess.svg.board(board, arrows=arrows, size=320,
                                 lastmove=move, coordinates=True)

            sev_class = ("high" if event.severity_score > 0.7
                         else "med" if event.severity_score > 0.4 else "low")

            html.append(f'''
            <div class="tactic {sev_class}">
              <h3>#{i+1} — {event.motif.value.replace("_"," ").title()}</h3>
              <div class="board-and-info">
                <div>{svg}</div>
                <div>
                  <p><strong>{event.description}</strong></p>
                  <table>
                    <tr><td>Move</td><td><code>{event.move_san}</code></td></tr>
                    <tr><td>Severity</td><td>{event.severity_score:.2f}</td></tr>
                    <tr><td>CP swing</td><td>{event.cp_swing or "N/A"}</td></tr>
                    <tr><td>FEN</td><td><code style="font-size:11px">{event.fen_before}</code></td></tr>
                  </table>
                </div>
              </div>
            </div>''')

        html.append('</body></html>')
        with open(output_path, "w") as f:
            f.write("\n".join(html))

    def close(self):
        self.classifier.close()
```

For **PGN NAG annotations**, python-chess defines constants: `NAG_GOOD_MOVE = 1` (!), `NAG_MISTAKE = 2` (?), `NAG_BRILLIANT_MOVE = 3` (!!), `NAG_BLUNDER = 4` (??), `NAG_SPECULATIVE_MOVE = 5` (!?), `NAG_DUBIOUS_MOVE = 6` (?!). Position assessment NAGs include `14` (White slight advantage ⩲), `16` (White moderate advantage ±), `18` (White decisive advantage +−). Comments are set via `node.comment = "text"`, and engine evaluations via `node.set_eval(PovScore, depth)` which embeds `[%eval 1.50]` into the comment.

For **SVG board diagrams**, `chess.svg.board()` accepts `arrows` (list of `chess.svg.Arrow` objects with source, destination, and color), `fill` (dict mapping squares to hex colors for highlighting), `squares` (a `SquareSet` to mark with dots), `lastmove`, and `orientation`. The returned SVG string includes Base64-encoded piece images—no external dependencies required—and can be embedded directly in HTML.

---

## Existing open-source references worth studying

The most valuable existing codebases for this problem, ranked by direct relevance:

- **lichess-puzzler** (`github.com/ornicar/lichess-puzzler`) — The production tagger behind Lichess's millions of puzzles. The `tagger/cook.py` file (~400 lines) implements detectors for fork, pin, skewer, discovered attack, back-rank mate, sacrifice, deflection, and 40+ total themes. Uses python-chess + Stockfish. AGPL-3.0 licensed.
- **pgn-tactics-generator** (`github.com/vitogit/pgn-tactics-generator`) — Reads PGN, analyzes with Stockfish, extracts positions with big eval swings. Outputs a tactics PGN file. MIT licensed and simpler to understand than the Lichess puzzler.
- **ChessGrammar** (`chessgrammar.com`) — Commercial API (not open-source) detecting 10 patterns at ~5ms/position using purely geometric heuristics on python-chess, no Stockfish runtime dependency. Demonstrates that engine-free detection is viable for the geometric phase.
- **python-chess examples** (`github.com/niklasf/python-chess/blob/master/examples/`) — The `xray_attacks.py` example shows how to compute x-ray rook and bishop attacks by modifying the occupancy bitboard, which is the efficient foundation for pin/skewer/discovered attack detection.

---

## Handling false positives systematically

False positives are the central challenge. A "fork" where the forking piece is immediately recaptured for equal material is not a real tactic. A "pin" where the pinned piece has no reason to move is positionally irrelevant. The two-phase approach—geometric detection followed by engine or heuristic validation—is the established solution.

**Static Exchange Evaluation (SEE)** is the fastest validation method. Before declaring a fork profitable, run SEE on the fork square: if the forking piece is captured and the recapture sequence results in material loss for the defender, the fork is real. python-chess doesn't expose SEE directly, but the logic is straightforward: alternate captures on a square, lowest-value attacker first, until one side runs out of attackers.

**Engine validation** is the gold standard. A tactic is "real" if the position's engine evaluation swings by more than the motif's threshold when the tactical move is played versus the best alternative. The Lichess puzzler requires the best move to be significantly better than the second-best (typically **>200cp gap** for puzzle creation). For annotation purposes, lower thresholds work: **>100cp** is sufficient to mark a move as tactically significant.

The most common false positives by motif type: **queen forks** (the queen can almost always be chased away—require that at least one target is undefended or the fork gives check), **relative pins** against pawns (technically a pin but rarely exploitable), **discovered attacks** where the revealed attack has no real impact (the target simply moves away), and **back-rank threats** that are two or more moves away from actual mate. For each, the severity scoring and engine validation together filter effectively—if the engine doesn't see a significant eval difference, the pattern is noise.

---

## Conclusion

The architectural approach that works best combines **fast geometric pattern matching** (~5ms/position, no engine) with **optional Stockfish validation** (~50ms/position at depth 18) for confirming tactical significance. The `TacticClassifier` class pattern—separate detector methods per motif, a unified `TacticEvent` data model, and a pipeline that reads PGN and emits three output formats—scales cleanly from single-game analysis to batch processing thousands of games.

The key technical insight is that python-chess's `board.attacks()`, `board.attackers()`, and `SquareSet` operations handle most of the heavy lifting. Pin/skewer/discovered attack detection all reduce to the same underlying operation: **cast rays from sliders, find aligned pieces, classify by value ordering or before/after comparison**. Fork detection is simpler: post-move attack intersection with enemy pieces filtered by value. The Lichess puzzler tagger's `cook.py` is the best reference for production-quality implementations of all these patterns.

For integration into a real pipeline, start with forks and pins (highest signal-to-noise ratio), add discovered attacks and back-rank threats (moderate complexity), and tackle overloading last (highest false-positive rate, most dependent on engine validation). The JSON schema should capture FEN, move (both UCI and SAN), motif type, involved squares and pieces with roles, severity score, and engine eval before/after—this supports downstream consumption by training pipelines, web UIs, or further analytical tools.
