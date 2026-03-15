"""
Tactical motif detector for chess positions.

Implements geometric detection (fork, pin, skewer, discovered attack,
overloaded defender, back-rank weakness) with optional Stockfish validation.
"""

import json
import chess
import chess.pgn
import chess.svg
import chess.engine
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Iterator, Optional


class MotifType(str, Enum):
    FORK = "fork"
    PIN = "pin"
    SKEWER = "skewer"
    DISCOVERED_ATTACK = "discovered_attack"
    OVERLOADED_DEFENDER = "overloaded_defender"
    BACK_RANK_WEAKNESS = "back_rank_weakness"


PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
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


def _squares_along_ray_from(origin: int, reference: int, ray: chess.SquareSet) -> Iterator[int]:
    """Yield squares along `ray` starting from `origin`, moving away from `reference`."""
    rank_delta = chess.square_rank(origin) - chess.square_rank(reference)
    file_delta = chess.square_file(origin) - chess.square_file(reference)
    rank_step = (1 if rank_delta > 0 else -1) if rank_delta != 0 else 0
    file_step = (1 if file_delta > 0 else -1) if file_delta != 0 else 0

    r = chess.square_rank(origin) + rank_step
    f = chess.square_file(origin) + file_step
    while 0 <= r <= 7 and 0 <= f <= 7:
        sq = chess.square(f, r)
        if sq in ray:
            yield sq
        r += rank_step
        f += file_step


class TacticClassifier:
    """Detects tactical motifs from a board position and move."""

    def __init__(self, engine_path: Optional[str] = None, engine_depth: int = 18):
        self.engine: Optional[chess.engine.SimpleEngine] = None
        self.engine_depth = engine_depth
        if engine_path:
            self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)

    def classify(self, board: chess.Board, move: chess.Move) -> list[TacticEvent]:
        """Run all detectors on board + move. Returns list of detected tactics."""
        fen_before = board.fen()
        san = board.san(move)
        uci = move.uci()
        events: list[TacticEvent] = []

        events.extend(self._detect_forks(board, move, fen_before, san, uci))
        events.extend(self._detect_pins_and_skewers(board, move, fen_before, san, uci))
        events.extend(self._detect_discovered_attacks(board, move, fen_before, san, uci))
        events.extend(self._detect_overloaded_defenders(board, move, fen_before, san, uci))
        events.extend(self._detect_back_rank(board, move, fen_before, san, uci))

        if self.engine and events:
            events = self._validate_with_engine(board, move, events)

        return events

    def close(self):
        if self.engine:
            self.engine.quit()

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    def _detect_forks(
        self, board: chess.Board, move: chess.Move,
        fen_before: str, san: str, uci: str,
    ) -> list[TacticEvent]:
        events: list[TacticEvent] = []
        color = board.turn
        enemy = not color

        board.push(move)
        to_sq = move.to_square
        piece = board.piece_at(to_sq)
        if piece is None:
            board.pop()
            return events

        attack_set = board.attacks(to_sq)
        attacked_enemies = [
            (sq, board.piece_at(sq))
            for sq in attack_set
            if board.piece_at(sq) and board.piece_at(sq).color == enemy  # type: ignore[union-attr]
        ]

        if len(attacked_enemies) >= 2:
            forker_val = PIECE_VALUES[piece.piece_type]
            valuable = [
                (sq, t) for sq, t in attacked_enemies
                if t.piece_type == chess.KING or PIECE_VALUES[t.piece_type] >= forker_val
            ]

            if len(valuable) >= 2:
                recapturers = board.attackers(enemy, to_sq)
                net_gain = sum(PIECE_VALUES[t.piece_type] for _, t in valuable)
                if recapturers:
                    min_target_val = min(PIECE_VALUES[t.piece_type] for _, t in valuable)
                    net_gain = min_target_val

                if net_gain > forker_val * 0.5:
                    fen_after = board.fen()
                    severity = min(1.0, net_gain / 900)

                    # Check bonus: fork that also gives check is extra forcing
                    if board.is_check():
                        severity = min(1.0, severity * 1.5)

                    events.append(TacticEvent(
                        motif=MotifType.FORK,
                        fen_before=fen_before,
                        fen_after=fen_after,
                        move_uci=uci,
                        move_san=san,
                        attacker_square=to_sq,
                        target_squares=[sq for sq, _ in valuable],
                        involved_pieces=[
                            {"square": chess.square_name(to_sq),
                             "piece": piece.symbol(), "role": "forker"},
                            *[{"square": chess.square_name(sq),
                               "piece": t.symbol(), "role": "target"}
                              for sq, t in valuable],
                        ],
                        severity_score=severity,
                        description=(
                            f"{piece.symbol()} fork on {chess.square_name(to_sq)} "
                            f"attacking {', '.join(chess.square_name(sq) for sq, _ in valuable)}"
                        ),
                    ))

        board.pop()
        return events

    def _detect_pins_and_skewers(
        self, board: chess.Board, move: chess.Move,
        fen_before: str, san: str, uci: str,
    ) -> list[TacticEvent]:
        events: list[TacticEvent] = []

        board.push(move)
        enemy = board.turn          # After push: opponent's turn
        friendly = not enemy        # The side that just moved

        # Diagonal sliders (bishops + queens) and linear sliders (rooks + queens)
        sliders = (
            (board.bishops | board.queens) & board.occupied_co[friendly],
            (board.rooks | board.queens) & board.occupied_co[friendly],
        )

        for slider_mask in sliders:
            for slider_sq in chess.scan_reversed(slider_mask):
                slider = board.piece_at(slider_sq)
                if slider is None:
                    continue
                attacks = board.attacks(slider_sq)

                for first_sq in attacks:
                    first_piece = board.piece_at(first_sq)
                    if not first_piece or first_piece.color != enemy:
                        continue

                    ray = chess.SquareSet.ray(slider_sq, first_sq)
                    if not ray:
                        continue

                    # Walk along the ray past first_piece
                    second_sq = None
                    second_piece = None
                    for candidate in _squares_along_ray_from(first_sq, slider_sq, ray):
                        occ = board.piece_at(candidate)
                        if occ:
                            if occ.color == enemy:
                                second_sq = candidate
                                second_piece = occ
                            break

                    if second_piece is None or second_sq is None:
                        continue

                    front_val = PIECE_VALUES[first_piece.piece_type]
                    back_val = PIECE_VALUES[second_piece.piece_type]

                    if back_val > front_val:
                        motif = MotifType.PIN
                        desc = (
                            f"{slider.symbol()} on {chess.square_name(slider_sq)} "
                            f"pins {first_piece.symbol()} on {chess.square_name(first_sq)} "
                            f"against {second_piece.symbol()} on {chess.square_name(second_sq)}"
                        )
                        severity = min(1.0, front_val / back_val)
                    elif front_val > back_val or first_piece.piece_type == chess.KING:
                        motif = MotifType.SKEWER
                        desc = (
                            f"{slider.symbol()} on {chess.square_name(slider_sq)} "
                            f"skewers {first_piece.symbol()} on {chess.square_name(first_sq)} "
                            f"winning {second_piece.symbol()} on {chess.square_name(second_sq)}"
                        )
                        severity = min(1.0, back_val / 900)
                    else:
                        continue  # Equal value — not a clear pin or skewer

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

    def _detect_discovered_attacks(
        self, board: chess.Board, move: chess.Move,
        fen_before: str, san: str, uci: str,
    ) -> list[TacticEvent]:
        events: list[TacticEvent] = []
        color = board.turn
        from_sq = move.from_square

        # Snapshot attacks of all friendly sliders BEFORE the move,
        # excluding the piece that is moving (we want pieces revealed by it).
        friendly_sliders = (
            (board.rooks | board.bishops | board.queens) & board.occupied_co[color]
        )
        friendly_sliders &= ~chess.BB_SQUARES[from_sq]

        before_attacks: dict[int, chess.SquareSet] = {}
        for sq in chess.SquareSet(friendly_sliders):
            before_attacks[sq] = board.attacks(sq)

        board.push(move)
        enemy = board.turn  # Opponent's turn now

        for sq in chess.SquareSet(friendly_sliders):
            if board.piece_at(sq) is None:
                continue
            after = board.attacks(sq)
            newly_attacked = after & ~before_attacks.get(sq, chess.SquareSet())

            new_targets = newly_attacked & chess.SquareSet(board.occupied_co[enemy])
            for target_sq in new_targets:
                target = board.piece_at(target_sq)
                if target is None:
                    continue
                slider = board.piece_at(sq)
                if slider is None:
                    continue
                is_check = target.piece_type == chess.KING

                severity = 0.9 if is_check else min(1.0, PIECE_VALUES[target.piece_type] / 900)

                unmasker = board.piece_at(move.to_square)
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
                         "piece": unmasker.symbol() if unmasker else "?",
                         "role": "unmasker"},
                        {"square": chess.square_name(sq),
                         "piece": slider.symbol(), "role": "revealed_attacker"},
                        {"square": chess.square_name(target_sq),
                         "piece": target.symbol(), "role": "target"},
                    ],
                    severity_score=severity,
                    description=(
                        f"Discovered {'check' if is_check else 'attack'}: "
                        f"{slider.symbol()} on {chess.square_name(sq)} "
                        f"now attacks {target.symbol()} on {chess.square_name(target_sq)}"
                    ),
                ))

        board.pop()
        return events

    def _detect_overloaded_defenders(
        self, board: chess.Board, move: chess.Move,
        fen_before: str, san: str, uci: str,
    ) -> list[TacticEvent]:
        events: list[TacticEvent] = []

        board.push(move)
        active = board.turn  # Opponent is now active (the defending side)

        for def_sq in chess.SquareSet(board.occupied_co[active]):
            defender = board.piece_at(def_sq)
            if defender is None:
                continue

            defense_set = board.attacks(def_sq)
            obligations = []

            for guarded_sq in defense_set:
                guarded = board.piece_at(guarded_sq)
                if guarded and guarded.color == active:
                    attackers = board.attackers(not active, guarded_sq)
                    if attackers:
                        all_defenders = board.attackers(active, guarded_sq)
                        all_defenders.discard(def_sq)
                        if not all_defenders:
                            obligations.append({
                                "type": "sole_defender",
                                "square": guarded_sq,
                                "piece": guarded.symbol(),
                                "value": PIECE_VALUES.get(guarded.piece_type, 0),
                            })

            if len(obligations) >= 2:
                total_at_risk = sum(o["value"] for o in obligations)
                severity = min(1.0, total_at_risk / 1200)

                fen_after = board.fen()
                events.append(TacticEvent(
                    motif=MotifType.OVERLOADED_DEFENDER,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_uci=uci,
                    move_san=san,
                    attacker_square=def_sq,
                    target_squares=[o["square"] for o in obligations],
                    involved_pieces=[
                        {"square": chess.square_name(def_sq),
                         "piece": defender.symbol(), "role": "overloaded"},
                        *[{"square": chess.square_name(o["square"]),
                           "piece": o["piece"], "role": "obligation"}
                          for o in obligations],
                    ],
                    severity_score=severity,
                    description=(
                        f"{defender.symbol()} on {chess.square_name(def_sq)} "
                        f"is overloaded defending {len(obligations)} pieces"
                    ),
                ))

        board.pop()
        return events

    def _detect_back_rank(
        self, board: chess.Board, move: chess.Move,
        fen_before: str, san: str, uci: str,
    ) -> list[TacticEvent]:
        events: list[TacticEvent] = []

        board.push(move)
        enemy = board.turn  # Defending side (the one whose king we're checking)
        back_rank = 0 if enemy == chess.WHITE else 7
        escape_rank = 1 if enemy == chess.WHITE else 6

        king_sq = board.king(enemy)
        if king_sq is None or chess.square_rank(king_sq) != back_rank:
            board.pop()
            return events

        king_file = chess.square_file(king_sq)

        # All three escape squares (one rank forward) must be blocked
        all_blocked = True
        for df in [-1, 0, 1]:
            f = king_file + df
            if not (0 <= f <= 7):
                continue
            escape_sq = chess.square(f, escape_rank)
            piece_on_escape = board.piece_at(escape_sq)
            is_blocked = (
                (piece_on_escape is not None and piece_on_escape.color == enemy)
                or board.is_attacked_by(not enemy, escape_sq)
            )
            if not is_blocked:
                all_blocked = False
                break

        if not all_blocked:
            board.pop()
            return events

        heavy = (board.rooks | board.queens) & board.occupied_co[not enemy]
        back_rank_mask = chess.BB_RANK_1 if enemy == chess.WHITE else chess.BB_RANK_8

        for attacker_sq in chess.SquareSet(heavy):
            attacker = board.piece_at(attacker_sq)
            if attacker is None:
                continue
            if int(board.attacks(attacker_sq)) & back_rank_mask:
                fen_after = board.fen()
                events.append(TacticEvent(
                    motif=MotifType.BACK_RANK_WEAKNESS,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    move_uci=uci,
                    move_san=san,
                    attacker_square=attacker_sq,
                    target_squares=[king_sq],
                    involved_pieces=[
                        {"square": chess.square_name(attacker_sq),
                         "piece": attacker.symbol(), "role": "threat"},
                        {"square": chess.square_name(king_sq),
                         "piece": "k" if enemy == chess.BLACK else "K",
                         "role": "target"},
                    ],
                    severity_score=0.85,
                    description=(
                        f"Back-rank weakness: {attacker.symbol()} on "
                        f"{chess.square_name(attacker_sq)} threatens mate"
                    ),
                ))
                break  # One threat is enough to flag

        board.pop()
        return events

    # ------------------------------------------------------------------
    # Engine validation
    # ------------------------------------------------------------------

    def _validate_with_engine(
        self, board: chess.Board, move: chess.Move, events: list[TacticEvent],
    ) -> list[TacticEvent]:
        """Filter events by Stockfish evaluation delta to remove false positives."""
        assert self.engine is not None
        limit = chess.engine.Limit(depth=self.engine_depth)

        info_before = self.engine.analyse(board, limit)
        score_before = info_before["score"].relative.score(mate_score=10000)

        board.push(move)
        info_after = self.engine.analyse(board, limit)
        score_after = info_after["score"].relative.score(mate_score=10000)
        board.pop()

        if score_before is None or score_after is None:
            return events  # Can't validate without scores — keep all

        cp_swing = -score_after - score_before  # positive = good for the mover

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


# ------------------------------------------------------------------
# Full PGN analysis pipeline
# ------------------------------------------------------------------

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

    def _analyze_game(self, game: chess.pgn.Game) -> chess.pgn.Game:
        board = game.board()

        for node in game.mainline():
            move = node.move
            events = self.classifier.classify(board, move)

            for event in events:
                if event.severity_score > 0.8:
                    node.nags.add(chess.pgn.NAG_BRILLIANT_MOVE)
                elif event.severity_score > 0.5:
                    node.nags.add(chess.pgn.NAG_GOOD_MOVE)

                tactic_comment = f"[{event.motif.value}] {event.description}"
                node.comment = f"{node.comment} {tactic_comment}".strip()

                if event.cp_swing is not None:
                    score = chess.engine.PovScore(
                        chess.engine.Cp(event.cp_swing), chess.WHITE
                    )
                    node.set_eval(score)

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
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

    def export_html(self, output_path: str):
        """Generate HTML report with embedded SVG board diagrams."""
        parts = [
            "<!DOCTYPE html><html><head>",
            '<meta charset="utf-8">',
            "<title>Tactical Analysis Report</title>",
            "<style>",
            "body{font-family:system-ui;max-width:1100px;margin:0 auto;padding:24px}",
            ".tactic{border:1px solid #ddd;border-radius:8px;padding:20px;margin:20px 0}",
            ".tactic h3{margin-top:0}",
            ".board-and-info{display:flex;gap:24px;align-items:flex-start}",
            "table{border-collapse:collapse}td{padding:4px 10px}",
            ".high{border-left:4px solid #e74c3c}",
            ".med{border-left:4px solid #e67e22}",
            ".low{border-left:4px solid #3498db}",
            "</style></head><body>",
            "<h1>&#9823; Tactical Analysis Report</h1>",
            f"<p>{len(self.all_events)} tactics detected</p>",
        ]

        for i, event in enumerate(self.all_events):
            board = chess.Board(event.fen_before)
            move = chess.Move.from_uci(event.move_uci)

            arrows = [chess.svg.Arrow(move.from_square, move.to_square, color="blue")]
            for sq in event.target_squares:
                arrows.append(chess.svg.Arrow(event.attacker_square, sq, color="red"))

            svg = chess.svg.board(board, arrows=arrows, size=320,
                                  lastmove=move, coordinates=True)

            sev_class = (
                "high" if event.severity_score > 0.7
                else "med" if event.severity_score > 0.4
                else "low"
            )

            parts.append(f"""
            <div class="tactic {sev_class}">
              <h3>#{i + 1} &mdash; {event.motif.value.replace("_", " ").title()}</h3>
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
            </div>""")

        parts.append("</body></html>")
        with open(output_path, "w") as f:
            f.write("\n".join(parts))

    def close(self):
        self.classifier.close()
