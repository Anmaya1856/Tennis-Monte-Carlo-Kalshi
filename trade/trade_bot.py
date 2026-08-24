"""
Kalshi in-play tennis trading bot.

Set DRY_RUN = True in config.py (default) to paper trade.
Set MATCH_CONFIG in config.py with at least one entry before running.

Usage:
    python -m trade.trade_bot
"""
import argparse, datetime, os, sys, time

import trade.config as cfg
from trade.kalshi_client import (get_best_ask_bid, place_order, close_position,
                                  fetch_milestone, fetch_milestone_id, fetch_best_of,
                                  get_event_competitor_map, parse_milestone_state,
                                  serve_stats_ready, fetch_prematch_price)
from trade.exact         import (estimate_win_prob_market,
                                  implied_point_probs, match_report)
from trade.state         import MatchStateStore
from trade.decision      import (compute_entry, edge_threshold, should_stop_loss,
                                  should_take_profit, should_trail_exit)
from trade               import logger


# State is keyed by event_ticker: one budget/position/cooldown per match,
# even though each match has two tradeable markets (one per player).
_store = MatchStateStore()

# event_ticker → {"p1_ticker", "p2_ticker", "p1_competitor_id", "p1_name_kalshi",
#                 "milestone_id", "p1_name", "p2_name", "best_of"}
#                 (last three filled on first successful sim)
_match_cache = {}

# event_ticker → {"score_key", "ts"} of the last sim attempt. Throttles retries
# when serve stats aren't ready yet (early in a match) by SIM_RETRY_SECS.
_sim_attempts = {}

# event_ticker → health, so a process self-terminates on sustained failure:
# {"first_seen", "last_init", "not_live_since"}.
_match_health = {}


def _stop_requested(event_ticker):
    """True (and clears the flag) if the monitor asked this match to stop."""
    path = os.path.join(cfg.LOG_DIR, f"stop_{event_ticker}.flag")
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
        return True
    return False


def _pos_player(cached, pos):
    return "p1" if pos["ticker"] == cached["p1_ticker"] else "p2"


def _print_poll(cached, p1_name, p2_name, score_str, game_score_str,
                server_name, p1_stats, p2_stats, mkt,
                mc_prob, game_prob, set_prob,
                p1_ask, p1_bid, p2_ask, p2_bid, best_of, ms, report=None,
                vol_point=None, vol_game=None, cond=None):
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    sep  = "─" * 62
    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    evk  = cached["p1_ticker"].rsplit("-", 1)[0]

    def last(name):
        return name.split()[-1]

    def edge_str(e, ask):
        s = f"{e*100:+.1f}¢"
        return s + "  EDGE" if e >= edge_threshold(ask) else s

    def prob_row(label, name, g, s, m, ask, bid):
        return (f"  {label} {last(name):<12} {g*100:5.1f}%  {s*100:5.1f}%  {m*100:5.1f}%  |"
                f"  {ask:.3f}   {bid:.3f}   {edge_str(m - ask, ask)}")

    def stat_cells(s):
        def pc(x):
            return f"{x*100:3.0f}%" if x is not None else "  —"
        return (f"1st-in {pc(s['first_in'])}  1st-won {pc(s['win_first'])}  "
                f"2nd-won {pc(s['win_second'])}  ret-1st {pc(s['return_first'])}  "
                f"ret-2nd {pc(s['return_second'])}")

    pos_line = "  No position"
    if ms.position:
        pos     = ms.position
        player  = _pos_player(cached, pos)
        age     = int(time.time() - pos["entry_time"])
        cur_val = p1_bid if player == "p1" else p2_bid
        value   = pos["count"] * cur_val
        unreal  = pos["count"] * (cur_val - pos["entry_price"])
        pos_line = (f"  Position: {player.upper()} YES @ {pos['entry_price']:.3f} × {pos['count']}"
                    f"  |  HWM {pos['high_water']:.3f}  |  now {cur_val:.3f}"
                    f"  |  value ${value:.2f}  |  uP&L ${unreal:+.2f}  ({age}s)")

    budget_line = f"  Budget: ${ms.budget_remaining:.2f}"
    if ms.cooldown_until and time.time() < ms.cooldown_until:
        budget_line += f"  |  Cooldown: {int(ms.cooldown_until - time.time())}s left"

    branch_line = ""
    if cond is not None:
        branch_line = (
            f"  P1 match% branches (now {mc_prob*100:.1f}%):\n"
            f"    this game:  win → {cond['win_game']*100:5.1f}%   lose → {cond['lose_game']*100:5.1f}%"
            f"   (wins game {game_prob*100:.0f}%)\n"
            f"    this set:   win → {cond['win_set']*100:5.1f}%   lose → {cond['lose_set']*100:5.1f}%"
            f"   (wins set {set_prob*100:.0f}%)\n\n")

    vol_line = ""
    if vol_point is not None:
        vol_line = (f"  Match vol:   next point ±{vol_point*100:.1f}pp"
                    f"   this game ±{vol_game*100:.1f}pp\n\n")

    dist_block = ""
    if report:
        need = best_of // 2 + 1
        sc = report["scorelines"]
        p1sc = "  ".join(f"{need}-{d} {sc[('p1', d)]*100:.0f}%"
                         for d in range(need) if sc.get(("p1", d), 0) >= 0.005)
        p2sc = "  ".join(f"{need}-{d} {sc[('p2', d)]*100:.0f}%"
                         for d in range(need) if sc.get(("p2", d), 0) >= 0.005)
        sets = []
        for i, (a, b) in enumerate(report["set_win"][:best_of]):
            if a + b < 1e-6:
                continue
            tag = f"({(a+b)*100:.0f}% pl)" if a + b < 0.999 else ""
            sets.append(f"S{i+1} P1 {a*100:.0f}%{tag}")
        games = "  ".join(f">{t} {p*100:.0f}%" for t, p in sorted(report["over_games"].items()))
        dist_block = (
            f"  Scorelines:  P1 {p1sc or '—'}   |   P2 {p2sc or '—'}\n"
            f"  Per set:     {'  '.join(sets)}\n"
            f"  Total games: {games}\n"
            f"\n"
        )

    print(
        f"\n{sep}\n"
        f"  [{ts}]  {evk}  [{mode}]  Best of {best_of}\n"
        f"  Score: {score_str}  |  Game: {game_score_str}  |  Serving: {last(server_name)}\n"
        f"\n"
        f"     {'':<12}  game     set   match  |  YES ask   bid     edge\n"
        f"{prob_row('P1', p1_name, game_prob, set_prob, mc_prob, p1_ask, p1_bid)}\n"
        f"{prob_row('P2', p2_name, 1 - game_prob, 1 - set_prob, 1 - mc_prob, p2_ask, p2_bid)}\n"
        f"\n"
        f"  P1 raw:  {stat_cells(p1_stats)}\n"
        f"  P2 raw:  {stat_cells(p2_stats)}\n"
        f"  pA {mkt['pa_blend']:.3f} (mkt {mkt['pa0']:.3f}, wt {mkt['wt_a']*100:.0f}%)"
        f"   pB {mkt['pb_blend']:.3f} (mkt {mkt['pb0']:.3f}, wt {mkt['wt_b']*100:.0f}%)\n"
        f"\n"
        f"{branch_line}"
        f"{vol_line}"
        f"{dist_block}"
        f"{pos_line}\n"
        f"{budget_line}\n"
        f"{sep}"
    )


def _init_event(event_ticker):
    """Resolve both market tickers + p1 identity + milestone ID once per event. Returns cache dict or None."""
    print(f"[init] resolving event {event_ticker} ...")
    event_map = get_event_competitor_map(event_ticker)
    if event_map is None:
        print(f"[init] FAILED: could not fetch event map for {event_ticker}")
        return None
    milestone_id = fetch_milestone_id(event_ticker)
    if milestone_id is None:
        print(f"[init] FAILED: no milestone found for {event_ticker}")
        return None
    # Tournament-outright events list one market per player; keep only the two
    # live markets (the current match). Normal match events have exactly two.
    active = {cid: info for cid, info in event_map.items() if info.get("status") == "active"}
    if len(active) != 2:
        print(f"[init] FAILED: expected 2 active markets, got {len(active)} for {event_ticker}")
        return None
    event_map = active
    # p1 = alphabetically first market ticker
    tickers   = sorted(info["ticker"] for info in event_map.values())
    p1_ticker, p2_ticker = tickers[0], tickers[1]
    p1_cid = next(cid for cid, info in event_map.items() if info["ticker"] == p1_ticker)
    p2_cid = next(cid for cid, info in event_map.items() if info["ticker"] == p2_ticker)
    best_of = fetch_best_of(milestone_id) or 3
    cached = {
        "p1_ticker":        p1_ticker,
        "p2_ticker":        p2_ticker,
        "p1_competitor_id": p1_cid,
        "p1_name_kalshi":   event_map[p1_cid]["name"],
        "p2_name_kalshi":   event_map[p2_cid]["name"],
        "milestone_id":     milestone_id,
        "best_of":          best_of,
        "p1_name":          None,
        "p2_name":          None,
        "pa0":              None,
        "pb0":              None,
        "prematch_price":   None,
    }
    print(f"[init] OK  {event_ticker.rsplit('-', 1)[-1]}  "
          f"P1 {event_map[p1_cid]['name']} ({p1_ticker})  vs  "
          f"P2 {event_map[p2_cid]['name']} ({p2_ticker})  Bo{best_of}  milestone={milestone_id}")
    return cached


def _run_sim(mc, cached, kalshi_state, score_key, p1_ask, p1_bid, p2_ask, p2_bid):
    """Re-run the exact sim from Kalshi's live serve stats. Updates store + cache,
    logs snapshot, prints dashboard. Returns True on success, False if stats aren't ready.

    Score, server, and serve stats all come from the same Kalshi milestone payload —
    so the two-source lag the old Hawkeye path guarded against can't happen here."""
    key = mc["event_ticker"]

    # Kalshi supplies per-competitor serve stats already oriented to p1/p2.
    p1_stats, p2_stats = kalshi_state["p1_stats"], kalshi_state["p2_stats"]
    if not (serve_stats_ready(p1_stats) and serve_stats_ready(p2_stats)):
        print(f"[poll] serve stats not ready yet ({cached['p1_ticker']})")
        return False

    p1_name, p2_name = cached["p1_name_kalshi"], cached["p2_name_kalshi"]
    best_of = cached["best_of"]
    ms = _store.get_or_create(key)
    total_points = p1_stats["first_in_den"] + p2_stats["first_in_den"]

    # Resolve the market-implied prior once per match (needs best_of).
    if cached["pa0"] is None:
        series = mc["event_ticker"].split("-")[0]
        est_start = time.time() - total_points * 45 - 300
        px = fetch_prematch_price(cached["p1_ticker"], series, est_start)
        cached["prematch_price"] = px
        if px is not None and 0.02 < px < 0.98:
            cached["pa0"], cached["pb0"] = implied_point_probs(px, best_of)
            print(f"[prior] {cached['p1_ticker']}: pre-match {px:.3f} -> "
                  f"pA0 {cached['pa0']:.3f} / pB0 {cached['pb0']:.3f}")
        else:
            cached["pa0"] = cached["pb0"] = cfg.INVERSION_BASE
            print(f"[prior] {cached['p1_ticker']}: no pre-match price -> neutral {cfg.INVERSION_BASE}")

    # LIVE model: market-implied prior blended with in-match service counts.
    wonA, playedA = p1_stats["win_first_num"] + p1_stats["win_second_num"], p1_stats["first_in_den"]
    wonB, playedB = p2_stats["win_first_num"] + p2_stats["win_second_num"], p2_stats["first_in_den"]
    probs = estimate_win_prob_market(
        cached["pa0"], cached["pb0"], wonA, playedA, wonB, playedB,
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        kalshi_state["p1_serves"], best_of,
    )
    mc_prob   = probs["match"]
    set_prob  = probs["set"]
    game_prob = probs["game"]

    # Derived DP distributions (scorelines / per-set / total-games) — logged only.
    # target_match=mc_prob makes them an exact decomposition of the traded match prob.
    report = match_report(
        probs["pa_blend"], probs["pb_blend"],
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        kalshi_state["p1_serves"], best_of, cfg.GAME_THRESHOLDS,
        target_match=mc_prob,
    )

    _store.update_mc_prob(key, mc_prob, game_prob, probs["cond"])
    _store.record_sim(key, score_key)

    # Divergence stand-down: sustained large model-market disagreement means
    # the market knows something we can't model — stop entering, keep exits live.
    standdown, changed = _store.update_divergence(key, mc_prob, (p1_ask + p1_bid) / 2)
    if changed:
        ema = _store.get_or_create(key).divergence_ema
        if standdown:
            print(f"[standdown] {key}: divergence EMA {ema:.2f} > {cfg.DIVERGENCE_PAUSE} — entries paused")
        else:
            print(f"[standdown] {key}: divergence EMA {ema:.2f} < {cfg.DIVERGENCE_RESUME} — entries resumed")
    cached["p1_name"] = p1_name
    cached["p2_name"] = p2_name

    pos      = ms.position
    pos_side = _pos_player(cached, pos) if pos else None
    pos_value = (p1_bid if pos_side == "p1" else p2_bid) if pos else None
    logger.log_snapshot(
        cached["p1_ticker"], p1_name, p2_name,
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        "p1" if kalshi_state["p1_serves"] else "p2",
        p1_stats, p2_stats,
        kalshi_state["p1_last10"], kalshi_state["p2_last10"],
        mc_prob, set_prob, game_prob,
        p1_ask, p1_bid, p2_ask, p2_bid,
        ms, pos_side, pos_value,
        p1_kstats=kalshi_state.get("p1_kstats"), p2_kstats=kalshi_state.get("p2_kstats"),
        prematch_price=cached["prematch_price"], pa0=cached["pa0"], pb0=cached["pb0"],
        pa_blend=probs["pa_blend"], pb_blend=probs["pb_blend"],
        report=report, vol_point=probs["vol"]["point"], vol_game=probs["vol"]["game"],
        best_of=best_of, cond=probs["cond"], milestone_id=cached["milestone_id"],
    )

    server_name = p1_name if kalshi_state["p1_serves"] else p2_name
    mkt = {"pa0": cached["pa0"], "pb0": cached["pb0"], "pa_blend": probs["pa_blend"],
           "pb_blend": probs["pb_blend"], "wt_a": probs["wt_a"], "wt_b": probs["wt_b"]}
    _print_poll(cached, p1_name, p2_name,
                kalshi_state["score_str"], kalshi_state["game_score_str"],
                server_name, p1_stats, p2_stats, mkt,
                mc_prob, game_prob, set_prob,
                p1_ask, p1_bid, p2_ask, p2_bid, best_of,
                _store.get_or_create(key), report=report,
                vol_point=probs["vol"]["point"], vol_game=probs["vol"]["game"],
                cond=probs["cond"])
    return True


def _check_entry(key, cached, mc_prob, p1_ask, p2_ask):
    """Evaluate buying YES on either player's market; place order if edge exists."""
    ms = _store.get_or_create(key)
    order_params = compute_entry(mc_prob, p1_ask, p2_ask, ms.budget_remaining,
                                 size_cap=ms.initial_budget)
    if order_params is None:
        return

    player = order_params["player"]
    ticker = cached["p1_ticker"] if player == "p1" else cached["p2_ticker"]

    # Entry timing: don't buy a side that's probably about to lose the current
    # game — wait for the game to resolve; the edge re-evaluates on the next sim.
    if ms.last_game_prob is not None:
        game_prob = ms.last_game_prob if player == "p1" else 1 - ms.last_game_prob
        if game_prob < cfg.ENTRY_GAME_PROB_MIN:
            print(f"[entry] deferred {ticker}: {player.upper()} game-win prob "
                  f"{game_prob:.2f} < {cfg.ENTRY_GAME_PROB_MIN}")
            return

    # Fragility filter: skip "short-gamma" entries where a single lost game/set
    # would crater our side (e.g. buying the server right before a possible break).
    if ms.last_cond is not None and ms.last_mc_prob is not None:
        c, m = ms.last_cond, ms.last_mc_prob
        if player == "p1":
            down_game, down_set = m - c["lose_game"], m - c["lose_set"]
        else:
            down_game, down_set = c["win_game"] - m, c["win_set"] - m
        if down_game > cfg.MAX_ENTRY_GAME_DRAWDOWN or down_set > cfg.MAX_ENTRY_SET_DRAWDOWN:
            print(f"[entry] blocked {ticker}: {player.upper()} too fragile — "
                  f"lose-game drop {down_game:.2f} (max {cfg.MAX_ENTRY_GAME_DRAWDOWN}), "
                  f"lose-set drop {down_set:.2f} (max {cfg.MAX_ENTRY_SET_DRAWDOWN})")
            return

    # Re-entry guard: after a trail exit, don't buy the same side back at or
    # above the price we just sold at.
    # Half-cent tolerance: prices on the same 1c tick must count as "not lower"
    guard = ms.trail_exit
    if (guard and guard["player"] == player
            and time.time() - guard["time"] < cfg.REENTRY_GUARD_SECS
            and order_params["entry_price"] >= guard["price"] - 0.005):
        print(f"[entry] blocked {ticker}: trail exit was {guard['price']:.2f}, "
              f"ask {order_params['entry_price']:.2f} not lower "
              f"({int(cfg.REENTRY_GUARD_SECS - (time.time() - guard['time']))}s left)")
        return

    fill = place_order(ticker, order_params["count"], order_params["price_cents"])
    if fill is None:
        return

    _store.deduct_fill(key, fill["cost_dollars"], fill["fee_dollars"])
    _store.set_position(key, ticker, order_params["entry_price"], order_params["count"])

    logger.log_trade(
        ticker, cached["p1_name"] or "", cached["p2_name"] or "",
        player, "entry",
        order_params["entry_price"], None,
        mc_prob, fill["cost_dollars"], fill["fee_dollars"], None,
        _store.get_or_create(key).budget_remaining,
    )
    print(f"\n*** ENTRY [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {ticker}"
          f"  player={player.upper()}"
          f"  price={order_params['entry_price']:.3f}"
          f"  count={order_params['count']}"
          f"  mc={mc_prob*100:.1f}%"
          f"  cost=${fill['cost_dollars']:.2f}"
          f"  budget_left=${_store.get_or_create(key).budget_remaining:.2f} ***")


def _check_exit(key, cached, p1_bid, p2_bid):
    """Check the open YES position for stop loss / profit target at its market's bid."""
    ms     = _store.get_or_create(key)
    pos    = ms.position
    player = _pos_player(cached, pos)

    current_value     = p1_bid if player == "p1" else p2_bid
    close_price_cents = round(current_value * 100)

    pos["high_water"] = max(pos["high_water"], current_value)

    exit_reason = None
    current_mc_prob = ms.last_mc_prob
    if current_mc_prob is not None:
        if should_take_profit(player, current_value, current_mc_prob, pos["entry_price"]):
            exit_reason = "profit_target"
        elif should_trail_exit(pos["entry_price"], pos["high_water"], current_value):
            exit_reason = "trail_lock"
        elif should_stop_loss(pos["entry_price"], current_value):
            exit_reason = "stop_loss"

    if exit_reason is None:
        return

    fill = close_position(pos["ticker"], pos["count"], close_price_cents)

    pnl = None
    if fill is not None:
        proceeds = pos["count"] * current_value - fill["fee_dollars"]
        pnl = proceeds - pos["count"] * pos["entry_price"]
        _store.restore_proceeds(key, proceeds)

    _store.clear_position(key)
    if exit_reason == "stop_loss":
        _store.set_cooldown(key)
    elif exit_reason == "trail_lock":
        _store.set_trail_exit(key, player, current_value)

    logger.log_trade(
        pos["ticker"], "", "",
        player, exit_reason,
        pos["entry_price"], current_value,
        current_mc_prob, 0.0,
        fill["fee_dollars"] if fill else 0.0, pnl,
        _store.get_or_create(key).budget_remaining,
    )
    pnl_str = f"${pnl:+.3f}" if pnl is not None else "n/a"
    print(f"\n*** {exit_reason.upper()} [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {pos['ticker']}"
          f"  player={player.upper()}"
          f"  entry={pos['entry_price']:.3f}"
          f"  exit={current_value:.3f}"
          f"  mc_now={current_mc_prob*100:.1f}%"
          f"  pnl={pnl_str}"
          f"  budget_left=${_store.get_or_create(key).budget_remaining:.2f} ***")


def _tick(mc):
    """One tick for one match: poll Kalshi score + prices, sim on score change, trade.
    Returns "dead" if the match should be dropped (bad ticker / match over), else None."""
    event_ticker = mc["event_ticker"]
    key = event_ticker
    h = _match_health.setdefault(key, {"first_seen": time.time(), "last_init": 0.0,
                                       "not_live_since": None})

    if event_ticker not in _match_cache:
        if time.time() - h["last_init"] < 5:      # throttle init retries (bad/early ticker)
            return None
        h["last_init"] = time.time()
        cached = _init_event(event_ticker)
        if cached is None:
            if time.time() - h["first_seen"] > cfg.INIT_TIMEOUT_SECS:
                print(f"[stop] {event_ticker}: could not initialize within "
                      f"{cfg.INIT_TIMEOUT_SECS}s — giving up (bad ticker?)")
                return "dead"
            return None
        _match_cache[event_ticker] = cached

    cached = _match_cache[event_ticker]

    # First call creates the state; optional per-match "budget" overrides MATCH_BUDGET
    _store.get_or_create(key, mc.get("budget"))

    if _store.is_budget_exhausted(key) and not _store.has_position(key):
        return

    # Kalshi live score + server (every tick — fast, public, reliable)
    details = fetch_milestone(cached["milestone_id"])
    if details is None:
        if h["not_live_since"] is None:
            h["not_live_since"] = time.time()
        elif time.time() - h["not_live_since"] > cfg.MATCH_END_GRACE_SECS:
            print(f"[stop] {event_ticker}: milestone not live for "
                  f">{cfg.MATCH_END_GRACE_SECS // 60} min — match over, exiting")
            return "dead"
        print(f"[poll] milestone not live (id={cached['milestone_id']})")
        return
    h["not_live_since"] = None   # match is live again — reset the grace timer
    kalshi_state = parse_milestone_state(details, cached["p1_competitor_id"])
    if not kalshi_state["is_live"]:
        print(f"[poll] match state is not live")
        return

    p1_ask, p1_bid = get_best_ask_bid(cached["p1_ticker"])
    p2_ask, p2_bid = get_best_ask_bid(cached["p2_ticker"])
    if None in (p1_ask, p1_bid, p2_ask, p2_bid):
        print(f"[poll] orderbook unavailable for {event_ticker}")
        return

    ms = _store.get_or_create(key)

    # Re-sim only when the score changed since the last successful sim,
    # or as a heartbeat when the cached mc_prob has gone stale.
    score_key = (kalshi_state["score_str"], kalshi_state["game_score_str"],
                 kalshi_state["p1_serves"])
    sim_fresh = False
    if score_key != ms.last_sim_score or _store.is_mc_stale(key):
        att  = _sim_attempts.get(key)
        same = att is not None and att["score_key"] == score_key
        # Throttle retries when stats aren't ready yet (early in a match); a new
        # score runs immediately.
        if not same or time.time() - att["ts"] >= cfg.SIM_RETRY_SECS:
            result = _run_sim(mc, cached, kalshi_state, score_key,
                              p1_ask, p1_bid, p2_ask, p2_bid)
            _sim_attempts[key] = {"score_key": score_key, "ts": time.time()}
            sim_fresh = result is True

    # Exits run every tick (they protect an existing position); entries only on
    # ticks with a freshly computed mc_prob — a cached prob can be stale relative
    # to the market, which knows the score before our feed does.
    if _store.has_position(key):
        _check_exit(key, cached, p1_bid, p2_bid)
    elif (sim_fresh
          and not ms.standdown
          and not _store.is_in_cooldown(key)
          and not _store.is_budget_exhausted(key)):
        # Re-fetch prices: the sim takes a moment and the market may have moved
        # since the tick started.
        p1_ask, _ = get_best_ask_bid(cached["p1_ticker"])
        p2_ask, _ = get_best_ask_bid(cached["p2_ticker"])
        if p1_ask is not None and p2_ask is not None:
            _check_entry(key, cached, ms.last_mc_prob, p1_ask, p2_ask)


def main():
    # Windows defaults redirected stdout to cp1252, which can't encode the
    # dashboard's box characters — don't let a log redirect kill the bot.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # One process per match: pass an event ticker to trade a single match (its own
    # budget, state, and log file). No args falls back to MATCH_CONFIG.
    parser = argparse.ArgumentParser(description="Kalshi in-play tennis trading bot")
    parser.add_argument("event_ticker", nargs="?", help="run a single match by event ticker")
    parser.add_argument("--budget", type=float, default=None, help="budget for this match (dollars)")
    args = parser.parse_args()

    if args.event_ticker:
        matches = [{"event_ticker": args.event_ticker,
                    **({"budget": args.budget} if args.budget is not None else {})}]
    else:
        matches = cfg.MATCH_CONFIG

    if not matches:
        print("No matches configured. Pass an event ticker or add entries to MATCH_CONFIG.")
        return

    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    print(f"Starting trade bot [{mode}] — {len(matches)} match(es)")

    while True:
        for mc in list(matches):
            if _stop_requested(mc["event_ticker"]):
                print(f"[stop] {mc['event_ticker']}: stop requested from monitor — exiting")
                return
            if _tick(mc) == "dead":
                matches.remove(mc)
        if not matches:
            print("[exit] no active matches remaining — shutting down")
            return
        time.sleep(cfg.FAST_POLL_SECS)


if __name__ == "__main__":
    main()
