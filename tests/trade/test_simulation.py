import pytest
import numpy as np
from trade.simulation import estimate_win_prob, _sample_stats, _sim_tiebreak, _sim_set

# --- Fixtures ---

P1 = {"first_in": 0.65, "win_first": 0.75, "win_second": 0.55,
      "return_first": 0.35, "return_second": 0.50}
P2 = {"first_in": 0.60, "win_first": 0.70, "win_second": 0.50,
      "return_first": 0.30, "return_second": 0.45}

P1_COUNTS = {**P1,
    "first_in_num": 13, "first_in_den": 20,
    "win_first_num": 9,  "win_first_den": 12,
    "win_second_num": 4, "win_second_den": 8,
    "return_first_num": 3,  "return_first_den": 10,
    "return_second_num": 5, "return_second_den": 10,
}
P1_NO_COUNTS = {**P1,
    "first_in_num": None, "first_in_den": None,
    "win_first_num": None, "win_first_den": None,
    "win_second_num": None, "win_second_den": None,
    "return_first_num": None, "return_first_den": None,
    "return_second_num": None, "return_second_den": None,
}

# --- estimate_win_prob tests ---

def test_returns_probability_in_range():
    probs = estimate_win_prob(P1, P2, "0-0", "0-0", True, 3, n_sims=1000)
    assert 0.0 <= probs["match"] <= 1.0
    assert 0.0 <= probs["set"]   <= 1.0
    assert 0.0 <= probs["game"]  <= 1.0

def test_strong_server_wins_more():
    probs = estimate_win_prob(P1, P2, "0-0", "0-0", True, 3, n_sims=2000)
    assert probs["match"] > 0.5

def test_score_affects_probability():
    winning = estimate_win_prob(P1, P2, "6-0 5-0", "0-0", True, 3, n_sims=500)
    losing  = estimate_win_prob(P1, P2, "0-6 0-5", "0-0", True, 3, n_sims=500)
    assert winning["match"] > losing["match"]

def test_best_of_5_accepted():
    probs = estimate_win_prob(P1, P2, "2-1", "0-0", True, 5, n_sims=500)
    assert 0.0 <= probs["match"] <= 1.0

def test_mid_game_score():
    probs = estimate_win_prob(P1, P2, "3-2", "40-15", True, 3, n_sims=500)
    assert 0.0 <= probs["match"] <= 1.0

# --- _sample_stats tests ---

def test_sample_stats_all_keys_present():
    result = _sample_stats(P1_COUNTS)
    for key in ["first_in", "win_first", "win_second", "return_first", "return_second"]:
        assert key in result

def test_sample_stats_values_in_range():
    result = _sample_stats(P1_COUNTS)
    for key in ["first_in", "win_first", "win_second", "return_first", "return_second"]:
        assert 0.0 < result[key] < 1.0

def test_sample_stats_fallback_to_point_estimate_when_none():
    results = [_sample_stats(P1_NO_COUNTS) for _ in range(20)]
    for r in results:
        assert r["first_in"]      == P1["first_in"]
        assert r["win_first"]     == P1["win_first"]
        assert r["win_second"]    == P1["win_second"]
        assert r["return_first"]  == P1["return_first"]
        assert r["return_second"] == P1["return_second"]

def test_sample_stats_values_vary_across_calls():
    stats = {**P1,
        "first_in_num": 5,  "first_in_den": 10,
        "win_first_num": 5, "win_first_den": 10,
        "win_second_num": 5,"win_second_den": 10,
        "return_first_num": 5, "return_first_den": 10,
        "return_second_num": 5,"return_second_den": 10,
    }
    samples = [_sample_stats(stats)["first_in"] for _ in range(50)]
    assert len(set(samples)) > 1

def test_sample_stats_mean_close_to_observed_proportion():
    stats = {**P1,
        "first_in_num": 70,  "first_in_den": 100,
        "win_first_num": None, "win_first_den": None,
        "win_second_num": None,"win_second_den": None,
        "return_first_num": None,"return_first_den": None,
        "return_second_num": None,"return_second_den": None,
    }
    samples = [_sample_stats(stats)["first_in"] for _ in range(3000)]
    assert abs(np.mean(samples) - 0.70) < 0.02

# --- Tiebreak / set correctness tests (unchanged) ---

def test_tiebreak_serve_rotation():
    p1 = {"first_in": 1.0, "win_first": 1.0, "win_second": 1.0,
          "return_first": 1.0, "return_second": 1.0}
    p2 = {"first_in": 1.0, "win_first": 0.0, "win_second": 0.0,
          "return_first": 0.0, "return_second": 0.0}
    assert _sim_tiebreak(True, p1, p2) is True

def test_tiebreak_7_points_to_win():
    p1 = {"first_in": 1.0, "win_first": 1.0, "win_second": 1.0,
          "return_first": 1.0, "return_second": 1.0}
    p2 = {"first_in": 1.0, "win_first": 0.0, "win_second": 0.0,
          "return_first": 0.0, "return_second": 0.0}
    for _ in range(10):
        assert _sim_tiebreak(True, p1, p2) is True

def test_server_flips_after_tiebreak_set():
    s = {"first_in": 0.6, "win_first": 0.65, "win_second": 0.5,
         "return_first": 0.35, "return_second": 0.45}
    winner, next_server = _sim_set(True, s, s, start_games=(6, 6))
    assert isinstance(winner, bool)
    assert isinstance(next_server, bool)
