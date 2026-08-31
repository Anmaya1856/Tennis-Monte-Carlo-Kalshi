import base64, datetime, math, uuid, requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import trade.config as cfg


def _load_private_key():
    with open(cfg.KEY_FILE, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())


def _auth_headers(method, path):
    private_key = _load_private_key()
    ts_ms = str(int(datetime.datetime.now().timestamp() * 1000))
    msg   = ts_ms + method + path
    sig   = base64.b64encode(
        private_key.sign(
            msg.encode('utf-8'),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256()
        )
    ).decode('utf-8')
    return {
        'KALSHI-ACCESS-KEY':       cfg.API_KEY,
        'KALSHI-ACCESS-SIGNATURE': sig,
        'KALSHI-ACCESS-TIMESTAMP': ts_ms,
    }


def _fetch_orderbook(ticker):
    """Fetch raw orderbook dict; no auth required."""
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/markets/{ticker}/orderbook"
    try:
        resp = requests.get(url, timeout=5)
        if not resp.ok:
            return None
        return resp.json()
    except Exception:
        return None


def _cval(d, key):
    """Read a candle price field from either schema ('{key}_dollars' or '{key}')."""
    if not d:
        return None
    v = d.get(f"{key}_dollars", d.get(key))
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _candle_price(c):
    bid, ask = _cval(c.get("yes_bid"), "close"), _cval(c.get("yes_ask"), "close")
    if bid and ask and 0 < bid < 1 and 0 < ask < 1 and (ask - bid) <= 0.10:
        return (bid + ask) / 2
    tr = _cval(c.get("price") or {}, "close")
    return tr if tr and 0 < tr < 1 else None


def fetch_prematch_price(ticker, series, before_ts):
    """Last clean candle price at or before `before_ts`, or None.

    Prefers the mid of a two-sided quote (spread <= 10c) and falls back to the
    last trade. `before_ts` is a flat offset back from now — far enough that it
    is always pre-match, so an in-play price can never be mistaken for the prior."""
    params = {"start_ts": int(before_ts - 30 * 3600),
              "end_ts": int(before_ts), "period_interval": 1}
    for base in (cfg.KALSHI_BASE, "https://api.elections.kalshi.com"):
        for tail in (f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                     f"/trade-api/v2/historical/markets/{ticker}/candlesticks"):
            try:
                r = requests.get(base + tail, params=params, timeout=10)
                if not r.ok:
                    continue
                for c in reversed(r.json().get("candlesticks", [])):
                    px = _candle_price(c)
                    if px is not None:
                        return px
            except Exception:
                continue
    return None


def get_best_ask_bid(ticker):
    """Return (best_yes_ask, best_yes_bid) in dollars, or (None, None)."""
    ob_data = _fetch_orderbook(ticker)
    if ob_data is None:
        return None, None
    ob = ob_data.get("orderbook_fp", {})
    no_bids  = ob.get("no_dollars",  [])
    yes_bids = ob.get("yes_dollars", [])
    ask = (1 - max(float(p) for p, _ in no_bids))  if no_bids  else None
    bid = max(float(p) for p, _ in yes_bids)        if yes_bids else None
    return ask, bid


def taker_fee(count, price):
    """Kalshi taker fee: roundup(0.07 * C * P * (1-P)), rounded UP to the next cent.

    The round-up applies once to the whole order, not per contract — which is why
    100 contracts at 1c cost $0.07 in fees rather than 100x the $0.01 charged on a
    single one. Reproduces Kalshi's published fee table exactly (see tests).
    Charged whenever an order crosses the book."""
    return math.ceil(0.07 * count * price * (1 - price) * 100 - 1e-9) / 100


def series_of(ticker):
    """Series ticker from a market or event ticker (the part before the first '-')."""
    return str(ticker).split("-")[0] if ticker else ""


def maker_fee(count, price, ticker):
    """Kalshi maker fee: roundup(M * 0.0175 * C * P * (1-P)), rounded up to a cent.

    M is per-series: ATP main tour charges maker fees (M=1), Challengers do not
    (M=0). The round-up is per order, so small lots pay a large surcharge — at 5
    contracts a 2.2c raw fee still costs 3c."""
    m = cfg.MAKER_FEE_MULTIPLIER.get(series_of(ticker), cfg.MAKER_FEE_MULTIPLIER_DEFAULT)
    if m <= 0:
        return 0.0
    return math.ceil(m * 0.0175 * count * price * (1 - price) * 100 - 1e-9) / 100


def fill_fee(count, price, ticker):
    """Fee for one fill under the configured execution mode."""
    return maker_fee(count, price, ticker) if cfg.MAKER_MODE else taker_fee(count, price)


def _parse_order_response(data, side):
    """Normalise a create-order response, denominated in YES.

    fill_count is what matched IMMEDIATELY; a resting maker order returns 0 and
    is not a position yet. average_fee_paid is per contract, so the total is
    fill x fee.

    Kalshi books a sell-YES as a buy-NO, so average_fill_price comes back as the
    NO price on an ask. Everything we track is YES-denominated, so flip it:
    selling YES at 0.05 is reported as 0.95 and must not be recorded as a 95c
    exit. cost_dollars is then the YES value of the fill — the proceeds on a sell.
    """
    fill = float(data.get("fill_count") or 0)
    price = float(data.get("average_fill_price") or 0)
    if side == "ask" and price:
        price = 1 - price
    fee = float(data.get("average_fee_paid") or 0)
    return {
        "order_id":      data["order_id"],
        "filled":        fill,
        "remaining":     float(data.get("remaining_count") or 0),
        "cost_dollars":  round(fill * price, 6),
        "fee_dollars":   round(fill * fee, 6),
        "avg_price":     price or None,
    }


_shard_cache = {}


def market_shard(ticker):
    """Which exchange shard a market lives on. Orders must target it explicitly:
    these series are split across shards, and routing to the wrong one returns
    market_not_found. Cached — a market never moves."""
    if ticker not in _shard_cache:
        try:
            r = requests.get(cfg.KALSHI_BASE + f"/trade-api/v2/markets/{ticker}", timeout=10)
            _shard_cache[ticker] = r.json()["market"].get("exchange_index") if r.ok else None
        except Exception:
            _shard_cache[ticker] = None
    return _shard_cache[ticker]


def funded_shards():
    """Shard indices where the account actually holds a balance. Ordering into a
    shard the account has no presence on fails with user_not_found."""
    path = "/trade-api/v2/portfolio/balance"
    try:
        r = requests.get(cfg.KALSHI_BASE + path, headers=_auth_headers("GET", path), timeout=10)
        if not r.ok:
            return set()
        return {b["exchange_index"] for b in r.json().get("balance_breakdown", [])
                if float(b.get("balance") or 0) > 0}
    except Exception:
        return set()


def _submit(ticker, side, count, price_cents):
    """POST a V2 event-market order. Returns the parsed response or None.

    MAKER_MODE rests the order (good_till_canceled + post_only, so it can never
    cross and accidentally pay taker fees). Otherwise fill_or_kill, which is
    all-or-nothing and immediate.
    """
    path = "/trade-api/v2/portfolio/events/orders"
    body = {
        "ticker":                     ticker,
        "client_order_id":            str(uuid.uuid4()),
        "side":                       side,
        "count":                      f"{count:.2f}",
        "price":                      f"{price_cents / 100:.4f}",
        "time_in_force":              "good_till_canceled" if cfg.MAKER_MODE else "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        # These series are split across exchange shards per market. Omitting this
        # falls back to shard 0 (market_not_found for a shard-3 market); -1 routes
        # by ticker but can land on a shard the account has no presence on
        # (user_not_found). Target the market's own shard explicitly.
        "exchange_index":             market_shard(ticker) or 0,
        **({"post_only": True} if cfg.MAKER_MODE else {}),
    }
    try:
        resp = requests.post(
            cfg.KALSHI_BASE + path,
            headers={**_auth_headers("POST", path), "Content-Type": "application/json"},
            json=body,
            timeout=5,
        )
        if not resp.ok:
            print(f"[kalshi] {side} order failed {resp.status_code} on {ticker} "
                  f"({count:g} @ {price_cents}c): {resp.text}")
            return None
        return _parse_order_response(resp.json(), side)
    except Exception as e:
        print(f"[kalshi] {side} order exception: {e}")
        return None


def _dry_order(ticker, count, price_cents):
    """Dry run assumes an immediate full fill at the quoted price."""
    price = price_cents / 100
    return {
        "order_id":     f"dry-{uuid.uuid4()}",
        "filled":       float(count),
        "remaining":    0.0,
        "cost_dollars": round(count * price, 6),
        "fee_dollars":  fill_fee(count, price, ticker),
        "avg_price":    price,
    }


def place_order(ticker, count, price_cents):
    """Buy YES. Returns the parsed order (possibly unfilled) or None on failure."""
    if cfg.DRY_RUN:
        return _dry_order(ticker, count, price_cents)
    return _submit(ticker, "bid", count, price_cents)


def close_position(ticker, count, price_cents):
    """Sell YES to close. Returns the parsed order (possibly unfilled) or None.

    cost_dollars is the fill VALUE, so on a sell it is the proceeds received."""
    if cfg.DRY_RUN:
        return _dry_order(ticker, count, price_cents)
    return _submit(ticker, "ask", count, price_cents)


def get_order(order_id, ticker=None):
    """GET /portfolio/orders/{id}. Returns {status, filled, remaining, cost, fee}
    or None. status is one of resting / canceled / executed.

    Returns None on any failure, including the brief 404 right after placement
    before the order propagates — callers treat None as transient and retry."""
    if cfg.DRY_RUN:
        return {"status": "executed", "filled": 0.0, "remaining": 0.0,
                "cost_dollars": 0.0, "fee_dollars": 0.0}
    path = f"/trade-api/v2/portfolio/orders/{order_id}"
    params = {"exchange_index": market_shard(ticker)} if ticker else None
    try:
        resp = requests.get(cfg.KALSHI_BASE + path, headers=_auth_headers("GET", path),
                            params=params, timeout=5)
        if not resp.ok:
            return None
        o = resp.json()["order"]
        filled = float(o.get("fill_count_fp") or 0)
        # YES-denominated: maker/taker_fill_cost are in NO terms on a sell, so
        # derive the value from yes_price instead of trusting the cost fields.
        yes_px = float(o.get("yes_price_dollars") or 0)
        return {
            "status":       o["status"],
            "filled":       filled,
            "remaining":    float(o.get("remaining_count_fp") or 0),
            "cost_dollars": round(filled * yes_px, 6),
            "fee_dollars":  (float(o.get("maker_fees_dollars") or 0)
                             + float(o.get("taker_fees_dollars") or 0)),
            "yes_price":    yes_px or None,
        }
    except Exception as e:
        print(f"[kalshi] get_order exception: {e}")
        return None


def cancel_order(order_id, ticker=None):
    """Cancel a resting order. True if it is no longer working.

    Must use the V2 events path with an explicit exchange_index: the V1
    DELETE /portfolio/orders/{id} now returns 410 deprecated_v1_order_endpoint,
    and without the shard the V2 route cannot see an order on shard 3."""
    if cfg.DRY_RUN:
        return True
    path = f"/trade-api/v2/portfolio/events/orders/{order_id}"
    params = {"exchange_index": market_shard(ticker)} if ticker else None
    try:
        resp = requests.delete(cfg.KALSHI_BASE + path,
                               headers={**_auth_headers("DELETE", path),
                                        "Content-Type": "application/json"},
                               params=params, timeout=5)
        if not resp.ok:
            print(f"[kalshi] cancel {order_id} failed {resp.status_code}: {resp.text}")
        return resp.ok
    except Exception as e:
        print(f"[kalshi] cancel exception: {e}")
        return False


def order_queue_position(order_id, ticker=None):
    """Contracts resting ahead of our order. None if unavailable. Diagnostic only —
    this is the number that decides whether maker fills are realistic."""
    if cfg.DRY_RUN:
        return None
    path = f"/trade-api/v2/portfolio/orders/{order_id}/queue_position"
    params = {"exchange_index": market_shard(ticker)} if ticker else None
    try:
        resp = requests.get(cfg.KALSHI_BASE + path,
                            headers=_auth_headers("GET", path), params=params, timeout=5)
        if not resp.ok:
            return None
        return float(resp.json().get("queue_position_fp") or 0)
    except Exception:
        return None


def fetch_milestone_id(event_ticker):
    """Scan /milestones to find the milestone ID for a given event ticker. Returns str or None."""
    import datetime as _dt
    min_date = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/milestones"
    params = {
        "limit":                1000,
        "minimum_start_date":   min_date,
        "category":             "Sports",
        "type":                 "tennis_tournament_singles",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if not resp.ok:
            return None
        for m in resp.json().get("milestones", []):
            if m.get("details", {}).get("main_game_event_ticker") == event_ticker:
                return m["id"]
        return None
    except Exception:
        return None


_GAME_SCORE_MAP = {0: 0, 15: 1, 30: 2, 40: 3, 50: 4}
_INT_TO_NOTATION = {0: '0', 1: '15', 2: '30', 3: '40', 4: 'Ad'}


def discover_live_events(series=("KXATPMATCH", "KXATPCHALLENGERMATCH"), lookback_hours=6):
    """Currently-live matches in the given Kalshi series. Returns [(event_ticker,
    milestone_id), ...]. Filters the milestone list by event-ticker prefix and
    start_date (the list 'status' is unreliable), then confirms live via live_data."""
    now = datetime.datetime.now(datetime.timezone.utc)
    mind = (now - datetime.timedelta(hours=lookback_hours + 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/milestones"
    params = {"limit": 1000, "minimum_start_date": mind, "category": "Sports",
              "type": "tennis_tournament_singles"}
    try:
        resp = requests.get(url, params=params, timeout=12)
        if not resp.ok:
            return []
        milestones = resp.json().get("milestones", [])
    except Exception:
        return []

    out = []
    for m in milestones:
        det = m.get("details", {}) or {}
        ev = det.get("main_game_event_ticker") or ""
        if not ev or ev.split("-")[0] not in series:
            continue
        sd = det.get("start_date")
        if sd:                          # skip clearly-future / ancient to limit live checks
            try:
                sdt = datetime.datetime.strptime(sd, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc)
                if sdt > now + datetime.timedelta(minutes=10) or now - sdt > datetime.timedelta(hours=lookback_hours):
                    continue
            except ValueError:
                pass
        details = fetch_milestone(m["id"])
        if details is not None and details.get("status") == "live":
            out.append((ev, m["id"]))
    return out


def fetch_best_of(milestone_id):
    """GET /milestones/{id} (static match metadata). Returns best_of as int, or None.
    Separate endpoint from the live-data poll; called once per match at init."""
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/milestones/{milestone_id}"
    try:
        resp = requests.get(url, timeout=5)
        if not resp.ok:
            return None
        bo = resp.json()["milestone"]["details"].get("best_of")
        return int(bo) if bo is not None else None
    except Exception:
        return None


def fetch_milestone(milestone_id):
    """GET /live_data/milestone/{id} (public). Returns details dict or None on network error."""
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/live_data/milestone/{milestone_id}"
    try:
        resp = requests.get(url, timeout=5)
        if not resp.ok:
            return None
        return resp.json()["live_data"]["details"]
    except Exception:
        return None


def get_event_competitor_map(event_ticker):
    """GET /events/{ticker} (public). Returns {competitor_id: {"name": str, "ticker": str}} or None."""
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/events/{event_ticker}"
    try:
        resp = requests.get(url, timeout=5)
        if not resp.ok:
            print(f"[kalshi] event map HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        markets = resp.json()["markets"]
        return {
            m["custom_strike"]["tennis_competitor"]: {
                "name":   m["yes_sub_title"],
                "ticker": m["ticker"],
                "status": m.get("status"),
            }
            for m in markets
        }
    except Exception as e:
        print(f"[kalshi] event map exception: {e}")
        return None


def parse_milestone_state(details, p1_competitor_id):
    """
    Extract match state from milestone details.
    p1_competitor_id: UUID of p1 (from get_event_competitor_map).
    Returns {"score_str", "game_score_str", "p1_serves", "is_live"}.
    """
    is_live = details.get("status") == "live"

    p1_is_comp1 = (p1_competitor_id == details["competitor1_id"])
    c1_sets = details["competitor1_round_scores"]
    c2_sets = details["competitor2_round_scores"]

    set_parts = []
    for s1, s2 in zip(c1_sets, c2_sets):
        p1g = s1["score"] if p1_is_comp1 else s2["score"]
        p2g = s2["score"] if p1_is_comp1 else s1["score"]
        set_parts.append(f"{p1g}-{p2g}")
    score_str = " ".join(set_parts)

    c1_game = details["competitor1_current_round_score"]
    c2_game = details["competitor2_current_round_score"]
    p1_game_raw = c1_game if p1_is_comp1 else c2_game
    p2_game_raw = c2_game if p1_is_comp1 else c1_game

    p1_sets = c1_sets if p1_is_comp1 else c2_sets
    p2_sets = c2_sets if p1_is_comp1 else c1_sets
    p1_ongoing = [s for s in p1_sets if s["outcome"] == "ongoing"]
    p2_ongoing = [s for s in p2_sets if s["outcome"] == "ongoing"]
    in_tiebreak = (
        bool(p1_ongoing) and bool(p2_ongoing)
        and p1_ongoing[0]["score"] == 6 and p2_ongoing[0]["score"] == 6
    )

    if in_tiebreak:
        game_score_str = f"{p1_game_raw}-{p2_game_raw}"
    else:
        p1_int = _GAME_SCORE_MAP.get(p1_game_raw, 0)
        p2_int = _GAME_SCORE_MAP.get(p2_game_raw, 0)
        game_score_str = f"{_INT_TO_NOTATION[p1_int]}-{_INT_TO_NOTATION[p2_int]}"

    p1_serves = (details.get("server", "") == p1_competitor_id)

    # "or {}" — pre-match the keys exist but hold null
    c1_stats = details.get("competitor1_statistics") or {}
    c2_stats = details.get("competitor2_statistics") or {}
    p1_last10 = c1_stats.get("points_won_from_last_10") if p1_is_comp1 else c2_stats.get("points_won_from_last_10")
    p2_last10 = c2_stats.get("points_won_from_last_10") if p1_is_comp1 else c1_stats.get("points_won_from_last_10")

    p1_stats = _serve_stats(c1_stats if p1_is_comp1 else c2_stats)
    p2_stats = _serve_stats(c2_stats if p1_is_comp1 else c1_stats)

    def _kstats(s):
        """Shot-level stats logged for the break-point research dataset."""
        return {
            "aces":                s.get("aces"),
            "double_faults":       s.get("double_faults"),
            "winners_fh":          s.get("forehand_winners"),
            "winners_bh":          s.get("backhand_winners"),
            "unforced_fh":         s.get("forehand_unforced_errors"),
            "unforced_bh":         s.get("backhand_unforced_errors"),
            "errors_groundstroke": s.get("groundstroke_errors"),
            "max_pts_streak":      s.get("max_points_in_a_row"),
            "max_games_streak":    s.get("max_games_in_a_row"),
            "bp_won":              s.get("breakpoints_won"),
            "bp_total":            s.get("total_breakpoints"),
        }
    p1_kstats = _kstats(c1_stats if p1_is_comp1 else c2_stats)
    p2_kstats = _kstats(c2_stats if p1_is_comp1 else c1_stats)

    return {
        "score_str":      score_str,
        "game_score_str": game_score_str,
        "p1_serves":      p1_serves,
        "is_live":        is_live,
        "p1_stats":       p1_stats,
        "p2_stats":       p2_stats,
        "p1_last10":      p1_last10,
        "p2_last10":      p2_last10,
        "p1_kstats":      p1_kstats,
        "p2_kstats":      p2_kstats,
    }


def _serve_stats(s):
    """Per-player serve stats in the shape the sim/logger expect, built from Kalshi
    competitor_statistics. Kalshi doesn't split the return side by serve or provide
    ratings, so those keys are None — the traded model uses only the serve counts."""
    s = s or {}
    fss  = s.get("first_serve_successful")     # first serves in
    sss  = s.get("second_serve_successful")    # second serves in
    fspw = s.get("first_serve_points_won")
    sspw = s.get("second_serve_points_won")
    spw  = s.get("service_points_won")
    spl  = s.get("service_points_lost")
    total = (spw + spl) if (spw is not None and spl is not None) else None

    def rate(n, d):
        return (n / d) if (n is not None and d) else None

    return {
        "first_in":          rate(fss, total),
        "win_first":         rate(fspw, fss),
        "win_second":        rate(sspw, sss),
        "return_first":      None,
        "return_second":     None,
        "first_in_num":      fss,   "first_in_den":      total,
        "win_first_num":     fspw,  "win_first_den":     fss,
        "win_second_num":    sspw,  "win_second_den":    sss,
        "return_first_num":  None,  "return_first_den":  None,
        "return_second_num": None,  "return_second_den": None,
    }


def serve_stats_ready(stats):
    """True when the serve counts the sim needs are present and non-trivial."""
    den = stats.get("first_in_den")
    if den is None or den < 2:
        return False
    return stats.get("win_first_num") is not None and stats.get("win_second_num") is not None
