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
                                  get_order, cancel_order, order_queue_position,
                                  market_shard, funded_shards,
                                  fetch_milestone, fetch_milestone_id, fetch_best_of,
                                  get_event_competitor_map, parse_milestone_state,
                                  serve_stats_ready, fetch_prematch_price)
from trade.exact         import (estimate_win_prob_market, implied_point_probs,
                                  match_report, _parse_match_state, _parse_game_score)
from trade.state         import MatchStateStore
from trade.decision        import compute_entry, on_serve
import trade.swing_thresholds as _swing_thresholds
from trade                 import logger


# State is keyed by event_ticker: one budget and one position per match,
# even though each match has two tradeable markets (one per player).
_store = MatchStateStore()

# event_ticker → {"p1_ticker", "p2_ticker", "p1_competitor_id", "p1_name_kalshi",
#                 "milestone_id", "p1_name", "p2_name", "best_of"}
#                 (last three filled on first successful sim)
_match_cache = {}

# event_ticker → {"score_key", "ts"} of the last sim attempt. Throttles retries
# when serve stats aren't ready yet (early in a match) by SIM_RETRY_SECS.
_sim_attempts = {}

# event_ticker → last known contracts resting ahead of our order (None if flat).
# Logged on every snapshot: this is the measurement that decides whether maker
# fills are achievable, and it is useless unless captured over time.
_queue_ahead = {}

# event_ticker → {"game_id": str, "allowed": bool}
# Swing gate: evaluated once per game (at 0-0, or when the bot first joins mid-game)
# and held for the rest of that game so mid-game stat drift cannot change the decision.
_entry_gate = {}

# event_ticker → health, so a process self-terminates on sustained failure:
# {"first_seen", "last_init", "not_live_since"}.
_match_health = {}


def _lock_path(event_ticker):
    return os.path.join(cfg.LOG_DIR, f".bot_{event_ticker}.lock")


def claim_match(event_ticker):
    """Take the lock for this match, or return False if a bot already holds it.

    The lock is a file this process touches every tick, so it stays valid from
    the moment the bot starts — unlike the launcher's old spawn marker, which
    expired while a bot was still waiting for serve stats and let a duplicate in.
    """
    path = _lock_path(event_ticker)
    if (os.path.exists(path)
            and time.time() - os.path.getmtime(path) < cfg.BOT_LOCK_STALE_SECS):
        return False
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    open(path, "w").close()
    return True


def _heartbeat(event_ticker):
    try:
        os.utime(_lock_path(event_ticker), None)
    except OSError:
        pass


def _release(event_ticker):
    try:
        os.remove(_lock_path(event_ticker))
    except OSError:
        pass


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


def _game_id(kalshi_state, best_of):
    """Identity of the holding period a position belongs to.

    Outside a tiebreak that is just score_str, which changes exactly when a game
    ends. Inside one score_str stays "6-6" for every point, so the server is
    folded in as well: serve rotates every two points and each rotation is a new
    receiver to roll onto. Only tiebreaks fold it in — elsewhere the server is
    constant within a game, and a lagging server field would churn the position.
    """
    score = kalshi_state["score_str"]
    _, games = _parse_match_state(score, best_of)
    if games == (6, 6):
        return f"{score}|{'p1' if kalshi_state['p1_serves'] else 'p2'}"
    return score


def _serve_score(score_str, game_score_str, best_of):
    """What on_serve() should judge: games in the current set, or POINTS once a
    tiebreak starts. At 6-6 the games are level forever, so judging on games
    would call every point of a tiebreak on-serve no matter how far down we are."""
    _, games = _parse_match_state(score_str, best_of)
    if games != (6, 6):
        return games
    if not cfg.TRADE_TIEBREAKS:
        return None                  # stand aside for the whole tiebreak
    try:
        return _parse_game_score(game_score_str, is_tiebreak=True)
    except (ValueError, KeyError, AttributeError):
        return None


def _in_tiebreak(score_str, best_of):
    _, games = _parse_match_state(score_str, best_of)
    return games == (6, 6)


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

    def prob_row(label, name, g, s, m, ask, bid, tag):
        return (f"  {label} {last(name):<12} {g*100:5.1f}%  {s*100:5.1f}%  {m*100:5.1f}%  |"
                f"  {ask:.3f}   {bid:.3f}   {tag}")

    def stat_cells(s):
        def pc(x):
            return f"{x*100:3.0f}%" if x is not None else "  —"
        return (f"1st-in {pc(s['first_in'])}  1st-won {pc(s['win_first'])}  "
                f"2nd-won {pc(s['win_second'])}  ret-1st {pc(s['return_first'])}  "
                f"ret-2nd {pc(s['return_second'])}")

    # Who the strategy wants: the receiver, but only while play is unbroken.
    cur_games = _serve_score(score_str, game_score_str, best_of)
    p1_serves = server_name == p1_name
    tradeable = on_serve(cur_games, p1_serves)
    p1_role = "serving" if p1_serves else "RECEIVER" + ("  BUY" if tradeable else "")
    p2_role = "serving" if not p1_serves else "RECEIVER" + ("  BUY" if tradeable else "")
    if not tradeable:
        if cur_games:
            srv_g, rcv_g = (cur_games if p1_serves else cur_games[::-1])
            why = "server ahead" if srv_g > rcv_g else f"{abs(srv_g - rcv_g)}-game gap"
        elif _in_tiebreak(score_str, best_of):
            why = "tiebreak — disabled"
        else:
            why = "no set in progress"
        p1_role = p2_role = f"off ({why})"

    pos_line = "  No position"
    if ms.position:
        pos     = ms.position
        player  = _pos_player(cached, pos)
        age     = int(time.time() - pos["entry_time"])
        cur_val = p1_bid if player == "p1" else p2_bid
        value   = pos["count"] * cur_val
        unreal  = pos["count"] * (cur_val - pos["entry_price"])
        pos_line = (f"  Position: {player.upper()} YES @ {pos['entry_price']:.3f} × {pos['count']}"
                    f"  |  now {cur_val:.3f}  |  held since [{pos['game_id']}]"
                    f"  |  value ${value:.2f}  |  uP&L ${unreal:+.2f}  ({age}s)")

    budget_line = f"  Budget: ${ms.budget_remaining:.2f}"

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
        f"     {'':<12}  game     set   match  |  YES ask   bid    role\n"
        f"{prob_row('P1', p1_name, game_prob, set_prob, mc_prob, p1_ask, p1_bid, p1_role)}\n"
        f"{prob_row('P2', p2_name, 1 - game_prob, 1 - set_prob, 1 - mc_prob, p2_ask, p2_bid, p2_role)}\n"
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

    # These series are split across exchange shards and the account only exists on
    # some of them. Check once here rather than failing on every order.
    if not cfg.DRY_RUN:
        shard = market_shard(p1_ticker)
        ok = funded_shards()
        if shard is not None and ok and shard not in ok:
            print(f"[init] FAILED: {event_ticker} trades on exchange shard {shard}; "
                  f"this account only has a balance on {sorted(ok)}")
            return None
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
    Returns a sim-result dict, or None if serve stats aren't ready yet. Logging and
    printing happen later in the tick, once trades have settled.

    Score, server, and serve stats all come from the same Kalshi milestone payload —
    so the two-source lag the old Hawkeye path guarded against can't happen here."""
    key = mc["event_ticker"]

    # Kalshi supplies per-competitor serve stats already oriented to p1/p2.
    p1_stats, p2_stats = kalshi_state["p1_stats"], kalshi_state["p2_stats"]
    if not (serve_stats_ready(p1_stats) and serve_stats_ready(p2_stats)):
        print(f"[poll] serve stats not ready yet ({cached['p1_ticker']})")
        return None

    p1_name, p2_name = cached["p1_name_kalshi"], cached["p2_name_kalshi"]
    best_of = cached["best_of"]
    ms = _store.get_or_create(key)

    # Resolve the market-implied prior once per match (needs best_of).
    if cached["pa0"] is None:
        series = mc["event_ticker"].split("-")[0]
        before = time.time() - cfg.PREMATCH_LOOKBACK_HOURS * 3600
        px = fetch_prematch_price(cached["p1_ticker"], series, before)
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

    _store.update_divergence(key, mc_prob, (p1_ask + p1_bid) / 2)
    cached["p1_name"] = p1_name
    cached["p2_name"] = p2_name

    # Everything the snapshot and dashboard need. Handed back rather than written
    # here: the position is not settled until the exit/entry run later in the tick,
    # and logging first recorded us holding the side we were about to sell —
    # which showed up as "holding the server" at every serve rotation.
    return {
        "p1_stats": p1_stats, "p2_stats": p2_stats,
        "p1_name": p1_name, "p2_name": p2_name, "best_of": best_of,
        "mc_prob": mc_prob, "set_prob": set_prob, "game_prob": game_prob,
        "probs": probs, "report": report,
    }


def _log_and_print(mc, cached, kalshi_state, sim, p1_ask, p1_bid, p2_ask, p2_bid):
    """Record the tick AFTER trading, so the snapshot shows what we actually hold."""
    key = mc["event_ticker"]
    ms = _store.get_or_create(key)
    probs, best_of = sim["probs"], sim["best_of"]
    p1_name, p2_name = sim["p1_name"], sim["p2_name"]

    pos      = ms.position
    pos_side = _pos_player(cached, pos) if pos else None
    pos_value = (p1_bid if pos_side == "p1" else p2_bid) if pos else None
    logger.log_snapshot(
        cached["p1_ticker"], p1_name, p2_name,
        kalshi_state["score_str"], kalshi_state["game_score_str"],
        "p1" if kalshi_state["p1_serves"] else "p2",
        sim["p1_stats"], sim["p2_stats"],
        kalshi_state["p1_last10"], kalshi_state["p2_last10"],
        sim["mc_prob"], sim["set_prob"], sim["game_prob"],
        p1_ask, p1_bid, p2_ask, p2_bid,
        ms, pos_side, pos_value,
        p1_kstats=kalshi_state.get("p1_kstats"), p2_kstats=kalshi_state.get("p2_kstats"),
        prematch_price=cached["prematch_price"], pa0=cached["pa0"], pb0=cached["pb0"],
        pa_blend=probs["pa_blend"], pb_blend=probs["pb_blend"],
        report=sim["report"], vol_point=probs["vol"]["point"], vol_game=probs["vol"]["game"],
        best_of=best_of, cond=probs["cond"], milestone_id=cached["milestone_id"],
        queue_ahead=_queue_ahead.get(key),
    )

    server_name = p1_name if kalshi_state["p1_serves"] else p2_name
    mkt = {"pa0": cached["pa0"], "pb0": cached["pb0"], "pa_blend": probs["pa_blend"],
           "pb_blend": probs["pb_blend"], "wt_a": probs["wt_a"], "wt_b": probs["wt_b"]}
    _print_poll(cached, p1_name, p2_name,
                kalshi_state["score_str"], kalshi_state["game_score_str"],
                server_name, sim["p1_stats"], sim["p2_stats"], mkt,
                sim["mc_prob"], sim["game_prob"], sim["set_prob"],
                p1_ask, p1_bid, p2_ask, p2_bid, best_of,
                ms, report=sim["report"],
                vol_point=probs["vol"]["point"], vol_game=probs["vol"]["game"],
                cond=probs["cond"])


def _book_fill(key, cached, p, qty, cost, fee):
    """Apply `qty` newly-filled contracts from pending order `p`."""
    px = cost / qty if qty else p["price"]
    if p["kind"] == "entry":
        _store.deduct_fill(key, cost, fee)
        _store.add_to_position(key, p["ticker"], px, qty, p["game_id"])
        logger.log_trade(p["ticker"], cached["p1_name"] or "", cached["p2_name"] or "",
                         p["player"], "entry", px, None, None, cost, fee, None,
                         _store.get_or_create(key).budget_remaining)
        print(f"*** FILL entry {p['ticker']} {qty:g} @ {px:.3f} fee ${fee:.3f} ***")
    else:
        pos = _store.get_or_create(key).position
        entry_px = pos["entry_price"] if pos else p["price"]
        proceeds = cost - fee
        _store.restore_proceeds(key, proceeds)
        _store.reduce_position(key, qty)
        logger.log_trade(p["ticker"], "", "", p["player"], p["reason"], entry_px, px,
                         None, 0.0, fee, proceeds - qty * entry_px,
                         _store.get_or_create(key).budget_remaining)
        print(f"*** FILL exit  {p['ticker']} {qty:g} @ {px:.3f} "
              f"pnl ${proceeds - qty * entry_px:+.3f} ***")


def _resolve_pending(key, cached, kalshi_state, px):
    """Poll the resting order and book whatever has filled.

    A maker order is NOT a position until it trades, so nothing is recorded until
    the exchange says so.

    Entries belong to one game: once it ends the order is cancelled and we keep
    only what filled. Exits are different — their job is to get flat, so they keep
    working across game boundaries and are only cancelled to re-quote when our
    limit is no longer at the touch. px maps player -> the price we want to quote.
    """
    ms = _store.get_or_create(key)
    p = ms.pending
    if p is None:
        return
    o = get_order(p["order_id"], p["ticker"])
    if o is None:
        return                                   # transient failure — retry next tick
    _queue_ahead[key] = order_queue_position(p["order_id"], p["ticker"])

    new_qty = o["filled"] - p["filled"]
    if new_qty > 1e-9:
        _book_fill(key, cached, p, new_qty,
                   o["cost_dollars"] - p["cost"], o["fee_dollars"] - p["fee"])
        p.update(filled=o["filled"], cost=o["cost_dollars"], fee=o["fee_dollars"])

    if o["status"] in ("executed", "canceled"):
        _store.clear_pending(key)
        _queue_ahead.pop(key, None)
        return

    # Still resting. Decide whether to pull it.
    if p["kind"] == "entry":
        # an entry belongs to one game; once that game is over the trade is moot
        if _game_id(kalshi_state, cached["best_of"]) == p["game_id"]:
            return
        why = "game over"
    else:
        # an exit stays working until we are flat; only re-quote if our limit has
        # drifted off the touch, otherwise we would churn away our queue position
        want = px.get(p["player"])
        if want is None or round(want * 100) == round(p["price"] * 100):
            return
        why = f"re-quote {p['price']:.2f} -> {want:.2f}"

    cancel_order(p["order_id"], p["ticker"])
    final = get_order(p["order_id"], p["ticker"])             # catch a fill that raced the cancel
    if final and final["filled"] - p["filled"] > 1e-9:
        _book_fill(key, cached, p, final["filled"] - p["filled"],
                   final["cost_dollars"] - p["cost"], final["fee_dollars"] - p["fee"])
    unfilled = p["count"] - (final["filled"] if final else p["filled"])
    print(f"[order] pulled {p['kind']} {p['ticker']} ({why}): "
          f"{unfilled:g} of {p['count']:g} unfilled")
    _store.clear_pending(key)
    _queue_ahead.pop(key, None)


def _register_order(key, fill, kind, ticker, player, count, price, game_id,
                    reason=None):
    """Record the unfilled remainder of an order so later ticks can chase it."""
    if fill["remaining"] <= 1e-9:
        return
    _store.set_pending(key, {
        "order_id": fill["order_id"], "kind": kind, "ticker": ticker,
        "player": player, "count": count, "price": price, "game_id": game_id,
        "reason": reason,
        "filled": fill["filled"], "cost": fill["cost_dollars"], "fee": fill["fee_dollars"],
        "placed_at": time.time(),
    })
    q = order_queue_position(fill["order_id"], ticker)
    _queue_ahead[key] = q
    print(f"[order] {kind} resting {ticker} {fill['remaining']:g} @ {price:.2f}"
          + (f"  queue ahead: {q:g}" if q is not None else ""))


def _check_entry(key, cached, kalshi_state, p1_px, p2_px):
    """Buy whoever is about to RECEIVE, at a fixed size.
    p1_px/p2_px are the bids in MAKER_MODE (we rest) and the asks otherwise.
    The swing gate is evaluated once per game in _tick() before this is called."""
    ms = _store.get_or_create(key)

    # The receiver of the game about to be played is whoever is not serving it.
    # In a tiebreak p1_serves is the next point's server; we hold that side's
    # opponent for the whole tiebreak, since the score_str game id only changes
    # when the set ends.
    receiver = "p2" if kalshi_state["p1_serves"] else "p1"
    ticker   = cached["p1_ticker"] if receiver == "p1" else cached["p2_ticker"]
    ask      = p1_px if receiver == "p1" else p2_px

    # Logged for analysis only — nothing about the trade depends on the model.
    gp = ms.last_game_prob
    break_prob = None if gp is None else (gp if receiver == "p1" else 1 - gp)

    order_params = compute_entry(ask, ms.budget_remaining, ticker)
    if order_params is None:
        print(f"[entry] skipped {ticker}: {cfg.CONTRACTS_PER_TRADE} @ {ask:.2f} "
              f"needs more than the ${ms.budget_remaining:.2f} left")
        return

    fill = place_order(ticker, order_params["count"], order_params["price_cents"])
    if fill is None:
        return

    gid = _game_id(kalshi_state, cached["best_of"])

    # Only what actually traded becomes a position; the rest rests on the book.
    if fill["filled"] > 1e-9:
        _store.deduct_fill(key, fill["cost_dollars"], fill["fee_dollars"])
        _store.add_to_position(key, ticker, fill["avg_price"] or order_params["entry_price"],
                               fill["filled"], gid)
        logger.log_trade(
            ticker, cached["p1_name"] or "", cached["p2_name"] or "",
            receiver, "entry",
            fill["avg_price"] or order_params["entry_price"], None,
            ms.last_mc_prob, fill["cost_dollars"], fill["fee_dollars"], None,
            _store.get_or_create(key).budget_remaining,
        )
    _register_order(key, fill, "entry", ticker, receiver, order_params["count"],
                    order_params["entry_price"], gid)
    if fill["filled"] <= 1e-9:
        return

    bp = "n/a" if break_prob is None else f"{break_prob*100:.1f}%"
    print(f"\n*** ENTRY [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {ticker}"
          f"  receiver={receiver.upper()}"
          f"  price={order_params['entry_price']:.3f}"
          f"  count={fill['filled']:g}"
          f"  break={bp}"
          f"  cost=${fill['cost_dollars']:.2f}"
          f"  fee=${fill['fee_dollars']:.3f}"
          f"  budget_left=${_store.get_or_create(key).budget_remaining:.2f} ***")


def _liquidate(event_ticker, reason="shutdown"):
    """Close any open position at the current market bid. Called on graceful shutdown."""
    cached = _match_cache.get(event_ticker)
    if cached is None:
        return
    ms  = _store.get_or_create(event_ticker)
    pos = ms.position
    if pos is None:
        return

    _, bid = get_best_ask_bid(pos["ticker"])
    if bid is None:
        print(f"[{reason}] {event_ticker}: no bid available — "
              f"position NOT closed ({pos['count']:g} x {pos['ticker']})")
        return

    qty  = pos["count"]
    fill = close_position(pos["ticker"], qty, round(bid * 100))
    if fill is None or fill["filled"] <= 1e-9:
        print(f"[{reason}] {event_ticker}: close order failed — "
              f"position NOT closed ({qty:g} x {pos['ticker']})")
        return

    sold     = fill["filled"]
    proceeds = fill["cost_dollars"] - fill["fee_dollars"]
    pnl      = proceeds - sold * pos["entry_price"]
    _store.restore_proceeds(event_ticker, proceeds)
    _store.reduce_position(event_ticker, sold)
    player = "p1" if pos["ticker"] == cached["p1_ticker"] else "p2"
    logger.log_trade(
        pos["ticker"], "", "",
        player, reason,
        pos["entry_price"], fill["avg_price"] or bid,
        ms.last_mc_prob, 0.0,
        fill["fee_dollars"], pnl,
        _store.get_or_create(event_ticker).budget_remaining,
    )
    print(f"[{reason}] {event_ticker}: closed {sold:g} x {pos['ticker']} "
          f"@ {fill['avg_price'] or bid:.2f}  pnl={pnl:+.4f}")


def _check_exit(key, cached, kalshi_state, p1_px, p2_px):
    """Square off as soon as the game we entered on has finished — or, in a
    tiebreak, as soon as serve rotates or we bank a mini-break. No other exit.
    p1_px/p2_px are the asks in MAKER_MODE (we rest) and the bids otherwise."""
    ms  = _store.get_or_create(key)
    pos = ms.position
    player = _pos_player(cached, pos)
    now_id = _game_id(kalshi_state, cached["best_of"])

    if now_id != pos["game_id"]:
        # the score moved on: a game ended, a set ended, or serve rotated in a tiebreak
        reason = ("serve_rotation"
                  if kalshi_state["score_str"] == pos["game_id"].split("|")[0]
                  else "game_end")
    else:
        return                                   # same game / service block — hold

    current_value     = p1_px if player == "p1" else p2_px
    close_price_cents = round(current_value * 100)

    entry_px, qty, gid = pos["entry_price"], pos["count"], pos["game_id"]
    fill = close_position(pos["ticker"], qty, close_price_cents)
    if fill is None:
        return                                   # could not place; retry next tick

    pnl = None
    if fill["filled"] > 1e-9:
        sold = fill["filled"]
        proceeds = fill["cost_dollars"] - fill["fee_dollars"]
        pnl = proceeds - sold * entry_px
        _store.restore_proceeds(key, proceeds)
        _store.reduce_position(key, sold)
        logger.log_trade(
            pos["ticker"], "", "",
            player, reason,
            entry_px, fill["avg_price"] or current_value,
            ms.last_mc_prob, 0.0,
            fill["fee_dollars"], pnl,
            _store.get_or_create(key).budget_remaining,
        )
    # whatever did not sell stays ours and keeps chasing the ask
    _register_order(key, fill, "exit", pos["ticker"], player, qty,
                    current_value, gid, reason=reason)
    if fill["filled"] <= 1e-9:
        return

    pnl_str = f"${pnl:+.3f}" if pnl is not None else "n/a"
    print(f"\n*** {reason.upper()} [{cfg.DRY_RUN and 'DRY' or 'LIVE'}]  {pos['ticker']}"
          f"  player={player.upper()}"
          f"  entry={pos['entry_price']:.3f}"
          f"  exit={current_value:.3f}"
          f"  {pos['game_id']} -> {now_id}"
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
        # Network error — grace timer guards against transient blips
        if h["not_live_since"] is None:
            h["not_live_since"] = time.time()
        elif time.time() - h["not_live_since"] > cfg.MATCH_END_GRACE_SECS:
            print(f"[stop] {event_ticker}: no milestone data for "
                  f">{cfg.MATCH_END_GRACE_SECS // 60} min — exiting")
            return "dead"
        print(f"[poll] milestone fetch failed (id={cached['milestone_id']})")
        return

    h["not_live_since"] = None  # got a response — reset grace timer

    match_status = details.get("match_status")
    status       = details.get("status")

    if status not in ("live", "suspended"):
        # Match is over in some form (ended, retired, walkover, abandoned, …)
        winner_id    = details.get("winner")
        p1_won       = (winner_id == cached["p1_competitor_id"])
        winner_name  = cached["p1_name"] if p1_won else cached["p2_name"]
        winner_ticker = cached["p1_ticker"] if p1_won else cached["p2_ticker"]
        print(f"[done] {event_ticker}: status={status!r} match_status={match_status!r} "
              f"— winner {winner_name}")

        ms  = _store.get_or_create(key)
        pos = ms.position
        if pos is not None:
            # Settlement: winner pays $1, loser pays $0, no fee
            settle_px = 1.0 if pos["ticker"] == winner_ticker else 0.0
            qty       = pos["count"]
            proceeds  = qty * settle_px
            pnl       = proceeds - qty * pos["entry_price"]
            _store.restore_proceeds(key, proceeds)
            _store.reduce_position(key, qty)
            player = "p1" if pos["ticker"] == cached["p1_ticker"] else "p2"
            logger.log_trade(
                pos["ticker"], "", "",
                player, "settlement",
                pos["entry_price"], settle_px,
                ms.last_mc_prob, 0.0,
                0.0, pnl,
                _store.get_or_create(key).budget_remaining,
            )
            print(f"[done] settled {qty} x {pos['ticker']} @ {settle_px:.2f}  pnl={pnl:+.4f}")

        logger.log_outcome(cached["p1_ticker"], match_status, winner_name)
        return "dead"

    if status == "suspended":
        print(f"[poll] {event_ticker}: match suspended — holding")
        return

    kalshi_state = parse_milestone_state(details, cached["p1_competitor_id"])
    if not kalshi_state["is_live"]:
        print(f"[poll] {event_ticker} (milestone={cached['milestone_id']}): match state is not live")
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
    sim = None
    if score_key != ms.last_sim_score or _store.is_mc_stale(key):
        att  = _sim_attempts.get(key)
        same = att is not None and att["score_key"] == score_key
        # Throttle retries when stats aren't ready yet (early in a match); a new
        # score runs immediately.
        if not same or time.time() - att["ts"] >= cfg.SIM_RETRY_SECS:
            sim = _run_sim(mc, cached, kalshi_state, score_key,
                           p1_ask, p1_bid, p2_ask, p2_bid)
            _sim_attempts[key] = {"score_key": score_key, "ts": time.time()}

    # Which side of the book we trade on. MAKER rests: we buy at the bid and sell
    # at the ask. TAKER crosses: buy the ask, sell the bid.
    entry_px = (p1_bid, p2_bid) if cfg.MAKER_MODE else (p1_ask, p2_ask)
    exit_px  = (p1_ask, p2_ask) if cfg.MAKER_MODE else (p1_bid, p2_bid)

    # Resolve any resting order first: book what filled, cancel what is stale.
    # Until this runs, a maker order is not a position.
    _resolve_pending(key, cached, kalshi_state,
                     {"p1": exit_px[0], "p2": exit_px[1]})

    # Square off next: the position is closed the moment its game ends, which
    # frees the budget to roll straight onto the new receiver in the same tick.
    # Skipped while an exit is already working, or we would double-sell.
    if _store.has_position(key) and not _store.has_pending(key):
        _check_exit(key, cached, kalshi_state, *exit_px)

    # Then take the new game's position. Entries need a freshly computed
    # game prob — a cached one belongs to the game that just finished.
    serve_score = _serve_score(kalshi_state["score_str"], kalshi_state["game_score_str"],
                               cached["best_of"])

    # Swing gate: evaluated once when a new game is first seen (at 0-0 or when the bot
    # joins mid-game). Held for the rest of that game so mid-game stat drift cannot flip
    # the decision after we have already committed to sitting out or trading.
    now_game_id = _game_id(kalshi_state, cached["best_of"])
    gate = _entry_gate.get(key)
    if sim is not None and (gate is None or gate["game_id"] != now_game_id):
        c = ms.last_cond
        is_on_serve = on_serve(serve_score, kalshi_state["p1_serves"])
        if c is not None and is_on_serve:
            swing = abs(c["win_game"] - c["lose_game"])
            sets_won, _ = _parse_match_state(kalshi_state["score_str"], cached["best_of"])
            set_num = sum(sets_won) + 1
            min_swing = _swing_thresholds.get_threshold(
                sim["probs"]["pa_blend"], sim["probs"]["pb_blend"],
                cached["best_of"], set_num)
            allowed = swing >= min_swing
            print(f"[gate] {now_game_id}: swing={swing*100:.1f}pp "
                  f"thresh={min_swing*100:.1f}pp → {'OPEN' if allowed else 'closed'}")
        else:
            allowed = False
        _entry_gate[key] = {"game_id": now_game_id, "allowed": allowed}

    if (not _store.has_position(key)
            and not _store.has_pending(key)
            and sim is not None
            and on_serve(serve_score, kalshi_state["p1_serves"])
            and _entry_gate.get(key, {}).get("allowed", False)
            and not _store.is_budget_exhausted(key)):
        # Re-fetch prices: the sim takes a moment and the market may have moved
        # since the tick started.
        a1, b1 = get_best_ask_bid(cached["p1_ticker"])
        a2, b2 = get_best_ask_bid(cached["p2_ticker"])
        px1, px2 = (b1, b2) if cfg.MAKER_MODE else (a1, a2)
        if px1 is not None and px2 is not None:
            _check_entry(key, cached, kalshi_state, px1, px2)

    # Record last, so the snapshot reflects the position we ended the tick with.
    if sim is not None:
        _log_and_print(mc, cached, kalshi_state, sim, p1_ask, p1_bid, p2_ask, p2_bid)


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

    # One bot per match, enforced here rather than by the launcher: two processes
    # on the same match each keep their own budget and position, so they double
    # the exposure and interleave into one log.
    claimed = [mc for mc in matches if claim_match(mc["event_ticker"])]
    for mc in matches:
        if mc not in claimed:
            print(f"[skip] {mc['event_ticker']}: another bot already holds this match")
    matches = claimed
    if not matches:
        print("[exit] every requested match is already being traded")
        return

    mode = "DRY RUN" if cfg.DRY_RUN else "LIVE"
    print(f"Starting trade bot [{mode}] — {len(matches)} match(es)")

    try:
        while True:
            for mc in list(matches):
                _heartbeat(mc["event_ticker"])
                if _stop_requested(mc["event_ticker"]):
                    print(f"[stop] {mc['event_ticker']}: stop requested from monitor — exiting")
                    return  # finally block handles liquidation
                if _tick(mc) == "dead":
                    _release(mc["event_ticker"])
                    matches.remove(mc)
            if not matches:
                print("[exit] no active matches remaining — shutting down")
                return
            time.sleep(cfg.FAST_POLL_SECS)
    finally:
        for mc in matches:
            _liquidate(mc["event_ticker"])
            _release(mc["event_ticker"])


if __name__ == "__main__":
    main()
