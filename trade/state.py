import time
from dataclasses import dataclass, field
import trade.config as cfg


@dataclass
class MatchState:
    budget_remaining: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    position: dict = None          # None or {ticker, entry_price, count, entry_time}
    cooldown_until: float = 0.0    # unix timestamp; 0 = no cooldown
    last_mc_prob: float = None     # updated on each successful sim
    last_game_prob: float = None   # p1's current-game win prob at last successful sim
    trail_exit: dict = None        # {player, price, time} of last trail_lock exit
    last_sim_score: tuple = None   # (score_str, game_score_str, p1_serves) at last successful sim
    last_sim_time: float = 0.0     # unix timestamp of last successful sim
    last_sim_total_points: int = 0 # total points in the Hawkeye stats at last successful sim


class MatchStateStore:
    def __init__(self):
        self._store: dict = {}

    def get_or_create(self, ticker, budget=None):
        if ticker not in self._store:
            self._store[ticker] = MatchState(
                budget_remaining=budget if budget is not None else cfg.MATCH_BUDGET
            )
        return self._store[ticker]

    def deduct_fill(self, ticker, cost, fee):
        self.get_or_create(ticker).budget_remaining -= (cost + fee)

    def restore_proceeds(self, ticker, proceeds):
        self.get_or_create(ticker).budget_remaining += proceeds

    def update_mc_prob(self, ticker, mc_prob, game_prob=None):
        ms = self.get_or_create(ticker)
        ms.last_mc_prob = mc_prob
        ms.last_game_prob = game_prob

    def set_trail_exit(self, ticker, player, price):
        self.get_or_create(ticker).trail_exit = {
            "player": player, "price": price, "time": time.time(),
        }

    def record_sim(self, ticker, score_key, total_points=0):
        ms = self.get_or_create(ticker)
        ms.last_sim_score        = score_key
        ms.last_sim_time         = time.time()
        ms.last_sim_total_points = total_points

    def is_mc_stale(self, ticker):
        return time.time() - self.get_or_create(ticker).last_sim_time > cfg.MAX_MC_STALENESS_SECS

    def set_position(self, key, ticker, entry_price, count):
        ms = self.get_or_create(key)
        ms.position = {
            "ticker":      ticker,
            "entry_price": entry_price,
            "count":       count,
            "entry_time":  time.time(),
            "high_water":  entry_price,
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
