"""Drill JSON management for the blunder drills feature."""
import json
import os
import logging
import re
from typing import Dict, List, Any, Optional
from chess_tools.lib.models import CrucialMoment

logger = logging.getLogger("chess_transfer")


def _make_game_id(metadata: Dict[str, str]) -> str:
    """Generate a stable game ID matching the report filename convention."""
    date = metadata.get("Date", "unknown").replace(".", "-")
    white = "".join(c for c in metadata.get("White", "?") if c.isalnum() or c in (" ", "_")).replace(" ", "_")
    black = "".join(c for c in metadata.get("Black", "?") if c.isalnum() or c in (" ", "_")).replace(" ", "_")
    return f"{date}_{white}_vs_{black}"


def load_drills_json(path: str) -> List[Dict[str, Any]]:
    """Load drill entries from JSON file, or return empty list if missing/invalid."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load drills JSON: {e}")
    return []


def update_drills_json(path: str, moments: List[CrucialMoment], metadata: Dict[str, str]) -> int:
    """
    Append new drill entries from moments to the cumulative drills JSON file.
    Deduplicates by drill id. Only blunders are included (not missed_chance/missed_mate).

    Args:
        path: Path to drills.json file.
        moments: CrucialMoment objects from analyze_game.
        metadata: Game metadata dict.

    Returns:
        Number of new drills added.
    """
    game_id = _make_game_id(metadata)
    game_date = metadata.get("Date", "?")

    existing = load_drills_json(path)
    existing_ids = {entry["id"] for entry in existing if "id" in entry}

    new_entries = []
    for moment in moments:
        if moment.moment_type != "blunder":
            continue
        entry = moment.to_drill_dict(game_id, game_date)
        if entry["id"] not in existing_ids:
            new_entries.append(entry)
            existing_ids.add(entry["id"])

    if new_entries:
        all_entries = existing + new_entries
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(all_entries, f, indent=2)
        logger.info(f"Drills JSON updated: {len(new_entries)} new entries added ({path})")
    else:
        logger.info("Drills JSON: no new blunder entries to add")

    return len(new_entries)
