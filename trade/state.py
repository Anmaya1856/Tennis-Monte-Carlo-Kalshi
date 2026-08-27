import time
from dataclasses import dataclass, field
import trade.config as cfg


@dataclass
class MatchState:
    budget_remaining: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    initial_budget: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    position: dict = None          # None or {ticker, entry_price, count, entry_time, game_id}
    pending: dict = None           # a resting order awaiting fill (maker mode only)
    last_mc_prob: float = None     # updated on each successful sim
    last_game_prob: float = None   # p1's current-game win prob at last successful sim
    last_cond: dict = None         # p1's branch match probs {win_game,lose_game,win_set,lose_set}
    last_sim_score: tuple = None   # (score_str, game_score_str, p1_serves) at last successful sim
    last_sim_time: float = 0.0     # unix timestamp of last successful sim
    divergence_ema: float = 0.0    # EMA of |model - market|; diagnostic only, gates nothing


class MatchStateStore:
    def __init__(self):
        self._store: dict = {}

    def get_or_create(self, ticker, budget=None):
        if ticker not in self._store:
            b = budget if budget is not None else cfg.MATCH_BUDGET
            self._store[ticker] = MatchState(budget_remaining=b, initial_budget=b)
        return self._store[ticker]

    def deduct_fill(self, ticker, cost, fee):
        self.get_or_create(ticker).budget_remaining -= (cost + fee)

    def restore_proceeds(self, ticker, proceeds):
        self.get_or_create(ticker).budget_remaining += proceeds

    def update_mc_prob(self, ticker, mc_prob, game_prob=None, cond=None):
        ms = self.get_or_create(ticker)
        ms.last_mc_prob = mc_prob
        ms.last_game_prob = game_prob
        ms.last_cond = cond

    def update_divergence(self, ticker, mc_prob, market_mid):
        """Track |model - market| as an EMA. Logged for analysis; the receiver-roll
        strategy does not trade the model's match prob, so it gates nothing."""
        ms = self.get_or_create(ticker)
        a = cfg.DIVERGENCE_EMA_ALPHA
        ms.divergence_ema = (1 - a) * ms.divergence_ema + a * abs(mc_prob - market_mid)
        return ms.divergence_ema

    def record_sim(self, ticker, score_key):
        ms = self.get_or_create(ticker)
        ms.last_sim_score = score_key
        ms.last_sim_time  = time.time()

    def is_mc_stale(self, ticker):
        return time.time() - self.get_or_create(ticker).last_sim_time > cfg.MAX_MC_STALENESS_SECS

    def set_position(self, key, ticker, entry_price, count, game_id):
        ms = self.get_or_create(key)
        ms.position = {
            "ticker":      ticker,
            "entry_price": entry_price,
            "count":       count,
            "entry_time":  time.time(),
            "game_id":     game_id,      # holding period id; changes when the game ends
        }

    def add_to_position(self, key, ticker, price, count, game_id):
        """Book a fill. Partial fills on the same order accumulate at a weighted
        average, so a position always reflects contracts actually owned."""
        ms = self.get_or_create(key)
        pos = ms.position
        if pos is None or pos["ticker"] != ticker:
            self.set_position(key, ticker, price, count, game_id)
            return
        total = pos["count"] + count
        pos["entry_price"] = (pos["entry_price"] * pos["count"] + price * count) / total
        pos["count"] = total

    def reduce_position(self, key, count):
        """Book a sell fill. Clears the position once it is fully closed."""
        ms = self.get_or_create(key)
        if ms.position is None:
            return
        ms.position["count"] -= count
        if ms.position["count"] <= 1e-9:
            ms.position = None

    def set_pending(self, key, order):
        self.get_or_create(key).pending = order

    def clear_pending(self, key):
        self.get_or_create(key).pending = None

    def has_pending(self, key):
        return self.get_or_create(key).pending is not None

    def clear_position(self, ticker):
        self.get_or_create(ticker).position = None

    def has_position(self, ticker):
        return self.get_or_create(ticker).position is not None

    def is_budget_exhausted(self, ticker):
        return self.get_or_create(ticker).budget_remaining <= 0
