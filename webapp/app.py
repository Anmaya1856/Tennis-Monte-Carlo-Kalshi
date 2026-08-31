"""Live trading monitor — a browser view of the bot's console output.

Read-only: the bot runs as its own process per match and writes CSV logs; this
app watches data/logs/ and renders the live state. It can spawn a bot process
(Add-match) and ask one to stop (writes a stop flag the bot polls) — it never
trades.

Run:  python -m webapp.app   ->  http://localhost:8050
"""
import datetime, glob, os, subprocess, sys, threading, time

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, ctx, Input, Output, State, ALL

import trade.config as cfg
import trade.swing_thresholds as _sw_thr
from trade.decision import on_serve
from trade.kalshi_client import discover_live_events
from trade.exact import (win_probs, win_prob_forward, weighted_quantile,
                         _parse_match_state, _parse_game_score, _parse_score, _is_set_complete)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(REPO_ROOT, cfg.LOG_DIR)
ACTIVE_WINDOW_SECS = 120
REFRESH_MS = 2000
# logs store UTC wall-clock; shift to local time for display
_LOCAL_OFFSET = datetime.datetime.now().astimezone().utcoffset() or datetime.timedelta(0)

# ── design tokens (dark surface; validated categorical + status palette) ──────
SURFACE, PLANE, INSET = "#1a1a19", "#0d0d0d", "#26261f"
INK, INK2, MUTED = "#ffffff", "#dcdbd2", "#a8a69b"   # brighter for contrast
HAIR, GRID, BASE = "#39392f", "#2c2c2a", "#45453a"
P1C, P2C = "#3987e5", "#e8843f"      # identity: blue / orange (CVD-safe)
GOOD, CRIT, WARN = "#26c281", "#f0564f", "#fab219"   # status (reserved)


# ── data access ───────────────────────────────────────────────────────────────
def _parse_ts(s):
    try:
        return datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _num(row, col):
    try:
        v = float(row.get(col))
        return v if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


def _str(row, col, default=""):
    """Return string value from row, treating NaN/None as default."""
    v = row.get(col)
    return default if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)


def active_matches():
    now = datetime.datetime.now(datetime.timezone.utc)
    files = glob.glob(os.path.join(LOG_DIR, "match_snapshots_*.csv"))
    files = [f for f in files if now.timestamp() - os.path.getmtime(f) < ACTIVE_WINDOW_SECS]
    best = {}
    for f in sorted(files, key=os.path.getmtime):
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if df.empty or "ticker" not in df.columns:
            continue
        for ticker, g in df.groupby("ticker"):
            ts = _parse_ts(g.iloc[-1].get("timestamp"))
            if ts is None or (now - ts).total_seconds() > ACTIVE_WINDOW_SECS:
                continue
            if ticker not in best or ts > best[ticker][0]:
                best[ticker] = (ts, g.reset_index(drop=True))
    return {t: g for t, (ts, g) in sorted(best.items())}


def match_trades(event_ticker):
    """Every trade logged for one match, newest first. Logs are named
    trade_log_<event ticker>_<date>.csv, so this is a direct file lookup."""
    files = glob.glob(os.path.join(LOG_DIR, f"trade_log_{event_ticker}_*.csv"))
    frames = []
    for f in sorted(files, key=os.path.getmtime):
        try:
            frames.append(pd.read_csv(f))
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).iloc[::-1]


def branch_probs(last):
    """P1's 4 conditional match probs (win/lose current game/set). Prefer logged
    cond_*; else recompute from logged blended point probs (works on old logs)."""
    keys = ("win_game", "lose_game", "win_set", "lose_set")
    logged = {k: _num(last, f"cond_{k}") for k in keys}
    if all(v is not None for v in logged.values()):
        return logged
    pa, pb = _num(last, "pa_blend"), _num(last, "pb_blend")
    if pa is None or pb is None:
        return None
    try:
        best_of = int(_num(last, "best_of") or 3)
        sets_won, cur = _parse_match_state(_str(last, "score_str", "0-0"), best_of)
        in_tb = cur == (6, 6)
        gs = _parse_game_score(_str(last, "game_score_str", "0-0"), is_tiebreak=in_tb)
        r = win_probs(pa, pb, sets_won, cur or (0, 0), in_tb, gs,
                      last.get("server") == "p1", best_of)
        return {k: float(r["cond"][k]) for k in keys}
    except Exception:
        return None


# ── formatting ────────────────────────────────────────────────────────────────
def _pct(x, d=1):
    return f"{x * 100:.{d}f}%" if x is not None else "—"


def _price(x):
    """Market price as whole cents — directly comparable to a model percentage."""
    return f"{x * 100:.0f}¢" if x is not None else "—"


def _last_name(n):
    parts = str(n).split()
    return parts[-1] if parts else str(n)


# ── card components ───────────────────────────────────────────────────────────
def range_bar(lose, win, cur, color):
    lo, hi = min(lose, win), max(lose, win)
    return html.Div([
        html.Div([
            html.Div(className="rb-track"),
            html.Div(className="rb-span", style={"left": f"{lo * 100}%",
                                                 "width": f"{(hi - lo) * 100}%", "background": color}),
            html.Div(className="rb-mark", style={"left": f"{cur * 100}%"}),
        ], className="rb-bar"),
        html.Div([html.Span(f"lose {lose * 100:.0f}%"), html.Span(f"win {win * 100:.0f}%")],
                 className="rb-ends"),
    ], className="rb")


def _pct_stat_row(last, base, label):
    def side(pl):
        pct = _num(last, f"{pl}_{base}_pct")
        if pct is None:
            return None
        num, den = _num(last, f"{pl}_{base}_num"), _num(last, f"{pl}_{base}_den")
        disp = (f"{int(num)}/{int(den)} ({pct * 100:.0f}%)"
                if num is not None and den is not None else f"{pct * 100:.0f}%")
        return disp, pct
    s1, s2 = side("p1"), side("p2")
    if s1 is None and s2 is None:
        return None
    d1, f1 = s1 or ("—", 0)
    d2, f2 = s2 or ("—", 0)
    return (label, d1, f1, d2, f2)


def _count_stat_row(last, key, label):
    c1, c2 = _num(last, f"p1_k_{key}"), _num(last, f"p2_k_{key}")
    if c1 is None and c2 is None:
        return None
    c1, c2 = c1 or 0, c2 or 0
    m = max(c1, c2, 1)
    return (label, str(int(c1)), c1 / m, str(int(c2)), c2 / m)


def _svc_row(label, d1, f1, d2, f2):
    return html.Div([
        html.Div(d1, className="sb2-val sb2-left"),
        html.Div(html.Div(className="sb2-fill", style={"width": f"{f1 * 100}%", "background": P1C}),
                 className="sb2-bar sb2-bar-l"),
        html.Div(label, className="sb2-label"),
        html.Div(html.Div(className="sb2-fill", style={"width": f"{f2 * 100}%", "background": P2C}),
                 className="sb2-bar sb2-bar-r"),
        html.Div(d2, className="sb2-val sb2-right"),
    ], className="sb2-row")


def service_stats(last):
    rows = []
    for base, label in (("first_serve", "FIRST SERVE"),
                        ("first_serve_won", "1ST SERVE POINTS WON"),
                        ("second_serve_won", "2ND SERVE POINTS WON")):
        r = _pct_stat_row(last, base, label)
        if r:
            rows.append(r)
    for key, label in (("aces", "ACES"), ("double_faults", "DOUBLE FAULTS")):
        r = _count_stat_row(last, key, label)
        if r:
            rows.append(r)
    if not rows:
        return html.Div()
    return html.Div([html.Div("SERVICE STATS", className="section-label"),
                     html.Div([_svc_row(*r) for r in rows], className="svc-stats")])


def player_col(nm, color, match, bid, ask, game, sett, br_game, br_set, badge_text=None):
    badge = html.Span(badge_text, className="badge") if badge_text else None
    def gsm(label, val, align):
        return html.Div([html.Div(label, className="gsm-label"),
                         html.Div(_pct(val, 1), className="gsm-val")],
                        className="gsm-cell", style={"textAlign": align})

    def book_tile(label, price, sub):
        return html.Div([html.Div(label, className="bk-label"),
                         html.Div(_price(price), className="bk-val"),
                         html.Div(sub, className="bk-sub")], className="bk-tile")

    return html.Div([
        html.Div([html.Span(className="dot", style={"background": color}),
                  html.Span(nm, className="col-name"), badge], className="col-head"),
        html.Div("MODEL  ·  WIN PROBABILITY", className="grp-label"),
        html.Div([gsm("GAME", game, "left"), gsm("SET", sett, "center"),
                  gsm("MATCH", match, "right")], className="gsm-grid"),
        html.Div("MARKET  ·  KALSHI ORDER BOOK", className="grp-label"),
        html.Div([book_tile("BID", bid, "sell YES here"),
                  book_tile("ASK", ask, "buy YES here")], className="bk-grid"),
        html.Div([
            html.Div("if this game …", className="rb-label"),
            range_bar(*br_game, color),
            html.Div("if this set …", className="rb-label"),
            range_bar(*br_set, color),
        ]) if br_game else html.Div(),
    ], className="pcol", style={"borderTop": f"3px solid {color}"})


def scoreline_readout(last, best_of, p1, p2):
    need = int(best_of) // 2 + 1
    items = []
    for pl, nm, color in (("p1", p1, P1C), ("p2", p2, P2C)):
        for d in range(need):
            v = _num(last, f"sc_{pl}_d{d}")
            if v and v > 0.005:
                items.append(html.Span([
                    html.Span("● ", style={"color": color}),
                    html.Span(f"{nm} {need}-{d}", className="sl-score"),
                    html.Span(f"{v * 100:.0f}%", className="sl-pct"),
                ], className="sl-item"))
    return html.Div([html.Div("FINAL SCORELINE", className="section-label"),
                     html.Div(items, className="sl-row")])


def over_under_readout(last):
    """Total-games over/under table from the logged p_games_over_* columns."""
    items = []
    for col in last.index:
        if isinstance(col, str) and col.startswith("p_games_over_"):
            v = _num(last, col)
            if v is None:
                continue
            try:
                items.append((float(col[len("p_games_over_"):].replace("_", ".")), v))
            except ValueError:
                pass
    if not items:
        return html.Div()
    items.sort()
    head = html.Tr([html.Th("line")] + [html.Th(f"{t:g}") for t, _ in items])
    over = html.Tr([html.Td("over")] + [html.Td(f"{v * 100:.0f}%") for _, v in items])
    under = html.Tr([html.Td("under")] + [html.Td(f"{(1 - v) * 100:.0f}%") for _, v in items])
    return html.Div([
        html.Div("TOTAL GAMES  (over / under)", className="section-label"),
        html.Div(html.Table([html.Thead(head), html.Tbody([over, under])], className="ou-table"),
                 className="ou-wrap"),
    ])


def compute_forward(last, max_games=6):
    """win_prob_forward from the logged point probs, or None if unavailable."""
    pa, pb = _num(last, "pa_blend"), _num(last, "pb_blend")
    if pa is None or pb is None:
        return None
    try:
        best_of = int(_num(last, "best_of") or 3)
        sets_won, cur = _parse_match_state(_str(last, "score_str", "0-0"), best_of)
        in_tb = cur == (6, 6)
        gs = _parse_game_score(_str(last, "game_score_str", "0-0"), is_tiebreak=in_tb)
        return win_prob_forward(pa, pb, sets_won, cur or (0, 0), in_tb, gs,
                                last.get("server") == "p1", best_of, max_games=max_games)
    except Exception:
        return None


def fan_fig(g, p1, fwd):
    ts = pd.to_datetime(g["timestamp"], errors="coerce") + _LOCAL_OFFSET   # UTC log -> local, tz-naive
    mc = pd.to_numeric(g.get("mc_prob_p1"), errors="coerce") * 100
    ask = pd.to_numeric(g.get("kalshi_p1_ask"), errors="coerce")
    bid = pd.to_numeric(g.get("kalshi_p1_bid"), errors="coerce")
    mid = (ask + bid) / 2 * 100
    fig = go.Figure()
    fig.add_scatter(x=ts, y=mid, name="Kalshi price", mode="lines",
                    line=dict(color=MUTED, width=1.5, dash="dot"),
                    hovertemplate="price %{y:.0f}%<extra></extra>")
    fig.add_scatter(x=ts, y=mc, name=f"model {p1}", mode="lines",
                    line=dict(color=P1C, width=2.5, shape="spline"),
                    hovertemplate="model %{y:.0f}%<extra></extra>")

    # forward cone: martingale median (flat) with widening percentile bands
    now = ts.dropna().max()
    x_lo, x_hi = ts.dropna().min(), now
    if fwd and pd.notna(now):
        levels = fwd["levels"]
        dt = pd.Timedelta(minutes=3.5)   # rough games→time mapping for the x-axis
        fx = [now + k * dt for k in range(len(levels))]
        x_hi = fx[-1]
        q = {p: [(weighted_quantile(lvl, p) or 0) * 100 for lvl in levels]
             for p in (0.05, 0.25, 0.5, 0.75, 0.95)}
        band = lambda lo, hi, a, nm: (
            fig.add_scatter(x=fx, y=q[lo], mode="lines", line=dict(width=0),
                            showlegend=False, hoverinfo="skip"),
            fig.add_scatter(x=fx, y=q[hi], mode="lines", line=dict(width=0), fill="tonexty",
                            fillcolor=f"rgba(57,135,229,{a})", name=nm, hoverinfo="skip"))
        band(0.05, 0.95, 0.12, "5–95%")
        band(0.25, 0.75, 0.22, "25–75%")
        fig.add_scatter(x=fx, y=q[0.5], mode="lines", name="forecast",
                        line=dict(color=P1C, width=2, dash="dash"),
                        hovertemplate="median %{y:.0f}%<extra></extra>")
        fig.add_vline(x=now, line=dict(color=BASE, width=1, dash="dot"))

    fig.update_layout(
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, height=340,
        margin=dict(l=40, r=14, t=46, b=30), hovermode="x unified",
        font=dict(color=INK2, size=14, family='system-ui,-apple-system,"Segoe UI",sans-serif'),
        title=dict(text=f"<b>{p1} win% — history & forecast cone</b>", font=dict(size=18, color=INK),
                   x=0, y=0.97, yanchor="top"),
        legend=dict(orientation="h", y=1.14, x=1, xanchor="right", font=dict(size=15),
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(gridcolor=GRID, zeroline=False, linecolor=BASE,
                   range=[x_lo, x_hi] if pd.notna(x_lo) and pd.notna(x_hi) else None),
        yaxis=dict(gridcolor=GRID, zeroline=False, linecolor=BASE, range=[0, 100], ticksuffix="%"))
    return fig


def serve_probs(last, p1, p2):
    """Per-server point-win probability in the three forms the engine uses:

      PRE-MATCH  pa0/pb0, inverted from the market's opening price
      IN-PLAY    the empirical rate from this match's service points
      BLENDED    what actually feeds the DP — the prior worth MARKET_PRIOR_N
                 pseudo points plus the real ones, so it tapers from one to the other

    Rendered with the same mirrored rows as SERVICE STATS below it, since it is
    the same shape of comparison.
    """
    pre = {"p1": _num(last, "pa0"), "p2": _num(last, "pb0")}
    bl = {"p1": _num(last, "pa_blend"), "p2": _num(last, "pb_blend")}
    if pre["p1"] is None and bl["p1"] is None:
        return html.Div()

    live, played = {}, {}
    for pl in ("p1", "p2"):
        won = (_num(last, f"{pl}_first_serve_won_num") or 0) +               (_num(last, f"{pl}_second_serve_won_num") or 0)
        n = _num(last, f"{pl}_first_serve_den") or 0
        played[pl] = n
        live[pl] = (won / n) if n else None

    n_prior = getattr(cfg, "MARKET_PRIOR_N", 40)
    w1 = n_prior / (n_prior + played["p1"]) if (n_prior + played["p1"]) else 1.0

    # How the pre-match row was built. implied_point_probs returns (base+d, base-d):
    # the base fixes the overall serve level (the price alone is one equation short
    # of two unknowns) and d is the skill gap the price implies. Both are recoverable.
    mkt = _num(last, "prematch_price")
    base = (pre["p1"] + pre["p2"]) / 2 if None not in pre.values() else None
    gap = abs(pre["p1"] - pre["p2"]) / 2 if None not in pre.values() else None

    def tile(label, value, sub, color=None):
        return html.Div([html.Div(label, className="bk-label"),
                         html.Div(value, className="bk-val",
                                  style={"color": color} if color else None),
                         html.Div(sub, className="bk-sub")], className="bk-tile")

    if base is None:
        deriv = None
    elif mkt is None:
        deriv = html.Div([
            tile("KALSHI OPEN", "—", "no price found", WARN),
            tile("BASE", f"{base * 100:.1f}%", "neutral fallback", WARN),
            tile("SKILL GAP", "±0.0%", "prior is uninformative", WARN),
        ], className="sp-deriv")
    else:
        deriv = html.Div([
            tile("KALSHI OPEN", _price(mkt), f"{_last_name(p1)}, pre-match"),
            tile("BASE", f"{base * 100:.1f}%", "assumed tour average"),
            tile("SKILL GAP", f"±{gap * 100:.1f}%", "what the price implies"),
        ], className="sp-deriv")

    def cells(d, sub=None):
        def one(pl):
            if d[pl] is None:
                return "—", 0.0
            txt = _pct(d[pl], 1) + (f"  ({played[pl]:g} pts)" if sub else "")
            return txt, d[pl]
        a, fa = one("p1")
        b, fb = one("p2")
        return a, fa, b, fb

    rows = [_svc_row("PRE-MATCH", *cells(pre)),
            _svc_row("IN-MATCH SERVE", *cells(live, sub=True)),
            _svc_row(f"BLENDED — USED  ({w1 * 100:.0f}% prior)", *cells(bl))]
    return html.Div([html.Div("SERVE POINT PROBABILITY", className="section-label"),
                     *([deriv] if deriv is not None else []),
                     html.Div(rows, className="svc-stats sp-stats")])


def scoreboard(p1full, p2full, last):
    """Two-line scoreboard, one row per player: name · serve · per-set games · game point."""
    score_str = _str(last, "score_str").strip()
    game_str  = _str(last, "game_score_str")
    server = last.get("server")
    try:
        sets = _parse_score(score_str) if score_str else []
    except Exception:
        sets = []
    gp = game_str.split("-") if "-" in game_str else ["", ""]

    def row(idx, name, serving):
        cells = []
        for a, b in sets:
            mine, theirs = (a, b) if idx == 0 else (b, a)
            won = _is_set_complete(a, b) and mine > theirs
            cells.append(html.Span(str(mine), className="sb-set" + (" sb-win" if won else "")))
        ball = html.Span(className="sb-ball" if serving else "sb-ball-hidden")
        pt = gp[idx] if idx < len(gp) else ""
        return html.Div([
            html.Span(name, className="sb-name"),
            ball,
            html.Div(cells, className="sb-sets"),
            html.Span(pt, className="sb-game"),
        ], className="sb-row")

    return html.Div([row(0, p1full, server == "p1"), row(1, p2full, server == "p2")],
                    className="scoreboard")


def build_card(ticker, g):
    last = g.iloc[-1]
    event = ticker.rsplit("-", 1)[0]
    p1full, p2full = last.get("p1_name", "P1"), last.get("p2_name", "P2")
    p1, p2 = _last_name(p1full), _last_name(p2full)
    best_of = int(_num(last, "best_of") or 3)

    mc = _num(last, "mc_prob_p1")
    st = _num(last, "mc_set_prob_p1")
    gm = _num(last, "mc_game_prob_p1")
    p1a, p1b = _num(last, "kalshi_p1_ask"), _num(last, "kalshi_p1_bid")
    p2a, p2b = _num(last, "kalshi_p2_ask"), _num(last, "kalshi_p2_bid")

    b = branch_probs(last)
    p1_bg = p2_bg = p1_bs = p2_bs = None
    if b and mc is not None:
        cur_g = gm * b["win_game"] + (1 - gm) * b["lose_game"] if gm is not None else mc
        cur_s = st * b["win_set"] + (1 - st) * b["lose_set"] if st is not None else mc
        p1_bg = (b["lose_game"], b["win_game"], cur_g)
        p1_bs = (b["lose_set"], b["win_set"], cur_s)
        p2_bg = (1 - b["win_game"], 1 - b["lose_game"], 1 - cur_g)
        p2_bs = (1 - b["win_set"], 1 - b["lose_set"], 1 - cur_s)

    header = html.Div([
        html.Span(className="live-dot"),
        html.Span(f"{p1full}  vs  {p2full}", className="match-title"),
        html.Span(f"Bo{best_of}", className="pill"),
        html.Button("Stop", id={"type": "stop-btn", "event": event}, n_clicks=0,
                    className="stopbtn"),
    ], className="card-head")

    mid = last.get("milestone_id")
    mid = None if mid is None or pd.isna(mid) or not str(mid).strip() else str(mid)
    ids = html.Div([html.Span(event, className="id-val"),
                    *([html.Span(" · milestone ", className="id-lbl"),
                       html.Span(mid, className="id-val")] if mid else [])], className="card-ids")

    score = scoreboard(p1full, p2full, last)

    # The strategy buys the receiver while the set is unbroken.
    sets_won_list, cur_games = _parse_match_state(_str(last, "score_str", "0-0"), best_of)
    set_num = sets_won_list[0] + sets_won_list[1] + 1
    srv = last.get("server")
    live = on_serve(cur_games, srv == "p1")
    p1_badge = "RECEIVER" if (srv == "p2" and live) else None
    p2_badge = "RECEIVER" if (srv == "p1" and live) else None

    cols = html.Div([
        player_col(p1, P1C, mc, p1b, p1a, gm, st, p1_bg, p1_bs, p1_badge),
        player_col(p2, P2C, None if mc is None else 1 - mc, p2b, p2a,
                   None if gm is None else 1 - gm, None if st is None else 1 - st,
                   p2_bg, p2_bs, p2_badge),
    ], className="pcol-grid")

    # footer — what we hold, what it's worth, and what we can still spend
    side = last.get("position_side")
    budget = _num(last, "budget_remaining")
    ep, cnt = _num(last, "position_entry_price"), _num(last, "position_count")
    cur = _num(last, "position_current_value")
    game_id = last.get("position_game_id")
    upnl, dv = _num(last, "position_unrealized_pnl"), _num(last, "divergence_ema")

    def ft_tile(label, value, sub, color=None, small=False):
        return html.Div([
            html.Div(label, className="ft-label"),
            html.Div(value, className="ft-val ft-val-sm" if small else "ft-val",
                     style={"color": color} if color else None),
            html.Div(sub, className="ft-sub"),
        ], className="ft-tile")

    if side in ("p1", "p2") and upnl is not None:
        who = p1 if side == "p1" else p2
        tiles = [
            ft_tile("HOLDING",
                    html.Span([html.Span("● ", style={"color": P1C if side == "p1" else P2C}),
                               f"{who} YES"]),
                    f"×{cnt:g} contracts" if cnt is not None else "", small=True),
            ft_tile("ENTRY", _price(ep), "we paid"),
            ft_tile("WORTH NOW", _price(cur),
                    f"opened at [{game_id}]" if game_id else "at market bid"),
            ft_tile("UNREALIZED", f"${upnl:+.2f}", "if we sold now",
                    GOOD if upnl >= 0 else CRIT),
        ]
    else:
        tiles = [ft_tile("HOLDING", "FLAT", "nothing open", MUTED, small=True)]
    tiles.append(ft_tile("BUDGET LEFT", f"${budget:.2f}" if budget is not None else "—",
                         "free to spend"))
    tiles.append(ft_tile("STRATEGY", "ON" if live else "STANDING BY",
                         "buying the receiver" if live
                         else "set is broken — waiting", GOOD if live else MUTED))
    tiles.append(ft_tile("MODEL−MARKET", f"{dv:.2f}" if dv is not None else "—",
                         "divergence, diagnostic only"))

    # swing threshold for this set
    pa_bl, pb_bl = _num(last, "pa_blend"), _num(last, "pb_blend")
    thr = None
    if pa_bl is not None and pb_bl is not None:
        try:
            thr = _sw_thr.get_threshold(pa_bl, pb_bl, best_of, set_num)
        except Exception:
            pass
    actual_swing = (b["win_game"] - b["lose_game"]) if b else None
    if thr is not None:
        clears = actual_swing is not None and actual_swing >= thr
        thr_color = GOOD if clears else WARN
        sub = (f"actual {actual_swing * 100:.1f}pp  ✓" if clears
               else f"actual {actual_swing * 100:.1f}pp  ✗" if actual_swing is not None
               else f"set {set_num}  ·  keep {cfg.KEEP_FRACTION * 100:.0f}%")
        tiles.append(ft_tile("SWING GATE", f"{thr * 100:.1f}pp", sub, thr_color))
    else:
        tiles.append(ft_tile("SWING GATE", "—", "DB not found — fallback active", WARN))

    footer = html.Div([
        html.Div("OUR POSITION  ·  RISK", className="grp-label"),
        html.Div(tiles, className="ft-grid"),
    ], className="card-foot")

    fwd = compute_forward(last)
    charts = dcc.Graph(figure=fan_fig(g, p1, fwd),
                       config={"displayModeBar": False}, className="chart")

    return html.Div([header, ids, score, cols,
                     serve_probs(last, p1, p2), service_stats(last),
                     scoreline_readout(last, best_of, p1, p2),
                     over_under_readout(last), charts, footer,
                     build_tradelog(event, p1, p2)], className="card",
                    id=f"card-{event}")


def build_tradelog(event_ticker, p1, p2):
    """This match's trades, paired into round trips, newest first."""
    df = match_trades(event_ticker)
    if df.empty:
        return html.Div([html.Div("TRADES  ·  THIS MATCH", className="grp-label"),
                         html.Div("No trades yet.", className="empty")])

    trips, pending = [], None
    for _, r in df.iloc[::-1].iterrows():          # back to chronological to pair
        if r.get("event") == "entry":
            pending = r
        elif pending is not None:
            trips.append((pending, r))
            pending = None
    if pending is not None:
        trips.append((pending, None))              # still open

    def _fee(r):
        return float(r["fee"]) if r is not None and pd.notna(r.get("fee")) else 0.0

    def _net(en, ex):
        """Round-trip P&L after BOTH legs' fees.

        The logged `pnl` is proceeds minus cost basis net of the EXIT fee only —
        the entry fee is taken out of budget separately at fill time, so it never
        reaches that column. Subtract it here or every trade reads better than it was.
        """
        if ex is None or pd.isna(ex.get("pnl")):
            return None
        return float(ex["pnl"]) - _fee(en)

    realized = sum(v for v in (_net(en, ex) for en, ex in trips) if v is not None)
    fees = sum(_fee(en) + _fee(ex) for en, ex in trips)

    head = html.Tr([html.Th(h) for h in
                    ("opened", "side", "entry", "exit", "fees", "P&L")])
    rows = []
    for en, ex in reversed(trips):
        who = p1 if en.get("direction") == "p1" else p2
        t = str(en.get("timestamp") or "")[11:16]
        fee = _fee(en) + _fee(ex)
        if ex is None:
            cells = [html.Td(t), html.Td(who), html.Td(_price(_num(en, "entry_price"))),
                     html.Td("open", style={"color": WARN}), html.Td(f"${fee:.3f}"),
                     html.Td("—", style={"color": MUTED})]
        else:
            net = _net(en, ex)
            cells = [html.Td(t), html.Td(who), html.Td(_price(_num(en, "entry_price"))),
                     html.Td(_price(_num(ex, "exit_price"))), html.Td(f"${fee:.3f}"),
                     html.Td(f"${net:+.3f}" if net is not None else "—",
                             style={"color": GOOD if (net or 0) >= 0 else CRIT,
                                    "fontWeight": 700})]
        rows.append(html.Tr(cells))

    summary = html.Div([
        html.Span(f"{len(trips)} trades", className="tl-stat"),
        html.Span(f"fees ${fees:.2f}", className="tl-stat"),
        html.Span(f"realized ${realized:+.2f}", className="tl-stat",
                  style={"color": GOOD if realized >= 0 else CRIT, "fontWeight": 700}),
    ], className="tl-summary")

    return html.Div([
        html.Div("TRADES  ·  THIS MATCH", className="grp-label"),
        summary,
        html.Div(html.Table([html.Thead(head), html.Tbody(rows)], className="tradelog"),
                 className="tl-wrap"),
    ])


# ── app ───────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="Tennis Trading Monitor")

app.index_string = """<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  * { box-sizing:border-box; }
  body { margin:0; background:""" + PLANE + """; color:""" + INK + """; font-size:16px;
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { display:flex; min-height:100vh; }

  /* ── fixed sidebar ── */
  .sidebar { width:300px; flex-shrink:0; position:fixed; left:0; top:0; height:100vh;
             overflow-y:auto; background:""" + SURFACE + """; border-right:1px solid """ + HAIR + """;
             padding:22px 20px 40px; display:flex; flex-direction:column; gap:24px; }
  .main { margin-left:300px; flex:1; min-width:0; padding:24px 28px 60px; }

  /* sidebar header */
  .brand { font-size:19px; font-weight:750; letter-spacing:-.01em; margin-bottom:6px; }
  .mode { display:inline-block; font-size:13px; font-weight:800; padding:2px 8px; border-radius:5px;
          background:rgba(250,178,25,.16); color:""" + WARN + """; letter-spacing:.05em; margin-bottom:4px; }
  .sb-stat { font-size:15px; color:""" + INK2 + """; font-variant-numeric:tabular-nums;
             line-height:1.6; }

  /* sidebar sections */
  .sb-section { display:flex; flex-direction:column; gap:0; }
  .sb-panel-title { font-size:13px; font-weight:800; letter-spacing:.09em; color:""" + MUTED + """;
                    margin-bottom:12px; }
  .sb-event { padding:14px 0; border-bottom:1px solid """ + GRID + """; }
  .sb-event:last-child { border-bottom:none; padding-bottom:0; }
  .sb-event-names { font-size:17px; font-weight:600; color:""" + INK2 + """; margin-bottom:5px;
                    line-height:1.4; }
  .sb-event-ticker { font-size:13px; color:""" + MUTED + """; font-family:ui-monospace,monospace;
                     margin-bottom:10px; }
  .sb-launch-btn { background:""" + GOOD + """; color:#04140a; border:none; border-radius:6px;
                   padding:7px 16px; font-weight:750; font-size:16px; cursor:pointer; }
  .sb-launch-btn:hover { filter:brightness(1.09); }
  .sb-running-lbl { font-size:16px; color:""" + GOOD + """; font-weight:700;
                    text-decoration:none; cursor:pointer; opacity:.5; transition:opacity .15s; }
  .sb-running-lbl:hover { opacity:.85; }
  .sb-event.sb-nav-active { border-left:3px solid """ + GOOD + """; padding-left:10px;
                             margin-left:-13px; background:rgba(38,194,129,.08);
                             border-radius:0 6px 6px 0; }
  .sb-event.sb-nav-active .sb-running-lbl { opacity:1; }
  .sb-event.sb-nav-active .sb-event-names { color:""" + INK + """; }
  .sb-empty { font-size:16px; color:""" + MUTED + """; padding:4px 0; }
  .sb-manual-form { display:flex; flex-direction:column; gap:10px; }
  .sb-manual-form input { width:100%; background:""" + INSET + """; color:""" + INK + """;
                          border:1px solid """ + HAIR + """; border-radius:7px; padding:10px 12px;
                          font-size:16px; outline:none; font-family:inherit; }
  .sb-manual-form input:focus { border-color:""" + P1C + """; }
  .sb-manual-btn { background:""" + P1C + """; color:#06121f; border:none; border-radius:7px;
                   padding:10px 14px; font-weight:750; font-size:16px; cursor:pointer; width:100%; }
  .sb-manual-btn:hover { filter:brightness(1.08); }
  .add-status { color:""" + MUTED + """; font-size:15px; }

  .card { background:""" + SURFACE + """; border:1px solid """ + HAIR + """; border-radius:12px;
          padding:20px 22px; margin-bottom:20px; scroll-margin-top:24px; }
  .card-head { display:flex; align-items:center; gap:11px; }
  .live-dot { width:9px; height:9px; border-radius:50%; background:""" + GOOD + """;
              box-shadow:0 0 0 3px rgba(38,194,129,.20); }
  .match-title { font-size:18px; font-weight:700; }
  .pill { font-size:16px; color:""" + INK2 + """; background:""" + INSET + """;
          padding:3px 9px; border-radius:5px; }
  .stopbtn { margin-left:auto; background:transparent; color:""" + CRIT + """;
             border:1px solid """ + CRIT + """; border-radius:6px; padding:5px 13px;
             font-weight:650; font-size:16px; cursor:pointer; }
  .stopbtn:hover { background:""" + CRIT + """; color:#fff; }

  .card-ids { margin-top:6px; font-family:ui-monospace,"Cascadia Code",Consolas,monospace;
              font-size:16px; color:""" + MUTED + """; }
  .id-lbl { color:""" + BASE + """; }
  .scoreboard { margin:12px auto 18px; max-width:460px; }
  .sb-row { display:flex; align-items:center; padding:9px 2px; }
  .sb-row + .sb-row { border-top:1px solid """ + GRID + """; }
  .sb-name { flex:1; font-size:17px; font-weight:600; }
  .sb-ball { width:13px; height:13px; border-radius:50%; margin-right:16px; flex:none;
             background:radial-gradient(circle at 35% 30%, #d8f06a, #a5cf34);
             box-shadow:inset 0 0 0 1px rgba(0,0,0,.18); }
  .sb-ball-hidden { width:13px; height:13px; margin-right:16px; flex:none; }
  .sb-sets { display:flex; gap:18px; margin-right:16px; }
  .sb-set { width:14px; text-align:center; font-size:17px; color:""" + MUTED + """;
            font-variant-numeric:tabular-nums; }
  .sb-win { color:""" + INK + """; font-weight:750; }
  .sb-game { min-width:46px; text-align:center; font-size:16px; font-weight:700; padding:5px 0;
             border:1px solid """ + HAIR + """; border-radius:7px; background:""" + INSET + """;
             font-variant-numeric:tabular-nums; }

  .pcol-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .pcol { background:""" + INSET + """; border-radius:10px; padding:15px 17px; }
  .col-head { display:flex; align-items:center; }
  .col-head .badge { margin-left:auto; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; }
  .col-name { font-size:16px; font-weight:700; letter-spacing:.01em; }
  .badge { font-size:16px; font-weight:800; letter-spacing:.05em; color:#04140a;
           background:""" + GOOD + """; padding:3px 8px; border-radius:5px; }
  .gsm-grid { display:flex; justify-content:space-between; align-items:flex-end; padding:2px 0 4px; }
  .gsm-label { font-size:16px; font-weight:600; letter-spacing:.05em; color:""" + MUTED + """;
               margin-bottom:3px; }
  .gsm-val { font-size:30px; font-weight:700; line-height:1; color:""" + INK + """;
             font-variant-numeric:tabular-nums; }

  .grp-label { font-size:14px; font-weight:800; letter-spacing:.09em; color:""" + MUTED + """;
               margin:13px 0 6px; padding-bottom:5px;
               border-bottom:1px solid """ + HAIR + """; }
  .bk-grid { display:grid; grid-template-columns:1fr 1fr; gap:11px; margin-bottom:4px; }
  .bk-tile { background:""" + PLANE + """; border:1px solid """ + HAIR + """; border-radius:9px;
             padding:9px 10px 8px; text-align:center; }
  .bk-label { font-size:16px; font-weight:700; letter-spacing:.05em; color:""" + MUTED + """; }
  .bk-val { font-size:30px; font-weight:700; line-height:1.1; color:""" + INK + """;
            font-variant-numeric:tabular-nums; }
  .bk-sub { font-size:13px; color:""" + MUTED + """; letter-spacing:.01em; }

  .svc-stats { margin-top:4px; }
  .sb2-row { display:grid; grid-template-columns:100px 1fr 210px 1fr 100px; align-items:center;
             gap:12px; padding:9px 0; }
  .sb2-row + .sb2-row { border-top:1px solid """ + GRID + """; }
  .sb2-val { font-size:16px; color:""" + INK + """; font-variant-numeric:tabular-nums; }
  .sb2-left { text-align:right; }
  .sb2-right { text-align:left; }
  .sb2-label { text-align:center; font-size:16px; font-weight:600; color:""" + MUTED + """;
               letter-spacing:.02em; line-height:1.2; }
  .sb2-bar { height:10px; border-radius:5px; background:""" + HAIR + """; display:flex; }
  .sb2-bar-l { justify-content:flex-end; }
  .sb2-fill { height:10px; border-radius:5px; }

  .rb-label { font-size:16px; color:""" + INK2 + """; margin:8px 0 5px; }
  .rb { margin-bottom:2px; }
  .rb-bar { position:relative; height:9px; }
  .rb-track { position:absolute; inset:0; background:""" + HAIR + """; border-radius:5px; }
  .rb-span { position:absolute; top:0; height:9px; opacity:.55; border-radius:5px; }
  .rb-mark { position:absolute; top:-3px; width:3px; height:15px; background:""" + INK + """;
             border-radius:2px; transform:translateX(-1px); }
  .rb-ends { display:flex; justify-content:space-between; margin-top:5px; font-size:16px;
             color:""" + MUTED + """; font-variant-numeric:tabular-nums; }

  .section-label { font-size:16px; font-weight:800; letter-spacing:.07em; color:""" + MUTED + """;
                   margin:18px 0 9px; text-align:center; }
  .sl-row { display:flex; flex-wrap:wrap; gap:9px; justify-content:center; }
  .sl-item { background:""" + INSET + """; border-radius:7px; padding:7px 12px; font-size:16px;
             font-variant-numeric:tabular-nums; }
  .sl-score { color:""" + INK2 + """; margin-right:9px; }
  .sl-pct { font-weight:700; }
  .ou-wrap { display:flex; justify-content:center; }
  .ou-table { border-collapse:collapse; font-variant-numeric:tabular-nums; }
  .ou-table th, .ou-table td { padding:6px 13px; font-size:16px; text-align:center; }
  .ou-table th { color:""" + MUTED + """; font-weight:600; border-bottom:1px solid """ + GRID + """; }
  .ou-table th:first-child, .ou-table td:first-child { color:""" + MUTED + """; text-align:right;
                                                       font-weight:600; }
  .ou-table tbody td { font-weight:650; }
  .chart { margin-top:10px; }

  .card-foot { margin-top:14px; padding-top:4px; border-top:1px solid """ + HAIR + """;
               font-variant-numeric:tabular-nums; }
  .ft-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(152px, 1fr)); gap:11px; }
  .ft-tile { background:""" + INSET + """; border:1px solid """ + HAIR + """; border-radius:9px;
             padding:9px 12px 8px; }
  .ft-label { font-size:14px; font-weight:700; letter-spacing:.06em; color:""" + MUTED + """; }
  .ft-val { font-size:26px; font-weight:700; line-height:1.2; color:""" + INK + """;
            font-variant-numeric:tabular-nums; white-space:nowrap; }
  .ft-val-sm { font-size:20px; }
  .ft-sub { font-size:13px; color:""" + MUTED + """; }

  .sp-stats .sb2-row:last-child .sb2-val { color:""" + INK + """; font-weight:750; }
  .sp-deriv { display:grid; grid-template-columns:repeat(3, 1fr); gap:11px; margin-bottom:12px; }

  .tl-summary { display:flex; gap:20px; margin-bottom:8px; font-size:16px;
                font-variant-numeric:tabular-nums; }
  .tl-stat { color:""" + INK2 + """; }
  .tl-wrap { max-height:260px; overflow-y:auto; overflow-x:auto;
             border:1px solid """ + HAIR + """; border-radius:9px; }
  .tl-wrap .tradelog th { position:sticky; top:0; background:""" + INSET + """; }
  .tradelog { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
  .tradelog th { text-align:left; color:""" + MUTED + """; font-weight:600; font-size:16px;
                 padding:7px 14px; border-bottom:1px solid """ + HAIR + """; letter-spacing:.03em; }
  .tradelog td { padding:7px 14px; font-size:16px; border-bottom:1px solid """ + GRID + """; }
  .empty { color:""" + INK2 + """; padding:18px 4px; }
  h3 { font-size:16px; font-weight:700; color:""" + INK2 + """; letter-spacing:.05em; margin:26px 0 8px; }
</style></head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer>
<script>
// ── scroll-to on sidebar link click ──────────────────────────────────────────
document.addEventListener('click', function(e) {
  var a = e.target.closest('a[href^="#card-"]');
  if (!a) return;
  e.preventDefault();
  var el = document.getElementById(a.getAttribute('href').slice(1));
  if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
});

// ── highlight active card in sidebar ─────────────────────────────────────────
(function() {
  var activeId = null;

  function applyActive() {
    document.querySelectorAll('.sb-event').forEach(function(row) {
      var a = row.querySelector('a.sb-running-lbl');
      var isActive = a && a.getAttribute('href').slice(1) === activeId;
      row.classList.toggle('sb-nav-active', isActive);
    });
  }

  // Only cards in the top ~40% of the viewport count as "active"
  var io = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (e.isIntersecting) activeId = e.target.id;
    });
    applyActive();
  }, {threshold: 0.1, rootMargin: '0px 0px -55% 0px'});

  function observeCards() {
    document.querySelectorAll('.card[id^="card-"]').forEach(function(el) { io.observe(el); });
  }

  // Re-apply highlight whenever sidebar re-renders (Dash 2s tick)
  function watchSidebar() {
    var sb = document.getElementById('sidebar-events');
    if (sb) new MutationObserver(applyActive).observe(sb, {childList:true, subtree:true});
  }

  // Re-observe cards whenever the main area re-renders
  function watchMain() {
    var main = document.getElementById('matches');
    if (main) new MutationObserver(observeCards).observe(main, {childList:true, subtree:true});
  }

  // Wait for Dash to mount its React tree before initialising
  var init = setInterval(function() {
    if (document.getElementById('matches')) {
      clearInterval(init);
      observeCards();
      watchSidebar();
      watchMain();
    }
  }, 200);
})();
</script>
</body></html>"""

app.layout = html.Div(className="wrap", children=[
    dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),
    # ── fixed sidebar ─────────────────────────────────────────────────────────
    html.Div(className="sidebar", children=[
        # brand + status stats
        html.Div(className="sb-section", children=[
            html.Div("Tennis Trading Monitor", className="brand"),
            html.Div(id="sb-mode"),
            html.Div(id="sb-stats", className="sb-stat"),
        ]),
        # live Kalshi events
        html.Div(className="sb-section", children=[
            html.Div("LIVE ON KALSHI", className="sb-panel-title"),
            html.Div(id="sidebar-events"),
        ]),
        # manual launch
        html.Div(className="sb-section", children=[
            html.Div("MANUAL", className="sb-panel-title"),
            html.Div(className="sb-manual-form", children=[
                dcc.Input(id="add-ticker", placeholder="KXATPMATCH-…", debounce=True),
                dcc.Input(id="add-budget", placeholder="budget (optional)", type="text"),
                html.Button("Launch", id="add-btn", n_clicks=0, className="sb-manual-btn"),
                html.Div(id="add-status", className="add-status"),
            ]),
        ]),
    ]),
    # ── main cards ────────────────────────────────────────────────────────────
    html.Div(id="matches", className="main"),
])


@app.callback(Output("matches", "children"), Output("sb-mode", "children"),
              Output("sb-stats", "children"),
              Input("tick", "n_intervals"))
def refresh(_):
    import traceback
    matches = active_matches()
    now = datetime.datetime.now().strftime("%H:%M:%S")
    cards = []
    if matches:
        for t, g in matches.items():
            try:
                cards.append(build_card(t, g))
            except Exception:
                tb = traceback.format_exc()
                cards.append(html.Div([
                    html.Div(t, style={"fontWeight": 700, "marginBottom": "6px"}),
                    html.Pre(tb, style={"fontSize": "12px", "color": CRIT,
                                        "whiteSpace": "pre-wrap", "margin": 0}),
                ], className="card"))
    else:
        cards = [html.Div("No live matches — launch one from the sidebar.", className="empty")]
    total = sum((_num(g.iloc[-1], "budget_remaining") or 0) for g in matches.values())
    mode  = html.Span("DRY RUN" if cfg.DRY_RUN else "LIVE", className="mode")
    stats = f"{len(matches)} live · budget ${total:.2f}\nupdated {now}"
    return cards, mode, stats


@app.callback(Output("add-status", "children"), Input("add-btn", "n_clicks"),
              State("add-ticker", "value"), State("add-budget", "value"),
              prevent_initial_call=True)
def add_match(n, ticker, budget):
    if not ticker or not ticker.strip():
        return "enter an event ticker"
    cmd = [sys.executable, "-m", "trade.trade_bot", ticker.strip()]
    if budget and str(budget).strip():
        try:
            cmd += ["--budget", str(float(budget))]
        except ValueError:
            return "invalid budget"
    try:
        subprocess.Popen(cmd, cwd=REPO_ROOT)
    except Exception as e:
        return f"failed: {e}"
    return f"launched {ticker.strip()} at {datetime.datetime.now():%H:%M:%S}"


@app.callback(Output("add-status", "children", allow_duplicate=True),
              Input({"type": "stop-btn", "event": ALL}, "n_clicks"),
              prevent_initial_call=True)
def stop_match(clicks):
    trig = ctx.triggered[0] if ctx.triggered else None
    if not trig or not trig.get("value"):     # ignore card re-renders (n_clicks reset to 0)
        return dash.no_update
    event = ctx.triggered_id["event"]
    try:
        open(os.path.join(LOG_DIR, f"stop_{event}.flag"), "w").close()
    except OSError as e:
        return f"stop failed: {e}"
    return f"stop requested for {event} — process will exit within a second"


# ── live-event discovery (background thread, updates every AUTO_LAUNCH_POLL_SECS) ──
from trade.kalshi_client import get_event_competitor_map

def _running_events():
    """Event tickers that already have a live bot, keyed by the lock file each bot
    touches every tick. Authoritative from the moment a bot starts."""
    evs = set()
    for f in glob.glob(os.path.join(LOG_DIR, ".bot_*.lock")):
        if time.time() - os.path.getmtime(f) < cfg.BOT_LOCK_STALE_SECS:
            evs.add(os.path.basename(f)[len(".bot_"):-len(".lock")])
    return evs


# Module-level cache updated by _discovery_loop; read by the sidebar callback.
_discovered: list = []            # [{"ticker": str, "p1": str, "p2": str}, ...]
_discovered_lock = threading.Lock()
_name_cache: dict = {}            # event_ticker -> {"p1": str, "p2": str}  (persists across cycles)


def _discovery_loop():
    while True:
        try:
            live = discover_live_events(tuple(cfg.AUTO_LAUNCH_SERIES))
            events = []
            for ev, _mid in live:
                ev_map = get_event_competitor_map(ev) or {}
                names  = [v["name"] for v in ev_map.values() if v.get("name")]
                if len(names) >= 2:
                    _name_cache[ev] = {"p1": names[0], "p2": names[1]}
                n = _name_cache.get(ev, {"p1": "?", "p2": "?"})
                events.append({"ticker": ev, "p1": n["p1"], "p2": n["p2"]})
            with _discovered_lock:
                _discovered[:] = events
        except Exception as e:
            print(f"[discover] error: {e}")
        time.sleep(cfg.AUTO_LAUNCH_POLL_SECS)


@app.callback(Output("sidebar-events", "children"), Input("tick", "n_intervals"))
def refresh_sidebar(_):
    with _discovered_lock:
        events = list(_discovered)
    running = _running_events()

    if not events:
        return html.Div("Scanning…", className="sb-empty")

    rows = []
    for ev in events:
        ticker = ev["ticker"]
        is_running = ticker in running
        rows.append(html.Div([
            html.Div(f"{ev['p1']} vs {ev['p2']}", className="sb-event-names"),
            html.Div(ticker, className="sb-event-ticker"),
            html.A("● running", href=f"#card-{ticker}", className="sb-running-lbl") if is_running else
            html.Button("Launch", id={"type": "sidebar-launch", "event": ticker},
                        n_clicks=0, className="sb-launch-btn"),
        ], className="sb-event"))
    return rows


@app.callback(Output("add-status", "children", allow_duplicate=True),
              Input({"type": "sidebar-launch", "event": ALL}, "n_clicks"),
              prevent_initial_call=True)
def sidebar_launch(clicks):
    trig = ctx.triggered[0] if ctx.triggered else None
    if not trig or not trig.get("value"):
        return dash.no_update
    ticker = ctx.triggered_id["event"]
    try:
        subprocess.Popen([sys.executable, "-m", "trade.trade_bot", ticker], cwd=REPO_ROOT)
    except Exception as e:
        return f"failed: {e}"
    return f"launched {ticker} at {datetime.datetime.now():%H:%M:%S}"


_DEBUG = True   # hot reload

if __name__ == "__main__":
    # Start discovery thread once, in the process that actually serves requests
    # (not the Werkzeug reloader parent), so we don't double-scan under hot reload.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not _DEBUG:
        threading.Thread(target=_discovery_loop, daemon=True).start()
        print(f"[discover] scanning {cfg.AUTO_LAUNCH_SERIES} every {cfg.AUTO_LAUNCH_POLL_SECS}s")
    app.run(host="127.0.0.1", port=8050, debug=_DEBUG)
