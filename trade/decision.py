import math
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


def edge_threshold(price):
    """Smooth threshold: EDGE_MAX at 50c where model error and fees peak,
    easing to EDGE_MIN at the price extremes (sin^2 keeps the hump narrow)."""
    return cfg.EDGE_MIN + (cfg.EDGE_MAX - cfg.EDGE_MIN) * math.sin(math.pi * price) ** 2


def compute_entry(mc_prob, p1_ask, p2_ask, budget_remaining):
    """
    Evaluate whether to buy YES on either player's market.
    Returns order params dict or None.
    """
    if budget_remaining <= 0:
        return None

    candidates = []
    for player, prob, ask in (("p1", mc_prob, p1_ask), ("p2", 1 - mc_prob, p2_ask)):
        if prob - ask >= edge_threshold(ask):
            candidates.append((prob - ask, player, prob, ask))
    if not candidates:
        return None

    _, player, prob, ask = max(candidates)
    count = _kelly_count(prob, ask, budget_remaining)
    if count <= 0:
        return None
    return {
        "player":      player,
        "price_cents": round(ask * 100),
        "count":       count,
        "entry_price": ask,
    }


def should_stop_loss(entry_price, current_value):
    stop_distance = max(cfg.STOP_LOSS_PCT * entry_price, cfg.STOP_LOSS_MIN_DOLLARS)
    return current_value <= entry_price - stop_distance + 1e-9


def should_trail_exit(entry_price, high_water, current_value):
    """Book profit when an armed position gives back TRAIL_GIVEBACK from its high."""
    armed = high_water >= entry_price + cfg.TRAIL_ARM_DOLLARS
    return armed and current_value <= high_water - cfg.TRAIL_GIVEBACK_DOLLARS


def should_take_profit(player, current_value, current_mc_prob, entry_price):
    """Exit when market has reached model price, but only if we're in profit."""
    if current_value <= entry_price:
        return False
    model_value = current_mc_prob if player == "p1" else (1 - current_mc_prob)
    return current_value >= model_value
