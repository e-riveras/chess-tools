import chess
import chess.engine
import chess.svg
import logging
import urllib.parse
from typing import Optional, List, Dict, Tuple, Any
from chess_tools.lib.models import CrucialMoment

logger = logging.getLogger("chess_transfer")

MATE_SCORE_CP = 10000
BLUNDER_CP = 200
DECIDED_POSITION_CP = 500

# Missed-chance thresholds
MISSED_CHANCE_CP = 200          # min cp lost to count as a missed chance
WINNING_THRESHOLD = 200         # position must be ≥ this to qualify
MISSED_MATE_MOVES = 5           # mate-in ≤ N that was missed

# Severity tiers (absolute eval swing magnitude)
SEVERITY_CRITICAL_CP = 500
SEVERITY_MAJOR_CP = 300

PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100,
}

TACTIC_LABELS = {
    "forced_mate": "Forced Mate",
    "back_rank_mate": "Back-Rank Mate",
    "skewer": "Skewer",
    "pin": "Pin",
    "discovered_attack": "Discovered Attack",
    "hanging_piece": "Hanging Piece",
    "hanging_pawn": "Hanging Pawn",
    "losing_exchange": "Losing Exchange",
    "fork": "Fork",
    "trapped_piece": "Trapped Piece",
    "positional": "Positional",
    "unknown": "Unknown",
}

TACTIC_COLORS = {
    "forced_mate": "#8b0000",
    "back_rank_mate": "#cc0000",
    "skewer": "#e65c00",
    "pin": "#b8860b",
    "discovered_attack": "#6a0dad",
    "hanging_piece": "#c0392b",
    "hanging_pawn": "#e67e22",
    "losing_exchange": "#d4ac0d",
    "fork": "#1a7a4a",
    "trapped_piece": "#1a5276",
    "positional": "#566573",
    "unknown": "#aaaaaa",
}

MOMENT_TYPE_LABELS = {
    "blunder": "Blunder",
    "missed_chance": "Missed Opportunity",
    "missed_mate": "Missed Mate",
}

SEVERITY_COLORS = {
    "critical": "#8b0000",
    "major": "#c0392b",
    "minor": "#d4ac0d",
}


def _compute_severity(eval_swing: int) -> str:
    """Determine severity tier from absolute eval swing magnitude."""
    magnitude = abs(eval_swing)
    if magnitude >= SEVERITY_CRITICAL_CP:
        return "critical"
    elif magnitude >= SEVERITY_MAJOR_CP:
        return "major"
    return "minor"


def classify_moment(
    best_eval: int,
    played_eval: int,
    best_mate_in: Optional[int],
    played_mate_in: Optional[int],
) -> Optional[Tuple[str, str]]:
    """
    Determine the moment type and severity.

    All eval values are from the hero's perspective (positive = good for hero).

    Args:
        best_eval: eval of best line from mover's POV (cp_before).
        played_eval: eval after the played move from mover's POV (cp_after).
        best_mate_in: mate-in from engine's best line (before move), or None.
        played_mate_in: mate-in from position after move (mover's POV), or None.

    Returns:
        (moment_type, severity) tuple, or None if not flagged.
    """
    swing = best_eval - played_eval  # centipawns left on the table

    # Missed mate (highest priority)
    if (best_mate_in is not None
            and best_mate_in <= MISSED_MATE_MOVES
            and best_mate_in > 0
            and played_mate_in is None):
        severity = _compute_severity(swing)
        return ("missed_mate", severity)

    # Blunder (position goes from okay/good to bad)
    if swing >= BLUNDER_CP and played_eval < -100:
        severity = _compute_severity(swing)
        return ("blunder", severity)

    # Missed chance (strong opportunity not taken, but no collapse)
    if (swing >= MISSED_CHANCE_CP
            and best_eval >= WINNING_THRESHOLD
            and played_eval >= -100):
        severity = _compute_severity(swing)
        return ("missed_chance", severity)

    return None


def describe_board(board: chess.Board, hero_color: chess.Color) -> str:
    """Plain-English board summary to ground LLM context without FEN parsing."""
    lines = []

    piece_values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                    chess.ROOK: 5, chess.QUEEN: 9}
    hero_mat = sum(len(board.pieces(pt, hero_color)) * v for pt, v in piece_values.items())
    opp_mat  = sum(len(board.pieces(pt, not hero_color)) * v for pt, v in piece_values.items())
    diff = hero_mat - opp_mat
    if diff == 0:
        lines.append("Material: equal.")
    elif diff > 0:
        lines.append(f"Material: you are up {diff} point(s).")
    else:
        lines.append(f"Material: you are down {abs(diff)} point(s).")

    undefended = []
    for pt in [chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN]:
        for sq in board.pieces(pt, hero_color):
            if not board.is_attacked_by(hero_color, sq):
                undefended.append(f"{chess.piece_name(pt).title()} on {chess.square_name(sq)}")
    if undefended:
        lines.append(f"Your undefended pieces: {', '.join(undefended)}.")

    king_sq = board.king(hero_color)
    if king_sq is not None:
        f = chess.square_file(king_sq)
        r = chess.square_rank(king_sq)
        back_rank = 0 if hero_color == chess.WHITE else 7
        if f >= 6:
            lines.append("Your king is castled kingside.")
        elif f <= 2:
            lines.append("Your king is castled queenside.")
        elif r == back_rank:
            lines.append("Your king is in the center on the back rank.")
        else:
            lines.append("Your king is exposed / uncastled.")

    return " ".join(lines)


def _is_back_rank_mate(board: chess.Board, mate_pv: list, mover_color: chess.Color) -> bool:
    """Check if a forced mate line ends with the king trapped on the back rank."""
    sim = board.copy()
    for m in mate_pv:
        try:
            sim.push(m)
        except Exception:
            return False
    if not sim.is_checkmate():
        return False
    loser = mover_color  # the side being mated
    king_sq = sim.king(loser)
    if king_sq is None:
        return False
    rank = chess.square_rank(king_sq)
    back = 0 if loser == chess.WHITE else 7
    return rank == back


def _ray_between(sq1: int, sq2: int) -> Optional[List[int]]:
    """Return squares strictly between sq1 and sq2 if they are collinear, else None."""
    f1, r1 = chess.square_file(sq1), chess.square_rank(sq1)
    f2, r2 = chess.square_file(sq2), chess.square_rank(sq2)
    df, dr = f2 - f1, r2 - r1
    if df == 0 and dr == 0:
        return None
    # Must be on same rank, file, or diagonal
    if df != 0 and dr != 0 and abs(df) != abs(dr):
        return None
    step_f = (1 if df > 0 else -1) if df != 0 else 0
    step_r = (1 if dr > 0 else -1) if dr != 0 else 0
    squares = []
    f, r = f1 + step_f, r1 + step_r
    while (f, r) != (f2, r2):
        squares.append(chess.square(f, r))
        f += step_f
        r += step_r
    return squares


def _slider_can_reach(piece_type: int, df: int, dr: int) -> bool:
    """Check if a sliding piece type can attack along the direction (df, dr)."""
    is_diagonal = (df != 0 and dr != 0 and abs(df) == abs(dr))
    is_straight = (df == 0) != (dr == 0)  # exactly one is zero
    if piece_type == chess.BISHOP:
        return is_diagonal
    if piece_type == chess.ROOK:
        return is_straight
    if piece_type == chess.QUEEN:
        return is_diagonal or is_straight
    return False


def _find_absolute_pins(board: chess.Board, color: chess.Color) -> set:
    """Return set of squares of `color`'s pieces that are pinned to their king."""
    pinned = set()
    for pt in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]:
        for sq in board.pieces(pt, color):
            if board.is_pinned(color, sq):
                pinned.add(sq)
    return pinned


def _find_relative_pins(board: chess.Board, pinner_color: chess.Color, victim_color: chess.Color) -> set:
    """
    Return set of `victim_color` squares that are relatively pinned.
    A relative pin: a sliding piece of `pinner_color` attacks through a lower-value
    `victim_color` piece to a higher-value `victim_color` piece behind it.
    """
    pinned = set()
    for slider_type in [chess.BISHOP, chess.ROOK, chess.QUEEN]:
        for slider_sq in board.pieces(slider_type, pinner_color):
            for pt in [chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN]:
                for front_sq in board.pieces(pt, victim_color):
                    ray = _ray_between(slider_sq, front_sq)
                    if ray is None:
                        continue
                    # Verify slider can actually attack along this direction
                    f_s, r_s = chess.square_file(slider_sq), chess.square_rank(slider_sq)
                    f_f, r_f = chess.square_file(front_sq), chess.square_rank(front_sq)
                    df = f_f - f_s
                    dr = r_f - r_s
                    if not _slider_can_reach(slider_type, df, dr):
                        continue
                    if any(board.piece_at(s) is not None for s in ray):
                        continue
                    step_f = (1 if df > 0 else -1) if df != 0 else 0
                    step_r = (1 if dr > 0 else -1) if dr != 0 else 0
                    f, r = f_f + step_f, r_f + step_r
                    while 0 <= f <= 7 and 0 <= r <= 7:
                        behind_sq = chess.square(f, r)
                        behind_piece = board.piece_at(behind_sq)
                        if behind_piece:
                            if behind_piece.color == victim_color:
                                front_val = PIECE_VALUES.get(pt, 0)
                                back_val = PIECE_VALUES.get(behind_piece.piece_type, 0)
                                if front_val < back_val:
                                    pinned.add(front_sq)
                            break
                        f += step_f
                        r += step_r
    return pinned


def classify_tactic(
    board_after: chess.Board,
    refutation_pv: list,
    mate_in,
    mover_color: chess.Color,
) -> str:
    """
    Deterministically label the tactic type from the refutation line.

    Detection order (highest priority first):
    1. Forced mate (+ back-rank sub-classification)
    2. Skewer
    3. Pin (absolute + relative) — must be NEWLY created
    4. Discovered attack (check or on high-value pieces)
    5. Hanging piece/pawn (+ en passant + promotion-capture)
    6. Losing exchange
    7. Fork (moved piece only, must have NEWLY attacked targets)
    8. Trapped piece
    9. Positional (catch-all)
    """
    opp_color = not mover_color

    # 1. Forced mate
    if mate_in is not None and mate_in <= 9:
        if refutation_pv and _is_back_rank_mate(board_after, refutation_pv, mover_color):
            return "back_rank_mate"
        return "forced_mate"

    if not refutation_pv:
        return "unknown"

    first_move = refutation_pv[0]

    # Apply first refutation move to get resulting position
    board_copy = board_after.copy()
    board_copy.push(first_move)

    # Determine the effective piece type (accounts for pawn promotions)
    moved_piece = board_after.piece_at(first_move.from_square)
    effective_piece_type = first_move.promotion if first_move.promotion else (
        moved_piece.piece_type if moved_piece else None
    )

    # 2. Skewer — after the refutation move, check if a sliding piece attacks through
    #    a higher-value piece to a lower-value piece along a ray
    if effective_piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        to_sq = first_move.to_square
        for pt in [chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
            for target_sq in board_copy.pieces(pt, mover_color):
                ray = _ray_between(to_sq, target_sq)
                if ray is None:
                    continue
                # Verify slider can attack along this direction
                f_to, r_to = chess.square_file(to_sq), chess.square_rank(to_sq)
                f_tgt, r_tgt = chess.square_file(target_sq), chess.square_rank(target_sq)
                df = f_tgt - f_to
                dr = r_tgt - r_to
                if not _slider_can_reach(effective_piece_type, df, dr):
                    continue
                blocked = any(board_copy.piece_at(s) is not None for s in ray)
                if blocked:
                    continue
                step_f = (1 if df > 0 else -1) if df != 0 else 0
                step_r = (1 if dr > 0 else -1) if dr != 0 else 0
                f, r = f_tgt + step_f, r_tgt + step_r
                while 0 <= f <= 7 and 0 <= r <= 7:
                    behind_sq = chess.square(f, r)
                    behind_piece = board_copy.piece_at(behind_sq)
                    if behind_piece:
                        if behind_piece.color == mover_color:
                            front_val = PIECE_VALUES.get(pt, 0)
                            back_val = PIECE_VALUES.get(behind_piece.piece_type, 0)
                            # Skewer: front piece is more valuable than back piece
                            if front_val > back_val:
                                return "skewer"
                        break
                    f += step_f
                    r += step_r

    # 3. Pin — must be NEWLY created by the refutation move.
    # Baseline is always board_after (post-blunder, pre-refutation) so we only
    # detect pins that the refutation itself introduces, not pre-existing ones.
    abs_pins_before = _find_absolute_pins(board_after, mover_color)
    abs_pins_after = _find_absolute_pins(board_copy, mover_color)
    if abs_pins_after - abs_pins_before:
        return "pin"

    # Relative pins: compare before vs after refutation
    rel_pins_before = _find_relative_pins(board_after, opp_color, mover_color)
    rel_pins_after = _find_relative_pins(board_copy, opp_color, mover_color)
    if rel_pins_after - rel_pins_before:
        return "pin"

    # 4a. Discovered check — check comes from a piece OTHER than the one that moved
    if board_copy.is_check():
        opp_king_sq = board_copy.king(mover_color)
        if opp_king_sq is not None:
            checkers = board_copy.attackers(opp_color, opp_king_sq)
            if first_move.to_square not in checkers and len(checkers) > 0:
                return "discovered_attack"

    # 4b. Discovered attack on high-value pieces (queen/rook) — a piece OTHER than
    #      the moved piece gained a new attacker after the refutation
    for hvp_type in [chess.QUEEN, chess.ROOK]:
        for hvp_sq in board_copy.pieces(hvp_type, mover_color):
            new_attackers = board_copy.attackers(opp_color, hvp_sq) - board_after.attackers(opp_color, hvp_sq)
            # Exclude the moved piece itself (that's a direct attack, not discovered)
            new_attackers.discard(first_move.to_square)
            if new_attackers:
                return "discovered_attack"

    # 5. Hanging piece/pawn
    if board_after.is_capture(first_move):
        captured = board_after.piece_at(first_move.to_square)
        # Handle en passant
        if captured is None and board_after.is_en_passant(first_move):
            return "hanging_pawn"
        # Handle promotion-capture: the moved piece may be a pawn promoting
        if captured is None and first_move.promotion:
            return "hanging_piece"
        if captured and captured.color == mover_color:
            is_defended = board_after.is_attacked_by(mover_color, first_move.to_square)
            if captured.piece_type == chess.PAWN:
                if not is_defended:
                    return "hanging_pawn"
                # defended pawn capture — fall through to other checks
            elif not is_defended:
                return "hanging_piece"
            else:
                # 6. Losing exchange — opponent captures a defended piece with a cheaper
                #    piece, so the hero loses material even after recapturing.
                attacker_piece = board_after.piece_at(first_move.from_square)
                if attacker_piece:
                    attacker_val = PIECE_VALUES.get(attacker_piece.piece_type, 0)
                    captured_val = PIECE_VALUES.get(captured.piece_type, 0)
                    if attacker_val < captured_val:
                        return "losing_exchange"
                # defended but not losing exchange — fall through to other checks

    # 7. Fork — restricted to attacks by the moved piece only, must have NEW targets
    if moved_piece:
        attacked_now = set()
        attacked_before = set()
        for pt in [chess.QUEEN, chess.ROOK, chess.KNIGHT, chess.BISHOP, chess.KING]:
            for sq in board_copy.pieces(pt, mover_color):
                attackers = board_copy.attackers(opp_color, sq)
                if first_move.to_square in attackers:
                    attacked_now.add(sq)
            # Check what the same piece attacked before (from its original square)
            for sq in board_after.pieces(pt, mover_color):
                attackers = board_after.attackers(opp_color, sq)
                if first_move.from_square in attackers:
                    attacked_before.add(sq)
        new_targets = attacked_now - attacked_before
        if len(attacked_now) >= 2 and len(new_targets) >= 1:
            return "fork"

    # 8. Trapped piece — attacked piece with no safe escape square
    for pt in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
        for sq in board_copy.pieces(pt, mover_color):
            if not board_copy.is_attacked_by(opp_color, sq):
                continue
            piece_val = PIECE_VALUES.get(pt, 0)
            has_escape = False
            for legal in board_copy.legal_moves:
                if legal.from_square != sq:
                    continue
                sim = board_copy.copy()
                sim.push(legal)
                if not sim.is_attacked_by(opp_color, legal.to_square):
                    has_escape = True
                    break
                defender_piece = board_copy.piece_at(legal.to_square)
                if defender_piece and PIECE_VALUES.get(defender_piece.piece_type, 0) >= piece_val:
                    has_escape = True
                    break
            if not has_escape:
                return "trapped_piece"

    # 9. Positional (catch-all)
    return "positional"


class ChessAnalyzer:
    """
    Wraps the Stockfish chess engine to analyze games and identify mistakes.
    """
    def __init__(self, engine_path: str, time_limit: float = 0.1):
        """
        Args:
            engine_path (str): Path to the Stockfish binary.
            time_limit (float): Time in seconds to spend analyzing each move.
        """
        self.engine_path = engine_path
        self.time_limit = time_limit
        self.engine: Optional[chess.engine.SimpleEngine] = None

    def __enter__(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            logger.info(f"Engine loaded: {self.engine_path}")
        except Exception as e:
            logger.critical(f"Failed to load Stockfish engine at {self.engine_path}: {e}")
            raise e
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.engine:
            self.engine.quit()

    def _score_to_cp(self, score: chess.engine.Score) -> int:
        """
        Converts a chess.engine.Score object to centipawns (clamped).
        Handles mate scores by converting them to +/- 10000.
        """
        if score.is_mate():
            return MATE_SCORE_CP if score.mate() > 0 else -MATE_SCORE_CP
        return score.score(mate_score=MATE_SCORE_CP)

    def analyze_game(self, pgn_text: str, hero_username: str = None, threshold: int = BLUNDER_CP) -> Tuple[List[CrucialMoment], Dict[str, str], List[Dict[str, Any]]]:
        """
        Iterates through the game moves and identifies crucial moments.

        Args:
            pgn_text (str): The raw PGN text of the game.
            hero_username (str): The username of the player to analyze (skips opponent moves).
            threshold (int): Centipawn loss required to flag a move as a mistake.

        Returns:
            Tuple of (moments, metadata, move_evals):
                moments: List of CrucialMoment objects.
                metadata: Game metadata dict.
                move_evals: Per-half-move eval list for chart/PGN annotation.
        """
        import io
        import chess.pgn

        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if not game:
            logger.error("Could not parse PGN.")
            return [], {}, []

        # Extract Metadata
        headers = game.headers
        metadata = {
            "White": headers.get("White", "Unknown"),
            "Black": headers.get("Black", "Unknown"),
            "Date": headers.get("Date", "Unknown"),
            "Event": headers.get("Event", "Unknown"),
            "Site": headers.get("Site", "Unknown"),
            "Result": headers.get("Result", "*")
        }

        # Determine Hero Color
        hero_color = None
        if hero_username:
            if metadata["White"].lower() == hero_username.lower():
                hero_color = chess.WHITE
            elif metadata["Black"].lower() == hero_username.lower():
                hero_color = chess.BLACK
            else:
                logger.warning(f"Hero {hero_username} not found in players: {metadata['White']} vs {metadata['Black']}")

        moments = []
        move_evals: List[Dict[str, Any]] = []
        # Map half_move_number -> index in moments list (for linking chart markers)
        moment_half_moves: Dict[int, int] = {}
        half_move_num = 0

        for node in game.mainline():
            if not self.engine:
                break

            board_before = node.parent.board()
            mover_color = board_before.turn
            half_move_num += 1

            # Always analyze both sides for eval tracking (chart needs all moves)
            info_before = self.engine.analyse(board_before, chess.engine.Limit(time=self.time_limit))
            score_before_white = info_before["score"].white()
            cp_white_before = self._score_to_cp(score_before_white)

            score_before = info_before["score"].pov(mover_color)
            cp_before = self._score_to_cp(score_before)

            board_after = node.board()
            info_after = self.engine.analyse(board_after, chess.engine.Limit(time=self.time_limit))

            # Eval after move from White's perspective (for chart)
            score_after_white = info_after["score"].white()
            cp_white_after = self._score_to_cp(score_after_white)

            # Mate-in from White's perspective
            mate_in_white = None
            if score_after_white.is_mate() and score_after_white.mate() is not None:
                mate_in_white = score_after_white.mate()

            # Record per-move eval (from White's perspective for chart/PGN)
            move_eval_entry = {
                "half_move": half_move_num,
                "san": node.san(),
                "eval_cp": cp_white_after,
                "mate_in": mate_in_white,
                "is_white": mover_color == chess.WHITE,
            }
            move_evals.append(move_eval_entry)

            # Filter: Only flag moments for hero's moves
            if hero_color is not None and mover_color != hero_color:
                continue

            engine_best_move = info_before.get("pv", [None])[0]
            engine_best_move_san = board_before.san(engine_best_move) if engine_best_move else "N/A"
            engine_best_move_uci = engine_best_move.uci() if engine_best_move else "N/A"

            # Score from mover's perspective (negate opponent's score)
            score_after_opponent = info_after["score"].pov(board_after.turn)
            cp_after = -self._score_to_cp(score_after_opponent)

            # Calculate Swing
            delta = cp_after - cp_before

            # Smart Filter (Context-Aware Mercy Rule)
            is_decided_before = abs(cp_before) > DECIDED_POSITION_CP
            is_decided_after = abs(cp_after) > DECIDED_POSITION_CP
            same_result = (cp_before > 0 and cp_after > 0) or (cp_before < 0 and cp_after < 0)

            if is_decided_before and is_decided_after and same_result:
                continue

            # Detect best mate-in from before-move analysis
            best_mate_in = None
            if score_before.is_mate() and score_before.mate() is not None and score_before.mate() > 0:
                best_mate_in = score_before.mate()

            # Played mate-in (from after-move, mover's POV)
            played_mate_in = None
            score_after_mover = info_after["score"].pov(mover_color)
            if score_after_mover.is_mate() and score_after_mover.mate() is not None and score_after_mover.mate() > 0:
                played_mate_in = score_after_mover.mate()

            # Determine moment type using new classify_moment (returns tuple or None)
            moment_result = classify_moment(cp_before, cp_after, best_mate_in, played_mate_in)

            if moment_result is None:
                continue

            moment_type, severity = moment_result

            # PV Line (engine's best continuation from before move)
            pv_moves = info_before.get("pv", [])
            dummy_board = board_before.copy()
            pv_san_list = []
            for move in pv_moves[:4]:
                pv_san_list.append(dummy_board.san(move))
                dummy_board.push(move)
            pv_line = " ".join(pv_san_list)

            best_line = pv_line

            # Tactical Alert Logic
            tactical_alert = None

            # Full refutation line
            refutation_pv = info_after.get("pv", [])
            refutation_board = board_after.copy()
            refutation_san_list = []
            for move in refutation_pv[:4]:
                try:
                    refutation_san_list.append(refutation_board.san(move))
                    refutation_board.push(move)
                except Exception:
                    break
            refutation_line = " ".join(refutation_san_list)

            # Mate detection from info_after
            refutation_score = info_after["score"].pov(board_after.turn)
            mate_in = abs(refutation_score.mate()) if refutation_score.is_mate() else None

            is_blunder = moment_type == "blunder"

            # Tactic classification
            if is_blunder:
                tactic_type = classify_tactic(board_after.copy(), refutation_pv, mate_in, mover_color)
            else:
                opp_color = not mover_color
                best_pv = info_before.get("pv", [])
                tactic_type = classify_tactic(board_before.copy(), best_pv, best_mate_in, opp_color)

            board_description = describe_board(board_before, mover_color)

            # Tactical alert (blunders only)
            if is_blunder:
                refutation_move = refutation_pv[0] if refutation_pv else None
                if refutation_move:
                    if board_after.is_capture(refutation_move):
                        captured_piece = board_after.piece_at(refutation_move.to_square)
                        if captured_piece and captured_piece.color == mover_color:
                            piece_name = chess.piece_name(captured_piece.piece_type).title()
                            color_name = "White" if mover_color == chess.WHITE else "Black"
                            square_name = chess.square_name(refutation_move.to_square)
                            tactical_alert = f"CRITICAL: Your move allowed the opponent to immediately capture your {color_name} {piece_name} on {square_name}."

            # Generate SVG with Arrows
            arrows = []
            if is_blunder:
                arrows.append(chess.svg.Arrow(node.move.from_square, node.move.to_square, color="#d40000cc"))
            else:
                arrows.append(chess.svg.Arrow(node.move.from_square, node.move.to_square, color="#ccaa00cc"))

            if engine_best_move:
                arrow_color = "#008800cc" if is_blunder else "#0066ddcc"
                arrows.append(chess.svg.Arrow(engine_best_move.from_square, engine_best_move.to_square, color=arrow_color))

            orientation = hero_color if hero_color is not None else chess.WHITE

            svg_data = chess.svg.board(
                board=board_before,
                arrows=arrows,
                orientation=orientation,
                size=400,
                coordinates=True
            )

            fen_encoded = urllib.parse.quote(board_before.fen())
            color_str = "white" if orientation == chess.WHITE else "black"
            image_url = f"https://lichess.org/export/fen.gif?fen={fen_encoded}&color={color_str}"

            # Punished detection: did the opponent play the refutation (or equivalent)?
            punished: Optional[bool] = None
            opponent_reply_san: Optional[str] = None
            if is_blunder and node.next() is not None:
                opp_node = node.next()
                opp_move_uci = opp_node.move.uci()
                opponent_reply_san = board_after.san(opp_node.move)
                if refutation_pv and opp_move_uci == refutation_pv[0].uci():
                    punished = True
                elif refutation_pv:
                    # Fallback: check if opponent's reply achieves eval within 100cp of refutation
                    board_opp = board_after.copy()
                    info_opp = self.engine.analyse(board_opp, chess.engine.Limit(time=self.time_limit),
                                                   root_moves=[opp_node.move])
                    opp_reply_eval = self._score_to_cp(info_opp["score"].pov(board_after.turn))
                    ref_eval = self._score_to_cp(info_after["score"].pov(board_after.turn))
                    punished = abs(opp_reply_eval - ref_eval) <= 100
                else:
                    punished = False
            elif is_blunder and node.next() is None:
                punished = None  # game ended after blunder

            # Refutation first move UCI for drill system
            refutation_move_uci = refutation_pv[0].uci() if refutation_pv else None

            moment = CrucialMoment(
                fen=board_before.fen(),
                move_played_san=node.san(),
                move_played_uci=node.move.uci(),
                best_move_san=engine_best_move_san,
                best_move_uci=engine_best_move_uci,
                eval_swing=delta,
                eval_after=cp_after,
                pv_line=pv_line,
                explanation=None,
                svg_content=svg_data,
                game_result=metadata["Result"],
                hero_color=hero_color,
                tactical_alert=tactical_alert,
                image_url=image_url,
                refutation_line=refutation_line if is_blunder else "",
                mate_in=mate_in,
                tactic_type=tactic_type,
                board_description=board_description,
                moment_type=moment_type,
                severity=severity,
                best_line=best_line if not is_blunder else "",
                half_move_number=half_move_num,
                punished=punished,
                opponent_reply_san=opponent_reply_san,
                fen_after=board_after.fen() if is_blunder else None,
                refutation_move_uci=refutation_move_uci if is_blunder else None,
            )

            moments.append(moment)
            moment_half_moves[half_move_num] = len(moments) - 1
            label = "Blunder" if is_blunder else "Missed chance"
            logger.info(f"{label} found for {hero_username}: {moment.move_played_san} (Delta: {delta}, {moment_type}/{severity})")

        # Tag move_evals with moment info for chart markers
        for hm, idx in moment_half_moves.items():
            for entry in move_evals:
                if entry["half_move"] == hm:
                    entry["moment_type"] = moments[idx].moment_type
                    entry["moment_index"] = idx
                    break

        return moments, metadata, move_evals
