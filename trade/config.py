import os

# Trading parameters
MATCH_BUDGET      = 5.00
EDGE_MIN = 0.02   # entry edge required at the price extremes (raise to ~0.03 before going live: fees+spread)
EDGE_MAX = 0.13   # entry edge required at 50c; threshold = EDGE_MIN + (EDGE_MAX-EDGE_MIN)*sin(pi*price)
STOP_LOSS_PCT     = 0.15
STOP_LOSS_MIN_DOLLARS = 0.04  # stop distance is max(STOP_LOSS_PCT * entry, this floor)
TRAIL_ARM_DOLLARS      = 0.05  # arm trailing lock once price is this far above entry
TRAIL_GIVEBACK_DOLLARS = 0.05  # once armed, exit when price falls this far from its high
ENTRY_GAME_PROB_MIN = 0.30  # defer entry while our side's current-game win prob is below this
REENTRY_GUARD_SECS  = 180   # after a trail exit, block same-side re-entry at >= exit price for this long
KELLY_FRACTION    = 1  #0.5 for half Kelly
COOLDOWN_SECONDS  = 300
FAST_POLL_SECS    = 1
MAX_MC_STALENESS_SECS = 120  # skip entries if sim older than this; forces re-sim heartbeat
SIM_RETRY_SECS    = 15   # min gap between Hawkeye retries after a failed sim (protects CF-guarded endpoint)
ATP_LAG_RETRY_SECS    = 2    # retry gap when Hawkeye stats lag the Kalshi score change
ATP_LAG_MAX_WAIT_SECS = 10   # after this long waiting for fresh stats, sim with what we have
N_SIMS            = 10_000
DRY_RUN           = True

# Career-stat prior (identity anchor)
PRIOR_N  = 40             # career stats worth this many virtual points per stat
SURFACE  = "Grass"        # default surface for career lookups; override per match with "surface"
ATP_DB   = "atp/data/atp.db"

# Kalshi API credentials
API_KEY     = "982c924c-9e22-44c8-a801-3be1ff50d45d"
KEY_FILE    = "Key1.txt"
KALSHI_BASE = "https://external-api.kalshi.com"

# Data logging
LOG_DIR = "data/logs"

# Match configuration
# Each entry: hawkeye_url, event_ticker, and optional budget (dollars; defaults to MATCH_BUDGET)
# milestone_id, canonical ticker, and p1 identity are all resolved automatically.
MATCH_CONFIG = [
    # {
    #     "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms004",
    #     "event_ticker": "KXATPCHALLENGERMATCH-26JUL03LEGBAR",
    #     "budget": 5.00,
    #     "surface": "Hard",
    # },
    {
        "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms005",
        "event_ticker": "KXATPCHALLENGERMATCH-26JUL03WINMAN",
        "budget": 5.00,
        "surface": "Hard",
    },
    # {
    #     "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/540/ms023",
    #     "event_ticker": "KXATPMATCH-26JUL03RINDJO",
    #     "budget": 5.00,
    #     "surface": "Grass",
    # },
]
