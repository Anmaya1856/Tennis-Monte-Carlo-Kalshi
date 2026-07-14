"""
Kalshi in-play tennis trading bot.

Set DRY_RUN = True in config.py (default) to paper trade.
Set MATCH_CONFIG in config.py with at least one entry before running.

Usage:
    python -m trade.trade_bot
"""
import datetime, re, sys, time, unicodedata

import trade.config as cfg
from trade.atp_client    import fetch_match_state, stats_ready
from trade.kalshi_client import (get_best_ask_bid, place_order, close_position,
                                  fetch_milestone, fetch_milestone_id,
                                  get_event_competitor_map, parse_milestone_state,
                                  fetch_prematch_price)
from trade.exact         import (estimate_win_prob, estimate_win_prob_market,
                                  implied_point_probs, match_report)
from trade               import career
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

# event_ticker → {"score_key", "ts", "stale_since"} of the last sim attempt.
# Throttles Hawkeye retries: failed sims (stats not ready / ATP down) wait
# SIM_RETRY_SECS; stale-stats sims (Hawkeye lagging the Kalshi score) retry
# after ATP_LAG_RETRY_SECS and are force-accepted after ATP_LAG_MAX_WAIT_SECS.
_sim_attempts = {}


def _norm(name):
    """Normalize player name for fuzzy comparison: strip diacritics, lowercase, letters only."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]", "", name.lower())


def _pos_player(cached, pos):
    return "p1" if pos["ticker"] == cached["p1_ticker"] else "p2"


def _print_poll(cached, p1_name, p2_name, score_str, game_score_str,
                server_name, p1_stats, p2_stats, mkt,
                mc_prob, game_prob, set_prob,
                p1_ask, p1_bid, p2_ask, p2_bid, best_of, ms, report=None):
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
        return (f"1st-in {s['first_in']*100:3.0f}%  1st-won {s['win_first']*100:3.0f}%  "
                f"2nd-won {s['win_second']*100:3.0f}%  ret-1st {s['return_first']*100:3.0f}%  "
                f"ret-2nd {s['return_second']*100:3.0f}%")

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
        f"   pB {mkt['pb_blend']:.3f} (mkt {mkt['pb0']:.3f}, wt {mkt['wt_b']*100:.0f}%)"
        f"   |  career model P1 {mkt['career_prob_p1']*100:.1f}%\n"
        f"\n"
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
    p1_cid = next(
        cid for cid, info in event_map.items()
        if info["ticker"] == p1_ticker
    )
    cached = {
        "p1_ticker":        p1_ticker,
        "p2_ticker":        p2_ticker,
        "p1_competitor_id": p1_cid,
        "p1_name_kalshi":   event_map[p1_cid]["name"],
        "milestone_id":     milestone_id,
        "p1_name":          None,
        "p2_name":          None,
        "best_of":          None,
        "p1_career":        None,
        "p2_career":        None,
        "pa0":              None,
        "pb0":              None,
        "prematch_price":   None,
    }
    p2_name_k = next(info["name"] for info in event_map.values() if info["ticker"] == p2_ticker)
    print(f"[init] OK  {event_ticker.rsplit('-', 1)[-1]}  "
          f"P1 {event_map[p1_cid]['name']} ({p1_ticker})  vs  "
          f"P2 {p2_name_k} ({p2_ticker})  milestone={milestone_id}")
    return cached


def _run_sim(mc, cached, kalshi_state, score_key, p1_ask, p1_bid, p2_ask, p2_bid,
             accept_stale=False):
    """Fetch ATP stats and re-run the MC sim. Updates store + cache, logs snapshot, prints dashboard.
    Returns True on success, "stale" if Hawkeye stats lag the Kalshi score, False on failure."""
    key = mc["event_ticker"]

    atp = fetch_match_state(mc["hawkeye_url"])
    if atp is None:
        print(f"[poll] ATP stats unavailable ({mc['hawkeye_url']})")
        return False

    # Align: if Hawkeye Team1 ≠ Kalshi p1, swap stats
    if _norm(atp["p1_name"]) == _norm(cached["p1_name_kalshi"]):
        p1_stats, p2_stats = atp["p1_stats"], atp["p2_stats"]
        p1_name, p2_name   = atp["p1_name"],  atp["p2_name"]
    else:
        p1_stats, p2_stats = atp["p2_stats"], atp["p1_stats"]
        p1_name, p2_name   = atp["p2_name"],  atp["p1_name"]

    if not (stats_ready(p1_stats) and stats_ready(p2_stats)):
        print(f"[poll] stats not ready yet ({cached['p1_ticker']})")
        return False

    # Freshness check: a score change means a point was played, so the Hawkeye
    # point count must have grown since the last sim — otherwise ATP hasn't
    # reflected the new point yet and we'd sim the new score with old stats.
    ms = _store.get_or_create(key)
    total_points = p1_stats["first_in_den"] + p2_stats["first_in_den"]
    if (not accept_stale
            and score_key != ms.last_sim_score
            and total_points <= ms.last_sim_total_points):
        print(f"[poll] ATP stats lag Kalshi score ({cached['p1_ticker']}); retrying")
        return "stale"

    cached["best_of"] = atp["best_of"]

    # Resolve the market-implied prior once per match (needs best_of).
    if cached["pa0"] is None:
        series = mc["event_ticker"].split("-")[0]
        est_start = time.time() - total_points * 45 - 300
        px = fetch_prematch_price(cached["p1_ticker"], series, est_start)
        cached["prematch_price"] = px
        if px is not None and 0.02 < px < 0.98:
            cached["pa0"], cached["pb0"] = implied_point_probs(px, atp["best_of"])
            print(f"[prior] {cached['p1_ticker']}: pre-match {px:.3f} -> "
                  f"pA0 {cached['pa0']:.3f} / pB0 {cached['pb0']:.3f}")
        else:
            cached["pa0"] = cached["pb0"] = cfg.INVERSION_BASE
            print(f"[prior] {cached['p1_ticker']}: no pre-match price -> neutral {cfg.INVERSION_BASE}")

    # Resolve career stats once per match (shadow model only — logged, not traded).
    if cached["p1_career"] is None:
        surface = mc.get("surface", cfg.SURFACE)
        cached["p1_career"] = career.lookup(p1_name, surface)
        cached["p2_career"] = career.lookup(p2_name, surface)
        for nm, c in ((p1_name, cached["p1_career"]), (p2_name, cached["p2_career"])):
            src = "NEUTRAL fallback" if c == career.neutral_for(surface) else f"{surface} career"
            print(f"[career] {nm}: {src}  " +
                  "  ".join(f"{k}={v*100:.0f}%" for k, v in c.items()))

    # LIVE model: market-implied prior blended with in-match service counts.
    wonA, playedA = p1_stats["win_first_num"] + p1_stats["win_second_num"], p1_stats["first_in_den"]
    wonB, playedB = p2_stats["win_first_num"] + p2_stats["win_second_num"], p2_stats["first_in_den"]
    probs = estimate_win_prob_market(
        cached["pa0"], cached["pb0"], wonA, playedA, wonB, playedB,
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        kalshi_state["p1_serves"], atp["best_of"],
    )
    mc_prob   = probs["match"]
    set_prob  = probs["set"]
    game_prob = probs["game"]

    # SHADOW model: career prior (logged for A/B comparison, never traded on).
    p1_blend = career.blend(p1_stats, cached["p1_career"])
    p2_blend = career.blend(p2_stats, cached["p2_career"])
    career_prob_p1 = estimate_win_prob(
        p1_blend, p2_blend,
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        kalshi_state["p1_serves"], atp["best_of"],
    )["match"]

    # Derived DP distributions (scorelines / per-set / total-games) — logged only.
    # target_match=mc_prob makes them an exact decomposition of the traded match prob.
    report = match_report(
        probs["pa_blend"], probs["pb_blend"],
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        kalshi_state["p1_serves"], atp["best_of"], cfg.GAME_THRESHOLDS,
        target_match=mc_prob,
    )

    _store.update_mc_prob(key, mc_prob, game_prob)
    _store.record_sim(key, score_key, total_points)

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
        pa_blend=probs["pa_blend"], pb_blend=probs["pb_blend"], career_prob_p1=career_prob_p1,
        report=report,
    )

    server_name = p1_name if kalshi_state["p1_serves"] else p2_name
    mkt = {"pa0": cached["pa0"], "pb0": cached["pb0"], "pa_blend": probs["pa_blend"],
           "pb_blend": probs["pb_blend"], "wt_a": probs["wt_a"], "wt_b": probs["wt_b"],
           "career_prob_p1": career_prob_p1}
    _print_poll(cached, p1_name, p2_name,
                kalshi_state["score_str"], kalshi_state["game_score_str"],
                server_name, p1_stats, p2_stats, mkt,
                mc_prob, game_prob, set_prob,
                p1_ask, p1_bid, p2_ask, p2_bid, atp["best_of"],
                _store.get_or_create(key), report=report)
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
    """One tick for one configured match: poll Kalshi score + prices, sim on score change, trade decisions."""
    event_ticker = mc["event_ticker"]
    key = event_ticker

    if event_ticker not in _match_cache:
        cached = _init_event(event_ticker)
        if cached is None:
            return
        _match_cache[event_ticker] = cached

    cached = _match_cache[event_ticker]

    # First call creates the state; optional per-match "budget" overrides MATCH_BUDGET
    _store.get_or_create(key, mc.get("budget"))

    if _store.is_budget_exhausted(key) and not _store.has_position(key):
        return

    # Kalshi live score + server (every tick — fast, public, reliable)
    details = fetch_milestone(cached["milestone_id"])
    if details is None:
        print(f"[poll] milestone not live (id={cached['milestone_id']})")
        return
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
        # Stale stats (ATP lagging Kalshi) retry quickly; other failures back off
        retry_gap = cfg.ATP_LAG_RETRY_SECS if (same and att["stale_since"]) else cfg.SIM_RETRY_SECS
        if not same or time.time() - att["ts"] >= retry_gap:
            stale_since  = att["stale_since"] if same else None
            accept_stale = (stale_since is not None
                            and time.time() - stale_since >= cfg.ATP_LAG_MAX_WAIT_SECS)
            result = _run_sim(mc, cached, kalshi_state, score_key,
                              p1_ask, p1_bid, p2_ask, p2_bid,
                              accept_stale=accept_stale)
            _sim_attempts[key] = {
                "score_key":   score_key,
                "ts":          time.time(),
                "stale_since": (stale_since or time.time()) if result == "stale" else None,
            }
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
        # Re-fetch prices: the Hawkeye fetch + sim take a few seconds and the
        # market may have moved since the tick started.
        p1_ask, _ = get_best_ask_bid(cached["p1_ticker"])
        p2_ask, _ = get_best_ask_bid(cached["p2_ticker"])
        if p1_ask is not None and p2_ask is not None:
            _check_entry(key, cached, ms.last_mc_prob, p1_ask, p2_ask)


def main():
    # Windows defaults redirected stdout to cp1252, which can't encode the
    # dashboard's box characters — don't let a log redirect kill the bot.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not cfg.MATCH_CONFIG:
        print("No matches configured. Add entries to MATCH_CONFIG in trade/config.py.")
        return

    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    print(f"Starting trade bot [{mode}] — {len(cfg.MATCH_CONFIG)} match(es) configured")

    while True:
        for mc in cfg.MATCH_CONFIG:
            _tick(mc)
        time.sleep(cfg.FAST_POLL_SECS)


if __name__ == "__main__":
    main()
