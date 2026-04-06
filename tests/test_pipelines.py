import unittest
from unittest.mock import MagicMock, patch, Mock, mock_open
import os
from chess_tools.lib.api.lichess import get_lichess_client, import_game_to_lichess
from chess_tools.lib.api.chesscom import get_chesscom_archives, get_games_from_archive
from chess_tools.lib.data.history import load_history, save_history
from chess_tools.transfer.sync import run_sync_pipeline
import berserk
import requests

class TestSyncGames(unittest.TestCase):

    @patch('chess_tools.lib.api.lichess.berserk.Client')
    @patch('chess_tools.lib.api.lichess.berserk.TokenSession')
    def test_get_lichess_client(self, mock_session, mock_client):
        client = get_lichess_client('fake_token')
        mock_session.assert_called_with('fake_token')
        mock_client.assert_called_once()

    @patch('chess_tools.lib.api.chesscom.requests.get')
    def test_get_chesscom_archives_success(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {'archives': ['url1', 'url2']}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        archives = get_chesscom_archives('testuser')
        self.assertEqual(archives, ['url1', 'url2'])

    @patch('chess_tools.lib.api.chesscom.requests.get')
    def test_get_chesscom_archives_failure(self, mock_get):
        mock_get.side_effect = requests.RequestException("Error")
        
        archives = get_chesscom_archives('testuser')
        self.assertEqual(archives, [])

    @patch('chess_tools.lib.api.chesscom.requests.get')
    def test_get_games_from_archive_success(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {'games': [{'pgn': '1. e4'}]}
        mock_get.return_value = mock_response

        games = get_games_from_archive('url1')
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]['pgn'], '1. e4')

    def test_import_game_to_lichess_success(self):
        mock_client = MagicMock()
        mock_client.games.import_game.return_value = {'url': 'http://lichess.org/game1'}

        status, url = import_game_to_lichess(mock_client, 'pgn_data')
        self.assertEqual(status, "IMPORTED")
        self.assertEqual(url, "http://lichess.org/game1")

    def test_import_game_to_lichess_duplicate(self):
        mock_client = MagicMock()

        class MockResponseError(berserk.exceptions.ResponseError):
            def __init__(self):
                pass
            def __str__(self):
                return "Game already imported"

        mock_client.games.import_game.side_effect = MockResponseError()

        status, url = import_game_to_lichess(mock_client, 'pgn_data')
        self.assertEqual(status, "DUPLICATE")
        self.assertIsNone(url)

    def test_import_game_to_lichess_rate_limit(self):
        mock_client = MagicMock()
        
        class MockResponseError(berserk.exceptions.ResponseError):
            def __init__(self):
                pass
            @property
            def status_code(self):
                return 429
            def __str__(self):
                return "Too Many Requests"
        
        mock_429 = MockResponseError()
        
        mock_client.games.import_game.side_effect = [
            mock_429,
            {'url': 'http://lichess.org/retry_success'}
        ]

        # Patch sleep in chess_tools.lib.api.lichess where import_game_to_lichess resides
        with patch('chess_tools.lib.api.lichess.time.sleep') as mock_sleep:
            status, url = import_game_to_lichess(mock_client, 'pgn_data')
            self.assertEqual(status, "IMPORTED")
            self.assertEqual(url, "http://lichess.org/retry_success")
            mock_sleep.assert_called_with(60)
            self.assertEqual(mock_client.games.import_game.call_count, 2)

    # Patch get_history_file_path to avoid path issues during test
    @patch('chess_tools.lib.data.history.get_history_file_path', return_value='data/history.json')
    def test_load_history_exists(self, mock_path):
        with patch("builtins.open", mock_open(read_data='{"imported_ids": ["123"]}' )):
            with patch("os.path.exists", return_value=True):
                history = load_history()
                self.assertEqual(history, {"imported_ids": ["123"], "last_analyzed_id": None})

    @patch('chess_tools.lib.data.history.get_history_file_path', return_value='data/history.json')
    def test_load_history_not_exists(self, mock_path):
        with patch("os.path.exists", return_value=False):
            history = load_history()
            self.assertEqual(history, {"imported_ids": [], "last_analyzed_id": None})

    @patch('chess_tools.lib.data.history.get_history_file_path', return_value='data/history.json')
    def test_save_history(self, mock_path):
        m_open = mock_open()
        with patch("builtins.open", m_open):
            save_history({"imported_ids": ["123"]})
            m_open.assert_called_with('data/history.json', 'w')

    # Test the Pipeline (Sync)
    # We patch modules where they are IMPORTED in chess_tools.transfer.sync
    @patch('chess_tools.transfer.sync.save_analysis_history')
    @patch('chess_tools.transfer.sync.update_analysis_history')
    @patch('chess_tools.transfer.sync.load_analysis_history', return_value={"games": [], "tactic_counts": {}, "total_blunders": 0, "total_missed": 0})
    @patch('chess_tools.transfer.sync.generate_markdown_report')
    @patch('chess_tools.transfer.sync.ChessAnalyzer')
    @patch('chess_tools.transfer.sync.time.sleep')
    @patch('chess_tools.transfer.sync.import_game_to_lichess')
    @patch('chess_tools.transfer.sync.get_games_from_archive')
    @patch('chess_tools.transfer.sync.get_chesscom_archives')
    @patch('chess_tools.transfer.sync.load_history')
    @patch('chess_tools.transfer.sync.save_history')
    @patch('chess_tools.transfer.sync.get_lichess_client')
    def test_sync_pipeline(self, mock_get_client, mock_save_hist, mock_load_hist, mock_get_archives, mock_get_games, mock_import, mock_sleep, mock_analyzer, mock_report, mock_load_analysis, mock_update_analysis, mock_save_analysis):

        mock_load_hist.return_value = {"imported_ids": ["old_game_id"], "last_analyzed_id": None}

        # Mock Environment Variables
        with patch('os.getenv') as mock_getenv:
            def side_effect(key, default=None):
                if key == 'STOCKFISH_PATH': return '/usr/games/stockfish'
                if key == 'LICHESS_TOKEN': return 'fake_token'
                if key == 'CHESSCOM_USERNAME': return 'testuser'
                return default
            mock_getenv.side_effect = side_effect

            mock_get_archives.return_value = ['archive_url']

            mock_get_games.return_value = [
                {
                    'url': 'https://chess.com/game/live/old_game_id',
                    'end_time': 1000,
                    'pgn': 'pgn1',
                    'time_class': 'blitz'
                },
                {
                    'url': 'https://chess.com/game/live/new_game_id',
                    'end_time': 1738368000,
                    'pgn': '1. e4 e5 ... 20. h3',
                    'time_class': 'rapid',
                    'white': {'username': 'me'},
                    'black': {'username': 'you'}
                }
            ]

            mock_import.return_value = ("IMPORTED", "http://lichess.org/game1")

            mock_analyzer_instance = mock_analyzer.return_value
            mock_analyzer_instance.__enter__.return_value = mock_analyzer_instance
            # Return empty moments list for simplicity in test
            mock_analyzer_instance.analyze_game.return_value = ([], {"White": "me", "Black": "you", "Date": "2025.01.01", "Event": "?", "Site": "?"}, [])

            run_sync_pipeline()

            # Verify core interactions
            mock_load_hist.assert_called_once()
            mock_import.assert_called_once()
            mock_save_hist.assert_called()

            # Verify Analysis was triggered
            mock_analyzer_instance.analyze_game.assert_called()
            mock_report.assert_called()

if __name__ == '__main__':
    unittest.main()
