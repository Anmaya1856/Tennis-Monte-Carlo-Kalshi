# Trading parameters
MATCH_BUDGET      = 20.00
# Receiver-roll strategy: while the current set is within MAX_GAME_DIFF games,
# hold the player about to RECEIVE and square off when the game ends. No stop
# loss, no trailing lock, no profit target — the game boundary is the only exit.
MAX_GAME_DIFF     = 1     # only hold a position while |p1 games - p2 games| <= this
# Trade tiebreaks? The receiver logic is correct there (see _serve_score and the
# on_serve docstring), but maker exits cannot keep up: measured fill latency in a
# tiebreak was 400-520s against ~45s points, so a position sits on the wrong side
# for eight-plus points. Off until exits are fast enough — the logic is retained,
# not deleted, so flipping this back on restores it.
TRADE_TIEBREAKS   = False
CONTRACTS_PER_TRADE = 20   # fixed size. No Kelly and no edge test: every on-serve
                          # game boundary buys this many contracts of the receiver.
# Only trade when THIS game is worth something. The gate is the model's own branch
# spread, |cond_win_game - cond_lose_game|: how much match probability turns on the
# game about to be played. It is what separates 5-5 in a decider (one break moves
# the price ~29c) from 1-1 in set one (~7c) — and a taker round trip costs 9-11c at
# 5 contracts, so low-swing games cannot clear the fee even when the call is right.
# Thresholds are looked up from data/swing_thresholds.db (keyed by pA, pB, set_num)
# rather than stored statically here — run simulation/precompute_swing_thresholds.ipynb
# to generate the DB.  The bot falls back to max(SWING_FLOOR, 0.15) if the DB is absent.
KEEP_FRACTION       = 0.30   # retain the top 30% of on-serve games by swing
SWING_FLOOR         = 0.08   # hard minimum — below this the taker fee cannot be cleared
SWING_THRESHOLDS_DB = "data/swing_thresholds.db"
# Execution mode. MAKER quotes passively: buy at the BID, sell at the ASK, and pay
# the maker fee instead of the taker fee. Kalshi's maker fee is
#   round up(M x 0.0175 x C x P x (1-P))   with M defaulting to 0, i.e. free.
# WARNING: in DRY_RUN this assumes every resting order fills at the touch. It will
# not — the median in-play book has ~6,600 contracts queued ahead of a 5-lot, and
# real fills are adversely selected. Treat maker P&L as an upper bound.
MAKER_MODE = False
# Kalshi's maker multiplier M, per series. ATP main tour charges maker fees;
# Challengers do not. Anything unlisted defaults to charged, so an unknown series
# can never understate costs.
MAKER_FEE_MULTIPLIER = {
    "KXATPMATCH":           1,
    "KXATPCHALLENGERMATCH": 0,
}
MAKER_FEE_MULTIPLIER_DEFAULT = 1
DIVERGENCE_EMA_ALPHA  = 0.05  # per-sim EMA weight; logged as a diagnostic, does not gate entries
FAST_POLL_SECS    = 1
MAX_MC_STALENESS_SECS = 120  # skip entries if sim older than this; forces re-sim heartbeat
SIM_RETRY_SECS    = 15   # min gap between retries after a failed sim (stats not ready yet)
MATCH_END_GRACE_SECS = 900  # exit the bot if the milestone stays not-live this long (survives breaks)
INIT_TIMEOUT_SECS    = 120  # exit if a match never initializes within this long (likely a bad ticker)
BOT_LOCK_STALE_SECS  = 30   # a bot touches .bot_<event>.lock every tick; older than this = dead
N_DRAWS           = 1000     # stat draws for the exact engine; each draw evaluated exactly
RANDOM_SEED       = 42       # seed for Beta draws in estimate_win_prob_market — same state → same draws
BP_PRESSURE       = 0.03      # subtract this from the server's point-win prob at break points (serving under pressure); 0 = off
DRY_RUN           = True

# Market-implied prior (live model): invert the pre-match Kalshi price into
# per-server point probs, then blend with in-match service counts.
MARKET_PRIOR_N = 40       # market-implied point probs worth this many virtual service points
# How far back to look for the pre-match price. A flat offset from the bot's first
# poll, rather than estimating the start from points played x 45s: markets open
# 13-41h before we attach, so 5h always lands in the candle history and can never
# land AFTER play began (which would bake the live score into the "prior" and then
# double-count it as the in-play blend accumulates). Measured over 18 matches, the
# two methods differ by 1.8c on average — under a point of serve probability.
PREMATCH_LOOKBACK_HOURS = 5
INVERSION_BASE = 0.64     # assumed tour-average serve level; fixes the overall level in the inversion
# GAME_THRESHOLDS = [16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5, 25.5, 26.5, 27.5, 30.5]  # log P(total match games > X) for each; changeable
GAME_THRESHOLDS = [34.5, 35.5, 36.5, 37.5, 38.5, 39.5, 40.5, 41.5, 42.5, 43.5, 44.5, 45.5]  # log P(total match games > X) for each; changeable

# Kalshi API credentials
API_KEY     = "982c924c-9e22-44c8-a801-3be1ff50d45d"
KEY_FILE    = "Key1.txt"
KALSHI_BASE = "https://external-api.kalshi.com"

# Data logging
# One CSV per match per day, named <event ticker>_<YYYYMMDD> (see logger._suffix),
# so a match's trades and snapshots are easy to find and restarts append rather
# than fragment.
LOG_DIR = "data/logs"

# Discovery: the monitor scans these Kalshi series and lists live matches in the
# sidebar so you can choose which to launch manually.
AUTO_LAUNCH_SERIES = ("KXATPMATCH",
                       "KXATPCHALLENGERMATCH"
                       )
AUTO_LAUNCH_POLL_SECS = 600    # how often to scan Kalshi for newly-live matches

# Match configuration
# Each entry: event_ticker, and optional budget (dollars; defaults to MATCH_BUDGET).
# milestone_id, best_of, canonical ticker, and p1 identity are all resolved automatically.
MATCH_CONFIG = [
    # {
    #     "event_ticker": "KXATPMATCH-26JUL30CERGEA",
    #     "budget": 5.00,
    # },
]
