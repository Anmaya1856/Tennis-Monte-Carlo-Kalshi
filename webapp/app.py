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
from trade.decision import edge_threshold
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


def recent_trades(limit=15):
    files = glob.glob(os.path.join(LOG_DIR, "trade_log_*.csv"))
    frames = []
    for f in sorted(files, key=os.path.getmtime)[-5:]:
        try:
            frames.append(pd.read_csv(f))
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).tail(limit).iloc[::-1]


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
        best_of = int(float(last.get("best_of") or 3))
        sets_won, cur = _parse_match_state(str(last.get("score_str") or "0-0"), best_of)
        in_tb = cur == (6, 6)
        gs = _parse_game_score(str(last.get("game_score_str") or "0-0"), is_tiebreak=in_tb)
        r = win_probs(pa, pb, sets_won, cur or (0, 0), in_tb, gs,
                      last.get("server") == "p1", best_of)
        return {k: float(r["cond"][k]) for k in keys}
    except Exception:
        return None


# ── formatting ────────────────────────────────────────────────────────────────
def _pct(x, d=1):
    return f"{x * 100:.{d}f}%" if x is not None else "—"


def _cents(x):
    return f"{x:.2f}" if x is not None else "—"


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


def player_col(nm, color, match, bid, ask, game, sett, br_game, br_set):
    tradeable = match is not None and ask is not None and (match - ask) >= edge_threshold(ask)
    badge = html.Span("EDGE", className="badge") if tradeable else None
    def gsm(label, val, align):
        return html.Div([html.Div(label, className="gsm-label"),
                         html.Div(_pct(val, 1), className="gsm-val")],
                        className="gsm-cell", style={"textAlign": align})

    return html.Div([
        html.Div([html.Span(className="dot", style={"background": color}),
                  html.Span(nm, className="col-name"), badge], className="col-head"),
        html.Div([gsm("GAME", game, "left"), gsm("SET", sett, "center"),
                  gsm("MATCH", match, "right")], className="gsm-grid"),
        html.Div([html.Span(f"bid {_cents(bid)}"), html.Span(f"ask {_cents(ask)}")],
                 className="col-book"),
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
        best_of = int(float(last.get("best_of") or 3))
        sets_won, cur = _parse_match_state(str(last.get("score_str") or "0-0"), best_of)
        in_tb = cur == (6, 6)
        gs = _parse_game_score(str(last.get("game_score_str") or "0-0"), is_tiebreak=in_tb)
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


def dist_fig(fwd, p1, p2):
    """Histogram of p1's match win% at the far end of the forecast — many states
    are reachable there, so it shows the distribution shape (spread / skew /
    bimodality) that the cone's percentile bands can't."""
    fig = go.Figure()
    khoriz = (len(fwd["levels"]) - 1) if fwd and fwd["levels"] else 0
    pairs = fwd["levels"][-1] if fwd and fwd["levels"] else []
    if pairs:
        nbins = 13
        binp = [0.0] * nbins
        for v, p in pairs:
            binp[min(int(v * nbins), nbins - 1)] += p
        centers = [(i + 0.5) * 100 / nbins for i in range(nbins)]
        colors = [P1C if c >= 50 else P2C for c in centers]
        fig.add_bar(x=centers, y=[b * 100 for b in binp], marker_color=colors,
                    width=100 / nbins * 0.88, marker_line_width=0,
                    hovertemplate="%{x:.0f}% win: %{y:.1f}% chance<extra></extra>")
    fig.update_layout(
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, height=340,
        margin=dict(l=40, r=14, t=46, b=30), bargap=0.06,
        font=dict(color=INK2, size=14, family='system-ui,-apple-system,"Segoe UI",sans-serif'),
        title=dict(text=f"<b>{p1} win% in {khoriz} games</b>", font=dict(size=18, color=INK),
                   x=0, y=0.97, yanchor="top"),
        xaxis=dict(gridcolor=GRID, zeroline=False, linecolor=BASE, range=[0, 100],
                   ticksuffix="%", title=dict(text=f"← {p2}    {p1} →", font=dict(size=13))),
        yaxis=dict(gridcolor=GRID, zeroline=False, linecolor=BASE, ticksuffix="%"))
    return fig


def scoreboard(p1full, p2full, last):
    """Two-line scoreboard, one row per player: name · serve · per-set games · game point."""
    score_str = str(last.get("score_str") or "").strip()
    game_str = str(last.get("game_score_str") or "")
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
    best_of = int(float(last.get("best_of") or 3))

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

    cols = html.Div([
        player_col(p1, P1C, mc, p1b, p1a, gm, st, p1_bg, p1_bs),
        player_col(p2, P2C, None if mc is None else 1 - mc, p2b, p2a,
                   None if gm is None else 1 - gm, None if st is None else 1 - st,
                   p2_bg, p2_bs),
    ], className="pcol-grid")

    # footer
    side = last.get("position_side")
    budget = _num(last, "budget_remaining")
    standdown = str(last.get("standdown")) in ("1", "1.0", "True")
    if side in ("p1", "p2"):
        ep, cnt = _num(last, "position_entry_price"), _num(last, "position_count")
        cur, upnl = _num(last, "position_current_value"), _num(last, "position_unrealized_pnl")
        who = p1 if side == "p1" else p2
        pos = html.Span([html.Span("● ", style={"color": P1C if side == "p1" else P2C}),
                         f"{who} @{_cents(ep)} ×{cnt:g}  now {_cents(cur)}  ",
                         html.Span(f"${upnl:+.2f}", style={"color": GOOD if (upnl or 0) >= 0 else CRIT,
                                                           "fontWeight": 700})]
                        if upnl is not None else "—")
    else:
        pos = html.Span("No position", style={"color": MUTED})
    footer = html.Div([
        html.Div(pos, className="pos"),
        html.Div([html.Span("⏸ STANDDOWN", className="standdown") if standdown else None,
                  html.Span(f"budget ${budget:.2f}" if budget is not None else "", className="budget")],
                 className="foot-right"),
    ], className="card-foot")

    fwd = compute_forward(last)
    charts = html.Div([
        dcc.Graph(figure=fan_fig(g, p1, fwd), config={"displayModeBar": False}, className="chart"),
        dcc.Graph(figure=dist_fig(fwd, p1, p2), config={"displayModeBar": False}, className="chart"),
    ], className="chart-grid2")

    return html.Div([header, ids, score, cols, service_stats(last),
                     scoreline_readout(last, best_of, p1, p2),
                     over_under_readout(last), charts, footer], className="card")


def build_tradelog(df):
    if df.empty:
        return html.Div("No trades yet.", className="empty")
    cols = [("timestamp", "time"), ("ticker", "market"), ("direction", "action"),
            ("entry_price", "entry"), ("exit_price", "exit"), ("pnl", "P&L")]
    head = html.Tr([html.Th(h) for _, h in cols])
    rows = []
    for _, r in df.iterrows():
        try:
            pnl_f = float(r.get("pnl"))
        except (TypeError, ValueError):
            pnl_f = None
        cells = []
        for c, _h in cols:
            val, style = r.get(c), {}
            if c == "pnl" and pnl_f is not None:
                val = f"${pnl_f:+.2f}"
                style = {"color": GOOD if pnl_f >= 0 else CRIT, "fontWeight": 700}
            cells.append(html.Td("" if pd.isna(val) else str(val), style=style))
        rows.append(html.Tr(cells))
    return html.Table([html.Thead(head), html.Tbody(rows)], className="tradelog")


# ── app ───────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="Tennis Trading Monitor")

app.index_string = """<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  * { box-sizing:border-box; }
  body { margin:0; background:""" + PLANE + """; color:""" + INK + """; font-size:16px;
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { max-width:1200px; margin:0 auto; padding:22px 26px 60px; }
  .topbar { display:flex; align-items:center; gap:16px; margin-bottom:6px; }
  .brand { font-size:20px; font-weight:750; letter-spacing:-.01em; }
  .status { color:""" + INK2 + """; font-size:16px; font-variant-numeric:tabular-nums; }
  .mode { font-size:16px; font-weight:800; padding:2px 8px; border-radius:5px;
          background:rgba(250,178,25,.16); color:""" + WARN + """; letter-spacing:.05em; margin-right:8px; }
  .addbar { display:flex; align-items:center; gap:8px; margin:14px 0 22px; }
  .addbar input { background:""" + INSET + """; color:""" + INK + """; border:1px solid """ + HAIR + """;
                  border-radius:7px; padding:9px 12px; font-size:16px; outline:none; }
  .addbar input:focus { border-color:""" + P1C + """; }
  .addbtn { background:""" + P1C + """; color:#06121f; border:none; border-radius:7px;
            padding:9px 16px; font-weight:750; font-size:16px; cursor:pointer; }
  .addbtn:hover { filter:brightness(1.08); }
  .add-status { color:""" + MUTED + """; font-size:16px; }

  .card { background:""" + SURFACE + """; border:1px solid """ + HAIR + """; border-radius:12px;
          padding:20px 22px; margin-bottom:20px; }
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
  .gsm-grid { display:flex; justify-content:space-between; align-items:flex-end; padding:10px 0 8px; }
  .gsm-label { font-size:16px; font-weight:600; letter-spacing:.05em; color:""" + MUTED + """;
               margin-bottom:3px; }
  .gsm-val { font-size:30px; font-weight:700; line-height:1; color:""" + INK + """;
             font-variant-numeric:tabular-nums; }
  .col-book { display:flex; gap:16px; font-size:16px; color:""" + INK2 + """;
              font-variant-numeric:tabular-nums; margin:2px 0 12px; }

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
  .chart-grid2 { display:grid; grid-template-columns:3fr 2fr; gap:14px; margin-top:10px; }

  .card-foot { display:flex; align-items:center; margin-top:12px; padding-top:13px;
               border-top:1px solid """ + HAIR + """; font-size:16px; font-variant-numeric:tabular-nums; }
  .foot-right { margin-left:auto; display:flex; align-items:center; gap:16px; }
  .standdown { color:""" + WARN + """; font-weight:700; font-size:16px; }
  .budget { color:""" + INK2 + """; }

  .tradelog { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
  .tradelog th { text-align:left; color:""" + MUTED + """; font-weight:600; font-size:16px;
                 padding:7px 14px; border-bottom:1px solid """ + HAIR + """; letter-spacing:.03em; }
  .tradelog td { padding:7px 14px; font-size:16px; border-bottom:1px solid """ + GRID + """; }
  .empty { color:""" + INK2 + """; padding:18px 4px; }
  h3 { font-size:16px; font-weight:700; color:""" + INK2 + """; letter-spacing:.05em; margin:26px 0 8px; }
</style></head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>"""

app.layout = html.Div(className="wrap", children=[
    dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),
    html.Div([html.Span("Tennis Trading Monitor", className="brand"),
              html.Span(id="status", className="status")], className="topbar"),
    html.Div([
        dcc.Input(id="add-ticker", placeholder="KXATPMATCH-…", debounce=True),
        dcc.Input(id="add-budget", placeholder="budget", type="number", style={"width": "96px"}),
        html.Button("Add match", id="add-btn", n_clicks=0, className="addbtn"),
        html.Span(id="add-status", className="add-status"),
    ], className="addbar"),
    html.Div(id="matches"),
    html.H3("TRADE LOG"),
    html.Div(id="tradelog"),
])


@app.callback(Output("matches", "children"), Output("status", "children"),
              Output("tradelog", "children"), Input("tick", "n_intervals"))
def refresh(_):
    matches = active_matches()
    now = datetime.datetime.now().strftime("%H:%M:%S")
    if matches:
        cards = [build_card(t, g) for t, g in matches.items()]
    else:
        cards = [html.Div("No live matches. Add one above, or run "
                          "python -m trade.trade_bot <event_ticker>.", className="empty")]
    total = sum((_num(g.iloc[-1], "budget_remaining") or 0) for g in matches.values())
    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    status = html.Span([html.Span(mode, className="mode"),
                        f"{len(matches)} live · budget ${total:.2f} · updated {now}"])
    return cards, status, build_tradelog(recent_trades())


@app.callback(Output("add-status", "children"), Input("add-btn", "n_clicks"),
              State("add-ticker", "value"), State("add-budget", "value"),
              prevent_initial_call=True)
def add_match(n, ticker, budget):
    if not ticker or not ticker.strip():
        return "enter an event ticker"
    cmd = [sys.executable, "-m", "trade.trade_bot", ticker.strip()]
    if budget:
        cmd += ["--budget", str(budget)]
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


# ── auto-launch ───────────────────────────────────────────────────────────────
def _running_events():
    """Event tickers that already have a live bot: fresh snapshot logs, plus a
    recent spawn-lock file (covers bots that started but aren't live-logging yet).
    The lock file survives a web-app reload, so we never double-spawn a match."""
    evs = {tk.rsplit("-", 1)[0] for tk in active_matches()}
    for f in glob.glob(os.path.join(LOG_DIR, ".spawn_*.lock")):
        if time.time() - os.path.getmtime(f) < 300:
            evs.add(os.path.basename(f)[len(".spawn_"):-len(".lock")])
    return evs


def _auto_launch_loop():
    while True:
        try:
            live = discover_live_events(tuple(cfg.AUTO_LAUNCH_SERIES))
            active = _running_events()
            for ev, _mid in live:
                if len(active) >= cfg.AUTO_LAUNCH_MAX:
                    break
                if ev in active:
                    continue
                try:
                    subprocess.Popen([sys.executable, "-m", "trade.trade_bot", ev], cwd=REPO_ROOT)
                    open(os.path.join(LOG_DIR, f".spawn_{ev}.lock"), "w").close()
                    active.add(ev)
                    print(f"[auto] launched {ev}  ({len(live)} live in series)")
                except Exception as e:
                    print(f"[auto] spawn failed {ev}: {e}")
        except Exception as e:
            print(f"[auto] discovery error: {e}")
        time.sleep(cfg.AUTO_LAUNCH_POLL_SECS)


_DEBUG = True   # hot reload

if __name__ == "__main__":
    # Start the auto-launcher once, in the process that actually serves (not the
    # Werkzeug reloader parent), so bots aren't spawned twice under hot reload.
    if cfg.AUTO_LAUNCH and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not _DEBUG):
        threading.Thread(target=_auto_launch_loop, daemon=True).start()
        print(f"[auto] auto-launch ON for {cfg.AUTO_LAUNCH_SERIES} (max {cfg.AUTO_LAUNCH_MAX})")
    app.run(host="127.0.0.1", port=8050, debug=_DEBUG)
