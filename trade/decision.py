import math
import trade.config as cfg


def _kelly_count(mc_prob, price, budget):
    """Number of contracts via half-Kelly. Returns 0 if no edge."""
    if price <= 0 or price >= 1:
        return 0
    kelly_frac = (mc_prob - price) / (1 - price)
    if kelly_frac <= 0:
        return 0
    half_kelly_frac = cfg.KELLY_FRACTION * kelly_frac
    bet_dollars = min(half_kelly_frac * budget, budget)
    return max(1, math.floor(bet_dollars / price))


def compute_entry(mc_prob, yes_ask, yes_bid, budget_remaining):
    """
    Evaluate whether to enter a position.
    Returns order params dict or None.
    """
    if budget_remaining <= 0:
        return None

    edge_yes = mc_prob - yes_ask
    no_ask   = 1 - yes_bid
    edge_no  = (1 - mc_prob) - no_ask

    if edge_yes >= cfg.EDGE_THRESHOLD and edge_yes >= edge_no:
        count = _kelly_count(mc_prob, yes_ask, budget_remaining)
        if count < 1:
            return None
        return {
            "side":            "yes",
            "yes_price_cents": round(yes_ask * 100),
            "count":           count,
            "entry_price":     yes_ask,
        }

    if edge_no >= cfg.EDGE_THRESHOLD:
        count = _kelly_count(1 - mc_prob, no_ask, budget_remaining)
        if count < 1:
            return None
        no_price_cents = round(no_ask * 100)
        return {
            "side":            "no",
            "yes_price_cents": 100 - no_price_cents,
            "count":           count,
            "entry_price":     no_ask,
        }

    return None


def should_stop_loss(entry_price, current_value):
    stop_price = entry_price * (1 - cfg.STOP_LOSS_PCT)
    return current_value <= stop_price


def should_take_profit(mc_prob_at_entry, current_value):
    return current_value >= mc_prob_at_entry
