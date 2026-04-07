#!/usr/bin/env python3
"""
Delete existing analysis outputs and regenerate from Chess.com only.

Fetches the N most recent rapid/blitz games for CHESSCOM_USERNAME (default erivera90),
runs Stockfish analysis with the current pipeline (reports, drills, CLAMP history),
and rebuilds docs/analysis/index.html.

Usage:
  export STOCKFISH_PATH=/path/to/stockfish
  export CHESSCOM_USERNAME=yourname   # optional
  python run_rebuild_chesscom_analysis.py [--limit 40]

Does not modify data/history.json (Chess.com import IDs) or touch Lichess.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from chess_tools.lib.utils import setup_logging, check_env_var, get_output_dir, get_repo_root
from chess_tools.lib.api.chesscom import get_chesscom_archives, get_games_from_archive
from chess_tools.analysis.engine import ChessAnalyzer
from chess_tools.analysis.report import (
    generate_markdown_report,
    generate_html_report,
    regenerate_index_page,
)
from chess_tools.analysis.drills import load_drills_json, update_drills_json
from chess_tools.analysis.history import save_analysis_history, load_analysis_history, update_analysis_history

logger = setup_logging()

ANALYZED_TIME_CLASSES = frozenset({"rapid", "blitz"})


def clear_analysis_artifacts(html_dir: str, md_dir: str, history_path: str, drills_path: str) -> None:
    for fpath in glob.glob(os.path.join(html_dir, "*.html")):
        try:
            os.remove(fpath)
        except OSError as e:
            logger.warning("Could not remove %s: %s", fpath, e)

    for fpath in glob.glob(os.path.join(md_dir, "*.md")):
        try:
            os.remove(fpath)
        except OSError as e:
            logger.warning("Could not remove %s: %s", fpath, e)

    save_analysis_history(
        {"games": [], "tactic_counts": {}, "total_blunders": 0, "total_missed": 0},
        history_path,
    )

    os.makedirs(os.path.dirname(drills_path), exist_ok=True)
    with open(drills_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    logger.info("Reset %s and %s", history_path, drills_path)


def collect_recent_games(username: str, limit: int) -> list[tuple[int, str, str]]:
    """
    Return list of (end_time, pgn, game_id) for the `limit` newest rapid/blitz games,
    sorted by end_time ascending (oldest first) for stable history order.
    """
    archives = get_chesscom_archives(username)
    archives.sort(reverse=True)
    raw: list[tuple[int, str, str]] = []

    for archive_url in archives:
        if len(raw) >= limit:
            break
        games = get_games_from_archive(archive_url, username)
        games.reverse()
        for game in games:
            if len(raw) >= limit:
                break
            pgn = game.get("pgn")
            url = game.get("url")
            et = game.get("end_time")
            tc = game.get("time_class")
            if not pgn or not url or et is None or tc not in ANALYZED_TIME_CLASSES:
                continue
            gid = urlparse(url).path.rstrip("/").split("/")[-1]
            raw.append((int(et), pgn, gid))

    raw.sort(key=lambda x: x[0])
    return raw[:limit]


def run_rebuild(limit: int, dry_run: bool) -> None:
    chesscom_username = os.getenv("CHESSCOM_USERNAME", "erivera90")
    repo = get_repo_root()
    html_dir = str(repo / "docs" / "analysis")
    history_path = str(repo / "docs" / "analysis" / "history.json")
    drills_path = str(repo / "docs" / "drills" / "drills.json")
    md_dir = get_output_dir("analysis")

    games = collect_recent_games(chesscom_username, limit)
    logger.info("Collected %d Chess.com games for %s", len(games), chesscom_username)
    if len(games) < limit:
        logger.warning("Only %d games available (requested %d).", len(games), limit)

    if dry_run:
        for i, (et, _pgn, gid) in enumerate(games, 1):
            print(f"  {i}. end_time={et} id={gid}")
        return

    if not games:
        logger.error("No games to analyze.")
        sys.exit(1)

    clear_analysis_artifacts(html_dir, md_dir, history_path, drills_path)

    stockfish_path = check_env_var("STOCKFISH_PATH")
    if not os.path.exists(stockfish_path):
        logger.error("Stockfish not found at %s", stockfish_path)
        sys.exit(1)

    analysis_history = load_analysis_history(history_path)

    with ChessAnalyzer(stockfish_path) as analyzer:
        for i, (_et, pgn, gid) in enumerate(games, 1):
            logger.info("[%d/%d] Analyzing game %s …", i, len(games), gid)
            try:
                moments, metadata, move_evals = analyzer.analyze_game(
                    pgn, hero_username=chesscom_username
                )
            except Exception as e:
                logger.exception("Analysis failed for %s: %s", gid, e)
                continue

            site = metadata.get("Site", "")
            lichess_url = site if site.startswith("https://lichess.org/") else None

            generate_markdown_report(moments, metadata, output_dir=md_dir, history_path=history_path)
            generate_html_report(
                moments,
                metadata,
                output_dir=html_dir,
                move_evals=move_evals,
                lichess_url=lichess_url,
                history_path=history_path,
            )

            update_drills_json(drills_path, moments, metadata)
            update_analysis_history(analysis_history, moments, metadata)
            save_analysis_history(analysis_history, history_path)

    regenerate_index_page(html_dir)
    n_html = len(glob.glob(os.path.join(html_dir, "*.html")))
    n_drills = len(load_drills_json(drills_path))
    logger.info("Done. HTML files in %s: %d (including index). Drill entries: %d", html_dir, n_html, n_drills)


def main():
    parser = argparse.ArgumentParser(description="Rebuild analysis from Chess.com games")
    parser.add_argument("--limit", type=int, default=40, help="Number of most recent games (default 40)")
    parser.add_argument("--dry-run", action="store_true", help="List games only")
    args = parser.parse_args()
    run_rebuild(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
