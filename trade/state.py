import time
from dataclasses import dataclass, field
import trade.config as cfg


@dataclass
class MatchState:
    budget_remaining: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    position: dict = None          # None or {side, entry_price, mc_prob_at_entry, count, entry_time}
    cooldown_until: float = 0.0    # unix timestamp; 0 = no cooldown


class MatchStateStore:
    def __init__(self):
        self._store: dict = {}

    def get_or_create(self, ticker):
        if ticker not in self._store:
            self._store[ticker] = MatchState(budget_remaining=cfg.MATCH_BUDGET)
        return self._store[ticker]

    def deduct_fill(self, ticker, cost, fee):
        self.get_or_create(ticker).budget_remaining -= (cost + fee)

    def set_position(self, ticker, side, entry_price, mc_prob, count):
        ms = self.get_or_create(ticker)
        ms.position = {
            "side":              side,
            "entry_price":       entry_price,
            "mc_prob_at_entry":  mc_prob,
            "count":             count,
            "entry_time":        time.time(),
        }

    def clear_position(self, ticker):
        self.get_or_create(ticker).position = None

    def set_cooldown(self, ticker):
        self.get_or_create(ticker).cooldown_until = time.time() + cfg.COOLDOWN_SECONDS

    def is_in_cooldown(self, ticker):
        return self.get_or_create(ticker).cooldown_until > time.time()

    def has_position(self, ticker):
        return self.get_or_create(ticker).position is not None

    def is_budget_exhausted(self, ticker):
        return self.get_or_create(ticker).budget_remaining <= 0
