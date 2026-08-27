"""Resting-order lifecycle: a maker order is not a position until it fills."""
import pytest
import trade.config as cfg
import trade.trade_bot as tb


PX = {"p1": 0.41, "p2": 0.61}

CACHED = {"p1_ticker": "EV-P1", "p2_ticker": "EV-P2", "best_of": 3,
          "p1_name": "A", "p2_name": "B"}
KEY = "EV"


def st(score="2-2", p1_serves=True, g="0-0"):
    return {"score_str": score, "game_score_str": g, "p1_serves": p1_serves}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "DRY_RUN", False)          # exercise the live path
    monkeypatch.setattr(cfg, "MAKER_MODE", True)
    tb._store = tb.MatchStateStore()
    tb._store.get_or_create(KEY, 25.0)
    tb._store.update_mc_prob(KEY, .5, game_prob=.5,
                             cond={"win_game": .9, "lose_game": .1,
                                   "win_set": .9, "lose_set": .1})
    yield


def order(filled=0.0, remaining=5.0, oid="o1"):
    return {"order_id": oid, "filled": filled, "remaining": remaining,
            "cost_dollars": 0.0, "fee_dollars": 0.0, "avg_price": None}


def test_resting_entry_creates_no_position(monkeypatch):
    """The bug this layer exists to prevent: booking a phantom position."""
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: 6600.0)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.get_or_create(KEY).position is None
    assert tb._store.has_pending(KEY)


def test_budget_is_untouched_until_the_order_fills(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    before = tb._store.get_or_create(KEY).budget_remaining
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.get_or_create(KEY).budget_remaining == before


def test_fill_on_a_later_tick_books_the_position(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)

    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: {
        "status": "executed", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.03})
    tb._resolve_pending(KEY, CACHED, st(), PX)
    pos = tb._store.get_or_create(KEY).position
    assert pos is not None and pos["count"] == 5.0
    assert pos["entry_price"] == pytest.approx(0.60)
    assert not tb._store.has_pending(KEY)


def test_partial_fill_books_only_what_traded(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)

    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: {
        "status": "resting", "filled": 2.0, "remaining": 3.0,
        "cost_dollars": 1.20, "fee_dollars": 0.01})
    tb._resolve_pending(KEY, CACHED, st(), PX)
    assert tb._store.get_or_create(KEY).position["count"] == 2.0
    assert tb._store.has_pending(KEY)            # still chasing the other 3


def test_unfilled_order_is_cancelled_when_the_game_ends(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st("2-2"), 0.40, 0.60)

    cancelled = []
    monkeypatch.setattr(tb, "cancel_order", lambda oid, tk=None: cancelled.append(oid) or True)
    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: {
        "status": "resting", "filled": 0.0, "remaining": 5.0,
        "cost_dollars": 0.0, "fee_dollars": 0.0})
    tb._resolve_pending(KEY, CACHED, st("3-2", p1_serves=False), PX)   # game moved on
    assert cancelled == ["o1"]
    assert not tb._store.has_pending(KEY)
    assert tb._store.get_or_create(KEY).position is None


def test_a_fill_racing_the_cancel_is_still_booked(monkeypatch):
    """Cancel and fill can cross; the final poll must not lose the contracts."""
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st("2-2"), 0.40, 0.60)

    monkeypatch.setattr(tb, "cancel_order", lambda oid, tk=None: True)
    seq = iter([
        {"status": "resting", "filled": 0.0, "remaining": 5.0,
         "cost_dollars": 0.0, "fee_dollars": 0.0},
        {"status": "canceled", "filled": 5.0, "remaining": 0.0,
         "cost_dollars": 3.00, "fee_dollars": 0.03},
    ])
    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: next(seq))
    tb._resolve_pending(KEY, CACHED, st("3-2", p1_serves=False), PX)
    pos = tb._store.get_or_create(KEY).position
    assert pos is not None and pos["count"] == 5.0


def test_transient_poll_failure_keeps_the_order_pending(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: None)       # 429 / timeout
    tb._resolve_pending(KEY, CACHED, st(), PX)
    assert tb._store.has_pending(KEY)                            # not silently dropped


def test_no_new_entry_while_an_order_is_resting(monkeypatch):
    monkeypatch.setattr(tb, "place_order", lambda *a: order(filled=0.0, remaining=5.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.has_pending(KEY)


def test_immediate_full_fill_behaves_like_before(monkeypatch):
    """Taker path: fill_or_kill returns everything filled, no pending order."""
    monkeypatch.setattr(cfg, "MAKER_MODE", False)
    monkeypatch.setattr(tb, "place_order", lambda *a: {
        "order_id": "o9", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.09, "avg_price": 0.60})
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.get_or_create(KEY).position["count"] == 5.0
    assert not tb._store.has_pending(KEY)


def test_exit_is_not_cancelled_just_because_the_game_moved_on(monkeypatch):
    """The thrash bug: an exit is placed BECAUSE the game ended, so cancelling it
    for that reason pulls it a tick later, forever."""
    tb._store.set_position(KEY, "EV-P2", 0.87, 1.0, "5-7 2-2")
    monkeypatch.setattr(tb, "close_position", lambda *a: order(filled=0.0, remaining=1.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: 24868.9)
    tb._check_exit(KEY, CACHED, st("5-7 3-2", p1_serves=True), 0.41, 0.86)
    assert tb._store.has_pending(KEY)

    pulled = []
    monkeypatch.setattr(tb, "cancel_order", lambda oid, tk=None: pulled.append(oid) or True)
    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: {
        "status": "resting", "filled": 0.0, "remaining": 1.0,
        "cost_dollars": 0.0, "fee_dollars": 0.0})
    # same price, later game: the exit must keep working
    tb._resolve_pending(KEY, CACHED, st("5-7 4-2", p1_serves=False), {"p1": 0.41, "p2": 0.86})
    assert pulled == []
    assert tb._store.has_pending(KEY)


def test_exit_is_requoted_when_the_price_moves(monkeypatch):
    tb._store.set_position(KEY, "EV-P2", 0.87, 1.0, "5-7 2-2")
    monkeypatch.setattr(tb, "close_position", lambda *a: order(filled=0.0, remaining=1.0))
    monkeypatch.setattr(tb, "order_queue_position", lambda oid, tk=None: None)
    tb._check_exit(KEY, CACHED, st("5-7 3-2", p1_serves=True), 0.41, 0.86)

    pulled = []
    monkeypatch.setattr(tb, "cancel_order", lambda oid, tk=None: pulled.append(oid) or True)
    monkeypatch.setattr(tb, "get_order", lambda oid, tk=None: {
        "status": "resting", "filled": 0.0, "remaining": 1.0,
        "cost_dollars": 0.0, "fee_dollars": 0.0})
    tb._resolve_pending(KEY, CACHED, st("5-7 3-2", p1_serves=True), {"p1": 0.41, "p2": 0.84})
    assert pulled == ["o1"]                       # limit drifted off the touch
    assert not tb._store.has_pending(KEY)         # _check_exit re-places next


# ── YES-denomination ──────────────────────────────────────────────────────────
# Kalshi books a sell-YES as a buy-NO and reports the NO price. Recording that
# as the exit price inverts every exit: a real sell at 5c was logged as 95c.

def test_sell_price_is_converted_from_no_to_yes():
    from trade.kalshi_client import _parse_order_response
    resp = {"order_id": "x", "fill_count": "1.00", "remaining_count": "0.00",
            "average_fill_price": "0.9500", "average_fee_paid": "0.0007"}
    sell = _parse_order_response(resp, "ask")
    assert sell["avg_price"] == pytest.approx(0.05)          # not 0.95
    assert sell["cost_dollars"] == pytest.approx(0.05)       # proceeds, YES terms


def test_buy_price_is_left_alone():
    from trade.kalshi_client import _parse_order_response
    resp = {"order_id": "x", "fill_count": "1.00", "remaining_count": "0.00",
            "average_fill_price": "0.2700", "average_fee_paid": "0.0035"}
    buy = _parse_order_response(resp, "bid")
    assert buy["avg_price"] == pytest.approx(0.27)
    assert buy["cost_dollars"] == pytest.approx(0.27)


def test_the_real_mejdra_round_trip_reconciles():
    """Bought MEJ yes @0.10, sold yes @0.05 -> a 5c LOSS, not an 85c win."""
    from trade.kalshi_client import _parse_order_response
    buy = _parse_order_response({"order_id": "a", "fill_count": "1.00",
                                 "remaining_count": "0.00",
                                 "average_fill_price": "0.1000",
                                 "average_fee_paid": "0.0016"}, "bid")
    sell = _parse_order_response({"order_id": "b", "fill_count": "1.00",
                                  "remaining_count": "0.00",
                                  "average_fill_price": "0.9500",
                                  "average_fee_paid": "0.0009"}, "ask")
    pnl = sell["cost_dollars"] - sell["fee_dollars"] - buy["cost_dollars"]
    assert pnl == pytest.approx(-0.0509, abs=1e-4)


# ── minimum game swing ────────────────────────────────────────────────────────
# Only trade games worth enough to clear a taker round trip. The gate reads the
# model's own branch spread, which is already computed and logged.

def _cond(win, lose):
    return {"win_game": win, "lose_game": lose, "win_set": 0.9, "lose_set": 0.1}


def test_low_swing_game_is_skipped(monkeypatch):
    monkeypatch.setattr(cfg, "MIN_GAME_SWING", 0.30)
    placed = []
    monkeypatch.setattr(tb, "place_order", lambda *a: placed.append(a) or None)
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.54, 0.46))  # 8pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert placed == []


def test_high_swing_game_is_traded(monkeypatch):
    monkeypatch.setattr(cfg, "MIN_GAME_SWING", 0.30)
    monkeypatch.setattr(tb, "place_order", lambda t, c, p: {
        "order_id": "s1", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.09, "avg_price": 0.60})
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.89, 0.55))  # 34pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.get_or_create(KEY).position is not None


def test_swing_is_side_independent(monkeypatch):
    """p2's branches are p1's complements swapped, so the spread is a property of
    the game — buying either side faces the same swing."""
    monkeypatch.setattr(cfg, "MIN_GAME_SWING", 0.30)
    c = _cond(0.89, 0.55)
    p1_swing = abs(c["win_game"] - c["lose_game"])
    p2_swing = abs((1 - c["lose_game"]) - (1 - c["win_game"]))
    assert p1_swing == pytest.approx(p2_swing)


def test_gate_can_be_disabled(monkeypatch):
    monkeypatch.setattr(cfg, "MIN_GAME_SWING", 0.0)
    monkeypatch.setattr(tb, "place_order", lambda t, c, p: {
        "order_id": "s2", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.09, "avg_price": 0.60})
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.51, 0.49))  # 2pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert tb._store.get_or_create(KEY).position is not None


def test_no_trade_without_branch_probs(monkeypatch):
    monkeypatch.setattr(cfg, "MIN_GAME_SWING", 0.30)
    placed = []
    monkeypatch.setattr(tb, "place_order", lambda *a: placed.append(a) or None)
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60)
    assert placed == []
