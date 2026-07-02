import csv, os, datetime
import trade.config as cfg

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
    "p1_last10_pts_won", "p2_last10_pts_won", "match_outcome",
]


def _now_str():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _append(filename, cols, row):
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    path = os.path.join(cfg.LOG_DIR, filename)
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
                 ms, pos_side, pos_value):
    """ms: MatchState for this match; pos_side/pos_value: 'p1'/'p2' and owned-market
    bid when a position is open, else None."""
    pos = ms.position
    _append("match_snapshots.csv", _SNAPSHOT_COLS, {
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
