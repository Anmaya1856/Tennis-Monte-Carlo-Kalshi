import math
import numpy as np
import pytest
import trade.config as cfg
from trade.exact import (game_win_prob, tiebreak_win_prob, win_probs,
                         estimate_win_prob, point_win_prob,
                         implied_point_probs, estimate_win_prob_market, match_report)
from trade.simulation import estimate_win_prob as mc_estimate


def _fixed(fi, wf, ws, rf, rs):
    # no _num/_den keys -> both engines use the raw rates deterministically
    return {"first_in": fi, "win_first": wf, "win_second": ws,
            "return_first": rf, "return_second": rs}

EVEN   = _fixed(0.62, 0.72, 0.51, 0.28, 0.49)
STRONG = _fixed(0.68, 0.80, 0.56, 0.33, 0.55)


def test_game_win_prob_closed_form(monkeypatch):
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.0)   # plain hold, no pressure penalty
    # explicit enumeration at p = 0.65
    p, q = 0.65, 0.35
    deuce = p * p / (1 - 2 * p * q)
    expected = p**4 + 4 * p**4 * q + 10 * p**4 * q**2 + 20 * p**3 * q**3 * deuce
    assert abs(game_win_prob(0.65) - expected) < 1e-12

def test_game_win_prob_boundaries(monkeypatch):
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.0)   # symmetric hold at p=0.5 needs no penalty
    assert game_win_prob(0.5) == pytest.approx(0.5)
    assert game_win_prob(0.7, 4, 2) == 1.0
    assert game_win_prob(0.7, 1, 4) == 0.0
    # advantage server at Ad-40 (4-3)
    assert game_win_prob(0.6, 4, 3) > game_win_prob(0.6, 3, 3) > game_win_prob(0.6, 3, 4)

def test_tiebreak_symmetric():
    assert tiebreak_win_prob(0.6, 0.6, 0, 0, True) == pytest.approx(0.5, abs=1e-9)

def test_deciding_set_equals_match():
    ex = estimate_win_prob(EVEN, STRONG, "6-1 4-6 5-5", "40-40", True, 3, n_draws=1)
    assert ex["set"] == pytest.approx(ex["match"], abs=1e-12)

def test_fresh_even_match_is_half():
    ex = estimate_win_prob(EVEN, EVEN, "0-0", "0-0", True, 3, n_draws=1)
    assert ex["match"] == pytest.approx(0.5, abs=1e-9)

def test_monotone_in_strength():
    weak = estimate_win_prob(EVEN, STRONG, "0-0", "0-0", True, 3, n_draws=1)
    strong = estimate_win_prob(STRONG, EVEN, "0-0", "0-0", True, 3, n_draws=1)
    assert strong["match"] > 0.5 > weak["match"]

def _within_mc(state_args, n_sims=10000):
    np.random.seed(11)
    mc = mc_estimate(*state_args, n_sims=n_sims)
    ex = estimate_win_prob(*state_args, n_draws=1)
    for k in ("match", "set", "game"):
        se = math.sqrt(max(ex[k] * (1 - ex[k]), 1e-6) / n_sims)
        assert abs(ex[k] - mc[k]) <= 5 * se + 1e-9, f"{k}: exact={ex[k]:.4f} mc={mc[k]:.4f}"

def test_oracle_mid_set():
    _within_mc((STRONG, EVEN, "6-4 3-2", "30-15", True, 3))

def test_oracle_tiebreak():
    _within_mc((EVEN, EVEN, "6-6", "3-2", True, 3))

def test_conditionals_law_of_total_probability():
    ex = estimate_win_prob(STRONG, EVEN, "6-4 3-2", "30-15", True, 5, n_draws=1)
    g, c = ex["game"], ex["cond"]
    assert ex["match"] == pytest.approx(g * c["win_game"] + (1 - g) * c["lose_game"], abs=1e-9)
    s = ex["set"]
    assert ex["match"] == pytest.approx(s * c["win_set"] + (1 - s) * c["lose_set"], abs=1e-6)

def test_cond_win_set_is_certain_when_it_clinches():
    # P1 has 2 sets in a Bo5: winning the current set means winning the match, exactly
    ex = estimate_win_prob(EVEN, STRONG, "4-6 7-6 7-6 3-2", "0-15", False, 5, n_draws=1)
    assert ex["cond"]["win_set"] == pytest.approx(1.0, abs=1e-9)

def test_scorelines_sum_to_one_and_are_reachable():
    ex = estimate_win_prob(EVEN, STRONG, "6-1 4-6 5-5", "40-40", True, 3, n_draws=1)
    assert sum(ex["scorelines"].values()) == pytest.approx(1.0, abs=1e-9)
    assert set(ex["scorelines"]) == {(2, 1), (1, 2)}   # one set each: only 2-1 finals exist
    # and the match prob equals P1's scoreline mass
    assert ex["scorelines"][(2, 1)] == pytest.approx(ex["match"], abs=1e-9)

def test_scorelines_fresh_bo5():
    ex = estimate_win_prob(STRONG, EVEN, "0-0", "0-0", True, 5, n_draws=1)
    assert set(ex["scorelines"]) == {(3, 0), (3, 1), (3, 2), (0, 3), (1, 3), (2, 3)}
    assert sum(ex["scorelines"].values()) == pytest.approx(1.0, abs=1e-9)

def test_implied_point_probs_roundtrip():
    # inversion targets the draw-averaged live model at t=0, so the market-blend
    # engine (not the bare point estimate) reproduces the price
    for price, bo in [(0.75, 3), (0.93, 5), (0.30, 3), (0.50, 5)]:
        pA0, pB0 = implied_point_probs(price, bo)
        assert abs((pA0 + pB0) / 2 - 0.64) < 1e-6
        out = estimate_win_prob_market(pA0, pB0, 0, 0, 0, 0, "0-0", "0-0", True, bo, n_draws=4000)
        assert abs(out["match"] - price) < 0.03

def test_implied_point_probs_monotone():
    a = implied_point_probs(0.60, 3)[0]
    b = implied_point_probs(0.80, 3)[0]
    assert b > a  # stronger favorite -> higher server point prob

def test_market_blend_starts_at_prior():
    # zero points played -> blended prob == the pure market prior's 0-0 value
    pA0, pB0 = implied_point_probs(0.75, 3)
    out = estimate_win_prob_market(pA0, pB0, 0, 0, 0, 0, "0-0", "0-0", True, 3, n_draws=4000)
    assert out["match"] == pytest.approx(0.75, abs=0.02)
    assert out["wt_a"] == pytest.approx(1.0)

def test_market_blend_moves_toward_evidence():
    # a heavy underdog (market pA0 low) who dominates his serve in-match should rise
    pA0, pB0 = implied_point_probs(0.30, 3)
    cold = estimate_win_prob_market(pA0, pB0, 0, 0, 0, 0, "0-0", "0-0", True, 3, n_draws=1)["match"]
    hot = estimate_win_prob_market(pA0, pB0, 90, 100, 40, 100, "0-0", "0-0", True, 3,
                                   prior_n=40, n_draws=2000)["match"]
    assert hot > cold
    # and the market weight has dropped well below 1
    w = estimate_win_prob_market(pA0, pB0, 90, 100, 40, 100, "0-0", "0-0", True, 3, prior_n=40)["wt_a"]
    assert w < 0.4

def test_match_report_scorelines_sum_to_one():
    r = match_report(0.66, 0.60, "0-0", "0-0", True, 5, [30.5, 35.5])
    assert sum(r["scorelines"].values()) == pytest.approx(1.0, abs=1e-9)
    # p1's scoreline mass equals point-estimate match win prob, > 0.5 for the favorite
    assert sum(p for (w, _), p in r["scorelines"].items() if w == "p1") > 0.5

def test_match_report_bo3_only_reachable_scores():
    # p1 up a set in Bo3: p1 can finish 2-0 or 2-1, p2 only 2-1 (can't win 2-0 anymore)
    r = match_report(0.63, 0.60, "6-3 2-2", "0-0", True, 3, [])
    keys = {k for k, v in r["scorelines"].items() if v > 1e-9}
    assert keys == {("p1", 0), ("p1", 1), ("p2", 1)}
    # set 1 already won by p1
    assert r["set_win"][0] == (pytest.approx(1.0), pytest.approx(0.0))
    # set 3 only played if it reaches 1-1
    p1_3, p2_3 = r["set_win"][2]
    assert 0 < p1_3 + p2_3 < 1

def test_match_report_target_match_decomposes():
    # with target_match set, p1 scoreline mass == the target (exact decomposition)
    r = match_report(0.70, 0.57, "0-0", "0-0", True, 3, [], target_match=0.845)
    p1_mass = sum(p for (w, _), p in r["scorelines"].items() if w == "p1")
    assert p1_mass == pytest.approx(0.845, abs=0.005)

def test_match_report_over_games_monotone():
    r = match_report(0.64, 0.64, "0-0", "0-0", True, 5, [25.5, 35.5, 45.5])
    o = r["over_games"]
    assert o[25.5] > o[35.5] > o[45.5]  # fewer matches exceed a higher game count
    assert 0 <= o[45.5] and o[25.5] <= 1

def test_match_report_played_set_completed_is_certain():
    r = match_report(0.6, 0.6, "3-6 2-1", "40-15", True, 3, [])
    # set 1 played and lost by p1 (0 win, 1 played-by-p2 mass)
    assert r["set_win"][0] == (pytest.approx(0.0), pytest.approx(1.0))
    # current set 2 certainly played
    p1_2, p2_2 = r["set_win"][1]
    assert p1_2 + p2_2 == pytest.approx(1.0, abs=1e-9)

def test_bp_pressure_lowers_hold(monkeypatch):
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.0)
    base = game_win_prob(0.65)
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.03)
    assert game_win_prob(0.65) < base

def test_bp_pressure_monotone(monkeypatch):
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.02)
    small = game_win_prob(0.65)
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.06)
    big = game_win_prob(0.65)
    assert big < small

def test_bp_pressure_applies_at_break_point_state(monkeypatch):
    # from 30-40 the server holds only by winning the BP (-> deuce), else broken;
    # so hold == p_bp * P(hold | deuce)
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.03)
    p, p_bp = 0.65, 0.62
    assert game_win_prob(p, 2, 3) == pytest.approx(p_bp * game_win_prob(p, 3, 3), abs=1e-12)

def test_bp_pressure_at_advantage_receiver(monkeypatch):
    # 40-Ad (advantage receiver) is a break point -> uses p_bp against the deuce value
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.03)
    p, p_bp = 0.65, 0.62
    assert game_win_prob(p, 3, 4) == pytest.approx(p_bp * game_win_prob(p, 3, 3), abs=1e-12)

def test_bp_pressure_not_applied_at_advantage_server(monkeypatch):
    # 40-Ad the other way (advantage server) is NOT a break point: win-point path uses full p
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.03)
    p = 0.65
    assert game_win_prob(p, 4, 3) == pytest.approx(p + (1 - p) * game_win_prob(p, 3, 3), abs=1e-12)

def test_bp_pressure_clip_no_negative(monkeypatch):
    # penalty larger than the point prob clips to 0, stays a valid probability
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.10)
    h = game_win_prob(0.05)
    assert 0.0 <= float(h) <= 1.0

def test_bp_pressure_keeps_even_match_symmetric(monkeypatch):
    monkeypatch.setattr(cfg, "BP_PRESSURE", 0.03)
    ex = estimate_win_prob(EVEN, EVEN, "0-0", "0-0", True, 3, n_draws=1)
    assert ex["match"] == pytest.approx(0.5, abs=1e-9)

def test_vol_present_and_nonnegative():
    ex = estimate_win_prob(STRONG, EVEN, "6-4 3-2", "30-15", True, 3, n_draws=1)
    assert set(ex["vol"]) == {"point", "game"}
    assert ex["vol"]["point"] >= 0.0
    assert ex["vol"]["game"] >= 0.0
    assert math.isfinite(ex["vol"]["point"]) and math.isfinite(ex["vol"]["game"])

def test_vol_game_matches_cond_decomposition():
    # vol_game == sqrt(g(1-g)) * |cond.win_game - cond.lose_game| from the same output
    ex = estimate_win_prob(STRONG, EVEN, "6-4 3-2", "30-15", True, 3, n_draws=1)
    g, c = ex["game"], ex["cond"]
    expected = math.sqrt(g * (1 - g)) * abs(c["win_game"] - c["lose_game"])
    assert ex["vol"]["game"] == pytest.approx(expected, abs=1e-9)

def test_vol_point_higher_at_pivotal_state():
    # a tiebreak at 6-6 (set point both ways) swings the match far more per point
    # than the very first point of an even match
    pivotal = estimate_win_prob(EVEN, EVEN, "6-6", "6-6", True, 3, n_draws=1)["vol"]["point"]
    calm = estimate_win_prob(EVEN, EVEN, "0-0", "0-0", True, 3, n_draws=1)["vol"]["point"]
    assert pivotal > calm

def test_vol_game_at_least_vol_point():
    # a game bundles several points of movement, so its jump variance dominates one point's
    ex = estimate_win_prob(STRONG, EVEN, "6-4 3-2", "30-15", True, 3, n_draws=1)
    assert ex["vol"]["game"] >= ex["vol"]["point"] - 1e-9

def test_vol_smaller_when_match_nearly_decided():
    # P1 down two sets in a Bo5 early in set 3: match is nearly settled, so a single
    # point barely moves the match prob compared with an even fresh match
    lopsided = estimate_win_prob(EVEN, STRONG, "3-6 4-6 2-3", "15-30", True, 5, n_draws=1)["vol"]["point"]
    even = estimate_win_prob(EVEN, EVEN, "0-0", "0-0", True, 5, n_draws=1)["vol"]["point"]
    assert lopsided < even

def test_draws_average_beta_uncertainty():
    stats = dict(EVEN)
    for k in list(EVEN):
        stats[k + "_num"] = EVEN[k] * 20
        stats[k + "_den"] = 20
    np.random.seed(3)
    ex = estimate_win_prob(stats, stats, "0-0", "0-0", True, 3, n_draws=400)
    assert ex["match"] == pytest.approx(0.5, abs=0.02)  # symmetric by construction
