"""Tests for the blunder drills module."""
import json
import os
import tempfile
import chess

import pytest
from chess_tools.lib.models import CrucialMoment
from chess_tools.analysis.drills import (
    load_drills_json,
    update_drills_json,
    _make_game_id,
)


def _make_moment(
    fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    half_move_number=2,
    tactic_type="hanging_piece",
    severity="critical",
    punished=True,
):
    return CrucialMoment(
        fen=fen,
        move_played_san="e4",
        move_played_uci="e2e4",
        best_move_san="Nf3",
        best_move_uci="g1f3",
        eval_swing=-300,
        eval_after=-320,
        pv_line="Nf3 d5",
        game_result="0-1",
        hero_color=chess.WHITE,
        tactic_type=tactic_type,
        moment_type="blunder",
        severity=severity,
        half_move_number=half_move_number,
        punished=punished,
        opponent_reply_san="Nxe4",
        fen_after="rnbqkbnr/pppppppp/8/8/4p3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        refutation_move_uci="e7e5",
        refutation_line="e7e5",
    )


METADATA = {
    "Date": "2026.03.10",
    "White": "erivera90",
    "Black": "opponent",
    "Result": "0-1",
    "Event": "Test",
    "Site": "https://lichess.org/abc123",
}


class TestMakeGameId:
    def test_basic(self):
        gid = _make_game_id(METADATA)
        assert gid == "2026-03-10_erivera90_vs_opponent"

    def test_strips_special_chars(self):
        meta = {**METADATA, "White": "player!@#", "Black": "opp ent"}
        gid = _make_game_id(meta)
        assert "!" not in gid
        assert " " not in gid

    def test_missing_keys(self):
        gid = _make_game_id({})
        assert "unknown" in gid


class TestLoadDrillsJson:
    def test_missing_file(self):
        result = load_drills_json("/tmp/nonexistent_drills_xyz.json")
        assert result == []

    def test_valid_file(self, tmp_path):
        path = str(tmp_path / "drills.json")
        data = [{"id": "game1_2", "fen": "startpos"}]
        with open(path, "w") as f:
            json.dump(data, f)
        result = load_drills_json(path)
        assert len(result) == 1
        assert result[0]["id"] == "game1_2"

    def test_invalid_json(self, tmp_path):
        path = str(tmp_path / "drills.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        result = load_drills_json(path)
        assert result == []


class TestUpdateDrillsJson:
    def test_adds_blunder_entry(self, tmp_path):
        path = str(tmp_path / "drills.json")
        moment = _make_moment()
        added = update_drills_json(path, [moment], METADATA)
        assert added == 1
        data = load_drills_json(path)
        assert len(data) == 1
        entry = data[0]
        assert entry["fen"] == moment.fen
        assert entry["best_move_uci"] == "g1f3"
        assert entry["punished"] is True
        assert entry["tactic_type"] == "hanging_piece"
        assert entry["fen_after"] is not None
        assert entry["refutation_move_uci"] == "e7e5"

    def test_skips_non_blunders(self, tmp_path):
        path = str(tmp_path / "drills.json")
        moment = _make_moment()
        moment.moment_type = "missed_chance"
        added = update_drills_json(path, [moment], METADATA)
        assert added == 0

    def test_deduplicates(self, tmp_path):
        path = str(tmp_path / "drills.json")
        moment = _make_moment()
        update_drills_json(path, [moment], METADATA)
        added = update_drills_json(path, [moment], METADATA)
        assert added == 0
        data = load_drills_json(path)
        assert len(data) == 1

    def test_multiple_moments(self, tmp_path):
        path = str(tmp_path / "drills.json")
        m1 = _make_moment(half_move_number=2)
        m2 = _make_moment(half_move_number=10)
        added = update_drills_json(path, [m1, m2], METADATA)
        assert added == 2
        data = load_drills_json(path)
        assert len(data) == 2

    def test_appends_to_existing(self, tmp_path):
        path = str(tmp_path / "drills.json")
        m1 = _make_moment(half_move_number=2)
        update_drills_json(path, [m1], METADATA)

        meta2 = {**METADATA, "White": "other", "Black": "player"}
        m2 = _make_moment(half_move_number=4)
        added = update_drills_json(path, [m2], meta2)
        assert added == 1
        data = load_drills_json(path)
        assert len(data) == 2

    def test_to_drill_dict_fields(self, tmp_path):
        path = str(tmp_path / "drills.json")
        moment = _make_moment()
        update_drills_json(path, [moment], METADATA)
        data = load_drills_json(path)
        entry = data[0]

        required_fields = [
            "id", "game_id", "game_date", "fen", "fen_after",
            "hero_color", "move_played_san", "move_played_uci",
            "best_move_san", "best_move_uci", "refutation_move_uci",
            "eval_swing", "moment_type", "severity", "tactic_type",
            "punished", "half_move_number",
        ]
        for field in required_fields:
            assert field in entry, f"Missing field: {field}"

    def test_hero_color_serialized(self, tmp_path):
        path = str(tmp_path / "drills.json")
        moment = _make_moment()
        update_drills_json(path, [moment], METADATA)
        data = load_drills_json(path)
        assert data[0]["hero_color"] == "white"
