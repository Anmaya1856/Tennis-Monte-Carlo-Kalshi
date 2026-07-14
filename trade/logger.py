import csv, os, datetime
import trade.config as cfg

_GAMES_COLS = [f"p_games_over_{str(t).replace('.', '_')}" for t in cfg.GAME_THRESHOLDS]

_TRADE_COLS = [
    "timestamp", "ticker", "p1_name", "p2_name", "direction", "event",
    "entry_price", "exit_price", "mc_prob_at_entry", "bet_amount",
    "fee", "pnl", "budget_remaining",
]

_SNAPSHOT_COLS = [
    "timestamp", "ticker", "p1_name", "p2_name", "score_str", "game_score_str", "server",
    "mc_prob_p1", "mc_set_prob_p1", "mc_game_prob_p1",
    "kalshi_p1_ask", "kalshi_p1_bid", "kalshi_p2_ask", "kalshi_p2_bid",
    "position_side", "position_entry_price", "position_count", "position_high_water",
    "position_current_value", "position_unrealized_pnl", "budget_remaining",
    "divergence_ema", "standdown",
    # market-implied prior (live model) + career shadow model
    "prematch_price", "pa0", "pb0", "pa_blend", "pb_blend", "career_prob_p1",
    # derived DP distributions (point estimate; forward-looking from current score)
    "sc_p1_d0", "sc_p1_d1", "sc_p1_d2", "sc_p2_d0", "sc_p2_d1", "sc_p2_d2",
    "p1_set1", "p2_set1", "p1_set2", "p2_set2", "p1_set3", "p2_set3",
    "p1_set4", "p2_set4", "p1_set5", "p2_set5",
    *_GAMES_COLS,
    "p1_first_serve_pct",    "p1_first_serve_num",    "p1_first_serve_den",
    "p1_first_serve_won_pct","p1_first_serve_won_num","p1_first_serve_won_den",
    "p1_second_serve_won_pct","p1_second_serve_won_num","p1_second_serve_won_den",
    "p1_first_return_won_pct","p1_first_return_won_num","p1_first_return_won_den",
    "p1_second_return_won_pct","p1_second_return_won_num","p1_second_return_won_den",
    "p2_first_serve_pct",    "p2_first_serve_num",    "p2_first_serve_den",
    "p2_first_serve_won_pct","p2_first_serve_won_num","p2_first_serve_won_den",
    "p2_second_serve_won_pct","p2_second_serve_won_num","p2_second_serve_won_den",
    "p2_first_return_won_pct","p2_first_return_won_num","p2_first_return_won_den",
    "p2_second_return_won_pct","p2_second_return_won_num","p2_second_return_won_den",
    "p1_last10_pts_won", "p2_last10_pts_won",
    # Hawkeye extras (break-point research)
    "p1_bp_saved_num", "p1_bp_saved_den", "p1_bp_conv_num", "p1_bp_conv_den",
    "p1_serve_rating", "p1_return_rating",
    "p2_bp_saved_num", "p2_bp_saved_den", "p2_bp_conv_num", "p2_bp_conv_den",
    "p2_serve_rating", "p2_return_rating",
    # Kalshi shot-level stats (break-point research)
    "p1_k_aces", "p1_k_double_faults", "p1_k_winners_fh", "p1_k_winners_bh",
    "p1_k_unforced_fh", "p1_k_unforced_bh", "p1_k_errors_groundstroke",
    "p1_k_max_pts_streak", "p1_k_max_games_streak", "p1_k_bp_won", "p1_k_bp_total",
    "p2_k_aces", "p2_k_double_faults", "p2_k_winners_fh", "p2_k_winners_bh",
    "p2_k_unforced_fh", "p2_k_unforced_bh", "p2_k_errors_groundstroke",
    "p2_k_max_pts_streak", "p2_k_max_games_streak", "p2_k_bp_won", "p2_k_bp_total",
    "match_outcome",
]

_HAWKEYE_EXTRAS = ["bp_saved_num", "bp_saved_den", "bp_conv_num", "bp_conv_den",
                   "serve_rating", "return_rating"]
_KALSHI_EXTRAS = ["aces", "double_faults", "winners_fh", "winners_bh", "unforced_fh",
                  "unforced_bh", "errors_groundstroke", "max_pts_streak",
                  "max_games_streak", "bp_won", "bp_total"]


def _now_str():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _append(filename, cols, row):
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    base, ext = os.path.splitext(filename)
    path = os.path.join(cfg.LOG_DIR, base + cfg.LOG_SUFFIX + ext)
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def log_trade(ticker, p1_name, p2_name, direction, event, entry_price, exit_price,
              mc_prob_at_entry, bet_amount, fee, pnl, budget_remaining):
    _append("trade_log.csv", _TRADE_COLS, {
        "timestamp":        _now_str(),
        "ticker":           ticker,
        "p1_name":          p1_name,
        "p2_name":          p2_name,
        "direction":        direction,
        "event":            event,
        "entry_price":      entry_price,
        "exit_price":       exit_price,
        "mc_prob_at_entry": mc_prob_at_entry,
        "bet_amount":       bet_amount,
        "fee":              fee,
        "pnl":              pnl,
        "budget_remaining": budget_remaining,
    })


def log_snapshot(ticker, p1_name, p2_name, score_str, game_score_str, server,
                 p1_stats, p2_stats, p1_last10, p2_last10,
                 mc_prob_p1, mc_set_prob_p1, mc_game_prob_p1,
                 p1_ask, p1_bid, p2_ask, p2_bid,
                 ms, pos_side, pos_value, p1_kstats=None, p2_kstats=None,
                 prematch_price=None, pa0=None, pb0=None, pa_blend=None, pb_blend=None,
                 career_prob_p1=None, report=None):
    """ms: MatchState for this match; pos_side/pos_value: 'p1'/'p2' and owned-market
    bid when a position is open, else None. p1_kstats/p2_kstats: Kalshi shot-level dicts.
    prematch/pa/pb/career: live market-prior inputs + career shadow-model probability.
    report: exact.match_report() dict (scorelines/set_win/over_games), or None."""
    pos = ms.position
    market = {"prematch_price": prematch_price, "pa0": pa0, "pb0": pb0,
              "pa_blend": pa_blend, "pb_blend": pb_blend, "career_prob_p1": career_prob_p1}
    market = {k: ("" if v is None else v) for k, v in market.items()}
    rep = {}
    sc = (report or {}).get("scorelines", {})
    sw = (report or {}).get("set_win", [])
    og = (report or {}).get("over_games", {})
    for pl in ("p1", "p2"):
        for dd in (0, 1, 2):
            rep[f"sc_{pl}_d{dd}"] = round(sc.get((pl, dd), 0.0), 4) if report else ""
    for i in range(5):
        rep[f"p1_set{i+1}"] = round(sw[i][0], 4) if (report and i < len(sw)) else ""
        rep[f"p2_set{i+1}"] = round(sw[i][1], 4) if (report and i < len(sw)) else ""
    for t, col in zip(cfg.GAME_THRESHOLDS, _GAMES_COLS):
        rep[col] = round(og.get(t, 0.0), 4) if report else ""
    extras = {}
    for p, stats in (("p1", p1_stats), ("p2", p2_stats)):
        for k in _HAWKEYE_EXTRAS:
            extras[f"{p}_{k}"] = stats.get(k, "")
    for p, ks in (("p1", p1_kstats), ("p2", p2_kstats)):
        for k in _KALSHI_EXTRAS:
            extras[f"{p}_k_{k}"] = (ks or {}).get(k, "")
    _append("match_snapshots.csv", _SNAPSHOT_COLS, {
        **extras,
        **market,
        **rep,
        "timestamp":                  _now_str(),
        "ticker":                     ticker,
        "p1_name":                    p1_name,
        "p2_name":                    p2_name,
        "score_str":                  score_str,
        "game_score_str":             game_score_str,
        "server":                     server,
        "mc_prob_p1":                 mc_prob_p1,
        "mc_set_prob_p1":             mc_set_prob_p1,
        "mc_game_prob_p1":            mc_game_prob_p1,
        "kalshi_p1_ask":              p1_ask,
        "kalshi_p1_bid":              p1_bid,
        "kalshi_p2_ask":              p2_ask,
        "kalshi_p2_bid":              p2_bid,
        "position_side":              pos_side if pos else "",
        "position_entry_price":       pos["entry_price"] if pos else "",
        "position_count":             pos["count"] if pos else "",
        "position_high_water":        pos["high_water"] if pos else "",
        "position_current_value":     pos_value if pos else "",
        "position_unrealized_pnl":    round(pos["count"] * (pos_value - pos["entry_price"]), 4) if pos else "",
        "budget_remaining":           ms.budget_remaining,
        "divergence_ema":             round(ms.divergence_ema, 4),
        "standdown":                  int(ms.standdown),
        "p1_first_serve_pct":         p1_stats["first_in"],
        "p1_first_serve_num":         p1_stats["first_in_num"],
        "p1_first_serve_den":         p1_stats["first_in_den"],
        "p1_first_serve_won_pct":     p1_stats["win_first"],
        "p1_first_serve_won_num":     p1_stats["win_first_num"],
        "p1_first_serve_won_den":     p1_stats["win_first_den"],
        "p1_second_serve_won_pct":    p1_stats["win_second"],
        "p1_second_serve_won_num":    p1_stats["win_second_num"],
        "p1_second_serve_won_den":    p1_stats["win_second_den"],
        "p1_first_return_won_pct":    p1_stats["return_first"],
        "p1_first_return_won_num":    p1_stats["return_first_num"],
        "p1_first_return_won_den":    p1_stats["return_first_den"],
        "p1_second_return_won_pct":   p1_stats["return_second"],
        "p1_second_return_won_num":   p1_stats["return_second_num"],
        "p1_second_return_won_den":   p1_stats["return_second_den"],
        "p2_first_serve_pct":         p2_stats["first_in"],
        "p2_first_serve_num":         p2_stats["first_in_num"],
        "p2_first_serve_den":         p2_stats["first_in_den"],
        "p2_first_serve_won_pct":     p2_stats["win_first"],
        "p2_first_serve_won_num":     p2_stats["win_first_num"],
        "p2_first_serve_won_den":     p2_stats["win_first_den"],
        "p2_second_serve_won_pct":    p2_stats["win_second"],
        "p2_second_serve_won_num":    p2_stats["win_second_num"],
        "p2_second_serve_won_den":    p2_stats["win_second_den"],
        "p2_first_return_won_pct":    p2_stats["return_first"],
        "p2_first_return_won_num":    p2_stats["return_first_num"],
        "p2_first_return_won_den":    p2_stats["return_first_den"],
        "p2_second_return_won_pct":   p2_stats["return_second"],
        "p2_second_return_won_num":   p2_stats["return_second_num"],
        "p2_second_return_won_den":   p2_stats["return_second_den"],
        "p1_last10_pts_won":          p1_last10,
        "p2_last10_pts_won":          p2_last10,
        "match_outcome":              "",
    })
