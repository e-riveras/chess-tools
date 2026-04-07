"""Tests for cross-game analysis history module."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
import chess
from chess_tools.analysis.history import (
    load_analysis_history,
    update_analysis_history,
    save_analysis_history,
    format_history_for_prompt,
    clamp_stats_rolling,
    MAX_RECENT_GAMES,
    _default_history,
)
from chess_tools.lib.models import CrucialMoment


def _make_moment(tactic_type="hanging_piece", moment_type="blunder", **kwargs):
    defaults = dict(
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        move_played_san="e4",
        move_played_uci="e2e4",
        best_move_san="d4",
        best_move_uci="d2d4",
        eval_swing=-300,
        eval_after=-200,
        pv_line="d4 d5",
        game_result="0-1",
        hero_color=chess.WHITE,
        tactic_type=tactic_type,
        moment_type=moment_type,
        clamp_tags=[],
    )
    defaults.update(kwargs)
    return CrucialMoment(**defaults)


class TestLoadAnalysisHistory:
    def test_returns_default_when_file_missing(self, tmp_path):
        result = load_analysis_history(str(tmp_path / "nonexistent.json"))
        assert result == _default_history()

    def test_loads_existing_file(self, tmp_path):
        path = tmp_path / "history.json"
        data = {"games": [{"date": "2026.01.01"}], "tactic_counts": {"fork": 2},
                "total_blunders": 3, "total_missed": 1}
        path.write_text(json.dumps(data))
        result = load_analysis_history(str(path))
        assert result["total_blunders"] == 3
        assert len(result["games"]) == 1

    def test_fills_missing_keys(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text('{"games": []}')
        result = load_analysis_history(str(path))
        assert "tactic_counts" in result
        assert "total_blunders" in result

    def test_handles_corrupt_json(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text("not json")
        result = load_analysis_history(str(path))
        assert result == _default_history()


class TestUpdateAnalysisHistory:
    def test_basic_update(self):
        history = _default_history()
        moments = [
            _make_moment("hanging_piece", "blunder"),
            _make_moment("fork", "missed_chance"),
        ]
        metadata = {"Date": "2026.02.26", "White": "me", "Black": "opp", "Result": "0-1"}
        result = update_analysis_history(history, moments, metadata)
        assert result["total_blunders"] == 1
        assert result["total_missed"] == 1
        assert result["tactic_counts"]["hanging_piece"] == 1
        assert result["tactic_counts"]["fork"] == 1
        assert len(result["games"]) == 1
        assert result["games"][0]["blunder_clamps"] == [[]]

    def test_blunder_clamps_preserved(self):
        history = _default_history()
        moments = [
            _make_moment("hanging_piece", "blunder", clamp_tags=["L", "A"]),
        ]
        metadata = {"Date": "2026.02.26", "White": "me", "Black": "opp", "Result": "0-1"}
        result = update_analysis_history(history, moments, metadata)
        assert result["games"][0]["blunder_clamps"] == [["L", "A"]]

    def test_caps_at_max_games(self):
        history = _default_history()
        history["games"] = [{"date": f"game_{i}"} for i in range(MAX_RECENT_GAMES)]
        moments = [_make_moment()]
        metadata = {"Date": "new", "White": "a", "Black": "b", "Result": "*"}
        result = update_analysis_history(history, moments, metadata)
        assert len(result["games"]) == MAX_RECENT_GAMES
        assert result["games"][-1]["date"] == "new"

    def test_accumulates_tactic_counts(self):
        history = _default_history()
        history["tactic_counts"]["fork"] = 3
        moments = [_make_moment("fork", "blunder")]
        metadata = {"Date": "x", "White": "a", "Black": "b", "Result": "*"}
        result = update_analysis_history(history, moments, metadata)
        assert result["tactic_counts"]["fork"] == 4


class TestClampStatsRolling:
    def test_counts_tags_across_blunders(self):
        history = {
            "games": [
                {"blunder_count": 2, "blunder_clamps": [["C"], ["L", "A"]]},
                {"blunder_count": 1, "blunder_clamps": [["P"]]},
            ],
            "tactic_counts": {},
            "total_blunders": 0,
            "total_missed": 0,
        }
        s = clamp_stats_rolling(history, max_games=20)
        assert s["total_blunders"] == 3
        assert s["by_letter"]["C"] == 1
        assert s["by_letter"]["L"] == 1
        assert s["by_letter"]["A"] == 1
        assert s["by_letter"]["P"] == 1
        assert s["total_tag_occurrences"] == 4

    def test_legacy_game_without_blunder_clamps(self):
        history = {
            "games": [
                {"blunder_count": 2, "missed_count": 0},
            ],
            "tactic_counts": {},
            "total_blunders": 0,
            "total_missed": 0,
        }
        s = clamp_stats_rolling(history, max_games=20)
        assert s["total_blunders"] == 2
        assert s["total_tag_occurrences"] == 0


class TestSaveAnalysisHistory:
    def test_creates_directory_and_saves(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "history.json"
        history = {"games": [], "tactic_counts": {}, "total_blunders": 0, "total_missed": 0}
        save_analysis_history(history, str(path))
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded == history


class TestFormatHistoryForPrompt:
    def test_empty_history(self):
        result = format_history_for_prompt(_default_history())
        assert result == ""

    def test_with_games(self):
        history = {
            "games": [
                {"white": "me", "black": "opp", "date": "2026.01.01",
                 "blunder_count": 2, "missed_count": 1, "result": "0-1"},
            ],
            "tactic_counts": {"fork": 5, "hanging_piece": 3},
            "total_blunders": 10,
            "total_missed": 5,
        }
        result = format_history_for_prompt(history)
        assert "CROSS-GAME CONTEXT" in result
        assert "fork (5x)" in result
        assert "Total blunders: 10" in result
        assert "me vs opp" in result
