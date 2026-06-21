import argparse
import os
import sqlite3
from curl_cffi import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

HISTORY_URL = 'https://www.atptour.com/en/-/www/rank/history/{player_id}?v=1'

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_rankings (
    player_id   TEXT NOT NULL,
    rank_date   TEXT NOT NULL,
    roll_rank   INTEGER,
    roll_points INTEGER,
    race_rank   INTEGER,
    race_points INTEGER,
    PRIMARY KEY (player_id, rank_date),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);
"""


def fetch_history(player_id):
    url = HISTORY_URL.format(player_id=player_id)
    try:
        resp = requests.get(url, impersonate='chrome', timeout=30)
        if resp.status_code != 200:
            print(f'  HTTP {resp.status_code} for {player_id}: {resp.url}')
            return player_id, None
        data = resp.json()
        rows = []
        for entry in data.get('History', []):
            rank_date = entry['RankDate'][:10]
            rows.append((
                player_id,
                rank_date,
                entry.get('SglRollRank'),
                entry.get('SglRollPoints'),
                entry.get('SglRaceRank'),
                entry.get('SglRacePoints'),
            ))
        return player_id, rows
    except Exception as e:
        print(f'  DETAIL {player_id}: {e}')
        return player_id, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db',      default=os.path.join(os.path.dirname(__file__), '..', 'data', 'atp.db'))
    parser.add_argument('--workers', type=int, default=20)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    conn.commit()

    player_ids = [row[0] for row in conn.execute('SELECT player_id FROM players')]
    print(f'Fetching rank history for {len(player_ids)} players with {args.workers} workers...')

    total_rows = 0
    errors     = 0
    done       = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_history, pid): pid for pid in player_ids}
        for fut in as_completed(futures):
            player_id, rows = fut.result()
            done += 1
            if rows is None:
                errors += 1
                print(f'  ERROR: {player_id}')
            else:
                conn.executemany(
                    'INSERT OR IGNORE INTO player_rankings '
                    '(player_id, rank_date, roll_rank, roll_points, race_rank, race_points) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    rows
                )
                conn.commit()
                total_rows += len(rows)
            if done % 50 == 0:
                print(f'  {done}/{len(player_ids)} players done, {total_rows} rows inserted')

    conn.close()
    print(f'\nDone. {done - errors}/{len(player_ids)} players succeeded, {total_rows} rows inserted, {errors} errors.')


if __name__ == '__main__':
    main()
