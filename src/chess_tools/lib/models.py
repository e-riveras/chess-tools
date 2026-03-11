from dataclasses import dataclass
from typing import Optional
import chess

@dataclass
class CrucialMoment:
    """
    Represents a significant moment in a chess game where the evaluation changed drastically.

    Attributes:
        fen: The FEN string of the position BEFORE the move.
        move_played_san: Standard Algebraic Notation of the move played (e.g., "Nf3").
        move_played_uci: Universal Chess Interface notation of the move (e.g., "g1f3").
        best_move_san: SAN of the engine's preferred move.
        best_move_uci: UCI of the engine's preferred move.
        eval_swing: The change in evaluation in centipawns (after - before). Negative means loss.
        eval_after: The evaluation of the position after the move (centipawns).
        pv_line: The Principal Variation (best continuation) according to the engine.
        game_result: The result of the game (e.g., "1-0", "0-1", "1/2-1/2").
        hero_color: The color played by the user (chess.WHITE, chess.BLACK, or None).
        explanation: Natural language explanation of the mistake (populated by LLM).
        image_url: URL to a static image of the position.
        svg_content: Raw SVG content with arrows highlighting the mistake and correction.
        tactical_alert: Warning message if the move allows an immediate capture.
    """
    fen: str
    move_played_san: str
    move_played_uci: str
    best_move_san: str
    best_move_uci: str
    eval_swing: int
    eval_after: int
    pv_line: str
    game_result: str
    hero_color: Optional[chess.Color]
    explanation: Optional[str] = None
    image_url: Optional[str] = None
    svg_content: Optional[str] = None
    tactical_alert: Optional[str] = None
    refutation_line: str = ""
    mate_in: Optional[int] = None
    tactic_type: str = "unknown"
    board_description: str = ""
    moment_type: str = "blunder"        # "blunder" | "missed_chance" | "missed_mate"
    severity: str = "minor"             # "critical" | "major" | "minor"
    best_line: str = ""                 # SAN moves of best PV (for missed chances)
    lichess_url: Optional[str] = None
    half_move_number: Optional[int] = None
    punished: Optional[bool] = None           # True if opponent exploited the blunder
    opponent_reply_san: Optional[str] = None  # What opponent actually played after the blunder
    fen_after: Optional[str] = None           # FEN after the blunder move (for "Punish" drill mode)
    refutation_move_uci: Optional[str] = None # First move of refutation PV as UCI

    def to_drill_dict(self, game_id: str, game_date: str) -> dict:
        """Return a JSON-serializable dict for the drills system (excludes svg_content)."""
        return {
            "id": f"{game_id}_{self.half_move_number}",
            "game_id": game_id,
            "game_date": game_date,
            "fen": self.fen,
            "fen_after": self.fen_after,
            "hero_color": "white" if self.hero_color == chess.WHITE else "black",
            "move_played_san": self.move_played_san,
            "move_played_uci": self.move_played_uci,
            "best_move_san": self.best_move_san,
            "best_move_uci": self.best_move_uci,
            "refutation_move_uci": self.refutation_move_uci,
            "eval_swing": self.eval_swing,
            "eval_after": self.eval_after,
            "moment_type": self.moment_type,
            "severity": self.severity,
            "tactic_type": self.tactic_type,
            "explanation": self.explanation,
            "tactical_alert": self.tactical_alert,
            "refutation_line": self.refutation_line,
            "best_line": self.best_line,
            "mate_in": self.mate_in,
            "punished": self.punished,
            "opponent_reply_san": self.opponent_reply_san,
            "lichess_url": self.lichess_url,
            "half_move_number": self.half_move_number,
            "board_description": self.board_description,
            "pv_line": self.pv_line,
        }
