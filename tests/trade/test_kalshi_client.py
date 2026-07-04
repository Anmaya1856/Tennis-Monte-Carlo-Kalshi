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
        assert result["fee_dollars"] == 0.0
        assert abs(result["cost_dollars"] - 5 * 0.55) < 1e-9
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


def test_parse_milestone_state_null_statistics():
    # pre-match: keys present but null — must not crash
    from trade.kalshi_client import parse_milestone_state
    details = dict(_MILESTONE_DETAILS, competitor1_statistics=None, competitor2_statistics=None)
    state = parse_milestone_state(details, "comp-a")
    assert state["p1_last10"] is None
    assert state["p2_last10"] is None

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
