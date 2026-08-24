import datetime

# Trading parameters
MATCH_BUDGET      = 5.00
EDGE_MIN = 0.01   # entry edge required at the price extremes (raise to ~0.03 before going live: fees+spread)
EDGE_MAX = 0.07   # entry edge required at 50c; threshold = EDGE_MIN + (EDGE_MAX-EDGE_MIN)*sin(pi*price)
EDGE_MIN_UNDERDOG = 0.06  # floor on the entry edge for cheap markets (longshots are usually overpriced)
UNDERDOG_PRICE    = 0.37  # floor applies below this price; ~where the sin^2 curve meets EDGE_MIN_UNDERDOG, so the two join with no dip
TRAIL_SCALE_FRAC  = 0.35  # trail arm/giveback capped at this fraction of entry (fixes penny positions)
STOP_MAX_FRAC     = 0.50  # stop distance capped at this fraction of entry
DIVERGENCE_PAUSE      = 0.20  # pause entries when |model-market| EMA exceeds this
DIVERGENCE_RESUME     = 0.15  # resume entries when the EMA falls back below this
DIVERGENCE_EMA_ALPHA  = 0.05  # per-sim EMA weight (~15-20 min memory at one sim per point)
STOP_LOSS_PCT     = 0.15
STOP_LOSS_MIN_DOLLARS = 0.04  # stop distance is max(STOP_LOSS_PCT * entry, this floor)
TRAIL_ARM_DOLLARS      = 0.05  # arm trailing lock once price is this far above entry
TRAIL_GIVEBACK_DOLLARS = 0.05  # once armed, exit when price falls this far from its high
ENTRY_GAME_PROB_MIN = 0.30  # defer entry while our side's current-game win prob is below this
# Fragility filter: block an entry if a single lost game/set would drop our side's
# match win prob by more than this (avoids "short-gamma" entries — e.g. buying the
# server right before a possible break). Set high (e.g. 1.0) to disable.
MAX_ENTRY_GAME_DRAWDOWN = 0.15
MAX_ENTRY_SET_DRAWDOWN  = 0.35
REENTRY_GUARD_SECS  = 180   # after a trail exit, block same-side re-entry at >= exit price for this long
KELLY_FRACTION    = 1  #0.5 for half Kelly
COOLDOWN_SECONDS  = 300
FAST_POLL_SECS    = 1
MAX_MC_STALENESS_SECS = 120  # skip entries if sim older than this; forces re-sim heartbeat
SIM_RETRY_SECS    = 15   # min gap between retries after a failed sim (stats not ready yet)
MATCH_END_GRACE_SECS = 900  # exit the bot if the milestone stays not-live this long (survives breaks)
INIT_TIMEOUT_SECS    = 120  # exit if a match never initializes within this long (likely a bad ticker)
N_DRAWS           = 1000     # stat draws for the exact engine; each draw evaluated exactly
BP_PRESSURE       = 0.03      # subtract this from the server's point-win prob at break points (serving under pressure); 0 = off
DRY_RUN           = True

# Market-implied prior (live model): invert the pre-match Kalshi price into
# per-server point probs, then blend with in-match service counts.
MARKET_PRIOR_N = 40       # market-implied point probs worth this many virtual service points
INVERSION_BASE = 0.64     # assumed tour-average serve level; fixes the overall level in the inversion
GAME_THRESHOLDS = [16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 25.5, 26.5, 27.5, 30.5]  # log P(total match games > X) for each; changeable

# Kalshi API credentials
API_KEY     = "982c924c-9e22-44c8-a801-3be1ff50d45d"
KEY_FILE    = "Key1.txt"
KALSHI_BASE = "https://external-api.kalshi.com"

# Data logging
LOG_DIR = "data/logs"
# Each bot process writes its own CSVs, suffixed with its start time, so
# parallel/restarted runs never collide.
LOG_SUFFIX = datetime.datetime.now().strftime("_%Y%m%d_%H%M%S")

# Match configuration
# Each entry: event_ticker, and optional budget (dollars; defaults to MATCH_BUDGET).
# milestone_id, best_of, canonical ticker, and p1 identity are all resolved automatically.
MATCH_CONFIG = [
    {
        "event_ticker": "KXATPMATCH-26JUL31WONGEA",
        "budget": 5.00,
    },
    # {
    #     "event_ticker": "KXATPMATCH-26JUL30CERGEA",
    #     "budget": 5.00,
    # },
]
