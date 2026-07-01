"""
Kalshi in-play tennis trading bot.

Set DRY_RUN = True in config.py (default) to paper trade.
Set MATCH_CONFIG in config.py with at least one entry before running.

Usage:
    python -m trade.trade_bot
"""
import datetime, re, threading, time, unicodedata

import trade.config as cfg
from trade.atp_client    import fetch_match_state
from trade.kalshi_client import (get_best_ask_bid, place_order, close_position,
                                  fetch_milestone, fetch_milestone_id,
                                  get_event_competitor_map, parse_milestone_state)
from trade.simulation    import estimate_win_prob
from trade.state         import MatchStateStore
from trade.decision      import compute_entry, edge_threshold, should_stop_loss, should_take_profit
from trade               import logger


_store = MatchStateStore()

# event_ticker → {"kalshi_ticker", "p1_competitor_id", "p1_name_kalshi"}
_match_cache = {}


def _norm(name):
    """Normalize player name for fuzzy comparison: strip diacritics, lowercase, letters only."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]", "", name.lower())


def _print_poll(ticker, p1_name, p2_name, score_str, game_score_str,
                server_name, p1_stats, p2_stats, mc_prob, game_prob, set_prob,
                yes_ask, yes_bid, best_of, ms):
    ts       = datetime.datetime.now().strftime("%H:%M:%S")
    sep      = "─" * 62
    mode     = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    edge_yes = mc_prob - yes_ask
    edge_no  = (1 - mc_prob) - (1 - yes_bid)
    spread   = yes_ask - yes_bid

    threshold = edge_threshold(yes_ask)
    def edge_str(e):
        return f"+{e*100:.1f}¢  EDGE" if e >= threshold else f"{e*100:.1f}¢"

    pos_line = "  No position"
    if ms.position:
        pos     = ms.position
        age     = int(time.time() - pos["entry_time"])
        cur_val = yes_bid if pos["side"] == "yes" else (1 - yes_ask)
        unreal  = pos["count"] * (cur_val - pos["entry_price"])
        pos_line = (f"  Position: {pos['side'].upper()} @ {pos['entry_price']:.3f}"
                    f" × {pos['count']}  |  unrealized P&L: ${unreal:+.2f}  ({age}s ago)")

    cooldown_line = ""
    if ms.cooldown_until and time.time() < ms.cooldown_until:
        secs_left = int(ms.cooldown_until - time.time())
        cooldown_line = f"  Cooldown: {secs_left}s remaining\n"

    def srow(label, name, s):
        return (f"  {label} {name}:  "
                f"1st-in={s['first_in']*100:.0f}%  "
                f"1st-won={s['win_first']*100:.0f}%  "
                f"2nd-won={s['win_second']*100:.0f}%  "
                f"ret-1st={s['return_first']*100:.0f}%  "
                f"ret-2nd={s['return_second']*100:.0f}%")

    print(
        f"\n{sep}\n"
        f"  [{ts}]  {ticker}  [{mode}]  Best of {best_of}\n"
        f"  P1  {p1_name}  vs  P2  {p2_name}\n"
        f"  Score: {score_str}  |  Game: {game_score_str}  |  Serving: {server_name}\n"
        f"  MC P1 win: {mc_prob*100:.1f}%  |  Set: {set_prob*100:.1f}%  |  Game: {game_prob*100:.1f}%\n"
        f"  YES ask: {yes_ask:.3f}  bid: {yes_bid:.3f}  spread: {spread*100:.1f}¢\n"
        f"  Edge YES: {edge_str(edge_yes)}  |  Edge NO: {edge_str(edge_no)}\n"
        f"{srow('P1', p1_name, p1_stats)}\n"
        f"{srow('P2', p2_name, p2_stats)}\n"
        f"{pos_line}  |  Budget: ${ms.budget_remaining:.2f}\n"
        f"{cooldown_line}"
        f"{sep}"
    )


def _slow_loop():
    """Every 20s: poll ATP stats → Kalshi score/server → MC → entry decisions."""
    while True:
        for mc in cfg.MATCH_CONFIG:
            event_ticker = mc["event_ticker"]

            # Resolve canonical ticker + p1 identity + milestone ID once per event (cached)
            if event_ticker not in _match_cache:
                print(f"[init] resolving event {event_ticker} ...")
                event_map = get_event_competitor_map(event_ticker)
                if event_map is None:
                    print(f"[init] FAILED: could not fetch event map for {event_ticker}")
                    continue
                milestone_id = fetch_milestone_id(event_ticker)
                if milestone_id is None:
                    print(f"[init] FAILED: no milestone found for {event_ticker}")
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
                print(f"[init] OK  ticker={kalshi_ticker}  p1={event_map[p1_cid]['name']}  milestone={milestone_id}")

            cached        = _match_cache[event_ticker]
            ticker        = cached["kalshi_ticker"]
            p1_cid        = cached["p1_competitor_id"]
            p1_name_k     = cached["p1_name_kalshi"]

            if _store.is_budget_exhausted(ticker):
                continue

            # Step 1: ATP stats
            atp = fetch_match_state(mc["hawkeye_url"])
            if atp is None:
                print(f"[poll] ATP stats unavailable ({mc['hawkeye_url']})")
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
                print(f"[poll] milestone not live (id={cached['milestone_id']})")
                continue
            kalshi_state = parse_milestone_state(details, p1_cid)
            if not kalshi_state["is_live"]:
                print(f"[poll] match state is not live")
                continue

            # Step 3: MC simulation
            probs = estimate_win_prob(
                p1_stats, p2_stats,
                kalshi_state["score_str"],
                kalshi_state["game_score_str"],
                kalshi_state["p1_serves"],
                atp["best_of"],
                n_sims=cfg.N_SIMS,
            )
            mc_prob   = probs["match"]
            set_prob  = probs["set"]
            game_prob = probs["game"]

            yes_ask, yes_bid = get_best_ask_bid(ticker)
            if yes_ask is None:
                print(f"[poll] orderbook unavailable for {ticker}")
                continue

            _store.update_mc_prob(ticker, mc_prob)

            logger.log_snapshot(
                ticker, p1_name, p2_name,
                kalshi_state["score_str"], kalshi_state["game_score_str"],
                "p1" if kalshi_state["p1_serves"] else "p2",
                p1_stats, p2_stats,
                kalshi_state["p1_last10"], kalshi_state["p2_last10"],
                mc_prob, set_prob, game_prob, yes_ask, yes_bid,
            )

            server_name = p1_name if kalshi_state["p1_serves"] else p2_name
            _print_poll(ticker, p1_name, p2_name,
                        kalshi_state["score_str"], kalshi_state["game_score_str"],
                        server_name, p1_stats, p2_stats,
                        mc_prob, game_prob, set_prob,
                        yes_ask, yes_bid, atp["best_of"],
                        _store.get_or_create(ticker))

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
                                order_params["count"])

            logger.log_trade(
                ticker, p1_name, p2_name,
                order_params["side"], "entry",
                order_params["entry_price"], None,
                mc_prob, fill["cost_dollars"], fill["fee_dollars"], None,
                _store.get_or_create(ticker).budget_remaining,
            )
            print(f"\n*** ENTRY [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {ticker}"
                  f"  side={order_params['side'].upper()}"
                  f"  price={order_params['entry_price']:.3f}"
                  f"  count={order_params['count']}"
                  f"  mc={mc_prob*100:.1f}%"
                  f"  cost=${fill['cost_dollars']:.2f}"
                  f"  budget_left=${_store.get_or_create(ticker).budget_remaining:.2f} ***")

        time.sleep(cfg.SLOW_POLL_SECS)


def _fast_loop():
    """Every 1s: check open positions for stop loss / profit target."""
    while True:
        for mc in cfg.MATCH_CONFIG:
            cached = _match_cache.get(mc["event_ticker"])
            if cached is None:
                continue
            ticker = cached["kalshi_ticker"]

            if not _store.has_position(ticker):
                continue

            ms  = _store.get_or_create(ticker)
            pos = ms.position
            yes_ask, yes_bid = get_best_ask_bid(ticker)
            if yes_ask is None or yes_bid is None:
                continue

            if pos["side"] == "yes":
                current_value     = yes_bid
                close_price_cents = round(yes_bid * 100)
            else:
                current_value     = 1 - yes_ask
                close_price_cents = round(yes_ask * 100)  # YES ask price to buy YES back

            exit_reason = None
            current_mc_prob = ms.last_mc_prob
            if current_mc_prob is not None:
                if should_stop_loss(pos["entry_price"], current_value):
                    exit_reason = "stop_loss"
                elif should_take_profit(pos["side"], current_value, current_mc_prob, pos["entry_price"]):
                    exit_reason = "profit_target"

            if exit_reason is None:
                continue

            fill = close_position(ticker, pos["side"], pos["count"], close_price_cents)

            pnl = None
            if fill is not None:
                proceeds = pos["count"] * current_value - fill["fee_dollars"]
                pnl = proceeds - pos["count"] * pos["entry_price"]
                _store.restore_proceeds(ticker, proceeds)

            _store.clear_position(ticker)
            if exit_reason == "stop_loss":
                _store.set_cooldown(ticker)

            logger.log_trade(
                ticker, "", "",
                pos["side"], exit_reason,
                pos["entry_price"], current_value,
                current_mc_prob, 0.0,
                fill["fee_dollars"] if fill else 0.0, pnl,
                _store.get_or_create(ticker).budget_remaining,
            )
            pnl_str = f"${pnl:+.3f}" if pnl is not None else "n/a"
            print(f"\n*** {exit_reason.upper()} [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {ticker}"
                  f"  side={pos['side'].upper()}"
                  f"  entry={pos['entry_price']:.3f}"
                  f"  exit={current_value:.3f}"
                  f"  mc_now={current_mc_prob*100:.1f}%"
                  f"  pnl={pnl_str}"
                  f"  budget_left=${_store.get_or_create(ticker).budget_remaining:.2f} ***")

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
