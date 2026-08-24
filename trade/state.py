import time
from dataclasses import dataclass, field
import trade.config as cfg


@dataclass
class MatchState:
    budget_remaining: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    initial_budget: float = field(default_factory=lambda: cfg.MATCH_BUDGET)
    position: dict = None          # None or {ticker, entry_price, count, entry_time}
    cooldown_until: float = 0.0    # unix timestamp; 0 = no cooldown
    last_mc_prob: float = None     # updated on each successful sim
    last_game_prob: float = None   # p1's current-game win prob at last successful sim
    last_cond: dict = None         # p1's branch match probs {win_game,lose_game,win_set,lose_set}
    trail_exit: dict = None        # {player, price, time} of last trail_lock exit
    last_sim_score: tuple = None   # (score_str, game_score_str, p1_serves) at last successful sim
    last_sim_time: float = 0.0     # unix timestamp of last successful sim
    divergence_ema: float = 0.0    # EMA of |model - market| updated each sim
    standdown: bool = False        # entries paused due to sustained model-market divergence


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
        """Update the divergence EMA and the stand-down state (with hysteresis).
        Returns (standdown, changed)."""
        ms = self.get_or_create(ticker)
        a = cfg.DIVERGENCE_EMA_ALPHA
        ms.divergence_ema = (1 - a) * ms.divergence_ema + a * abs(mc_prob - market_mid)
        prev = ms.standdown
        if not ms.standdown and ms.divergence_ema > cfg.DIVERGENCE_PAUSE:
            ms.standdown = True
        elif ms.standdown and ms.divergence_ema < cfg.DIVERGENCE_RESUME:
            ms.standdown = False
        return ms.standdown, ms.standdown != prev

    def set_trail_exit(self, ticker, player, price):
        self.get_or_create(ticker).trail_exit = {
            "player": player, "price": price, "time": time.time(),
        }

    def record_sim(self, ticker, score_key):
        ms = self.get_or_create(ticker)
        ms.last_sim_score = score_key
        ms.last_sim_time  = time.time()

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
