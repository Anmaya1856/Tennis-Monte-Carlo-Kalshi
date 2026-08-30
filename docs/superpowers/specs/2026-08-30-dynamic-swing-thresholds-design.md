# Dynamic Swing Thresholds — Design Spec

**Date:** 2026-08-30  
**Status:** Approved

## Problem

`MIN_GAME_SWING` in `config.py` is a static dict keyed by `best_of` with fixed per-set values.
The notebook `simulation/game_swing_by_set.ipynb` shows the swing distribution shifts substantially
with `pA`/`pB` (the blended serve-win probabilities). A fixed threshold over-trades in low-volatility
matches and under-trades in high-volatility ones.

## Goal

Replace the static threshold with a lookup into a precomputed table keyed by `(pA, pB, best_of, set_num)`.
The threshold for a given game uses whatever `pa_blend`/`pb_blend` the DP engine has at that moment,
which means:

- Set 1 uses the pre-match prior (few service points accumulated).
- Set 2 uses the post-set-1 blend.
- Mid-match bot entry uses the current blend at join time.

No explicit "freeze at set boundary" logic is needed — it falls out naturally.

## Combined Policy

```
threshold = max(SWING_FLOOR, quantile(1 - KEEP_FRACTION))
```

- `SWING_FLOOR = 0.12` — hard minimum; no trade can clear the taker fee below this.
- `KEEP_FRACTION = 0.30` — retain the top 30% of on-serve games by swing.
- Both are config params so they can be changed without re-running precomputation.

## 1. DB Schema

File: `data/swing_thresholds.db`, table `thresholds`.

```sql
CREATE TABLE thresholds (
    pA       REAL NOT NULL,
    pB       REAL NOT NULL,
    best_of  INTEGER NOT NULL,
    set_num  INTEGER NOT NULL,
    keep_pct INTEGER NOT NULL,   -- 10, 15, 20, 25, 30, 35, 40, 45, 50
    threshold REAL NOT NULL,
    PRIMARY KEY (pA, pB, best_of, set_num, keep_pct)
);
```

Grid parameters:
- `pA`, `pB`: 0.55 to 0.75 inclusive, step 0.01 → 21 values each → 441 pairs
- `best_of`: 3, 5
- `set_num`: 1–3 (Bo3), 1–5 (Bo5)
- `keep_pct`: 10, 15, 20, 25, 30, 35, 40, 45, 50

Total rows: 441 × (3 + 5) × 9 ≈ 31,752.

The `threshold` column stores the raw quantile value (before flooring). The floor is applied at
lookup time so changing `SWING_FLOOR` does not require recomputing the DB.

## 2. Precompute Script

**New file:** `trade/precompute_swing_thresholds.py`

Runs offline, once. Idempotent (uses `INSERT OR REPLACE`).

Steps:
1. For each `(pA, pB)` grid pair × `best_of`:
   - Simulate 20,000 matches using the same MC engine as the notebook
     (`Engine` + the game-boundary record loop from `game_swing_by_set.ipynb`).
   - Filter to on-serve, non-tiebreak boundaries.
   - Group by `set_num`; for each set compute `swing.quantile(1 - keep_pct/100)`
     for all nine `keep_pct` levels.
   - Write rows to the DB.
2. Print progress (outer loop is ~882 (pA,pB,best_of) combos — estimated runtime ~30 min
   at 20k matches / ~7s per Bo3, ~21s per Bo5; can be reduced to 5k matches for a quick run).

The script imports `trade.exact` directly; no Kalshi connectivity required.

## 3. Lookup Module

**New file:** `trade/swing_thresholds.py`

```python
import sqlite3, os
import trade.config as cfg

_conn = None  # opened once at first call, read-only

def get_threshold(pa: float, pb: float, best_of: int, set_num: int) -> float:
    """Return the combined-policy threshold for this game state.

    Rounds pa/pb to the nearest grid point (0.01), clamps to [0.55, 0.75],
    looks up the configured KEEP_FRACTION quantile, and applies SWING_FLOOR.
    Falls back to a hardcoded default (0.15) if the DB is absent.
    """
    ...
```

- Connection opened once (module-level `_conn`), `check_same_thread=False` safe for read-only.
- Grid snapping: `round(pa * 100) / 100`, clamped to [0.55, 0.75].
- `keep_pct = round(cfg.KEEP_FRACTION * 100 / 5) * 5` — maps 0.30 → 30, etc.
- Returns `max(cfg.SWING_FLOOR, row["threshold"])`.
- If DB file missing or query fails: returns `max(cfg.SWING_FLOOR, 0.15)` and logs a warning once.

## 4. Config Changes

In `trade/config.py`:

**Remove:**
```python
MIN_GAME_SWING = { 3: [...], 5: [...] }
```

**Add:**
```python
KEEP_FRACTION        = 0.30   # retain top 30% of on-serve games by swing
SWING_FLOOR          = 0.12   # hard floor — below this the fee cannot be cleared
SWING_THRESHOLDS_DB  = "data/swing_thresholds.db"
```

## 5. Bot Integration

In `trade/trade_bot.py`, `_check_entry()`:

**Current signature:**
```python
def _check_entry(key, cached, kalshi_state, p1_px, p2_px):
```

**New signature:**
```python
def _check_entry(key, cached, kalshi_state, p1_px, p2_px, pa_blend, pb_blend):
```

`pa_blend` and `pb_blend` come from `sim["probs"]["pa_blend"]` / `sim["probs"]["pb_blend"]`,
which are already in scope in `_tick()` when `_check_entry` is called.

**Current threshold block (lines 535–539):**
```python
thresholds = cfg.MIN_GAME_SWING[cached["best_of"]]
min_swing = thresholds[min(set_num - 1, len(thresholds) - 1)]
```

**Replacement:**
```python
from trade.swing_thresholds import get_threshold
min_swing = get_threshold(pa_blend, pb_blend, cached["best_of"], set_num)
```

The `print` line that mentions `need {min_swing*100:.0f}pp` already handles the new value correctly.

No other files are touched.

## Files Changed

| File | Change |
|------|--------|
| `trade/config.py` | Remove `MIN_GAME_SWING`; add `KEEP_FRACTION`, `SWING_FLOOR`, `SWING_THRESHOLDS_DB` |
| `trade/swing_thresholds.py` | **New** — lookup function |
| `trade/precompute_swing_thresholds.py` | **New** — offline precompute script |
| `trade/trade_bot.py` | `_check_entry` signature + threshold lookup |
| `data/swing_thresholds.db` | **Generated** by precompute script (not checked into git) |

## Testing

1. Unit test `get_threshold`: mock DB → verify floor applied; verify grid snapping; verify missing-DB fallback.
2. Unit test `_check_entry`: pass known `pa_blend`/`pb_blend`; assert skip when `get_threshold` returns above swing; assert entry when below.
3. Precompute smoke test: run with 500 matches instead of 20k, verify DB has expected row count and no nulls.
4. Existing `tests/trade/test_decision.py` and `test_fills.py` must pass unchanged (they don't touch threshold logic).

## Open Questions / Non-Goals

- **Kelly sizing**: still not in scope; contracts remain fixed at `CONTRACTS_PER_TRADE`.
- **Bo5 coverage**: the grid covers Bo5 but Bo5 is not currently in `AUTO_LAUNCH_SERIES`; rows are precomputed anyway.
- **Grid extension**: if `pa_blend` ever falls outside [0.55, 0.75] (extreme match), the clamp applies silently. This is acceptable — serve win probs outside that range are rare at tour level.
