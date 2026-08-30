"""Lookup table for per-game swing thresholds.

Reads a precomputed SQLite DB (see simulation/precompute_swing_thresholds.ipynb)
and returns the combined-policy threshold:
    max(SWING_FLOOR, quantile(1 - KEEP_FRACTION))

Falls back to max(SWING_FLOOR, 0.15) if the DB is missing or the row is absent,
logging a warning once per session.
"""
import logging, os, sqlite3
import trade.config as cfg

_conn    = None
_warned  = False

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

    pa_g = _snap(pa)
    pb_g = _snap(pb)
    sn   = max(1, min(best_of, set_num))
    kp   = _keep_pct()

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
