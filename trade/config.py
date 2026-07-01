import os

# Trading parameters
MATCH_BUDGET      = 5.00
EDGE_THRESHOLD           = 0.08
CONTESTED_EDGE_THRESHOLD = 0.13
STOP_LOSS_PCT     = 0.25
KELLY_FRACTION    = 1 #0.5 for half Kelly
COOLDOWN_SECONDS  = 60
SLOW_POLL_SECS    = 20
FAST_POLL_SECS    = 1
N_SIMS            = 10_000
DRY_RUN           = True

# Kalshi API credentials
API_KEY     = "982c924c-9e22-44c8-a801-3be1ff50d45d"
KEY_FILE    = "Key1.txt"
KALSHI_BASE = "https://external-api.kalshi.com"

# Data logging
LOG_DIR = "data/logs"

# Match configuration
# Each entry: hawkeye_url, event_ticker
# milestone_id, canonical ticker, and p1 identity are all resolved automatically.
MATCH_CONFIG = [
    {
        "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms019",
        "event_ticker": "KXATPCHALLENGERMATCH-26JUN30BARFIC",
    },
    {
        "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms031",
        "event_ticker": "KXATPCHALLENGERMATCH-26JUN30SURDRA",
    },
    {
        "hawkeye_url":  "https://www.atptour.com/-/Hawkeye/MatchStats/2026/7316/ms030",
        "event_ticker": "KXATPCHALLENGERMATCH-26JUN30SUNSHI",
    },
]
