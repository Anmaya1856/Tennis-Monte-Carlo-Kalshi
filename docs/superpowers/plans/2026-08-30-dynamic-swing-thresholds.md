# Dynamic Swing Thresholds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `MIN_GAME_SWING` config dict with a precomputed SQLite lookup table keyed by `(pA, pB, best_of, set_num)`, so the entry threshold adapts to each match's actual serve levels.

**Architecture:** A one-time offline script (`trade/precompute_swing_thresholds.py`) runs the Monte Carlo engine over a 21×21 grid of (pA, pB) pairs and writes quantile rows to `data/swing_thresholds.db`. A small lookup module (`trade/swing_thresholds.py`) snaps the live blended probs to the nearest grid point and returns `max(SWING_FLOOR, quantile)`. `_check_entry` in `trade_bot.py` gets two new args (`pa_blend`, `pb_blend`) and calls the lookup instead of reading config.

**Tech Stack:** Python 3, sqlite3 (stdlib), numpy (already in project), `trade.exact` DP engine

**Spec:** `docs/superpowers/specs/2026-08-30-dynamic-swing-thresholds-design.md`

## Global Constraints

- Python stdlib only for the DB layer — no SQLAlchemy, no ORM.
- No new third-party dependencies.
- `DRY_RUN = True` is the project default; tests must not require Kalshi API access.
- `trade.exact` is the only simulation engine to use — do not reimplement point/game/tiebreak math.
- All monetary values are in dollars. Swing values are probabilities (0–1), not percentages.
- Grid: pA/pB in [0.55, 0.75] step 0.01 — 21 values each. `keep_pct` stored for 10, 15, 20, 25, 30, 35, 40, 45, 50.

---

### Task 1: Lookup Module

**Files:**
- Create: `trade/swing_thresholds.py`
- Create: `tests/trade/test_swing_thresholds.py`

**Interfaces:**
- Produces: `get_threshold(pa: float, pb: float, best_of: int, set_num: int) -> float`
  - Reads `cfg.KEEP_FRACTION` (float 0–1), `cfg.SWING_FLOOR` (float), `cfg.SWING_THRESHOLDS_DB` (str path)
  - Returns `max(cfg.SWING_FLOOR, raw_quantile)`, or `max(cfg.SWING_FLOOR, 0.15)` if DB missing/row missing

- [ ] **Step 1: Write the failing tests**

Create `tests/trade/test_swing_thresholds.py`:

```python
import sqlite3, pytest
import trade.config as cfg
import trade.swing_thresholds as m


def _make_db(path):
    """Minimal DB with rows for pA=0.64, pB=0.64, Bo3, sets 1-3, keep_pct=30."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE thresholds (
            pA REAL, pB REAL, best_of INTEGER, set_num INTEGER,
            keep_pct INTEGER, threshold REAL,
            PRIMARY KEY (pA, pB, best_of, set_num, keep_pct)
        )
    """)
    conn.executemany("INSERT INTO thresholds VALUES (?,?,?,?,?,?)", [
        (0.64, 0.64, 3, 1, 30, 0.165),
        (0.64, 0.64, 3, 2, 30, 0.165),
        (0.64, 0.64, 3, 3, 30, 0.340),
    ])
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def reset_module():
    """Reset module-level cached connection and warned flag between tests."""
    m._conn = None
    m._warned = False
    yield
    if m._conn:
        m._conn.close()
    m._conn = None
    m._warned = False


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "swing_thresholds.db")
    _make_db(path)
    monkeypatch.setattr(cfg, "SWING_THRESHOLDS_DB", path)
    monkeypatch.setattr(cfg, "KEEP_FRACTION", 0.30)
    monkeypatch.setattr(cfg, "SWING_FLOOR", 0.12)
    return path


def test_basic_lookup(db):
    # set 1: raw 0.165, floor 0.12 → returns 0.165
    assert m.get_threshold(0.64, 0.64, 3, 1) == pytest.approx(0.165)


def test_floor_applied(db, monkeypatch):
    # raw threshold for set 2 is 0.165; raise floor above it
    monkeypatch.setattr(cfg, "SWING_FLOOR", 0.20)
    assert m.get_threshold(0.64, 0.64, 3, 2) == pytest.approx(0.20)


def test_set3_above_floor(db):
    assert m.get_threshold(0.64, 0.64, 3, 3) == pytest.approx(0.340)


def test_grid_snapping_rounds_to_nearest(db):
    # 0.638 and 0.641 both round to 0.64
    assert m.get_threshold(0.638, 0.641, 3, 1) == pytest.approx(0.165)
    assert m.get_threshold(0.641, 0.638, 3, 1) == pytest.approx(0.165)


def test_out_of_range_pa_clamps_to_grid(db, monkeypatch):
    # Values outside [0.55, 0.75] clamp to the nearest grid edge.
    # DB only has 0.64 rows; 0.50 clamps to 0.55 which has no row → fallback.
    monkeypatch.setattr(cfg, "SWING_FLOOR", 0.12)
    result = m.get_threshold(0.50, 0.64, 3, 1)
    assert result == pytest.approx(max(0.12, 0.15))   # fallback value


def test_missing_db_returns_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "SWING_THRESHOLDS_DB", str(tmp_path / "nope.db"))
    monkeypatch.setattr(cfg, "SWING_FLOOR", 0.12)
    result = m.get_threshold(0.64, 0.64, 3, 1)
    assert result == pytest.approx(max(0.12, 0.15))


def test_keep_fraction_selects_correct_column(tmp_path, monkeypatch):
    path = str(tmp_path / "swing_thresholds.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE thresholds (
            pA REAL, pB REAL, best_of INTEGER, set_num INTEGER,
            keep_pct INTEGER, threshold REAL,
            PRIMARY KEY (pA, pB, best_of, set_num, keep_pct)
        )
    """)
    conn.executemany("INSERT INTO thresholds VALUES (?,?,?,?,?,?)", [
        (0.64, 0.64, 3, 1, 25, 0.195),
        (0.64, 0.64, 3, 1, 30, 0.165),
        (0.64, 0.64, 3, 1, 35, 0.144),
    ])
    conn.commit(); conn.close()
    monkeypatch.setattr(cfg, "SWING_THRESHOLDS_DB", path)
    monkeypatch.setattr(cfg, "SWING_FLOOR", 0.12)

    monkeypatch.setattr(cfg, "KEEP_FRACTION", 0.25)
    assert m.get_threshold(0.64, 0.64, 3, 1) == pytest.approx(0.195)

    m._conn = None   # force reconnect so new KEEP_FRACTION is used
    monkeypatch.setattr(cfg, "KEEP_FRACTION", 0.35)
    assert m.get_threshold(0.64, 0.64, 3, 1) == pytest.approx(0.144)
```

- [ ] **Step 2: Run tests — confirm they all fail**

```
cd "D:\CMU\Kalshi\Tennis Monte Carlo"
python -m pytest tests/trade/test_swing_thresholds.py -v
```
Expected: collection error or `ModuleNotFoundError: No module named 'trade.swing_thresholds'`

- [ ] **Step 3: Implement `trade/swing_thresholds.py`**

```python
"""Lookup table for per-game swing thresholds.

Reads a precomputed SQLite DB (see trade/precompute_swing_thresholds.py) and
returns the combined-policy threshold:  max(SWING_FLOOR, quantile(1-KEEP_FRACTION)).
Falls back to max(SWING_FLOOR, 0.15) if the DB is missing or the row is absent.
"""
import logging, os, sqlite3
import trade.config as cfg

_conn = None
_warned = False

_GRID_LO   = 0.55
_GRID_HI   = 0.75
_GRID_STEP = 0.01
_FALLBACK  = 0.15


def _connection():
    global _conn
    if _conn is not None:
        return _conn
    path = cfg.SWING_THRESHOLDS_DB
    if not os.path.exists(path):
        return None
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    return _conn


def _snap(v):
    """Round to nearest grid point and clamp to [GRID_LO, GRID_HI]."""
    snapped = round(round(v / _GRID_STEP) * _GRID_STEP, 2)
    return max(_GRID_LO, min(_GRID_HI, snapped))


def _keep_pct():
    """Map cfg.KEEP_FRACTION to the nearest stored keep_pct integer (5pp steps)."""
    raw = round(cfg.KEEP_FRACTION * 100 / 5) * 5
    return max(10, min(50, raw))


def get_threshold(pa: float, pb: float, best_of: int, set_num: int) -> float:
    """Return the combined-policy entry threshold for this game state.

    Rounds pa/pb to the nearest 0.01 grid point, clamps to [0.55, 0.75],
    selects the row matching cfg.KEEP_FRACTION (rounded to nearest 5%), and
    returns max(cfg.SWING_FLOOR, raw_quantile).  Falls back to
    max(cfg.SWING_FLOOR, 0.15) and logs a warning if the DB is absent or the
    row is missing.
    """
    global _warned
    conn = _connection()
    if conn is None:
        if not _warned:
            logging.warning(
                "swing_thresholds: DB not found at %s — falling back to %.2f",
                cfg.SWING_THRESHOLDS_DB, _FALLBACK,
            )
            _warned = True
        return max(cfg.SWING_FLOOR, _FALLBACK)

    pa_g  = _snap(pa)
    pb_g  = _snap(pb)
    sn    = max(1, min(best_of, set_num))
    kp    = _keep_pct()

    try:
        row = conn.execute(
            "SELECT threshold FROM thresholds "
            "WHERE pA=? AND pB=? AND best_of=? AND set_num=? AND keep_pct=?",
            (pa_g, pb_g, best_of, sn, kp),
        ).fetchone()
    except Exception as exc:
        if not _warned:
            logging.warning("swing_thresholds: lookup failed (%s) — falling back", exc)
            _warned = True
        return max(cfg.SWING_FLOOR, _FALLBACK)

    if row is None:
        if not _warned:
            logging.warning(
                "swing_thresholds: no row for pA=%.2f pB=%.2f bo=%d sn=%d kp=%d — falling back",
                pa_g, pb_g, best_of, sn, kp,
            )
            _warned = True
        return max(cfg.SWING_FLOOR, _FALLBACK)

    return max(cfg.SWING_FLOOR, row["threshold"])
```

- [ ] **Step 4: Run tests — confirm they all pass**

```
python -m pytest tests/trade/test_swing_thresholds.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trade/swing_thresholds.py tests/trade/test_swing_thresholds.py
git commit -m "feat(trade): swing_thresholds lookup module with grid snapping and fallback"
```

---

### Task 2: Config swap + bot integration + test updates

**Files:**
- Modify: `trade/config.py` — remove `MIN_GAME_SWING`, add `KEEP_FRACTION`, `SWING_FLOOR`, `SWING_THRESHOLDS_DB`
- Modify: `trade/trade_bot.py` — update `_check_entry` signature and call site in `_tick`
- Modify: `tests/trade/test_fills.py` — update `_check_entry` calls and swing tests

**Interfaces:**
- Consumes: `get_threshold` from Task 1
- The `fresh` fixture gains a monkeypatch that opens the gate for all non-swing tests: `get_threshold` returns `0.0`.

**Why the fixture patch:** Non-swing tests (order lifecycle, budget, cancellation) have a cond with `win_game=0.9 / lose_game=0.1` (swing = 80pp) already well above any real threshold, but after the change `_check_entry` will call the real `get_threshold` which needs a DB. Patching it to `0.0` keeps those tests fast and DB-independent.

- [ ] **Step 1: Write the failing tests (edit `tests/trade/test_fills.py`)**

Make these four changes to `tests/trade/test_fills.py`:

**1a. Add import at the top of the file (after existing imports):**
```python
import trade.swing_thresholds as _st_mod
```

**1b. Add one line to the `fresh` fixture** (after the existing `update_mc_prob` call, before `yield`):
```python
    monkeypatch.setattr(_st_mod, "get_threshold", lambda *a: 0.0)
```

**1c. Add `pa_blend=0.64, pb_blend=0.64` to every `_check_entry` call that does NOT already pass them.**
The calls to update are at lines 40, 49, 56, 71, 84, 100, 120, 128, 139.
Each changes from:
```python
tb._check_entry(KEY, CACHED, st(...), 0.40, 0.60)
```
to:
```python
tb._check_entry(KEY, CACHED, st(...), 0.40, 0.60, 0.64, 0.64)
```

**1d. Rewrite the five swing-specific tests** (the block starting at `# ── minimum game swing ──`). Replace the entire block with:

```python
# ── minimum game swing ────────────────────────────────────────────────────────
# Only trade games worth enough to clear a taker round trip. The gate reads the
# model's own branch spread via get_threshold (pa_blend, pb_blend, best_of, set_num).

def _cond(win, lose):
    return {"win_game": win, "lose_game": lose, "win_set": 0.9, "lose_set": 0.1}


def test_low_swing_game_is_skipped(monkeypatch):
    monkeypatch.setattr(_st_mod, "get_threshold", lambda *a: 0.30)
    placed = []
    monkeypatch.setattr(tb, "place_order", lambda *a: placed.append(a) or None)
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.54, 0.46))  # 8pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60, 0.64, 0.64)
    assert placed == []


def test_high_swing_game_is_traded(monkeypatch):
    monkeypatch.setattr(_st_mod, "get_threshold", lambda *a: 0.30)
    monkeypatch.setattr(tb, "place_order", lambda t, c, p: {
        "order_id": "s1", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.09, "avg_price": 0.60})
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.89, 0.55))  # 34pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60, 0.64, 0.64)
    assert tb._store.get_or_create(KEY).position is not None


def test_swing_is_side_independent():
    """p2's branches are p1's complements swapped, so the spread is a property of
    the game — buying either side faces the same swing."""
    c = _cond(0.89, 0.55)
    p1_swing = abs(c["win_game"] - c["lose_game"])
    p2_swing = abs((1 - c["lose_game"]) - (1 - c["win_game"]))
    assert p1_swing == pytest.approx(p2_swing)


def test_gate_can_be_disabled(monkeypatch):
    monkeypatch.setattr(_st_mod, "get_threshold", lambda *a: 0.0)
    monkeypatch.setattr(tb, "place_order", lambda t, c, p: {
        "order_id": "s2", "filled": 5.0, "remaining": 0.0,
        "cost_dollars": 3.00, "fee_dollars": 0.09, "avg_price": 0.60})
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=_cond(0.51, 0.49))  # 2pp
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60, 0.64, 0.64)
    assert tb._store.get_or_create(KEY).position is not None


def test_no_trade_without_branch_probs(monkeypatch):
    monkeypatch.setattr(_st_mod, "get_threshold", lambda *a: 0.30)
    placed = []
    monkeypatch.setattr(tb, "place_order", lambda *a: placed.append(a) or None)
    tb._store.update_mc_prob(KEY, 0.50, game_prob=0.5, cond=None)
    tb._check_entry(KEY, CACHED, st(), 0.40, 0.60, 0.64, 0.64)
    assert placed == []
```

- [ ] **Step 2: Run tests — confirm they fail for the right reason**

```
python -m pytest tests/trade/test_fills.py -v 2>&1 | head -40
```
Expected: `TypeError: _check_entry() takes 5 positional arguments but 7 were given` (or similar — the signature hasn't changed yet).

- [ ] **Step 3: Update `trade/config.py`**

Remove:
```python
MIN_GAME_SWING = {           # per-set thresholds, keyed by best_of
    3: [0.17, 0.18, 0.37],  # sets 1-3
    5: [0.13, 0.13, 0.17, 0.18, 0.37,],  # sets 1-5 — adjust as needed
}
```

Add (place it in the same location in the file, right after the `CONTRACTS_PER_TRADE` block comment):
```python
# Per-game entry gate: only trade when the match-probability swing on this game
# exceeds a threshold derived from the players' actual serve levels.
# KEEP_FRACTION: retain the top X% of on-serve games by swing (per-set quantile).
# SWING_FLOOR:   hard minimum — below this the taker fee cannot be cleared.
# Thresholds are precomputed for a grid of (pA, pB) pairs; see
# trade/precompute_swing_thresholds.py.  Set KEEP_FRACTION=1.0 to trade every
# on-serve boundary (equivalent to the old MIN_GAME_SWING = 0.0).
KEEP_FRACTION        = 0.30
SWING_FLOOR          = 0.12
SWING_THRESHOLDS_DB  = "data/swing_thresholds.db"
```

- [ ] **Step 4: Update `trade/trade_bot.py`**

**4a. Add import** at the top of the file, alongside the other `trade.*` imports:
```python
from trade.swing_thresholds import get_threshold
```

**4b. Update `_check_entry` signature** (line 513):
```python
def _check_entry(key, cached, kalshi_state, p1_px, p2_px, pa_blend, pb_blend):
```

**4c. Replace the threshold lookup block** inside `_check_entry` (the two lines that read `thresholds = ...` and `min_swing = ...`):
```python
    min_swing = get_threshold(pa_blend, pb_blend, cached["best_of"], set_num)
```

**4d. Update the `_check_entry` call in `_tick`** (the call inside the `if (not _store.has_position(key) ...` block at the end of `_tick`):
```python
            _check_entry(key, cached, kalshi_state, px1, px2,
                         sim["probs"]["pa_blend"], sim["probs"]["pb_blend"])
```

- [ ] **Step 5: Run the full test suite — confirm all tests pass**

```
python -m pytest tests/trade/ -v
```
Expected: all tests in `test_decision.py`, `test_fills.py`, and `test_swing_thresholds.py` PASS

- [ ] **Step 6: Commit**

```bash
git add trade/config.py trade/trade_bot.py tests/trade/test_fills.py
git commit -m "feat(trade): dynamic swing thresholds — replace MIN_GAME_SWING with DB lookup

- Remove static MIN_GAME_SWING config dict
- Add KEEP_FRACTION=0.30, SWING_FLOOR=0.12, SWING_THRESHOLDS_DB config
- _check_entry now takes pa_blend/pb_blend and calls get_threshold()
- Threshold updates each game using the current blended serve probs"
```

---

### Task 3: Precompute script

**Files:**
- Create: `trade/precompute_swing_thresholds.py`

**Interfaces:**
- Consumes: `trade.exact.game_win_prob`, `trade.exact.tiebreak_win_prob`, `trade.exact.win_probs`
- Produces: `data/swing_thresholds.db` with a populated `thresholds` table matching the schema in Task 1

**Runtime:** ~30–60 min at default 20k matches. Use `--matches 5000` for a first run (~8–15 min).

- [ ] **Step 1: Write the smoke test**

Create `tests/trade/test_precompute_smoke.py`:

```python
"""Smoke test for the precompute script: runs a tiny grid and validates the DB."""
import sqlite3, pytest
import trade.config as cfg


def test_precompute_smoke(tmp_path, monkeypatch):
    """3×3 grid, 200 matches — validates row count and no nulls."""
    from trade.precompute_swing_thresholds import run

    db_path = str(tmp_path / "smoke.db")
    # 3×3 grid: pA in [0.62,0.64,0.66], pB in [0.60,0.62,0.64], both best_of
    grid = [round(0.62 + i * 0.02, 2) for i in range(3)]
    run(pa_grid=grid, pb_grid=grid, n_matches=200, db_path=db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM thresholds").fetchone()[0]
    nulls = conn.execute(
        "SELECT COUNT(*) FROM thresholds WHERE threshold IS NULL"
    ).fetchone()[0]
    conn.close()

    # 3 pA × 3 pB × (3 sets Bo3 + 5 sets Bo5) × 9 keep_pcts = 3×3×8×9 = 648
    assert rows == 648, f"expected 648 rows, got {rows}"
    assert nulls == 0


def test_precompute_is_idempotent(tmp_path):
    """Running twice with INSERT OR REPLACE must not raise and must not double rows."""
    from trade.precompute_swing_thresholds import run

    db_path = str(tmp_path / "idem.db")
    grid = [0.64]
    run(pa_grid=grid, pb_grid=grid, n_matches=100, db_path=db_path)
    run(pa_grid=grid, pb_grid=grid, n_matches=100, db_path=db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM thresholds").fetchone()[0]
    conn.close()
    # 1×1×8×9 = 72
    assert rows == 72
```

- [ ] **Step 2: Run smoke test — confirm it fails**

```
python -m pytest tests/trade/test_precompute_smoke.py -v
```
Expected: `ModuleNotFoundError: No module named 'trade.precompute_swing_thresholds'`

- [ ] **Step 3: Implement `trade/precompute_swing_thresholds.py`**

```python
"""Precompute per-set swing quantiles for a (pA, pB) grid and write to SQLite.

Run once offline:
    python -m trade.precompute_swing_thresholds [--matches N] [--db PATH]

Default: 20 000 matches per (pA, pB, best_of) combination (~30-60 min total).
For a quick first run:  --matches 5000  (~8-15 min).
"""
import argparse, sqlite3, numpy as np, sys, time

import trade.exact as E

_GRID_DEFAULT = [round(0.55 + i * 0.01, 2) for i in range(21)]   # 0.55 … 0.75
_KEEP_PCTS    = [10, 15, 20, 25, 30, 35, 40, 45, 50]


# ── Monte Carlo engine ────────────────────────────────────────────────────────

class _Engine:
    """Memoised DP values at game boundaries for one (pA, pB) pair."""
    def __init__(self, pA, pB, best_of):
        self.pA, self.pB, self.bo = pA, pB, best_of
        self._v, self._g = {}, {}

    def state(self, sets, games, p1_serves, tb_flag):
        key = (sets, games, p1_serves, tb_flag)
        if key not in self._v:
            r = E.win_probs(np.array([self.pA]), np.array([self.pB]),
                            sets, games, tb_flag, (0, 0), p1_serves, self.bo)
            self._v[key] = (float(r["cond"]["win_game"][0]),
                            float(r["cond"]["lose_game"][0]))
        return self._v[key]

    def hold(self, p1_serves):
        if p1_serves not in self._g:
            self._g[p1_serves] = E.game_win_prob(self.pA if p1_serves else self.pB)
        return self._g[p1_serves]


def _set_over(g):
    hi, lo = max(g), min(g)
    return (hi >= 6 and hi - lo >= 2) or hi == 7


def _simulate(pA, pB, n_matches, best_of, rng):
    """Return list of (setno, swing) for on-serve, non-tiebreak boundaries."""
    eng   = _Engine(pA, pB, best_of)
    need  = best_of // 2 + 1
    recs  = []

    for _ in range(n_matches):
        sets      = [0, 0]
        p1_serves = bool(rng.integers(2))
        while max(sets) < need:
            setno = sets[0] + sets[1] + 1
            games = [0, 0]
            while True:
                tb_flag = (games == [6, 6])
                W, L    = eng.state(tuple(sets), tuple(games), p1_serves, tb_flag)
                swing   = abs(W - L)

                sg = games[0] if p1_serves else games[1]
                rg = games[1] if p1_serves else games[0]
                on_serve = (not tb_flag) and abs(games[0] - games[1]) <= 1 and sg <= rg

                if on_serve:
                    recs.append((setno, swing))

                if tb_flag:
                    p1_win = rng.random() < E.tiebreak_win_prob(pA, pB, a_serves_next=p1_serves)
                    games[0 if p1_win else 1] += 1
                else:
                    holds = rng.random() < eng.hold(p1_serves)
                    if p1_serves:
                        games[0 if holds else 1] += 1
                    else:
                        games[1 if holds else 0] += 1

                p1_serves = not p1_serves
                if _set_over(games):
                    break
            sets[0 if games[0] > games[1] else 1] += 1

    return recs


# ── DB setup ──────────────────────────────────────────────────────────────────

def _init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thresholds (
            pA       REAL    NOT NULL,
            pB       REAL    NOT NULL,
            best_of  INTEGER NOT NULL,
            set_num  INTEGER NOT NULL,
            keep_pct INTEGER NOT NULL,
            threshold REAL   NOT NULL,
            PRIMARY KEY (pA, pB, best_of, set_num, keep_pct)
        )
    """)
    conn.commit()
    return conn


def _write_rows(conn, pA, pB, best_of, recs):
    """Compute quantiles from recs and upsert into the DB."""
    from collections import defaultdict
    by_set = defaultdict(list)
    for setno, swing in recs:
        by_set[setno].append(swing)

    rows = []
    for setno, swings in by_set.items():
        arr = np.array(swings)
        for kp in _KEEP_PCTS:
            q = float(np.quantile(arr, 1.0 - kp / 100.0))
            rows.append((round(pA, 2), round(pB, 2), best_of, setno, kp, q))

    conn.executemany(
        "INSERT OR REPLACE INTO thresholds (pA, pB, best_of, set_num, keep_pct, threshold) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


# ── Public entry point (also used by the smoke test) ─────────────────────────

def run(pa_grid=None, pb_grid=None, n_matches=20_000, db_path="data/swing_thresholds.db"):
    if pa_grid is None:
        pa_grid = _GRID_DEFAULT
    if pb_grid is None:
        pb_grid = _GRID_DEFAULT

    conn  = _init_db(db_path)
    rng   = np.random.default_rng(42)
    total = len(pa_grid) * len(pb_grid) * 2
    done  = 0
    t0    = time.time()

    for pA in pa_grid:
        for pB in pb_grid:
            for best_of in (3, 5):
                recs = _simulate(pA, pB, n_matches, best_of, rng)
                _write_rows(conn, pA, pB, best_of, recs)
                done += 1
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done)
                print(f"  [{done}/{total}]  pA={pA:.2f} pB={pB:.2f} Bo{best_of}"
                      f"  elapsed {elapsed:.0f}s  ETA {eta:.0f}s", flush=True)

    conn.close()
    print(f"Done. Written to {db_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Precompute swing thresholds")
    p.add_argument("--matches", type=int, default=20_000,
                   help="MC matches per (pA,pB,best_of) combination (default 20000)")
    p.add_argument("--db", default="data/swing_thresholds.db",
                   help="Output SQLite path (default data/swing_thresholds.db)")
    args = p.parse_args()
    run(n_matches=args.matches, db_path=args.db)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run smoke test — confirm it passes**

```
python -m pytest tests/trade/test_precompute_smoke.py -v
```
Expected: both tests PASS (takes ~5–15 seconds with 200 matches on a 3×3 grid)

- [ ] **Step 5: Run the full suite — confirm nothing regressed**

```
python -m pytest tests/trade/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add trade/precompute_swing_thresholds.py tests/trade/test_precompute_smoke.py
git commit -m "feat(trade): swing threshold precompute script

Sweeps pA/pB grid and writes quantile rows to data/swing_thresholds.db.
Run: python -m trade.precompute_swing_thresholds [--matches N] [--db PATH]"
```

- [ ] **Step 7: Run the full precompute (optional, do when you have ~1 hour)**

```
python -m trade.precompute_swing_thresholds --matches 5000
```
This writes `data/swing_thresholds.db` (~31,752 rows). The bot will use fallback thresholds until this file exists.
