# Tennis Monte Carlo — Kalshi Betting Simulator

Monte Carlo match simulator for ATP tennis. Runs 50,000+ simulations per match to estimate win probabilities using Bayesian models of serve and return performance, then blends the result with ATP ranking signals for use as Kalshi betting signals.

---

## Folder Structure

```
Tennis Monte Carlo/
├── atp/
│   ├── scrapers/       — all ATP data collection scripts
│   └── data/           — generated CSVs and SQLite databases
├── kalshi/
│   ├── data/2026/      — Kalshi candle data (one CSV per market)
│   └── analysis/       — Kalshi Brier score notebooks and results
└── simulation/         — Monte Carlo engine notebooks and backtest results
```

---

## Data Pipeline

### One-time migration (already run — skip unless rebuilding tennis.db from scratch)

```
cd atp/scrapers
python backfill_rankings.py
```

Drops and recreates the `player_rankings` table in `tennis.db` with full historical weekly ATP ranking data for all 500 players. Hits `https://www.atptour.com/en/-/www/rank/history/{player_id}?v=1` — no CF cookie required. ~500 API calls, runs in under a minute.

**Schema:** `player_id`, `rank_date` (YYYY-MM-DD), `roll_rank`, `roll_points`, `race_rank`, `race_points`. ~211k rows total.

---

### Weekly Incremental Update (preferred)

```
cd atp/scrapers
python update_db.py --cf <CF_CLEARANCE_TOKEN>
```

Updates `tennis.db` and `staging.db` directly with only new data — no CSV intermediaries, no re-fetching historical data. Typical cost: ~5,600 API calls vs ~23,000+ for a full rebuild.

**What it does per run:**
1. Scrapes ATP rankings page → upserts any new players; fetches rank history delta for all 500 players
2. Re-fetches career stats for `year='all'` and `year=CURRENT_YEAR` for all players (5,000 calls — these accumulate throughout the season)
3. Populates `tourney_slug` on any tournaments missing it (no HTTP — derived from existing DB/CSV data)
4. Scrapes results pages for recently active tournaments → discovers new match codes
5. Fetches Hawkeye stats for new matches only → `staging.db`
6. Loads new staging rows into normalized `tennis.db` tables

**Options:**
- `--dry-run`: print what would be fetched without making any HTTP calls or DB writes
- `--year 2026`: defaults to current year
- `--future-window 30`: skip tournaments starting more than N days from today
- `--past-window 60`: skip tournaments whose start_date is more than N days in the past
- `--workers 10`: parallel workers for Hawkeye fetches

**CF token:** Pass via `--cf TOKEN`, or set `CF_CLEARANCE` env var. Refresh from Chrome DevTools → Application → Cookies → cf_clearance (see [Cloudflare section](#cloudflare--cookies)).

---

### Full Rebuild from Scratch

Only needed if `tennis.db` is lost. Run these steps in order to rebuild the full dataset (~23,000+ API calls).

### Step 1 — Player Rankings

```
cd atp/scrapers
python player_rankings.py
```

**What it does:** Scrapes the ATP top-500 singles rankings page.  
**Output:** `atp/data/player_rankings_YYYY-MM-DD.csv`  
**Columns:** `ranking`, `ranking_points`, `full_name`, `first_name`, `last_name`, `player_url`, `player_id`, `scrape_date`  
**Notes:** Single-page scrape, runs in seconds. Requires a fresh `CF_CLEARANCE` cookie (see [Cloudflare section](#cloudflare--cookies) below).

---

### Step 2 — Player Stats

```
cd atp/scrapers
python player_stats.py
```

**What it does:** For every player in the most recent `player_rankings_*.csv`, fetches serve/return/break-point stats across 5 years × 5 surfaces = 25 combinations per player (~100k API calls total).  
**Requires:** Step 1 output (auto-detected by glob).  
**Output:** `atp/data/player_stats_YYYY-MM-DD.csv`  
**Columns:** `player_id`, `player_slug`, `full_name`, `year`, `surface`, then 18 stat columns (Aces, DoubleFaults, FirstServePercentage, FirstServePointsWonPercentage, SecondServePointsWonPercentage, BreakPointsFaced, BreakPointsSavedPercentage, BreakPointsOpportunities, BreakPointsConvertedPercentage, and others).  
**Notes:**
- Uses 20 parallel workers via `ThreadPoolExecutor` — runs in several minutes.
- **Resume-safe:** if interrupted, re-running skips already-completed player/year/surface combos in today's output file.
- Use `player_stats_no_concurrency.py` as a fallback if Cloudflare starts blocking parallel requests.

---

### Step 3 — Tournaments

```
cd atp/scrapers
python tournaments.py
```

**What it does:** Scrapes tournament schedule and metadata for a configured year range.  
**Output:** `atp/data/tournaments_YYYY-YYYY.csv`  
**Columns:** `tourney_year_id`, `tourney_order`, `tourney_type`, `tourney_name`, `tourney_id`, `tourney_slug`, `tourney_location`, `tourney_date`, `year`, `tourney_start_day/month/year`, `tourney_end_day/month/year`, `tourney_surface`, `tourney_url_suffix`  
**Notes:** United Cup is skipped. Requires `CF_CLEARANCE`.

---

### Step 4 — Match Scores

```
cd atp/scrapers
python match_scores_scraper.py
```

**What it does:** For each tournament in the tournaments CSV, scrapes every match result page.  
**Requires:** Step 3 output. The script prompts for the path; default is `../data/tournaments_2023-2026.csv`.  
**Output:** `atp/data/match_scores_YYYY-YYYY.csv` (prompted; default `../data/match_scores_2023-2026.csv`)  
**Columns:** `tourney_year_id`, `tourney_name`, `tourney_type`, `year`, `match_header`, `match_link`, `p1_name`, `p1_id`, `p1_rank`, `p2_name`, `p2_id`, `p2_rank`, `winner_name`, `winner_id`, `p1_score`, `p2_score`  
**Notes:**
- **Idempotent by tournament:** already-scraped `tourney_year_id` values are skipped on re-runs.
- Set scores are stored as semicolon-separated strings (e.g. `6;4;7`). See [Score Parsing Quirk](#score-parsing-quirk) below.

---

### Step 5 — Hawkeye Match Stats

```
cd atp/scrapers
python fetch_match_stats.py
# or with explicit args:
python fetch_match_stats.py --db ../data/staging.db --csv ../data/match_scores_2023-2026.csv --workers 15
```

**What it does:** Calls the ATP Hawkeye API for point-by-point stats for every match in the scores CSV.  
**Requires:** Step 4 output.  
**Output:** `atp/data/staging.db` — SQLite table `raw_match_stats` with columns `year`, `event_id`, `match_code`, `raw_json`, `status` (`ok`/`error`/`no_data`), `http_code`, `fetched_at`.  
**Notes:**
- **Idempotent:** skips matches with `status='ok'`; re-fetches `error`/`no_data` on each run.
- Stops automatically after 5 consecutive 403 responses to avoid getting rate-limited hard. Restart after refreshing the cookie.
- No `CF_CLEARANCE` required for this endpoint (Hawkeye API is less aggressively protected), but the standard `User-Agent` header is still set.

---

### Step 6 — Load to DB

```
cd atp/scrapers
python load_to_db.py
# or with explicit args:
python load_to_db.py --db ../data/tennis.db --staging ../data/staging.db
```

**What it does:** Consolidates all CSVs and `staging.db` into a normalised SQLite database.  
**Requires:** Steps 1–5 outputs.  
**Output:** `atp/data/tennis.db`  
**Tables:** `players`, `player_rankings`, `player_career_stats`, `tournaments`, `matches`, `match_players`, `match_stats`, `set_stats`, `match_ytd_stats`  
**Notes:** Only inserts matches where **both** players exist in the `players` table (populated from the rankings CSV). The `player_rankings` table is managed separately by `backfill_rankings.py` (not `load_to_db.py`).

---

## Running Predictions

Open `simulation/monte_carlo_basic.ipynb` in Jupyter and run all cells.

Set the four inputs at the top of the `INPUTS` cell:

```python
P1_NAME    = "Carlos Alcaraz"
P2_NAME    = "Jannik Sinner"
COURT_TYPE = "Clay"   # "Hard", "Clay", "Grass", "Carpet", or "all"
YEAR       = 2026     # loads YEAR-1 stats (i.e. 2025 data)
```

The notebook auto-loads the most recent `player_stats_*.csv` and `player_rankings_*.csv` from `atp/data/`, runs 50,000 simulations, and outputs:

- Monte Carlo win probability ± standard error + 95% CI for each player
- ATP 52-week rolling ranking points for each player
- Final blended probability via logistic regression: `sigmoid(-1.7396 + 3.4792×MC_prob + 0.3129×log(pts1/pts2))`

### Notebook Guide

| Notebook | Purpose |
|----------|---------|
| `simulation/monte_carlo_basic.ipynb` | **Production.** Reads live CSVs, full Bayesian model with break-point detection, logistic regression blend. Use this for real predictions. |
| `simulation/logistic_blend.ipynb` | Trains and evaluates the logistic regression that combines MC probability with ATP ranking points. Train 2023–2025, test 2026. |
| `simulation/monte_carlo_basicV1.ipynb` | **Development history.** Hardcoded Sinner/Djokovic stats. Contains three progressively complex model variants: (1) basic serve/return, (2) + momentum decay (BetaModel), (3) + break-point modeling. Useful for understanding the model architecture; not for real predictions. |
| `simulation/brier_backtest.ipynb` | Backtests Monte Carlo predictions against historical results using Brier scores. |
| `simulation/set1_brier.ipynb` | Post-set-1 backtest: uses set-1 stats as prior, starts simulation from 1-0/0-1, applies LR blend. Best model: Brier 0.1408 (skill 43.7%). |
| `kalshi/analysis/kalshi_brier.ipynb` | Brier score analysis of Kalshi market prices vs actual outcomes. |
| `kalshi/analysis/kalshi_inplay_brier.ipynb` | Same analysis using in-play (live) Kalshi candle data. |

---

## Cloudflare & Cookies

atptour.com is protected by Cloudflare. All scraping scripts (`player_rankings.py`, `player_stats.py`, `tournaments.py`, `match_scores_scraper.py`) bypass it using a browser session cookie.

**Where the token lives:** Top of each scraper file, two variables:
```python
CF_CLEARANCE = 'paste-token-here'
USER_AGENT   = 'Mozilla/5.0 ...'   # must match the browser you used
```

**How to refresh:**
1. Open `https://www.atptour.com` in Chrome and wait for the page to load.
2. Open DevTools (F12) → Application tab → Cookies → `https://www.atptour.com`.
3. Find `cf_clearance` and copy its value.
4. Also copy the User-Agent from DevTools → Network → any request → Request Headers → `user-agent`.
5. Paste both into the scraper you're about to run.

**Important:** The `CF_CLEARANCE` value and `USER_AGENT` must come from the **same browser session**. Mixing them breaks authentication. Tokens typically last 30 minutes to a few hours.

---

## Rate Limiting

- `player_stats.py` uses 20 parallel workers. If Cloudflare starts returning 403s in bulk, switch to `player_stats_no_concurrency.py` and refresh the cookie first.
- `fetch_match_stats.py` automatically stops after **5 consecutive 403 responses** (`CONSECUTIVE_403_LIMIT = 5`). Refresh the cookie and restart to continue; already-fetched matches are skipped.

---

## Score Parsing Quirk

On ATP match result pages, each set score has two `<span>` elements: the main score and (optionally) a tiebreak superscript. The scraper takes only the **first span** per set to avoid duplicating or misaligning scores. Set scores are joined with `;` so `p1_score = "6;7;6"` means the player won 6, 7, and 6 games in each set.

---

## Stat Loading Fallback

`load_player_stats()` in the simulation notebook tries stats in this order:

1. `surface` / `YEAR-1` — e.g. Clay/2025
2. `surface` / `all` — e.g. Clay/all-years
3. `all` / `all` — career totals across all surfaces and years

A warning is printed if it falls back. If all three fail, the player name is likely misspelled or not in the rankings CSV (check exact spelling with `player_stats_*.csv`).

---

## Hyperparameters

| Parameter | Range | Effect |
|-----------|-------|--------|
| `prior_strength` | 50–200 | Higher = more stable, slower to respond to in-match momentum. Lower = more reactive. |
| `lam` | 0.90–0.95 | Exponential decay per observation. Lower = faster decay, stronger momentum effect. |
| `N` | 50,000+ | Simulations per run. Double to 100,000 to roughly halve the 95% CI width. |

The blend weight is no longer a tunable hyperparameter — it is set by the logistic regression coefficients trained in `simulation/logistic_blend.ipynb`.

Break-point models use the player's actual career `bp_save_faced` / `bp_convert_opps` counts as their prior strength (not the tunable `prior_strength`), since those events are rare and career counts are naturally informative.

---

## Common Issues

**Player not found**  
Check the exact spelling in `atp/data/player_stats_*.csv`. Names come from ATP URL slugs (e.g. `"Carlos Alcaraz"` not `"Alcaraz"`). Try `surface="all"` if a specific surface is missing.

**Wide confidence intervals**  
Increase `N` (simulations) or `prior_strength`. A 95% CI wider than ±5% usually means `N < 10,000` or very sparse career stats.

**Cloudflare 403**  
Refresh `CF_CLEARANCE` from Chrome (see above) and update it at the top of the relevant scraper. If parallel requests trigger it, switch to `player_stats_no_concurrency.py`.

**Stale stats**  
Always use the most recent `player_stats_*.csv`. Year-old data misses current form. Re-run Steps 1–2 before major tournaments.

**`staging.db` missing**  
`load_to_db.py` needs `fetch_match_stats.py` to have run first. If you only need rankings/stats for the simulation notebook, you can skip Steps 3–6 entirely.
