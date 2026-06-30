import csv, os, datetime
import trade.config as cfg

_TRADE_COLS = [
    "timestamp", "ticker", "p1_name", "p2_name", "direction", "event",
    "entry_price", "exit_price", "mc_prob_at_entry", "bet_amount",
    "fee", "pnl", "budget_remaining",
]

_SNAPSHOT_COLS = [
    "timestamp", "ticker", "p1_name", "p2_name", "score_str", "game_score_str", "server",
    "p1_first_serve_pct", "p1_first_serve_won_pct", "p1_second_serve_won_pct",
    "p1_first_return_won_pct", "p1_second_return_won_pct",
    "p2_first_serve_pct", "p2_first_serve_won_pct", "p2_second_serve_won_pct",
    "p2_first_return_won_pct", "p2_second_return_won_pct",
    "mc_prob_p1", "kalshi_yes_ask", "kalshi_yes_bid", "match_outcome",
]


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
        "timestamp":        datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
                 p1_stats, p2_stats, mc_prob_p1, yes_ask, yes_bid):
    _append("match_snapshots.csv", _SNAPSHOT_COLS, {
        "timestamp":                datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ticker":                   ticker,
        "p1_name":                  p1_name,
        "p2_name":                  p2_name,
        "score_str":                score_str,
        "game_score_str":           game_score_str,
        "server":                   server,
        "p1_first_serve_pct":       p1_stats["first_in"],
        "p1_first_serve_won_pct":   p1_stats["win_first"],
        "p1_second_serve_won_pct":  p1_stats["win_second"],
        "p1_first_return_won_pct":  p1_stats["return_first"],
        "p1_second_return_won_pct": p1_stats["return_second"],
        "p2_first_serve_pct":       p2_stats["first_in"],
        "p2_first_serve_won_pct":   p2_stats["win_first"],
        "p2_second_serve_won_pct":  p2_stats["win_second"],
        "p2_first_return_won_pct":  p2_stats["return_first"],
        "p2_second_return_won_pct": p2_stats["return_second"],
        "mc_prob_p1":               mc_prob_p1,
        "kalshi_yes_ask":           yes_ask,
        "kalshi_yes_bid":           yes_bid,
        "match_outcome":            "",
    })
