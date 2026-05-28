from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import os
import sqlite3
import threading
from datetime import datetime

CF_CLEARANCE = '9aehtbVKLpaxluFa4L0wZF.qYWCn.GOGJryp4jCyBgQ-1777964797-1.2.1.1-dhJmRDP.r9OtDwz4EYkkuglboczFbIrvQjD5zO.a7vQzxA7Hde9UTRDOkE3V.XqYiSg0gAWNpGq5GUWmMWLR1ApChE7B8cwHqSoPPYyz8hmOmqJabci2J4yvElq1j5gJkEw7WYzJU9adn7at9KUMzgqeIwDqgr0_qRFlK3NPxl9V0J1vJ8x7HN..CrgJJ1hLLKDya1ucuJTHBwjy3vpXfQVAh1wFQByWZZVoopZwOWnIO.gqWoAaH0UpGLHPV4P5ihAz9qV3dPkkMVP6lpLc8DUKWXfZFPlwhQHT26oivRq5lYW05OEJDqPReeQlf2yj0L2NBNQixIhQFTwLYoQQUA'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}

YEARS    = ['all', '2026', '2025', '2024']
SURFACES = ['all', 'Hard', 'Clay', 'Grass', 'Carpet']

STAT_COLS = [
    'Aces', 'DoubleFaults', 'FirstServePercentage',
    'FirstServePointsWonPercentage', 'SecondServePointsWonPercentage',
    'BreakPointsFaced', 'BreakPointsSavedPercentage',
    'ServiceGamesPlayed', 'ServiceGamesWonPercentage', 'ServicePointsWonPercentage',
    'FirstServeReturnPointsWonPercentage', 'SecondServeReturnPointsWonPercentage',
    'BreakPointsOpportunities', 'BreakPointsConvertedPercentage',
    'ReturnGamesPlayed', 'ReturnGamesWonPercentage',
    'ReturnPointsWonPercentage', 'TotalPointsWonPercentage',
]

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'atp.db')
conn = sqlite3.connect(db_path)

conn.execute(f'''
    CREATE TABLE IF NOT EXISTS player_stats (
        player_id  TEXT NOT NULL,
        year       TEXT NOT NULL,
        surface    TEXT NOT NULL,
        {chr(10).join(f"        {col}  REAL," for col in STAT_COLS)}
        PRIMARY KEY (player_id, year, surface),
        FOREIGN KEY (player_id) REFERENCES players(player_id)
    )
''')
conn.commit()

players = [
    {'player_id': row[0], 'player_url': row[3]}
    for row in conn.execute('SELECT player_id, first_name, last_name, player_url FROM players')
]
print(f'Loaded {len(players)} players from atp.db')

done_set = set(conn.execute('SELECT player_id, year, surface FROM player_stats'))
if done_set:
    print(f'Resuming — skipping {len(done_set)} already-completed tasks')


def fetch_one(player, year, surface):
    pid = player['player_id']
    url = f'https://www.atptour.com/en/-/www/stats/{pid}/{year}/{surface}?v=1'
    try:
        resp = requests.get(url, cookies=COOKIES, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            try:
                j        = resp.json()
                stats    = j.get('Stats', {})
                combined = {**stats.get('ServiceRecordStats', {}), **stats.get('ReturnRecordStats', {})}
                stat_vals = [combined.get(col) for col in STAT_COLS]
            except Exception:
                stat_vals = [None] * len(STAT_COLS)
        else:
            stat_vals = [None] * len(STAT_COLS)
    except Exception:
        stat_vals = [None] * len(STAT_COLS)
    return (pid, year, surface, *stat_vals)


all_tasks = [(p, y, s) for p in players for y in YEARS for s in SURFACES]
tasks     = [(p, y, s) for p, y, s in all_tasks if (p['player_id'], y, s) not in done_set]
total     = len(tasks)
done      = 0

print(f'Fetching {total} combinations with 20 parallel workers...')

placeholders = ', '.join(['?'] * (3 + len(STAT_COLS)))
col_names    = 'player_id, year, surface, ' + ', '.join(STAT_COLS)
insert_sql   = f'INSERT OR IGNORE INTO player_stats ({col_names}) VALUES ({placeholders})'

with ThreadPoolExecutor(max_workers=20) as pool:
    futures = {pool.submit(fetch_one, p, y, s): None for p, y, s in tasks}
    for fut in as_completed(futures):
        try:
            row = fut.result()
        except Exception:
            row = None
        if row:
            conn.execute(insert_sql, row)
            conn.commit()
        done += 1
        if done % 50 == 0:
            print(f'{done}/{total}')

conn.close()
print(f'Done. {done} tasks completed.')
