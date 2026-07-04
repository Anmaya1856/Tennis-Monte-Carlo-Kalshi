"""Career-stat priors: look up a player's career serve/return rates from atp.db
and blend them into in-match stats as Beta pseudo-counts (PRIOR_N virtual points)."""
import re, sqlite3, unicodedata
import trade.config as cfg

# Tour averages (games-weighted, 2025, players with >50 service games) — used
# when a player can't be found in atp.db.
NEUTRAL = {
    "first_in":      0.62,
    "win_first":     0.72,
    "win_second":    0.51,
    "return_first":  0.28,
    "return_second": 0.49,
}

_COLS = {
    "first_in":      "FirstServePercentage",
    "win_first":     "FirstServePointsWonPercentage",
    "win_second":    "SecondServePointsWonPercentage",
    "return_first":  "FirstServeReturnPointsWonPercentage",
    "return_second": "SecondServeReturnPointsWonPercentage",
}

_STAT_KEYS = list(NEUTRAL)


def _norm(name):
    name = unicodedata.normalize("NFD", str(name))
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]", "", name.lower())


def lookup(player_name, surface):
    """Career rates for a player on a surface, with fallback chain
    (2025/surface -> all/surface -> 2025/all -> all/all -> NEUTRAL)."""
    try:
        con = sqlite3.connect(cfg.ATP_DB)
        target = _norm(player_name)
        row = con.execute(
            "SELECT player_id, first_name, last_name FROM players"
        ).fetchall()
        pid = next((r[0] for r in row if _norm(f"{r[1] or ''}{r[2] or ''}") == target), None)
        if pid is None:
            con.close()
            return dict(NEUTRAL)
        cols = ", ".join(_COLS[k] for k in _STAT_KEYS)
        for year, surf in [("2025", surface), ("all", surface), ("2025", "all"), ("all", "all")]:
            r = con.execute(
                f"SELECT {cols} FROM player_stats WHERE player_id=? AND year=? AND surface=?",
                (pid, year, surf),
            ).fetchone()
            if r is not None:
                vals = {k: (v or 0) / 100.0 for k, v in zip(_STAT_KEYS, r)}
                if all(0.02 < v < 0.98 for v in vals.values()):
                    con.close()
                    return vals
        con.close()
    except Exception as e:
        print(f"[career] lookup failed for {player_name}: {e}")
    return dict(NEUTRAL)


def blend(stats, career, prior_n=None):
    """Blend in-match counts with PRIOR_N virtual points at the career rate."""
    if prior_n is None:
        prior_n = cfg.PRIOR_N
    out = dict(stats)
    for k in _STAT_KEYS:
        num, den = stats[k + "_num"], stats[k + "_den"]
        out[k + "_num"] = num + career[k] * prior_n
        out[k + "_den"] = den + prior_n
        out[k] = out[k + "_num"] / out[k + "_den"]
    return out
