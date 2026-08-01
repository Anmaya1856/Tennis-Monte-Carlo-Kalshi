import pytest
import trade.config as cfg
from trade.decision import compute_entry, edge_threshold, should_stop_loss, should_take_profit, should_trail_exit

def test_edge_threshold_max_at_midpoint():
    assert abs(edge_threshold(0.50) - cfg.EDGE_MAX) < 1e-9

def test_edge_threshold_relaxes_at_extremes():
    assert edge_threshold(0.92) < 0.06          # entries possible on strong favorites
    assert edge_threshold(0.98) < edge_threshold(0.92) < edge_threshold(0.70)

def test_edge_threshold_underdog_floor():
    # cheap side floored at EDGE_MIN_UNDERDOG; favorite side untouched
    assert edge_threshold(0.10) == cfg.EDGE_MIN_UNDERDOG
    assert edge_threshold(0.05) == cfg.EDGE_MIN_UNDERDOG
    assert edge_threshold(0.90) < cfg.EDGE_MIN_UNDERDOG          # favorite side keeps sin^2
    # the cutoff sits at/above where the sin^2 curve meets the floor, so they join
    # continuously — no step at the cutoff and no dip below the floor
    below = edge_threshold(cfg.UNDERDOG_PRICE - 1e-6)
    above = edge_threshold(cfg.UNDERDOG_PRICE + 1e-6)
    assert above >= cfg.EDGE_MIN_UNDERDOG - 1e-9                 # curve has reached the floor (no dip)
    assert abs(above - below) < 0.005                           # continuous across the cutoff
    for p in (0.05, 0.15, 0.25, 0.35, cfg.UNDERDOG_PRICE):
        assert edge_threshold(p) >= cfg.EDGE_MIN_UNDERDOG - 1e-9  # threshold never dips below the floor

def test_trail_scales_on_cheap_entries():
    # entry 0.06: arm = giveback = 0.35 * 0.06 = 0.021
    assert should_trail_exit(entry_price=0.06, high_water=0.09, current_value=0.068) is True
    assert should_trail_exit(entry_price=0.06, high_water=0.09, current_value=0.075) is False
    assert should_trail_exit(entry_price=0.06, high_water=0.079, current_value=0.05) is False  # not armed

def test_trail_unchanged_mid_range():
    # entry 0.40: caps don't bind (0.35*0.40 > 0.05) — same as before
    assert should_trail_exit(entry_price=0.40, high_water=0.55, current_value=0.50) is True
    assert should_trail_exit(entry_price=0.40, high_water=0.43, current_value=0.30) is False

def test_stop_capped_on_cheap_entries():
    # entry 0.06: dist = min(max(0.009, 0.04), 0.03) = 0.03 -> stop at 0.03
    assert should_stop_loss(entry_price=0.06, current_value=0.03) is True
    assert should_stop_loss(entry_price=0.06, current_value=0.035) is False

def test_entry_p1_when_mc_above_ask():
    # p1_ask=0.55 is in the contested 35-65 range → threshold 0.13; edge = 0.15
    result = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert result is not None
    assert result["player"] == "p1"
    assert result["entry_price"] == 0.55

def test_entry_p2_when_mc_below():
    # p2 model value 0.60 vs p2_ask 0.47 → edge 0.13 ≥ contested threshold
    result = compute_entry(mc_prob=0.40, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert result is not None
    assert result["player"] == "p2"
    assert result["entry_price"] == 0.47

def test_no_entry_when_edge_below_threshold():
    result = compute_entry(mc_prob=0.60, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert result is None

def test_no_entry_when_budget_zero():
    result = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=0.0)
    assert result is None

def test_kelly_count_is_positive():
    result = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert result["count"] > 0

def test_size_cap_prevents_post_win_escalation():
    # inflated budget (after wins) sizes as if budget were still the cap
    inflated = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=10.0, size_cap=5.0)
    normal   = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert inflated["count"] == normal["count"]
    # losses still shrink sizing: low budget under the cap sizes off the low budget
    depleted = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=2.0, size_cap=5.0)
    assert depleted["count"] < normal["count"]

def test_kelly_count_is_fractional():
    # fractional contracts: count should be float, not necessarily integer
    result = compute_entry(mc_prob=0.70, p1_ask=0.55, p2_ask=0.47, budget_remaining=5.0)
    assert isinstance(result["count"], float)

def test_stop_loss_triggers_below_threshold():
    assert should_stop_loss(entry_price=0.60, current_value=0.60 * (1 - cfg.STOP_LOSS_PCT)) is True

def test_stop_loss_does_not_trigger_above_threshold():
    assert should_stop_loss(entry_price=0.60, current_value=0.60 * (1 - cfg.STOP_LOSS_PCT) + 0.01) is False

def test_stop_loss_min_distance_on_cheap_entries():
    # entry 0.15: 10% = 1.5c but the 4c floor applies → stop at 0.11
    assert should_stop_loss(entry_price=0.15, current_value=0.12) is False
    assert should_stop_loss(entry_price=0.15, current_value=0.11) is True

def test_take_profit_p1_triggers_at_model_price():
    # p1 YES position: profit when market price reaches current MC prob (and above entry)
    assert should_take_profit("p1", current_value=0.65, current_mc_prob=0.65, entry_price=0.55) is True

def test_take_profit_p1_does_not_trigger_below():
    assert should_take_profit("p1", current_value=0.60, current_mc_prob=0.65, entry_price=0.55) is False

def test_take_profit_p1_does_not_trigger_when_not_in_profit():
    # at model price but below entry → no take profit
    assert should_take_profit("p1", current_value=0.65, current_mc_prob=0.65, entry_price=0.70) is False

def test_take_profit_p2_triggers_at_model_price():
    # p2 YES position: model value = 1 - mc_prob
    # mc_prob=0.40 → model p2 = 0.60; current value = 0.60 → trigger
    assert should_take_profit("p2", current_value=0.60, current_mc_prob=0.40, entry_price=0.50) is True

def test_take_profit_p2_does_not_trigger_below():
    assert should_take_profit("p2", current_value=0.55, current_mc_prob=0.40, entry_price=0.50) is False

def test_trail_not_armed_before_arm_threshold():
    # high never reached entry + TRAIL_ARM → no exit even on a drop
    assert should_trail_exit(entry_price=0.40, high_water=0.43, current_value=0.30) is False

def test_trail_exits_on_giveback_from_high():
    # armed (high 0.55 ≥ 0.40 + 0.05), gave back ≥ 0.05 from high
    assert should_trail_exit(entry_price=0.40, high_water=0.55, current_value=0.50) is True

def test_trail_holds_while_near_high():
    assert should_trail_exit(entry_price=0.40, high_water=0.55, current_value=0.52) is False

def test_trail_arms_exactly_at_threshold():
    assert should_trail_exit(entry_price=0.40, high_water=0.45, current_value=0.40) is True
