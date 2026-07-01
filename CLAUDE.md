# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Monte Carlo tennis match simulator for Kalshi betting. Runs 50,000+ simulations to estimate ATP match win probabilities, then blends results with ATP ranking points for betting signals.

## Behavioral guidelines
### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Folder Structure

```
Tennis Monte Carlo/
├── atp/
│   ├── scrapers/                  — all ATP scraper scripts (write to atp.db)
│   ├── data/                      — atp.db (single SQLite database, all tables)
│   └── retirement_check.ipynb     — data quality: validates retirement flags vs scores
├── kalshi/
│   ├── collect_candles.py         — downloads 1-min OHLC candles from Kalshi API → kalshi/data/kalshi_candles.db
│   ├── kalshi_data_collection.ipynb — market data collection notebook
│   ├── kalshi_atp_match_markets.csv — master list of ATP match markets
│   ├── data/2026/                 — candle CSVs (one per player per market, ~1,400 files)
│   └── analysis/                  — Kalshi Brier score notebooks and results
├── simulation/
│   ├── monte_carlo_basic.ipynb    — PRODUCTION: full Bayesian model, LR blend
│   ├── monte_carlo_basicV1.ipynb  — development history: 3 model variants with hardcoded stats
│   ├── sim_from_score.ipynb       — simulate from arbitrary mid-match state (manual stat input)
│   ├── live_atp_hawkeye_sim_from_score.ipynb — polls live ATP Hawkeye API, runs MC every 20s
│   ├── logistic_blend.ipynb       — trains logistic regression blend (MC + ranking points)
│   ├── set1_winner_analysis.ipynb — post-set-1 prediction backtest
│   ├── upset_analysis.ipynb       — upset probability analysis
│   ├── points_per_set_analysis.ipynb — set-by-set metrics analysis
│   ├── set_stat_drift.ipynb       — temporal stat variation analysis
│   └── set_stats_analysis.ipynb   — set stat summary
└── apt_live_match_example.json    — sample Hawkeye API response (used by live sim notebook)
```

## Running Code

**Match prediction (production):** Open `simulation/monte_carlo_basic.ipynb` in Jupyter and run all cells. Set `P1_NAME`, `P2_NAME`, `COURT_TYPE`, and `YEAR` in the `INPUTS` cell. Loads stats from `atp/data/atp.db`.

**Mid-match prediction (manual):** Open `simulation/sim_from_score.ipynb`. Set player stats (serve/return percentages), `SCORE_STRING` (e.g. `"1-6 6-4 4-6 7-6 0-0"`), `GAME_SCORE`, `P1_SERVES_NEXT`, and `BEST_OF`. Outputs win probability, current set probability, and scoreline breakdown. No database or API access — stats are hardcoded per use.

**Live match tracking:** Open `simulation/live_atp_hawkeye_sim_from_score.ipynb`. Set Hawkeye API URLs in the `CONFIG` cell. Polls live match JSON every 20s, extracts in-match stats and score state, runs 5,000 MC sims per poll. Auto-stops when match finishes. Requires `curl_cffi` for Cloudflare bypass.

**Match prediction (exploration):** `simulation/monte_carlo_basicV1.ipynb` contains three progressively complex implementations with hardcoded stats — useful for understanding or testing model variants.

**Full rebuild from scratch (only if atp.db is lost) — run in order:**
```
cd atp/scrapers
python player_top500.py        # → players table in atp.db  (requires CF token)
python tournaments.py          # → tournaments table in atp.db  (requires CF token)
python match_scores_scraper.py # → matches, match_stats, set_stats, match_ytd_stats in atp.db  (requires CF token)
python player_rankings_and_ratings.py  # → player_rankings table in atp.db  (no CF token needed, 20 parallel workers)
python player_stats.py         # → player_stats table in atp.db  (requires CF token, 20 parallel workers)
```
All scripts support resumption — safe to interrupt and re-run; already-scraped records are skipped.

**Kalshi candle data:** `python kalshi/collect_candles.py` from repo root. Downloads 1-min OHLC candles for KXATPMATCH markets into `kalshi/data/kalshi_candles.db`. Resume-safe, handles 429 rate limiting with auto-retry.

No build step. Dependencies: `numpy`, `pandas`, `requests`, `lxml`, `tqdm`, `curl_cffi`, `sklearn` (for logistic blend training only).

## Architecture

### Simulation Engine (notebooks)

The `BetaModel` class models each probability (serve %, break point rate, etc.) using a Bayesian beta distribution with two components:

- **Fixed prior** — anchored to career stats (alpha/beta conjugate pair); `prior_strength` controls how many virtual observations this is worth
- **In-match component** — exponentially decayed live observations with decay factor `lam` (0.90–0.95)

During warmup (< `int(1 / (1 - lam))` observations), the model returns pure career stats. After warmup it blends: `(alpha_prior + alpha_match) / total`.

Simulation is hierarchical: **point → game → tiebreak → set → match**.

At the point level, win probability blends server and receiver perspectives:
- First serve: `avg(server_1st_win, 1 - receiver_1st_return)`
- Second serve: `avg(server_2nd_win, 1 - receiver_2nd_return)`

Break point detection (`is_break_point`) triggers a dedicated `bp_save_model` / `bp_convert_model` override at critical moments. Tiebreaks bypass break point logic by passing an impossible score to `sim_point`.

**Important:** Break point models use actual career counts (`bp_save_faced`, `bp_convert_opps`) as prior strength — not the tunable `prior_strength` hyperparameter, which only applies to serve/return models.

**Stat loading fallback:** `load_player_stats()` tries `surface/year` → `surface/all` → `all/all`. The `YEAR` input uses `YEAR - 1` stats (e.g., `YEAR=2026` loads 2025 data).

**Betting signal:** Logistic regression trained in `logistic_blend.ipynb`: `sigmoid(-1.7396 + 3.4792 * MC_prob + 0.3129 * log(pts1/pts2))`. Trained on 2023–2025 mirrored data, tested on 2026. Replaces the old hardcoded `BLEND_W = 0.5` linear blend.

### Mid-match simulation (`sim_from_score.ipynb`)

Simplified model for simulating from any match state. No `BetaModel` or break point logic — just raw serve/return percentages. Accepts a score string (e.g. `"6-1 2-3"`), game score in tennis notation (`"40-15"`) or tiebreak counts (`"3-2"`), and who serves next. Simulates point → game → tiebreak → set → match from that state. Outputs win probability with SE/CI, current set win probability, and final scoreline distribution.

### Live match polling (`live_atp_hawkeye_sim_from_score.ipynb`)

Polls the ATP Hawkeye API (`https://www.atptour.com/-/Hawkeye/MatchStats/{year}/{event_id}/{match_code}`) every 20s. Extracts player names, in-match serve/return stats, set/game/point scores, and server identity from the JSON response. Feeds extracted state into the same sim engine as `sim_from_score.ipynb`. Tracks multiple matches simultaneously; auto-removes finished matches (status != "P"). Uses `curl_cffi` with Chrome impersonation for Cloudflare bypass.

### Web Scraping (`atp/scrapers/`)

`player_top500.py`, `tournaments.py`, `player_stats.py`, and `match_scores_scraper.py` require a `CF_CLEARANCE` cookie + matching `User-Agent` header to bypass Cloudflare. **The token is hardcoded at the top of each script** — update it in source when it expires. Refresh via Chrome DevTools → Application → Cookies → `cf_clearance`. `player_rankings_and_ratings.py` hits a public JSON endpoint and does not need the cookie. Scripts that do parallel fetching use `concurrent.futures.ThreadPoolExecutor` (20 workers).

## Key Data Files

All generated data lives in `atp/data/atp.db` (single SQLite database).

| Table | Contents |
|-------|----------|
| `players` | player_id, first_name, last_name, player_url |
| `tournaments` | tourney_year_id, tourney_type, tourney_name, tourney_id, tourney_location, start_date, end_date, tourney_url |
| `matches` | year, event_id, match_code, tourney_year_id, **match_date** (absolute date), round, p1_id, p2_id, p1_seed, p2_seed, winner_id, p1_score, p2_score, match_url, surface, duration_minutes, court_name, number_of_sets, match_status, reason, is_qualifier |
| `match_stats` | (year, event_id, match_code, player_id) — per-player aggregated serve/return/BP stats for the full match |
| `set_stats` | (year, event_id, match_code, player_id, set_number) — same stat columns as match_stats, per set |
| `match_ytd_stats` | (year, event_id, match_code, player_id) — YTD aggregate stats at time of match |
| `player_rankings` | (player_id, rank_date) — weekly ATP roll_rank, roll_points, race_rank, race_points |
| `player_stats` | (player_id, year, surface) — career serve/return/BP percentages; key columns: FirstServePercentage, FirstServePointsWonPercentage, SecondServePointsWonPercentage, FirstServeReturnPointsWonPercentage, SecondServeReturnPointsWonPercentage, BreakPointsFaced, BreakPointsSavedPercentage, BreakPointsOpportunities, BreakPointsConvertedPercentage |

Kalshi data lives in `kalshi/data/kalshi_candles.db` (separate database).

**Note:** `matches` stores `match_date` as an absolute date. `p1_id`/`p2_id` are inline in `matches` (no join table).

### Kalshi Data (`kalshi/`)

`collect_candles.py` downloads 1-minute OHLC candle data from the Kalshi API for KXATPMATCH markets. Handles schema differences between live and historical endpoints. Outputs to `kalshi/data/kalshi_candles.db` (separate from `atp.db`). Candle CSVs in `kalshi/data/2026/` are per-player-per-market snapshots. `kalshi_data_collection.ipynb` is the original collection notebook. Analysis notebooks in `kalshi/analysis/` compute Brier scores comparing Kalshi market prices vs actual outcomes.

## Hyperparameter Tuning

- **`prior_strength`** (50–200): Higher = more stable, slower momentum response. Lower = more reactive to in-match events.
- **`lam`** (0.90–0.95): Exponential decay per observation. Lower = faster decay, stronger momentum effect.
- **`N`** (simulations): 50,000 default; increase to 100,000 to narrow 95% CI.

The blend weights are no longer tunable — they are set by logistic regression coefficients trained in `logistic_blend.ipynb`.

## Common Issues

- **Player not found:** Check exact name spelling in `player_stats` table in `atp/data/atp.db`; try `surface="all"` if specific court type is missing.
- **Wide confidence intervals:** Increase `N` or `prior_strength`.
- **Cloudflare 403:** Refresh `CF_CLEARANCE` token from browser and update it at the top of the relevant scraper script in `atp/scrapers/`.
- **Stale stats:** Re-run `player_stats.py` to refresh the `player_stats` table in `atp.db`; year-old data misses form changes.