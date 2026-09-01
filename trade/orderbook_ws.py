"""
WebSocket orderbook client for Kalshi.

Drop-in replacement for get_best_ask_bid() in kalshi_client.py.
A single background thread holds one persistent WS connection and maintains
a local book updated by push messages. Protocol and field names are taken
directly from mm/ws_recorder.py, which is verified against the live API.

Deleting this file reverts every caller to the REST fallback automatically —
kalshi_client.py imports this module and falls back silently on ImportError.

Requires: pip install websocket-client
"""
import base64, datetime, json, threading, time
import websocket  # websocket-client
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import trade.config as cfg

# Verified against mm/ws_recorder.py
_WS_URL  = "wss://api.elections.kalshi.com/trade-api/ws/v2"
_WS_PATH = "/trade-api/ws/v2"

_lock    = threading.Lock()
_ws_lock = threading.Lock()

# {ticker: (best_bid, best_ask, bid_size, ask_size)}
_cache: dict = {}

# Full local book: {ticker: Book}
_books: dict = {}

# Tickers the connection should be subscribed to
_subscribed: set = set()

_ws_app    = None
_connected = threading.Event()


# ── Auth ─────────────────────────────────────────────────────────────────────

def _make_headers():
    with open(cfg.KEY_FILE, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None,
                                                  backend=default_backend())
    ts  = str(int(datetime.datetime.now().timestamp() * 1000))
    msg = (ts + "GET" + _WS_PATH).encode()
    sig = base64.b64encode(
        key.sign(msg,
                 padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                             salt_length=padding.PSS.DIGEST_LENGTH),
                 hashes.SHA256())
    ).decode()
    return {"KALSHI-ACCESS-KEY": cfg.API_KEY,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts}


# ── Local book ────────────────────────────────────────────────────────────────

class _Book:
    """Live orderbook for one market; prices in dollars."""
    def __init__(self):
        self.yes: dict = {}   # {price: qty}  resting YES bids
        self.no:  dict = {}   # {price: qty}  resting NO bids (YES ask at 1-p)

    def load_snapshot(self, msg):
        self.yes = {float(p): float(q) for p, q in msg.get("yes_dollars_fp") or []}
        self.no  = {float(p): float(q) for p, q in msg.get("no_dollars_fp")  or []}

    def apply_delta(self, msg):
        side = self.yes if msg["side"] == "yes" else self.no
        p = float(msg["price_dollars"])
        q = side.get(p, 0.0) + float(msg["delta_fp"])
        if q > 1e-9:
            side[p] = q
        else:
            side.pop(p, None)

    def top(self):
        """(best_ask, best_bid, ask_size, bid_size); None when a side is empty."""
        bid     = max(self.yes) if self.yes else None
        no_best = max(self.no)  if self.no  else None
        ask     = round(1 - no_best, 4) if no_best is not None else None
        bid_fp = self.yes.get(bid)     if bid     is not None else None
        ask_fp = self.no.get(no_best)  if no_best is not None else None
        # qty is contract_count_fp (2 decimal fixed-point) — round directly to whole contracts
        bid_sz = round(bid_fp) if bid_fp is not None else None
        ask_sz = round(ask_fp) if ask_fp is not None else None
        return ask, bid, ask_sz, bid_sz


# ── WebSocket callbacks ───────────────────────────────────────────────────────

def _on_message(ws, raw):
    try:
        data   = json.loads(raw)
        typ    = data.get("type")
        msg    = data.get("msg", {})
        ticker = msg.get("market_ticker")
        if typ not in ("orderbook_snapshot", "orderbook_delta") or not ticker:
            if typ == "error":
                print(f"[ws:ob] server error: {msg}")
            return
        with _lock:
            if ticker not in _books:
                _books[ticker] = _Book()
            book = _books[ticker]
            if typ == "orderbook_snapshot":
                book.load_snapshot(msg)
            else:
                book.apply_delta(msg)
            _cache[ticker] = book.top()
    except Exception as e:
        print(f"[ws:ob] message error: {e}")


def _on_open(ws):
    _connected.set()
    print(f"[ws:ob] connected to {_WS_URL}")
    with _lock:
        tickers = list(_subscribed)
    if tickers:
        _do_subscribe(ws, tickers)


def _on_close(ws, code, msg):
    _connected.clear()
    print(f"[ws:ob] connection closed ({code}) — reconnecting in 5 s")


def _on_error(ws, error):
    print(f"[ws:ob] error: {error}")


# ── Subscription ──────────────────────────────────────────────────────────────

def _do_subscribe(ws, tickers):
    ws.send(json.dumps({
        "id": 1, "cmd": "subscribe",
        "params": {"channels": ["orderbook_delta"], "market_tickers": list(tickers)},
    }))
    print(f"[ws:ob] subscribed: {tickers}")


# ── Background thread ─────────────────────────────────────────────────────────

def _run_loop():
    global _ws_app
    while True:
        try:
            app = websocket.WebSocketApp(
                _WS_URL,
                header=_make_headers(),
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            with _ws_lock:
                _ws_app = app
            app.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"[ws:ob] run_forever crashed: {e}")
        _connected.clear()
        with _ws_lock:
            _ws_app = None
        time.sleep(5)


_thread      = None
_thread_lock = threading.Lock()


def _ensure_started():
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run_loop, daemon=True, name="kalshi-ob-ws")
        _thread.start()


# ── Public API ────────────────────────────────────────────────────────────────

def _ensure_subscribed(ticker):
    """Subscribe ticker if not already tracked; no-op otherwise."""
    with _lock:
        already = ticker in _subscribed
    if not already:
        with _lock:
            _subscribed.add(ticker)
        with _ws_lock:
            ws = _ws_app
        if ws is not None and _connected.is_set():
            try:
                _do_subscribe(ws, [ticker])
            except Exception as e:
                print(f"[ws:ob] subscribe failed for {ticker}: {e}")


def get_best_ask_bid(ticker):
    """Return (best_yes_ask, best_yes_bid) from the WS cache, or (None, None) if not yet received."""
    _ensure_started()
    _ensure_subscribed(ticker)
    with _lock:
        cached = _cache.get(ticker)
    if cached is None:
        return None, None
    ask, bid, _ask_sz, _bid_sz = cached
    return ask, bid


def get_top_of_book(ticker):
    """Return {"ask", "bid", "ask_size", "bid_size"} from the WS cache, or all-None if not yet received."""
    _ensure_started()
    _ensure_subscribed(ticker)
    with _lock:
        cached = _cache.get(ticker)
    if cached is None:
        return {"ask": None, "bid": None, "ask_size": None, "bid_size": None}
    ask, bid, ask_sz, bid_sz = cached
    return {"ask": ask, "bid": bid, "ask_size": ask_sz, "bid_size": bid_sz}


def unsubscribe(ticker):
    """Drop a ticker from tracking. Server keeps sending (no unsubscribe command),
    but the message handler ignores tickers not in _books after the book is cleared."""
    with _lock:
        _subscribed.discard(ticker)
        _cache.pop(ticker, None)
        _books.pop(ticker, None)
