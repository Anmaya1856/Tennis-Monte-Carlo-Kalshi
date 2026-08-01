import pytest
from unittest.mock import patch
import trade.config as cfg

def _mock_orderbook(yes_bids, no_bids):
    return {"orderbook_fp": {"yes_dollars": yes_bids, "no_dollars": no_bids}}

def test_best_ask_derived_from_no_bids():
    from trade.kalshi_client import get_best_ask_bid
    ob = _mock_orderbook(yes_bids=[["0.50", "100"]], no_bids=[["0.40", "50"], ["0.38", "30"]])
    with patch("trade.kalshi_client._fetch_orderbook", return_value=ob):
        ask, bid = get_best_ask_bid("FAKE-TICKER")
    assert abs(ask - 0.60) < 1e-9

def test_best_bid_from_yes_bids():
    from trade.kalshi_client import get_best_ask_bid
    ob = _mock_orderbook(yes_bids=[["0.50", "100"], ["0.48", "30"]], no_bids=[["0.40", "50"]])
    with patch("trade.kalshi_client._fetch_orderbook", return_value=ob):
        ask, bid = get_best_ask_bid("FAKE-TICKER")
    assert abs(bid - 0.50) < 1e-9

def test_returns_none_on_empty_book():
    from trade.kalshi_client import get_best_ask_bid
    ob = _mock_orderbook(yes_bids=[], no_bids=[])
    with patch("trade.kalshi_client._fetch_orderbook", return_value=ob):
        ask, bid = get_best_ask_bid("FAKE-TICKER")
    assert ask is None and bid is None

def test_dry_run_place_order_returns_mock():
    original = cfg.DRY_RUN
    cfg.DRY_RUN = True
    try:
        from trade.kalshi_client import place_order
        result = place_order("FAKE-TICKER", count=5, price_cents=55)
        assert result is not None
        # Kalshi taker fee: roundup(0.07 * 5 * 0.55 * 0.45) to a centicent = 0.0867
        assert result["fee_dollars"] == pytest.approx(0.0867)
        assert abs(result["cost_dollars"] - 5 * 0.55) < 1e-9
    finally:
        cfg.DRY_RUN = original

def test_taker_fee_formula():
    from trade.kalshi_client import taker_fee
    assert taker_fee(1, 0.50) == pytest.approx(0.0175)   # max fee point
    assert taker_fee(1, 0.95) == pytest.approx(0.0034)   # 0.003325 rounded up
    assert taker_fee(0, 0.50) == 0.0

def test_dry_run_close_pays_fee():
    original = cfg.DRY_RUN
    cfg.DRY_RUN = True
    try:
        from trade.kalshi_client import close_position
        result = close_position("FAKE-TICKER", count=3, price_cents=40)
        assert result["fee_dollars"] == pytest.approx(0.0504)  # 0.07*3*0.4*0.6 = 0.0504 exactly
    finally:
        cfg.DRY_RUN = original


# ── Milestone ID lookup tests ────────────────────────────────────────────────

def test_fetch_milestone_id_finds_match():
    milestones_resp = {
        "milestones": [
            {"id": "id-other", "details": {"main_game_event_ticker": "EVENT-OTHER"}},
            {"id": "id-target", "details": {"main_game_event_ticker": "EVENT-ABC"}},
        ]
    }
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = milestones_resp
        from trade.kalshi_client import fetch_milestone_id
        assert fetch_milestone_id("EVENT-ABC") == "id-target"


def test_fetch_milestone_id_returns_none_when_not_found():
    milestones_resp = {
        "milestones": [
            {"id": "id-other", "details": {"main_game_event_ticker": "EVENT-OTHER"}},
        ]
    }
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = milestones_resp
        from trade.kalshi_client import fetch_milestone_id
        assert fetch_milestone_id("EVENT-MISSING") is None


# ── Milestone live data tests ─────────────────────────────────────────────────

_MILESTONE_DETAILS = {
    "status": "live",
    "server": "comp-b",
    "competitor1_id": "comp-a",
    "competitor2_id": "comp-b",
    "competitor1_round_scores": [
        {"outcome": "winner", "score": 6},
        {"outcome": "ongoing", "score": 3},
    ],
    "competitor2_round_scores": [
        {"outcome": "loser", "score": 4},
        {"outcome": "ongoing", "score": 5},
    ],
    "competitor1_current_round_score": 30,
    "competitor2_current_round_score": 40,
}

def _milestone_response(details):
    return {"live_data": {"details": details}}


def test_fetch_milestone_returns_none_when_not_live():
    dead = dict(_MILESTONE_DETAILS, status="complete")
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = _milestone_response(dead)
        from trade.kalshi_client import fetch_milestone
        assert fetch_milestone("mid-123") is None


def test_fetch_milestone_returns_details_when_live():
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = _milestone_response(_MILESTONE_DETAILS)
        from trade.kalshi_client import fetch_milestone
        result = fetch_milestone("mid-123")
        assert result is not None
        assert result["server"] == "comp-b"


def test_get_event_competitor_map():
    event_resp = {
        "event": {"event_ticker": "EVENT"},
        "markets": [
            {"custom_strike": {"tennis_competitor": "comp-a"}, "yes_sub_title": "Alice", "ticker": "EVENT-A"},
            {"custom_strike": {"tennis_competitor": "comp-b"}, "yes_sub_title": "Bob",   "ticker": "EVENT-B"},
        ],
    }
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = event_resp
        from trade.kalshi_client import get_event_competitor_map
        result = get_event_competitor_map("EVENT")
    assert result["comp-a"] == {"name": "Alice", "ticker": "EVENT-A"}
    assert result["comp-b"] == {"name": "Bob",   "ticker": "EVENT-B"}


def test_parse_milestone_state_p1_is_competitor1():
    from trade.kalshi_client import parse_milestone_state
    state = parse_milestone_state(_MILESTONE_DETAILS, "comp-a")
    assert state["score_str"] == "6-4 3-5"
    assert state["game_score_str"] == "30-40"
    assert state["p1_serves"] is False
    assert state["is_live"] is True


def test_parse_milestone_state_p1_is_competitor2():
    from trade.kalshi_client import parse_milestone_state
    state = parse_milestone_state(_MILESTONE_DETAILS, "comp-b")
    assert state["score_str"] == "4-6 5-3"
    assert state["game_score_str"] == "40-30"
    assert state["p1_serves"] is True


def test_parse_milestone_state_advantage():
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS,
                   competitor1_current_round_score=40,
                   competitor2_current_round_score=50)
    state = parse_milestone_state(details, "comp-a")
    assert state["game_score_str"] == "40-Ad"


def test_parse_milestone_state_kstats_oriented():
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS,
                   competitor1_statistics={"aces": 5, "double_faults": 2, "forehand_winners": 7,
                                           "breakpoints_won": 1, "total_breakpoints": 4,
                                           "max_points_in_a_row": 6},
                   competitor2_statistics={"aces": 1, "double_faults": 4, "forehand_winners": 3,
                                           "breakpoints_won": 2, "total_breakpoints": 3,
                                           "max_points_in_a_row": 5})
    # p1 = comp-b -> stats must swap
    state = parse_milestone_state(details, "comp-b")
    assert state["p1_kstats"]["aces"] == 1
    assert state["p2_kstats"]["aces"] == 5
    assert state["p2_kstats"]["bp_won"] == 1
    assert state["p1_kstats"]["winners_fh"] == 3

def test_parse_milestone_state_null_statistics():
    # pre-match: keys present but null — must not crash
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS, competitor1_statistics=None, competitor2_statistics=None)
    state = parse_milestone_state(details, "comp-a")
    assert state["p1_last10"] is None
    assert state["p2_last10"] is None

def _serve(fss, sss, fspw, sspw, spw, spl):
    return {"first_serve_successful": fss, "second_serve_successful": sss,
            "first_serve_points_won": fspw, "second_serve_points_won": sspw,
            "service_points_won": spw, "service_points_lost": spl}


def test_parse_milestone_state_serve_stats_oriented():
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS,
                   competitor1_statistics=_serve(9, 6, 6, 3, 9, 6),
                   competitor2_statistics=_serve(20, 5, 15, 4, 19, 3))
    st = parse_milestone_state(details, "comp-b")   # p1 = competitor2
    p1 = st["p1_stats"]
    assert p1["first_in_den"] == 22                 # service_points_won + lost = 19 + 3
    assert p1["win_first_num"] == 15 and p1["win_first_den"] == 20
    assert p1["win_second_num"] == 4 and p1["win_second_den"] == 5
    # engine uses win_first_num + win_second_num as service points won == service_points_won
    assert p1["win_first_num"] + p1["win_second_num"] == 19
    assert st["p2_stats"]["first_in_den"] == 15     # 9 + 6


def test_serve_stats_ready_true_and_false():
    from trade.kalshi_client import parse_milestone_state, serve_stats_ready
    live = parse_milestone_state(
        dict(_MILESTONE_DETAILS,
             competitor1_statistics=_serve(9, 6, 6, 3, 9, 6),
             competitor2_statistics=_serve(9, 6, 6, 3, 9, 6)), "comp-a")
    assert serve_stats_ready(live["p1_stats"]) is True
    nul = parse_milestone_state(
        dict(_MILESTONE_DETAILS, competitor1_statistics=None, competitor2_statistics=None), "comp-a")
    assert serve_stats_ready(nul["p1_stats"]) is False


def test_fetch_best_of():
    resp = {"milestone": {"details": {"best_of": "5"}}}
    with patch("trade.kalshi_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = resp
        from trade.kalshi_client import fetch_best_of
        assert fetch_best_of("mid-123") == 5


def test_parse_milestone_state_tiebreak():
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS)
    details["competitor1_round_scores"] = [
        {"outcome": "winner", "score": 6},
        {"outcome": "ongoing", "score": 6},
    ]
    details["competitor2_round_scores"] = [
        {"outcome": "loser", "score": 4},
        {"outcome": "ongoing", "score": 6},
    ]
    details["competitor1_current_round_score"] = 4
    details["competitor2_current_round_score"] = 3
    state = parse_milestone_state(details, "comp-a")
    assert state["score_str"] == "6-4 6-6"
    assert state["game_score_str"] == "4-3"
