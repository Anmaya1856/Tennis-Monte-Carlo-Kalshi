import time, pytest
import trade.config as cfg
from trade.state import MatchStateStore

def make_store():
    return MatchStateStore()

def test_initial_budget_equals_match_budget():
    store = make_store()
    ms = store.get_or_create("TICKER-A")
    assert ms.budget_remaining == cfg.MATCH_BUDGET

def test_deduct_fill_reduces_budget():
    store = make_store()
    store.get_or_create("TICKER-A")
    store.deduct_fill("TICKER-A", cost=1.50, fee=0.05)
    assert abs(store.get_or_create("TICKER-A").budget_remaining - (cfg.MATCH_BUDGET - 1.55)) < 1e-9

def test_no_position_initially():
    store = make_store()
    assert not store.has_position("TICKER-A")

def test_set_and_clear_position():
    store = make_store()
    store.get_or_create("TICKER-A")
    store.set_position("TICKER-A", "yes", 0.55, count=2.50)
    assert store.has_position("TICKER-A")
    ms = store.get_or_create("TICKER-A")
    assert ms.position["entry_price"] == 0.55
    assert ms.position["count"] == 2.50
    store.clear_position("TICKER-A")
    assert not store.has_position("TICKER-A")

def test_update_mc_prob():
    store = make_store()
    assert store.get_or_create("TICKER-A").last_mc_prob is None
    store.update_mc_prob("TICKER-A", 0.63)
    assert store.get_or_create("TICKER-A").last_mc_prob == 0.63

def test_cooldown_active_after_set():
    store = make_store()
    store.get_or_create("TICKER-A")
    assert not store.is_in_cooldown("TICKER-A")
    store.set_cooldown("TICKER-A")
    assert store.is_in_cooldown("TICKER-A")

def test_cooldown_expires():
    original = cfg.COOLDOWN_SECONDS
    cfg.COOLDOWN_SECONDS = 0
    try:
        store = make_store()
        store.get_or_create("TICKER-A")
        store.set_cooldown("TICKER-A")
        time.sleep(0.01)
        assert not store.is_in_cooldown("TICKER-A")
    finally:
        cfg.COOLDOWN_SECONDS = original

def test_budget_exhausted():
    store = make_store()
    store.get_or_create("TICKER-A")
    store.deduct_fill("TICKER-A", cost=cfg.MATCH_BUDGET, fee=0.0)
    assert store.is_budget_exhausted("TICKER-A")

def test_restore_proceeds_adds_to_budget():
    store = make_store()
    store.deduct_fill("TICKER-A", cost=2.50, fee=0.05)
    # Exit: 3 contracts × $0.80 exit price − $0.03 fee = $2.37 proceeds
    store.restore_proceeds("TICKER-A", 2.37)
    expected = cfg.MATCH_BUDGET - 2.55 + 2.37
    assert abs(store.get_or_create("TICKER-A").budget_remaining - expected) < 1e-9

def test_restore_proceeds_can_exceed_initial_budget():
    # Profitable trade: proceeds > original cost → budget rises above starting point
    store = make_store()
    store.deduct_fill("TICKER-A", cost=2.00, fee=0.05)
    store.restore_proceeds("TICKER-A", 3.50)
    assert store.get_or_create("TICKER-A").budget_remaining > cfg.MATCH_BUDGET
