import pytest
import trade.config as cfg
from trade.decision import compute_entry, should_stop_loss, should_take_profit

def test_entry_yes_when_mc_above_ask():
    result = compute_entry(mc_prob=0.65, yes_ask=0.55, yes_bid=0.53, budget_remaining=5.0)
    assert result is not None
    assert result["side"] == "yes"
    assert result["entry_price"] == 0.55

def test_entry_no_when_mc_below_bid():
    result = compute_entry(mc_prob=0.40, yes_ask=0.55, yes_bid=0.53, budget_remaining=5.0)
    assert result is not None
    assert result["side"] == "no"

def test_no_entry_when_edge_below_threshold():
    result = compute_entry(mc_prob=0.60, yes_ask=0.55, yes_bid=0.53, budget_remaining=5.0)
    assert result is None

def test_no_entry_when_budget_zero():
    result = compute_entry(mc_prob=0.70, yes_ask=0.55, yes_bid=0.53, budget_remaining=0.0)
    assert result is None

def test_kelly_count_is_positive():
    result = compute_entry(mc_prob=0.70, yes_ask=0.55, yes_bid=0.53, budget_remaining=5.0)
    assert result["count"] > 0

def test_kelly_count_is_fractional():
    # fractional contracts: count should be float, not necessarily integer
    result = compute_entry(mc_prob=0.70, yes_ask=0.55, yes_bid=0.53, budget_remaining=5.0)
    assert isinstance(result["count"], float)

def test_stop_loss_triggers_below_threshold():
    assert should_stop_loss(entry_price=0.60, current_value=0.50) is True

def test_stop_loss_does_not_trigger_above_threshold():
    assert should_stop_loss(entry_price=0.60, current_value=0.55) is False

def test_take_profit_yes_triggers_at_model_price():
    # YES position: profit when market price reaches current MC prob
    assert should_take_profit("yes", current_value=0.65, current_mc_prob=0.65) is True

def test_take_profit_yes_does_not_trigger_below():
    assert should_take_profit("yes", current_value=0.60, current_mc_prob=0.65) is False

def test_take_profit_no_triggers_at_model_price():
    # NO position: profit when NO market value (1 - yes_ask) reaches 1 - mc_prob
    # mc_prob=0.40 → model NO = 0.60; current NO value = 0.60 → trigger
    assert should_take_profit("no", current_value=0.60, current_mc_prob=0.40) is True

def test_take_profit_no_does_not_trigger_below():
    assert should_take_profit("no", current_value=0.55, current_mc_prob=0.40) is False
