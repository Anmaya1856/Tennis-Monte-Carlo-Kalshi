import base64, datetime, uuid, requests
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


def _parse_order_response(data):
    fill   = float(data["fill_count"])
    price  = float(data.get("average_fill_price") or 0)
    fee    = float(data.get("average_fee_paid")   or 0)
    return {
        "cost_dollars": round(fill * price, 6),
        "fee_dollars":  round(fill * fee,   6),
        "order_id":     data["order_id"],
    }


def place_order(ticker, side, count, yes_price_cents):
    """
    Place a limit buy order on Kalshi (V2 API).
    side: "yes" (buy YES = bid) or "no" (buy NO = ask on YES leg)
    count: contracts, supports up to 2 decimal places
    yes_price_cents: limit price in cents on the YES leg (1–99)
    Returns {"cost_dollars", "fee_dollars", "order_id"} or None on failure.
    """
    if cfg.DRY_RUN:
        cost = round(count * (yes_price_cents / 100), 6)
        return {"cost_dollars": cost, "fee_dollars": 0.0, "order_id": f"dry-{uuid.uuid4()}"}

    path = "/trade-api/v2/orders"
    body = {
        "ticker":                      ticker,
        "client_order_id":             str(uuid.uuid4()),
        "side":                        "bid" if side == "yes" else "ask",
        "count":                       f"{count:.2f}",
        "price":                       f"{yes_price_cents / 100:.4f}",
        "time_in_force":               "fill_or_kill",
        "self_trade_prevention_type":  "taker_at_cross",
    }
    try:
        resp = requests.post(
            cfg.KALSHI_BASE + path,
            headers={**_auth_headers("POST", path), "Content-Type": "application/json"},
            json=body,
            timeout=5,
        )
        if not resp.ok:
            print(f"[kalshi] order failed {resp.status_code}: {resp.text}")
            return None
        return _parse_order_response(resp.json())
    except Exception as e:
        print(f"[kalshi] order exception: {e}")
        return None


def close_position(ticker, side, count, yes_price_cents):
    """
    Close an open position (V2 API).
    side: "yes" (sell YES = ask) or "no" (buy YES back = bid)
    yes_price_cents: YES-leg price in cents to close at
    """
    if cfg.DRY_RUN:
        return {"cost_dollars": 0.0, "fee_dollars": 0.0, "order_id": f"dry-close-{uuid.uuid4()}"}

    path = "/trade-api/v2/orders"
    body = {
        "ticker":                      ticker,
        "client_order_id":             str(uuid.uuid4()),
        "side":                        "ask" if side == "yes" else "bid",
        "count":                       f"{count:.2f}",
        "price":                       f"{yes_price_cents / 100:.4f}",
        "time_in_force":               "fill_or_kill",
        "self_trade_prevention_type":  "taker_at_cross",
    }
    try:
        resp = requests.post(
            cfg.KALSHI_BASE + path,
            headers={**_auth_headers("POST", path), "Content-Type": "application/json"},
            json=body,
            timeout=5,
        )
        if not resp.ok:
            print(f"[kalshi] close failed {resp.status_code}: {resp.text}")
            return None
        return _parse_order_response(resp.json())
    except Exception as e:
        print(f"[kalshi] close exception: {e}")
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


def fetch_milestone(milestone_id):
    """GET /live_data/milestone/{id} (public). Returns details dict or None if not live."""
    url = f"{cfg.KALSHI_BASE}/trade-api/v2/live_data/milestone/{milestone_id}"
    try:
        resp = requests.get(url, timeout=5)
        if not resp.ok:
            return None
        details = resp.json()["live_data"]["details"]
        return details if details.get("status") == "live" else None
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

    c1_stats = details.get("competitor1_statistics", {})
    c2_stats = details.get("competitor2_statistics", {})
    p1_last10 = c1_stats.get("points_won_from_last_10") if p1_is_comp1 else c2_stats.get("points_won_from_last_10")
    p2_last10 = c2_stats.get("points_won_from_last_10") if p1_is_comp1 else c1_stats.get("points_won_from_last_10")

    return {
        "score_str":      score_str,
        "game_score_str": game_score_str,
        "p1_serves":      p1_serves,
        "is_live":        is_live,
        "p1_last10":      p1_last10,
        "p2_last10":      p2_last10,
    }
