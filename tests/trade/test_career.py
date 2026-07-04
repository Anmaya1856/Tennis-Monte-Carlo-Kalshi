import pytest
import trade.config as cfg
from trade.career import blend, lookup, NEUTRAL


def _match_stats(num, den):
    d = {}
    for k in NEUTRAL:
        d[k + "_num"] = num
        d[k + "_den"] = den
        d[k] = num / den
    return d


def test_blend_pulls_toward_career():
    # hot 12/14 (86%) with career 0.72 at PRIOR_N=40 -> (12 + 28.8) / 54 = 75.6%
    stats = _match_stats(12, 14)
    career = {k: 0.72 for k in NEUTRAL}
    out = blend(stats, career, prior_n=40)
    assert abs(out["win_first"] - (12 + 0.72 * 40) / (14 + 40)) < 1e-9
    assert 0.72 < out["win_first"] < 12 / 14

def test_blend_weight_fades_with_sample():
    career = {k: 0.50 for k in NEUTRAL}
    small = blend(_match_stats(8, 10), career, prior_n=40)    # 80% on 10 pts
    large = blend(_match_stats(80, 100), career, prior_n=40)  # 80% on 100 pts
    assert small["win_first"] < large["win_first"]  # small sample pulled harder

def test_blend_zero_prior_is_identity():
    stats = _match_stats(7, 9)
    out = blend(stats, {k: 0.5 for k in NEUTRAL}, prior_n=0)
    assert out["win_first"] == stats["win_first"]

def test_lookup_unknown_player_falls_back_to_neutral():
    assert lookup("Zxq Nonexistent Player", "Grass") == NEUTRAL

def test_lookup_known_player_differs_from_neutral():
    c = lookup("Novak Djokovic", "Grass")
    assert c != NEUTRAL
    assert all(0.02 < v < 0.98 for v in c.values())
