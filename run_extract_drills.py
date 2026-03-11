#!/usr/bin/env python3
"""
Retroactive blunder drill extraction script.

Fetches all imported Lichess games for the authenticated user, re-analyzes
each one with Stockfish, and populates docs/drills/drills.json with drill
entries (including punished-detection). Already-processed games are skipped.

Usage:
    python run_extract_drills.py [--limit N] [--dry-run]

Options:
    --limit N    Process at most N games (default: all)
    --dry-run    Print what would be processed without running analysis
"""
import argparse
import os
import re
import sys
import logging
import requests
import time

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from chess_tools.lib.utils import setup_logging, check_env_var, get_repo_root
from chess_tools.analysis.engine import ChessAnalyzer
from chess_tools.analysis.drills import load_drills_json, update_drills_json

logger = setup_logging()


def fetch_user_games_pgn(username: str, max_games: int = 200) -> list[tuple[str, str]]:
    """
    Fetch PGNs for all games of a Lichess user, split into individual games.

    Returns:
        List of (pgn_text, lichess_game_id) tuples.
    """
    url = f"https://lichess.org/api/games/user/{username}"
    params = {
        "max": max_games,
        "pgnInJson": False,
        "moves": True,
        "tags": True,
        "clocks": False,
        "evals": False,
        "opening": False,
    }
    headers = {"Accept": "application/x-chess-pgn"}
    token = os.getenv("LICHESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info(f"Fetching up to {max_games} games for {username} from Lichess…")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch games: {e}")
        return []

    # Split the bulk PGN into individual games
    raw = resp.text.strip()
    if not raw:
        logger.warning("No games returned from Lichess")
        return []

    games = []
    # Split on double-newline before [Event
    chunks = re.split(r'\n\n(?=\[Event )', raw)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # Extract Lichess game ID from [Site] header
        site_match = re.search(r'\[Site "https://lichess\.org/([^"]+)"\]', chunk)
        game_id = site_match.group(1) if site_match else "unknown"
        games.append((chunk, game_id))

    logger.info(f"Found {len(games)} games")
    return games


def main():
    parser = argparse.ArgumentParser(description="Extract blunder drills from Lichess games")
    parser.add_argument("--limit", type=int, default=None, help="Max games to process")
    parser.add_argument("--dry-run", action="store_true", help="Print games without running analysis")
    args = parser.parse_args()

    lichess_token = os.getenv("LICHESS_TOKEN")
    if not lichess_token:
        logger.error("LICHESS_TOKEN not set")
        sys.exit(1)

    # Fetch username directly to avoid berserk JSON encoding issues
    try:
        resp = requests.get(
            "https://lichess.org/api/account",
            headers={"Authorization": f"Bearer {lichess_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        username = resp.json().get("username")
    except Exception as e:
        logger.error(f"Could not determine Lichess username: {e}")
        sys.exit(1)

    if not username:
        logger.error("Could not determine Lichess username")
        sys.exit(1)

    drills_path = str(get_repo_root() / "docs" / "drills" / "drills.json")
    existing_drills = load_drills_json(drills_path)
    existing_game_ids = {d.get("game_id") for d in existing_drills if d.get("game_id")}
    logger.info(f"Existing drills: {len(existing_drills)} entries from {len(existing_game_ids)} games")

    max_fetch = (args.limit or 200)
    games = fetch_user_games_pgn(username, max_games=max_fetch)

    if args.limit:
        games = games[:args.limit]

    if args.dry_run:
        for pgn, lid in games:
            date_m = re.search(r'\[Date "([^"]+)"\]', pgn)
            white_m = re.search(r'\[White "([^"]+)"\]', pgn)
            black_m = re.search(r'\[Black "([^"]+)"\]', pgn)
            date = date_m.group(1).replace(".", "-") if date_m else "?"
            white = white_m.group(1) if white_m else "?"
            black = black_m.group(1) if black_m else "?"
            game_id = f"{date}_{white}_vs_{black}"
            status = "SKIP (already in drills)" if game_id in existing_game_ids else "PROCESS"
            print(f"  [{status}] {game_id}")
        return

    stockfish_path = check_env_var("STOCKFISH_PATH")
    if not os.path.exists(stockfish_path):
        logger.error(f"Stockfish not found at: {stockfish_path}")
        sys.exit(1)

    total_added = 0
    with ChessAnalyzer(stockfish_path) as analyzer:
        for i, (pgn, lichess_id) in enumerate(games, 1):
            date_m = re.search(r'\[Date "([^"]+)"\]', pgn)
            white_m = re.search(r'\[White "([^"]+)"\]', pgn)
            black_m = re.search(r'\[Black "([^"]+)"\]', pgn)
            date = date_m.group(1).replace(".", "-") if date_m else "?"
            white = white_m.group(1) if white_m else "?"
            black = black_m.group(1) if black_m else "?"
            game_id_check = f"{date}_{white}_vs_{black}"

            if game_id_check in existing_game_ids:
                logger.info(f"[{i}/{len(games)}] Skip (already processed): {game_id_check}")
                continue

            logger.info(f"[{i}/{len(games)}] Analyzing: {game_id_check}")
            try:
                moments, metadata, _ = analyzer.analyze_game(pgn, hero_username=username)
                blunders = [m for m in moments if m.moment_type == "blunder"]
                logger.info(f"  Found {len(blunders)} blunder(s)")

                # Attach Lichess URL if available
                site = metadata.get("Site", "")
                for m in moments:
                    if site.startswith("https://lichess.org/") and not m.lichess_url:
                        m.lichess_url = site

                added = update_drills_json(drills_path, moments, metadata)
                total_added += added

                # Brief pause to be a good citizen with the engine
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"  Error analyzing {game_id_check}: {e}")
                continue

    logger.info(f"Done. Added {total_added} new drill entries to {drills_path}")


if __name__ == "__main__":
    main()
