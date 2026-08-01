"""Kalshi WebSocket bid/ask recorder.

Connects to the Kalshi WS API, subscribes to orderbook deltas and trades for the
given market tickers, and records into mm/data/orderbooks.db:

  raw_events — every snapshot/delta/trade message verbatim (full book reconstruction)
  spreads    — best bid/ask (+ sizes) each time the top of book changes
  trades     — every trade print

Usage (from repo root, where Key1.txt lives):
  python mm/ws_recorder.py                        # auto-discover all open markets in SERIES
  python mm/ws_recorder.py KXATPGTOTAL-26JUL14RINTAB-23 [MORE_TICKERS ...]
  python mm/ws_recorder.py --duration 60 TICKER   # stop after 60s (testing)

In auto mode the open-market list is re-checked every 10 minutes; the socket is
reconnected with the updated list when markets appear or settle.
"""
import argparse
import asyncio
import base64
import datetime
import json
import sqlite3
import time
from pathlib import Path

import requests
import websockets
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

API_KEY  = "982c924c-9e22-44c8-a801-3be1ff50d45d"
KEY_FILE = "Key1.txt"
WS_URL   = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST_URL = "https://external-api.kalshi.com/trade-api/v2"
DB_PATH  = Path(__file__).parent / "data" / "orderbooks.db"
SERIES   = ["KXATPGTOTAL", "KXATPEXACTMATCH", "KXATPSETWINNER"]
DISCOVER_SECS = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
    ts_ms   INTEGER,  -- local receive time
    ticker  TEXT,
    type    TEXT,     -- orderbook_snapshot | orderbook_delta | trade
    seq     INTEGER,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS spreads (
    ts_ms   INTEGER,
    ticker  TEXT,
    bid     REAL, ask     REAL,
    bid_qty REAL, ask_qty REAL
);
CREATE TABLE IF NOT EXISTS trades (
    ts_ms      INTEGER,
    ticker     TEXT,
    price      REAL,
    count      REAL,
    taker_side TEXT
);
CREATE INDEX IF NOT EXISTS idx_spreads ON spreads (ticker, ts_ms);
"""


def _auth_headers():
    with open(KEY_FILE, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    ts = str(int(datetime.datetime.now().timestamp() * 1000))
    msg = ts + "GET" + "/trade-api/ws/v2"
    sig = base64.b64encode(key.sign(
        msg.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )).decode()
    return {"KALSHI-ACCESS-KEY": API_KEY, "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts}


class Book:
    """Live orderbook for one market: {price: qty} per side, prices in dollars."""

    def __init__(self):
        self.yes = {}   # resting YES bids
        self.no  = {}   # resting NO bids (a NO bid at p is a YES ask at 1-p)

    def load_snapshot(self, msg):
        self.yes = {float(p): float(q) for p, q in msg.get("yes_dollars_fp") or []}
        self.no  = {float(p): float(q) for p, q in msg.get("no_dollars_fp") or []}

    def apply_delta(self, msg):
        side = self.yes if msg["side"] == "yes" else self.no
        p = float(msg["price_dollars"])
        q = side.get(p, 0.0) + float(msg["delta_fp"])
        if q > 1e-9:
            side[p] = q
        else:
            side.pop(p, None)

    def top(self):
        """(bid, ask, bid_qty, ask_qty); None fields when a side is empty."""
        bid = max(self.yes) if self.yes else None
        no_best = max(self.no) if self.no else None
        ask = round(1 - no_best, 4) if no_best is not None else None
        return (bid, ask,
                self.yes.get(bid) if bid is not None else None,
                self.no.get(no_best) if no_best is not None else None)


def discover(series_list=None):
    """All open market tickers across the given series list, via REST (paginated)."""
    tickers = []
    for series in (series_list or SERIES):
        cursor = ""
        while True:
            r = requests.get(f"{REST_URL}/markets",
                             params={"series_ticker": series, "status": "open",
                                     "limit": 200, "cursor": cursor}, timeout=10)
            r.raise_for_status()
            d = r.json()
            tickers += [m["ticker"] for m in d.get("markets") or []]
            cursor = d.get("cursor") or ""
            if not cursor:
                break
    return sorted(tickers)


async def record(tickers, duration):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # WAL: analysis reads never block writes; timeout rides out any residual lock
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)

    auto = not tickers
    if auto:
        tickers = discover()
        print(f"[discover] {len(tickers)} open market(s) across {', '.join(SERIES)}")
    next_discover = time.time() + DISCOVER_SECS

    books = {}
    last_top = {}
    deadline = time.time() + duration if duration else None
    n_events = 0

    while deadline is None or time.time() < deadline:
        if not tickers:
            if not auto:
                break
            print(f"[discover] no open markets; retrying in {DISCOVER_SECS}s")
            await asyncio.sleep(DISCOVER_SECS if deadline is None
                                else min(DISCOVER_SECS, max(1, deadline - time.time())))
            try:
                tickers = discover()
            except requests.RequestException as e:
                print(f"[discover] failed ({e})")
            continue
        try:
            async with websockets.connect(WS_URL, additional_headers=_auth_headers(),
                                          open_timeout=10, ping_interval=10) as ws:
                await ws.send(json.dumps({
                    "id": 1, "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta", "trade"],
                               "market_tickers": tickers},
                }))
                print(f"[ws] connected, subscribed to {len(tickers)} market(s)")
                while deadline is None or time.time() < deadline:
                    if auto and time.time() >= next_discover:
                        next_discover = time.time() + DISCOVER_SECS
                        try:
                            new = discover()
                            if set(new) != set(tickers):
                                print(f"[discover] list changed {len(tickers)} -> {len(new)}; resubscribing")
                                tickers = new
                                break  # reconnect with the new list
                        except requests.RequestException as e:
                            print(f"[discover] failed ({e}); keeping current list")
                    timeouts = [deadline - time.time()] if deadline else []
                    if auto:
                        timeouts.append(next_discover - time.time())
                    timeout = max(1, min(timeouts)) if timeouts else None
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except (asyncio.TimeoutError, TimeoutError):
                        continue  # loop top re-checks deadline/discovery
                    now_ms = int(time.time() * 1000)
                    data = json.loads(raw)
                    typ, msg = data.get("type"), data.get("msg", {})
                    ticker = msg.get("market_ticker")
                    if typ not in ("orderbook_snapshot", "orderbook_delta", "trade"):
                        if typ == "error":
                            print(f"[ws] error: {msg}")
                        continue

                    db.execute("INSERT INTO raw_events VALUES (?,?,?,?,?)",
                               (now_ms, ticker, typ, data.get("seq"), raw))

                    if typ == "trade":
                        price = msg.get("yes_price_dollars", msg.get("price_dollars"))
                        db.execute("INSERT INTO trades VALUES (?,?,?,?,?)",
                                   (now_ms, ticker,
                                    float(price) if price is not None else None,
                                    float(msg.get("count_fp", msg.get("count", 0))),
                                    msg.get("taker_side")))
                        print(f"[trade] {ticker} {msg.get('count_fp')}@{price} taker={msg.get('taker_side')}")
                    else:
                        book = books.setdefault(ticker, Book())
                        if typ == "orderbook_snapshot":
                            book.load_snapshot(msg)
                        else:
                            book.apply_delta(msg)
                        top = book.top()
                        if top != last_top.get(ticker):
                            last_top[ticker] = top
                            db.execute("INSERT INTO spreads VALUES (?,?,?,?,?,?)",
                                       (now_ms, ticker, *top))
                            bid, ask, bq, aq = top
                            print(f"[top] {ticker} {bid}/{ask} ({bq} x {aq})")
                    n_events += 1
                    db.commit()
        except (asyncio.TimeoutError, TimeoutError):
            break
        except (websockets.ConnectionClosed, OSError) as e:
            print(f"[ws] disconnected ({e}); reconnecting in 3s")
            await asyncio.sleep(3)

    db.commit()
    db.close()
    print(f"[done] {n_events} events recorded to {DB_PATH}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tickers", nargs="*",
                    help="Kalshi market ticker(s); omit to auto-discover open markets in SERIES")
    ap.add_argument("--duration", type=float, default=None,
                    help="stop after this many seconds (default: run until Ctrl+C)")
    args = ap.parse_args()
    try:
        asyncio.run(record(args.tickers, args.duration))
    except KeyboardInterrupt:
        print("\n[done] interrupted")


if __name__ == "__main__":
    main()
