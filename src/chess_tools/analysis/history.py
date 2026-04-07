"""Cross-game analysis history for tracking recurring patterns."""
import json
import os
import logging
from typing import Dict, List, Any, Optional
from chess_tools.lib.models import CrucialMoment
from chess_tools.analysis.clamp import CLAMP_ORDER

logger = logging.getLogger("chess_transfer")

MAX_RECENT_GAMES = 20


def _default_history() -> dict:
    return {
        "games": [],
        "tactic_counts": {},
        "total_blunders": 0,
        "total_missed": 0,
    }


def clamp_stats_rolling(history: dict, max_games: int = MAX_RECENT_GAMES) -> Dict[str, Any]:
    """
    Aggregate CLAMP tag counts across blunders in the last `max_games` stored games.

    Each tag on a blunder increments that letter once (a blunder can contribute to
    multiple letters). Games before ``blunder_clamps`` was added are treated as
    empty tags per blunder (same length as ``blunder_count``).
    """
    games = history.get("games", [])
    window = games[-max_games:] if len(games) > max_games else list(games)
    by_letter: Dict[str, int] = {letter: 0 for letter in CLAMP_ORDER}
    total_blunders = 0
    blunders_with_any_tag = 0

    for g in window:
        bc = g.get("blunder_clamps")
        if bc is None:
            bc = [[] for _ in range(int(g.get("blunder_count", 0) or 0))]
        for tags in bc:
            total_blunders += 1
            if tags:
                blunders_with_any_tag += 1
            for t in tags:
                if t in by_letter:
                    by_letter[t] += 1

    total_tag_occurrences = sum(by_letter.values())
    return {
        "window_games": len(window),
        "max_games": max_games,
        "total_blunders": total_blunders,
        "blunders_with_any_clamp_tag": blunders_with_any_tag,
        "by_letter": by_letter,
        "total_tag_occurrences": total_tag_occurrences,
    }


def load_analysis_history(path: str) -> dict:
    """Load analysis history from JSON file, or return default if missing."""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Ensure all keys exist
            for key, default in _default_history().items():
                data.setdefault(key, default)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load analysis history: {e}")
    return _default_history()


def update_analysis_history(history: dict, moments: List[CrucialMoment],
                            metadata: Dict[str, str]) -> dict:
    """
    Update history with data from a new game analysis.

    Keeps at most MAX_RECENT_GAMES entries in the games list.
    """
    blunders = [m for m in moments if m.moment_type == "blunder"]
    missed = [m for m in moments if m.moment_type in ("missed_chance", "missed_mate")]

    game_entry = {
        "date": metadata.get("Date", "?"),
        "white": metadata.get("White", "?"),
        "black": metadata.get("Black", "?"),
        "result": metadata.get("Result", "*"),
        "blunder_count": len(blunders),
        "missed_count": len(missed),
        "tactics": [],
        "blunder_clamps": [list(m.clamp_tags) for m in blunders],
    }

    for m in moments:
        game_entry["tactics"].append(m.tactic_type)
        history["tactic_counts"][m.tactic_type] = history["tactic_counts"].get(m.tactic_type, 0) + 1

    history["total_blunders"] += len(blunders)
    history["total_missed"] += len(missed)
    history["games"].append(game_entry)

    # Cap at MAX_RECENT_GAMES
    if len(history["games"]) > MAX_RECENT_GAMES:
        history["games"] = history["games"][-MAX_RECENT_GAMES:]

    return history


def save_analysis_history(history: dict, path: str):
    """Save analysis history to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(history, f, indent=2)
    logger.info(f"Analysis history saved: {path}")


def format_history_for_prompt(history: dict) -> str:
    """
    Format analysis history as context for the LLM summarize_game prompt.

    Returns empty string if no prior games exist.
    """
    games = history.get("games", [])
    if not games:
        return ""

    lines = ["CROSS-GAME CONTEXT (from previous analyses):"]
    lines.append(f"Games analyzed so far: {len(games)}")
    lines.append(f"Total blunders: {history.get('total_blunders', 0)}")
    lines.append(f"Total missed opportunities: {history.get('total_missed', 0)}")

    # Top recurring tactics
    tactic_counts = history.get("tactic_counts", {})
    if tactic_counts:
        sorted_tactics = sorted(tactic_counts.items(), key=lambda x: x[1], reverse=True)
        top = sorted_tactics[:5]
        lines.append("Most common tactic types: " + ", ".join(f"{t} ({c}x)" for t, c in top))

    # Recent game summaries
    recent = games[-3:]
    if recent:
        lines.append("\nRecent games:")
        for g in recent:
            lines.append(
                f"  - {g.get('white', '?')} vs {g.get('black', '?')} ({g.get('date', '?')}): "
                f"{g.get('blunder_count', 0)} blunders, {g.get('missed_count', 0)} missed, "
                f"result {g.get('result', '?')}"
            )

    return "\n".join(lines)
