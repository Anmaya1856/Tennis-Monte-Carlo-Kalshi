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
│   ├── scrapers/       — all ATP scraper scripts
│   └── data/           — CSVs and SQLite databases (player_stats, player_rankings, match_scores, tournaments, tennis.db)
├── kalshi/
│   ├── data/2026/      — Kalshi candle CSVs (one per market); add 2027/ etc. for future years
│   └── analysis/       — Kalshi Brier score notebooks and results
└── simulation/         — Monte Carlo engine notebooks and backtest results
```

## Running Code

**Match prediction (production):** Open `simulation/monte_carlo_basic.ipynb` in Jupyter and run all cells. Set `P1_NAME`, `P2_NAME`, `COURT_TYPE`, and `YEAR` in the `INPUTS` cell. The notebook auto-loads stats from the most recent `atp/data/player_stats_*.csv` and blends with rankings.

**Match prediction (exploration):** `simulation/monte_carlo_basicV1.ipynb` contains three progressively complex implementations with hardcoded stats — useful for understanding or testing model variants without CSV data.

**Update player stats (required periodically) — run in order:**
```
cd atp/scrapers
python player_rankings.py     # → atp/data/player_rankings_YYYY-MM-DD.csv
python player_stats.py        # → atp/data/player_stats_YYYY-MM-DD.csv  (~100k API calls, 20 parallel workers)
python tournaments.py         # → atp/data/tournaments_YYYY-YYYY.csv
python match_scores_scraper.py  # prompts for paths; defaults to atp/data/
python fetch_match_stats.py   # → atp/data/staging.db  (Hawkeye API)
python load_to_db.py          # → atp/data/tennis.db
```

No build step. Dependencies: `numpy`, `pandas`, `requests`, `lxml`, `tqdm`.

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

**Betting signal:** `Final_P = BLEND_W * monte_carlo_P + (1 - BLEND_W) * ranking_P`, where `ranking_P = points_A / (points_A + points_B)` and `BLEND_W = 0.5` by default.

### Web Scraping (`atp/scrapers/`)

All scripts use `CF_CLEARANCE` cookie + matching `User-Agent` header to bypass Cloudflare. **The token is hardcoded at the top of each script** (`player_stats.py`, `match_scores_scraper.py`) — update it in source when it expires. Refresh via Chrome DevTools → Application → Cookies → `cf_clearance`. Scripts use `concurrent.futures.ThreadPoolExecutor` (20 workers) for parallel requests.

## Key Data Files

All generated data lives in `atp/data/`.

| File | Contents |
|------|----------|
| `player_stats_YYYY-MM-DD.csv` | Per-player serve/return/break-point stats by surface and year |
| `player_rankings_YYYY-MM-DD.csv` | Current ATP ranking and ranking points |
| `match_scores_2023-2026.csv` | Historical match results with player IDs, ranks, scores |
| `tournaments_2023-2026.csv` | Tournament metadata including surface |
| `tennis.db` | Normalised SQLite DB (players, rankings, career stats, matches, set stats) |
| `staging.db` | Raw Hawkeye API JSON responses (intermediate; consumed by `load_to_db.py`) |

Player stats CSV key columns: `FirstServePercentage`, `FirstServePointsWonPercentage`, `SecondServePointsWonPercentage`, `FirstServeReturnPointsWonPercentage`, `SecondServeReturnPointsWonPercentage`, `BreakPointsFaced`, `BreakPointsSavedPercentage`, `BreakPointsOpportunities`, `BreakPointsConvertedPercentage`.

## Hyperparameter Tuning

- **`prior_strength`** (50–200): Higher = more stable, slower momentum response. Lower = more reactive to in-match events.
- **`lam`** (0.90–0.95): Exponential decay per observation. Lower = faster decay, stronger momentum effect.
- **`BLEND_W`** (0.0–1.0): Weight on Monte Carlo vs. ATP ranking signal.
- **`N`** (simulations): 50,000 default; increase to 100,000 to narrow 95% CI.

## Common Issues

- **Player not found:** Check exact name spelling in `atp/data/player_stats_*.csv`; try `surface="all"` if specific court type is missing.
- **Wide confidence intervals:** Increase `N` or `prior_strength`.
- **Cloudflare 403:** Refresh `CF_CLEARANCE` token from browser and update it at the top of the relevant scraper script in `atp/scrapers/`.
- **Stale stats:** Always use the most recent `atp/data/player_stats_*.csv`; year-old data misses form changes.