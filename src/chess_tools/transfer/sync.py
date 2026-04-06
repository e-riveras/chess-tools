import os
import logging
import time
from datetime import datetime
from urllib.parse import urlparse
from chess_tools.lib.utils import check_env_var, get_output_dir, get_repo_root
from chess_tools.lib.api.lichess import get_lichess_client, get_lichess_username, import_game_to_lichess
from chess_tools.lib.api.chesscom import get_chesscom_archives, get_games_from_archive
from chess_tools.lib.data.history import load_history, save_history
from chess_tools.analysis.engine import ChessAnalyzer
from chess_tools.analysis.report import generate_markdown_report, generate_html_report, regenerate_index_page
from chess_tools.analysis.history import load_analysis_history, update_analysis_history, save_analysis_history

logger = logging.getLogger("chess_transfer")

MAX_IMPORTS_PER_RUN = 100
IMPORT_DELAY_SECONDS = 6
DUPLICATE_DELAY_SECONDS = 1
ANALYZED_TIME_CLASSES = frozenset({"rapid", "blitz"})


def run_sync_pipeline():
    """
    Orchestrates the synchronization of games from Chess.com to Lichess
    and triggers analysis for the latest game.
    """
    lichess_token = check_env_var("LICHESS_TOKEN")
    chesscom_username = os.getenv('CHESSCOM_USERNAME', 'erivera90')
    max_imports = MAX_IMPORTS_PER_RUN

    logger.info("Starting Chess.com to Lichess sync...")
    
    client = get_lichess_client(lichess_token)
    lichess_username = get_lichess_username(client)
    if not lichess_username:
        logger.error("Could not determine Lichess username from token. Aborting.")
        return

    # 1. Load local history
    history = load_history()
    imported_ids = set(history.get("imported_ids", []))
    last_analyzed_id = history.get("last_analyzed_id")

    logger.info(f"Loaded {len(imported_ids)} imported games.")

    # 2. Get Chess.com archives
    archives = get_chesscom_archives(chesscom_username)
    archives.sort(reverse=True) 
    
    actions_count = 0
    lichess_url_map: dict = {}

    # Track the absolute latest game found in the archives
    latest_candidate_game = None

    for archive_url in archives:
        logger.info(f"Checking archive: {archive_url}")
        games = get_games_from_archive(archive_url, chesscom_username)

        # Chess.com API returns games oldest-first within each archive.
        # After reversing, games are processed newest-first.
        # Capture the latest game from the END of the pre-reverse list (= newest game).
        if latest_candidate_game is None:
            for g in reversed(games):
                url_g = g.get('url', '')
                pgn_g = g.get('pgn', '')
                if url_g and pgn_g and g.get('time_class') in ANALYZED_TIME_CLASSES:
                    gid = urlparse(url_g).path.rstrip('/').split('/')[-1]
                    latest_candidate_game = {'id': gid, 'pgn': pgn_g}
                    break

        games.reverse()

        for game in games:
            url = game.get('url')
            end_time = game.get('end_time')
            pgn = game.get('pgn')
            time_class = game.get('time_class')

            if not url or not pgn:
                continue

            game_id = urlparse(url).path.rstrip('/').split('/')[-1]
            
            # --- STEP 1: IMPORT ---
            if game_id not in imported_ids:
                logger.info(f"Found new game {game_id} ended at {datetime.fromtimestamp(end_time)}. Attempting import...")
                import_status, lichess_url = import_game_to_lichess(client, pgn)

                if import_status == "IMPORTED" or import_status == "DUPLICATE":
                    imported_ids.add(game_id)
                    actions_count += 1
                    if import_status == "IMPORTED":
                        if lichess_url:
                            lichess_url_map[game_id] = lichess_url
                        time.sleep(IMPORT_DELAY_SECONDS)
                    else:
                        time.sleep(DUPLICATE_DELAY_SECONDS)
                else:
                    time.sleep(DUPLICATE_DELAY_SECONDS)
                    continue

            # Update history object in memory
            history["imported_ids"] = sorted(list(imported_ids))

            if actions_count >= max_imports:
                logger.info(f"Reached limit of {max_imports} actions for this run. Saving and stopping.")
                save_history(history)
                break
        
        if actions_count >= max_imports:
            break
        
    save_history(history)
    logger.info(f"Sync complete. {actions_count} actions performed.")

    # --- STEP 2: ANALYSIS LOGIC ---
    pgn_to_analyze = None
    id_to_analyze = None

    if latest_candidate_game:
        if latest_candidate_game['id'] != last_analyzed_id:
            logger.info(f"Found recent game {latest_candidate_game['id']} not yet analyzed. Analyzing...")
            pgn_to_analyze = latest_candidate_game['pgn']
            id_to_analyze = latest_candidate_game['id']
        else:
            logger.info(f"Latest game {latest_candidate_game['id']} has already been analyzed.")

    if pgn_to_analyze:
        stockfish_path = os.getenv("STOCKFISH_PATH")

        if not stockfish_path:
            logger.error("Skipping analysis: STOCKFISH_PATH not set.")
        else:
            try:
                with ChessAnalyzer(stockfish_path) as analyzer:
                    logger.info(f"Starting analysis for {chesscom_username}")
                    moments, metadata, move_evals = analyzer.analyze_game(pgn_to_analyze, hero_username=chesscom_username)

                    # Load cross-game history for context
                    history_path = str(get_repo_root() / "docs" / "analysis" / "history.json")
                    analysis_history = load_analysis_history(history_path)

                    output_dir = get_output_dir("analysis")

                    generate_markdown_report(moments, metadata, output_dir=output_dir)

                    html_dir = str(get_repo_root() / "docs" / "analysis")
                    analyzed_lichess_url = lichess_url_map.get(id_to_analyze)
                    generate_html_report(moments, metadata, output_dir=html_dir,
                                         move_evals=move_evals, lichess_url=analyzed_lichess_url)
                    regenerate_index_page(html_dir)

                    # Update cross-game analysis history
                    update_analysis_history(analysis_history, moments, metadata)
                    save_analysis_history(analysis_history, history_path)

                    logger.info("Analysis report generated.")
                    
                    history["last_analyzed_id"] = id_to_analyze
                    save_history(history)
                    
            except Exception as e:
                logger.error(f"Analysis failed: {e}")
    else:
        logger.info("No games to analyze.")
