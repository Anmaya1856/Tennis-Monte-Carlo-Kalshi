"""
fetch_match_stats.py

Reads match_scores_*.csv, calls the Hawkeye API for each match,
and stores raw JSON in a SQLite staging table (raw_match_stats).

On every run:
  - Skips matches already stored with status='ok'
  - Re-fetches matches with status='error' or 'no_data'

Usage:
    python fetch_match_stats.py
    python fetch_match_stats.py --db staging.db --csv match_scores_2023-2026.csv --workers 15
"""

import argparse
import csv
import glob
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'}

STATS_URL = 'https://www.atptour.com/-/Hawkeye/MatchStats/Complete/{year}/{event_id}/{match_code}'

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_match_stats (
    year        TEXT    NOT NULL,
    event_id    TEXT    NOT NULL,
    match_code  TEXT    NOT NULL,
    raw_json    TEXT,
    status      TEXT    NOT NULL,   -- 'ok' | 'error' | 'no_data'
    http_code   INTEGER,
    fetched_at  TEXT,
    PRIMARY KEY (year, event_id, match_code)
);
"""

CONSECUTIVE_403_LIMIT = 5


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_done(conn):
    """Return set of (year, event_id, match_code) that already have status='ok'."""
    return {
        (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT year, event_id, match_code FROM raw_match_stats WHERE status='ok'"
        )
    }


def save_result(conn, year, event_id, match_code, status, raw_json, http_code):
    conn.execute("""
        INSERT OR REPLACE INTO raw_match_stats
            (year, event_id, match_code, raw_json, status, http_code, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (year, event_id, match_code, raw_json, status, http_code,
          datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()


# ── CSV parsing ───────────────────────────────────────────────────────────────

def parse_match_link(link):
    """
    Extract (year, event_id, match_code) from match_link URLs. Two formats exist:
      https://www.atptour.com/en/scores/stats-centre/archive/2023/2843/ms001
      https://www.atptour.com/en/scores/match-stats/archive/2023/580/ms001
    Both have /archive/{year}/{event_id}/{match_code} as the tail.
    Returns None if malformed or missing.
    """
    if not link:
        return None
    parts = link.rstrip('/').split('/')
    try:
        idx = parts.index('archive')
        year, event_id, match_code = parts[idx + 1], parts[idx + 2], parts[idx + 3]
        return year, event_id, match_code
    except (ValueError, IndexError):
        return None


def load_match_keys(csv_path):
    """Return list of unique (year, event_id, match_code) from the CSV."""
    keys, seen = [], set()
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            parsed = parse_match_link(row.get('match_link', ''))
            if parsed and parsed not in seen:
                seen.add(parsed)
                keys.append(parsed)
    return keys


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_one(year, event_id, match_code):
    """Fetch one match from the Hawkeye API. Returns a result dict."""
    url = STATS_URL.format(year=year, event_id=event_id, match_code=match_code.lower())
    t0  = time.time()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        elapsed = time.time() - t0
        if resp.status_code == 200:
            text = resp.text.strip()
            if text and text != 'null':
                return {'status': 'ok',      'json': text,  'http_code': 200,                  'elapsed': elapsed}
            else:
                return {'status': 'no_data', 'json': None,  'http_code': 200,                  'elapsed': elapsed}
        else:
            return     {'status': 'error',   'json': None,  'http_code': resp.status_code,     'elapsed': elapsed}
    except Exception as exc:
        return         {'status': 'error',   'json': None,  'http_code': None,                 'elapsed': time.time() - t0, 'exc': str(exc)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Fetch Hawkeye match stats into a SQLite staging table')
    parser.add_argument('--db',      default='../data/staging.db',  help='SQLite staging database (default: ../data/staging.db)')
    parser.add_argument('--csv',     default=None,           help='match_scores CSV (default: most recent match_scores_*.csv)')
    parser.add_argument('--workers', default=10, type=int,   help='parallel HTTP workers (default: 10)')
    parser.add_argument('--limit',   default=None, type=int, help='only fetch this many matches (for testing)')
    args = parser.parse_args()

    # Resolve CSV
    csv_path = args.csv
    if not csv_path:
        files = sorted(glob.glob('../data/match_scores_*.csv'), reverse=True)
        if not files:
            print('ERROR: No match_scores_*.csv found. Pass --csv path/to/file.csv')
            return
        csv_path = files[0]

    print(f'CSV     : {csv_path}')
    print(f'DB      : {args.db}')
    print(f'Workers : {args.workers}')
    print()

    conn    = init_db(args.db)
    done    = load_done(conn)
    all_keys = load_match_keys(csv_path)

    total_in_csv = len(all_keys)
    to_fetch   = [k for k in all_keys if k not in done]
    already_ok = total_in_csv - len(to_fetch)
    if args.limit:
        to_fetch = to_fetch[:args.limit]

    print(f'Matches in CSV    : {total_in_csv:>6,}')
    print(f'Already OK in DB  : {already_ok:>6,}')
    print(f'To fetch          : {len(to_fetch):>6,}')
    print()

    if not to_fetch:
        print('Nothing to do.')
        conn.close()
        return

    print(f'{"#":>7}  {"Status":<8}  {"Year/EventID/MatchCode":<26}  {"HTTP":>4}  {"Time":>6}')
    print(f'{"-"*7}  {"-"*8}  {"-"*26}  {"-"*4}  {"-"*6}')

    counters           = {'ok': 0, 'error': 0, 'no_data': 0}
    consecutive_403s   = 0
    cancelled          = False
    n                  = len(to_fetch)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_one, y, e, c): (y, e, c)
            for y, e, c in to_fetch
        }

        for i, future in enumerate(as_completed(futures), 1):
            year, event_id, match_code = futures[future]
            result = future.result()

            status    = result['status']
            http_code = result.get('http_code')
            elapsed   = result['elapsed']

            counters[status] += 1
            save_result(conn, year, event_id, match_code, status, result.get('json'), http_code)

            # 403 guard
            if http_code == 403:
                consecutive_403s += 1
            else:
                consecutive_403s = 0

            # Log line
            label    = status.upper()
            key_str  = f'{year}/{event_id}/{match_code}'
            http_str = str(http_code) if http_code else '---'
            exc_str  = f'  ({result["exc"]})' if result.get('exc') else ''
            print(f'[{i:>5}/{n}]  {label:<8}  {key_str:<26}  {http_str:>4}  {elapsed:>5.1f}s{exc_str}')

            if consecutive_403s >= CONSECUTIVE_403_LIMIT:
                print()
                print(f'WARNING: {CONSECUTIVE_403_LIMIT} consecutive 403 responses — the API may be blocking requests.')
                print('         Already-fetched matches will be skipped on resume.')
                cancelled = True
                pool.shutdown(wait=False, cancel_futures=True)
                break

    conn.close()
    print()
    print('-' * 55)
    print(f'  OK       : {counters["ok"]:,}')
    print(f'  No data  : {counters["no_data"]:,}')
    print(f'  Errors   : {counters["error"]:,}')
    if cancelled:
        print(f'  CANCELLED after {CONSECUTIVE_403_LIMIT} consecutive 403s')
    print('-' * 55)
    print(f'Re-run to retry errors/no_data. OK matches are skipped automatically.')


if __name__ == '__main__':
    main()
