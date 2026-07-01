import json, pathlib, pytest
from unittest.mock import patch
from trade.atp_client import fetch_match_state

EXAMPLE_JSON = pathlib.Path("apt_live_match_example.json")

def _load_example():
    with open(EXAMPLE_JSON) as f:
        return json.load(f)

def test_extracts_player_names():
    data = _load_example()
    with patch("trade.atp_client._get_json", return_value=data):
        state = fetch_match_state("http://fake-url")
    assert state["p1_name"] == "Francisco Cerundolo"
    assert state["p2_name"] == "Tommy Paul"

def test_extracts_best_of():
    data = _load_example()
    with patch("trade.atp_client._get_json", return_value=data):
        state = fetch_match_state("http://fake-url")
    assert state["best_of"] in (3, 5)

def test_extracts_p1_stats_keys():
    data = _load_example()
    with patch("trade.atp_client._get_json", return_value=data):
        state = fetch_match_state("http://fake-url")
    for key in ("first_in", "win_first", "win_second", "return_first", "return_second"):
        assert key in state["p1_stats"]
        assert 0.0 <= state["p1_stats"][key] <= 1.0

def test_returns_none_for_finished_match():
    data = _load_example()
    data["Match"]["MatchStatus"] = "F"
    with patch("trade.atp_client._get_json", return_value=data):
        assert fetch_match_state("http://fake-url") is None

def test_returns_none_on_fetch_error():
    with patch("trade.atp_client._get_json", return_value=None):
        assert fetch_match_state("http://fake-url") is None
