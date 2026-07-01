import trade.config as cfg


def _kelly_count(mc_prob, price, budget):
    """Fractional contracts via half-Kelly. Returns 0.0 if no edge."""
    if price <= 0 or price >= 1:
        return 0.0
    kelly_frac = (mc_prob - price) / (1 - price)
    if kelly_frac <= 0:
        return 0.0
    bet_dollars = min(cfg.KELLY_FRACTION * kelly_frac * budget, budget)
    return round(bet_dollars / price, 2)


def edge_threshold(yes_ask):
    """Higher threshold in the contested 35-65 range, tapering linearly outside it."""
    p = yes_ask * 100
    if p <= 30 or p >= 70:
        return cfg.EDGE_THRESHOLD
    if 35 <= p <= 65:
        return cfg.CONTESTED_EDGE_THRESHOLD
    if p < 35:
        return cfg.EDGE_THRESHOLD + (p - 30) * 0.01
    # 65 < p < 70
    return cfg.CONTESTED_EDGE_THRESHOLD - (p - 65) * 0.01


def compute_entry(mc_prob, yes_ask, yes_bid, budget_remaining):
    """
    Evaluate whether to enter a position.
    Returns order params dict or None.
    """
    if budget_remaining <= 0:
        return None

    threshold = edge_threshold(yes_ask)
    edge_yes  = mc_prob - yes_ask
    no_ask    = 1 - yes_bid
    edge_no   = (1 - mc_prob) - no_ask

    if edge_yes >= threshold and edge_yes >= edge_no:
        count = _kelly_count(mc_prob, yes_ask, budget_remaining)
        if count <= 0:
            return None
        return {
            "side":            "yes",
            "yes_price_cents": round(yes_ask * 100),
            "count":           count,
            "entry_price":     yes_ask,
        }

    if edge_no >= threshold:
        count = _kelly_count(1 - mc_prob, no_ask, budget_remaining)
        if count <= 0:
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
    return current_value <= entry_price * (1 - cfg.STOP_LOSS_PCT)


def should_take_profit(side, current_value, current_mc_prob, entry_price):
    """Exit when market has reached model price, but only if we're in profit."""
    if current_value <= entry_price:
        return False
    model_value = current_mc_prob if side == "yes" else (1 - current_mc_prob)
    return current_value >= model_value
