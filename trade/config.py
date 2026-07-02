import os

# Trading parameters
MATCH_BUDGET      = 5.00
EDGE_THRESHOLD           = 0.08
CONTESTED_EDGE_THRESHOLD = 0.13
STOP_LOSS_PCT     = 0.25
TRAIL_ARM_DOLLARS      = 0.05  # arm trailing lock once price is this far above entry
TRAIL_GIVEBACK_DOLLARS = 0.05  # once armed, exit when price falls this far from its high
KELLY_FRACTION    = 0.75  #0.5 for half Kelly
COOLDOWN_SECONDS  = 60
FAST_POLL_SECS    = 1
MAX_MC_STALENESS_SECS = 120  # skip entries if sim older than this; forces re-sim heartbeat
SIM_RETRY_SECS    = 15   # min gap between Hawkeye retries after a failed sim (protects CF-guarded endpoint)
ATP_LAG_RETRY_SECS    = 2    # retry gap when Hawkeye stats lag the Kalshi score change
ATP_LAG_MAX_WAIT_SECS = 10   # after this long waiting for fresh stats, sim with what we have
N_SIMS            = 10_000
DRY_RUN           = True

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
    #     "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/540/ms043",
    #     "event_ticker": "KXATPMATCH-26JUL01FUCTIE",
    #     "budget": 5.00,
    # },
    {
        "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms010",
        "event_ticker": "KXATPCHALLENGERMATCH-26JUL01GLIWIN",
        "budget": 5.00
    },
]
