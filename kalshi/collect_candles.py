"""Re-download 1-min candles for KXATPMATCH markets into kalshi_candles.db.

Fixes vs the original kalshi_data_collection.ipynb cell 12:
- to_sql(method="ignore") is invalid pandas -> explicit INSERT OR IGNORE.
- Resume guard checks the candles table itself, not markets.downloaded_at
  (336 markets were stamped by the aborted run that left candles empty).
- The live series endpoint 404s for markets older than ~2 months; the
  historical endpoint serves them but with a different schema:
    live:       price.close_dollars (float str), volume_fp
    historical: price.close (string dollars),    volume
  parse_candles normalizes both.
- 429 -> sleep 5s, retry up to 3 times.

Run from repo root:  python kalshi/collect_candles.py
Safe to interrupt and re-run.
"""
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import requests

DB_PATH = "kalshi/data/kalshi_candles.db"
BASE_V2 = "https://api.elections.kalshi.com/trade-api/v2"

START = "2026-01-25"
END = "2026-05-31"

CANDLE_COLS = ["market_ticker", "end_period_ts", "open", "high", "low", "close",
               "mean", "previous", "volume", "open_interest", "bid_close", "ask_close"]


def _val(d, key):
    """Read a price field from either schema: '{key}_dollars' (live) or '{key}' (historical)."""
    if not d:
        return None
    v = d.get(f"{key}_dollars", d.get(key))
    return float(v) if v is not None else None


def parse_candles(raw, market_ticker):
    rows = []
    for c in raw:
        p = c.get("price") or {}
        bid = c.get("yes_bid") or {}
        ask = c.get("yes_ask") or {}
        vol = c.get("volume_fp", c.get("volume", 0))
        oi = c.get("open_interest_fp", c.get("open_interest", 0))
        rows.append({
            "market_ticker": market_ticker,
            "end_period_ts": pd.Timestamp(c["end_period_ts"], unit="s", tz="UTC").isoformat(),
            "open": _val(p, "open"),
            "high": _val(p, "high"),
            "low": _val(p, "low"),
            "close": _val(p, "close"),
            "mean": _val(p, "mean"),
            "previous": _val(p, "previous"),
            "volume": float(vol or 0),
            "open_interest": float(oi or 0),
            "bid_close": _val(bid, "close"),
            "ask_close": _val(ask, "close"),
        })
    cdf = pd.DataFrame(rows)
    # Carry last close forward so 'previous' is always populated (mirrors old CSVs)
    cdf["previous"] = cdf["previous"].fillna(cdf["close"].ffill())
    return cdf


def fetch_candles(series, ticker, scheduled_start):
    """Returns list of candles, or None if every endpoint failed."""
    match_ts = pd.Timestamp(scheduled_start)
    params = {
        "start_ts": int((match_ts - pd.Timedelta(days=2)).timestamp()),
        "end_ts": int((match_ts + pd.Timedelta(hours=8)).timestamp()),
        "period_interval": 1,
    }
    for url in [
        f"{BASE_V2}/series/{series}/markets/{ticker}/candlesticks",
        f"{BASE_V2}/historical/markets/{ticker}/candlesticks",
    ]:
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=15)
            except Exception:
                time.sleep(2)
                continue
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.ok:
                return r.json().get("candlesticks", [])
            break  # 4xx other than 429: try next endpoint
    return None


def main():
    con = sqlite3.connect(DB_PATH)
    todo = con.execute("""
        SELECT market_ticker, series_ticker, scheduled_start
        FROM markets
        WHERE series_ticker = 'KXATPMATCH'
          AND scheduled_start BETWEEN ? AND ?
          AND market_ticker NOT IN (SELECT DISTINCT market_ticker FROM candles)
        ORDER BY scheduled_start
    """, (START, END + "T23:59:59Z")).fetchall()
    print(f"{len(todo)} markets to download")

    insert_sql = f"INSERT OR IGNORE INTO candles ({','.join(CANDLE_COLS)}) VALUES ({','.join('?' * len(CANDLE_COLS))})"
    errors, empty = [], 0

    def _fetch(job):
        ticker, series, sched = job
        time.sleep(0.05)
        return ticker, fetch_candles(series, ticker, sched)

    # fetch in a small thread pool (network-bound); insert on the main thread
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (ticker, raw) in enumerate(pool.map(_fetch, todo)):
            if raw is None:
                errors.append(ticker)
            elif raw:
                cdf = parse_candles(raw, ticker)
                con.executemany(insert_sql, cdf[CANDLE_COLS].itertuples(index=False, name=None))
            else:
                empty += 1
            con.execute("UPDATE markets SET downloaded_at=? WHERE market_ticker=?",
                        (datetime.now(timezone.utc).isoformat(), ticker))
            if i % 100 == 0:
                con.commit()
                print(f"{i}/{len(todo)}  errors: {len(errors)}  empty: {empty}", flush=True)

    con.commit()
    n = con.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    print(f"\nDone. candles rows: {n}  errors: {len(errors)}  empty: {empty}")
    for t in errors[:20]:
        print("  failed:", t)
    con.close()


if __name__ == "__main__":
    main()
