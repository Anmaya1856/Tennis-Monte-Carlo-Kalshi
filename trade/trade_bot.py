"""
Kalshi in-play tennis trading bot.

Set DRY_RUN = True in config.py (default) to paper trade.
Set MATCH_CONFIG in config.py with at least one entry before running.

Usage:
    python -m trade.trade_bot
"""
import re, threading, time, unicodedata

import trade.config as cfg
from trade.atp_client    import fetch_match_state
from trade.kalshi_client import (get_best_ask_bid, place_order, close_position,
                                  fetch_milestone, fetch_milestone_id,
                                  get_event_competitor_map, parse_milestone_state)
from trade.simulation    import estimate_win_prob
from trade.state         import MatchStateStore
from trade.decision      import compute_entry, should_stop_loss, should_take_profit
from trade               import logger


_store = MatchStateStore()

# event_ticker → {"kalshi_ticker", "p1_competitor_id", "p1_name_kalshi"}
_match_cache = {}


def _norm(name):
    """Normalize player name for fuzzy comparison: strip diacritics, lowercase, letters only."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]", "", name.lower())


def _slow_loop():
    """Every 20s: poll ATP stats → Kalshi score/server → MC → entry decisions."""
    while True:
        for mc in cfg.MATCH_CONFIG:
            event_ticker = mc["event_ticker"]

            # Resolve canonical ticker + p1 identity + milestone ID once per event (cached)
            if event_ticker not in _match_cache:
                event_map = get_event_competitor_map(event_ticker)
                if event_map is None:
                    continue
                milestone_id = fetch_milestone_id(event_ticker)
                if milestone_id is None:
                    continue
                # p1 = alphabetically first market ticker
                kalshi_ticker = min(info["ticker"] for info in event_map.values())
                p1_cid = next(
                    cid for cid, info in event_map.items()
                    if info["ticker"] == kalshi_ticker
                )
                _match_cache[event_ticker] = {
                    "kalshi_ticker":    kalshi_ticker,
                    "p1_competitor_id": p1_cid,
                    "p1_name_kalshi":   event_map[p1_cid]["name"],
                    "milestone_id":     milestone_id,
                }

            cached        = _match_cache[event_ticker]
            ticker        = cached["kalshi_ticker"]
            p1_cid        = cached["p1_competitor_id"]
            p1_name_k     = cached["p1_name_kalshi"]

            if _store.is_budget_exhausted(ticker):
                continue

            # Step 1: ATP stats
            atp = fetch_match_state(mc["hawkeye_url"])
            if atp is None:
                continue

            # Align: if Hawkeye Team1 ≠ Kalshi p1, swap stats
            if _norm(atp["p1_name"]) == _norm(p1_name_k):
                p1_stats, p2_stats = atp["p1_stats"], atp["p2_stats"]
                p1_name, p2_name   = atp["p1_name"],  atp["p2_name"]
            else:
                p1_stats, p2_stats = atp["p2_stats"], atp["p1_stats"]
                p1_name, p2_name   = atp["p2_name"],  atp["p1_name"]

            # Step 2: Kalshi live score + server
            details = fetch_milestone(cached["milestone_id"])
            if details is None:
                continue
            kalshi_state = parse_milestone_state(details, p1_cid)
            if not kalshi_state["is_live"]:
                continue

            # Step 3: MC simulation
            mc_prob = estimate_win_prob(
                p1_stats, p2_stats,
                kalshi_state["score_str"],
                kalshi_state["game_score_str"],
                kalshi_state["p1_serves"],
                atp["best_of"],
                n_sims=cfg.N_SIMS,
            )

            yes_ask, yes_bid = get_best_ask_bid(ticker)
            if yes_ask is None:
                continue

            logger.log_snapshot(
                ticker, p1_name, p2_name,
                kalshi_state["score_str"], kalshi_state["game_score_str"],
                "p1" if kalshi_state["p1_serves"] else "p2",
                p1_stats, p2_stats,
                mc_prob, yes_ask, yes_bid,
            )

            if _store.has_position(ticker) or _store.is_in_cooldown(ticker):
                continue

            ms = _store.get_or_create(ticker)
            order_params = compute_entry(mc_prob, yes_ask, yes_bid, ms.budget_remaining)
            if order_params is None:
                continue

            fill = place_order(
                ticker,
                order_params["side"],
                order_params["count"],
                order_params["yes_price_cents"],
            )
            if fill is None:
                continue

            _store.deduct_fill(ticker, fill["cost_dollars"], fill["fee_dollars"])
            _store.set_position(ticker, order_params["side"], order_params["entry_price"],
                                mc_prob, order_params["count"])

            logger.log_trade(
                ticker, p1_name, p2_name,
                order_params["side"], "entry",
                order_params["entry_price"], None,
                mc_prob, fill["cost_dollars"], fill["fee_dollars"], None,
                _store.get_or_create(ticker).budget_remaining,
            )
            print(f"[entry] {ticker}  side={order_params['side']}  "
                  f"price={order_params['entry_price']:.2f}  mc={mc_prob:.3f}  "
                  f"count={order_params['count']}  dry={cfg.DRY_RUN}")

        time.sleep(cfg.SLOW_POLL_SECS)


def _fast_loop():
    """Every 2s: check open positions for stop loss / profit target."""
    while True:
        for mc in cfg.MATCH_CONFIG:
            ticker = mc["kalshi_ticker"]

            if not _store.has_position(ticker):
                continue

            pos = _store.get_or_create(ticker).position
            yes_ask, yes_bid = get_best_ask_bid(ticker)
            if yes_ask is None or yes_bid is None:
                continue

            if pos["side"] == "yes":
                current_value     = yes_bid
                close_price_cents = round(yes_bid * 100)
            else:
                current_value     = 1 - yes_ask
                close_price_cents = 100 - round(yes_ask * 100)

            exit_reason = None
            if should_stop_loss(pos["entry_price"], current_value):
                exit_reason = "stop_loss"
            elif should_take_profit(pos["mc_prob_at_entry"], current_value):
                exit_reason = "profit_target"

            if exit_reason is None:
                continue

            fill = close_position(ticker, pos["side"], pos["count"], close_price_cents)

            pnl = None
            if fill is not None:
                pnl = (pos["count"] * (current_value - pos["entry_price"])
                       - fill["fee_dollars"])

            _store.clear_position(ticker)
            if exit_reason == "stop_loss":
                _store.set_cooldown(ticker)

            logger.log_trade(
                ticker, "", "",
                pos["side"], exit_reason,
                pos["entry_price"], current_value,
                pos["mc_prob_at_entry"], 0.0,
                fill["fee_dollars"] if fill else 0.0, pnl,
                _store.get_or_create(ticker).budget_remaining,
            )
            print(f"[{exit_reason}] {ticker}  entry={pos['entry_price']:.2f}  "
                  f"exit={current_value:.2f}  pnl={pnl:.3f}  dry={cfg.DRY_RUN}")

        time.sleep(cfg.FAST_POLL_SECS)


def main():
    if not cfg.MATCH_CONFIG:
        print("No matches configured. Add entries to MATCH_CONFIG in trade/config.py.")
        return

    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    print(f"Starting trade bot [{mode}] � {len(cfg.MATCH_CONFIG)} match(es) configured")

    fast_thread = threading.Thread(target=_fast_loop, daemon=True)
    fast_thread.start()

    _slow_loop()


if __name__ == "__main__":
    main()
