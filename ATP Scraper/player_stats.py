from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import csv
import glob
import os
import threading
from datetime import datetime

CF_CLEARANCE = '9aehtbVKLpaxluFa4L0wZF.qYWCn.GOGJryp4jCyBgQ-1777964797-1.2.1.1-dhJmRDP.r9OtDwz4EYkkuglboczFbIrvQjD5zO.a7vQzxA7Hde9UTRDOkE3V.XqYiSg0gAWNpGq5GUWmMWLR1ApChE7B8cwHqSoPPYyz8hmOmqJabci2J4yvElq1j5gJkEw7WYzJU9adn7at9KUMzgqeIwDqgr0_qRFlK3NPxl9V0J1vJ8x7HN..CrgJJ1hLLKDya1ucuJTHBwjy3vpXfQVAh1wFQByWZZVoopZwOWnIO.gqWoAaH0UpGLHPV4P5ihAz9qV3dPkkMVP6lpLc8DUKWXfZFPlwhQHT26oivRq5lYW05OEJDqPReeQlf2yj0L2NBNQixIhQFTwLYoQQUA'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}

YEARS    = ['all', '2026', '2025', '2024', '2023']
SURFACES = ['all', 'Hard', 'Clay', 'Grass', 'Carpet']

YEAR_ORDER    = {y: i for i, y in enumerate(YEARS)}
SURFACE_ORDER = {s: i for i, s in enumerate(SURFACES)}

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

# Load players from most recent player_rankings CSV (search current dir and parent)
ranking_files = sorted(glob.glob('player_rankings_*.csv') + glob.glob('../player_rankings_*.csv') + glob.glob('../../player_rankings_*.csv'), reverse=True)
if not ranking_files:
    raise FileNotFoundError('No player_rankings_*.csv found. Run player_rankings.py first.')

players   = []
rank_map  = {}  # player_id -> rank (int)
with open(ranking_files[0], newline='') as f:
    for row in csv.DictReader(f):
        players.append({'player_id': row['player_id'], 'full_name': row['full_name'], 'player_url': row['player_url']})
        rank_map[row['player_id']] = int(row['ranking'])

print(f'Loaded {len(players)} players from {ranking_files[0]}')


def fetch_one(player, year, surface):
    pid  = player['player_id']
    name = player['full_name']
    slug = player['player_url'].split('/')[5] if len(player['player_url'].split('/')) > 5 else pid
    url  = f'https://www.atptour.com/en/-/www/stats/{pid}/{year}/{surface}?v=1'
    try:
        resp = requests.get(url, cookies=COOKIES, headers=HEADERS, timeout=30)
        row  = [pid, slug, name, year, surface]
        if resp.status_code == 200:
            try:
                j        = resp.json()
                stats    = j.get('Stats', {})
                combined = {**stats.get('ServiceRecordStats', {}), **stats.get('ReturnRecordStats', {})}
                row += [combined.get(col, '') for col in STAT_COLS]
            except Exception:
                row += ['' for _ in STAT_COLS]
        else:
            row += ['' for _ in STAT_COLS]
        return row
    except Exception:
        return [pid, slug, name, year, surface] + ['' for _ in STAT_COLS]


scrape_date = datetime.today().strftime('%Y-%m-%d')
filename    = f'player_stats_{scrape_date}.csv'

# Resume: skip tasks already written to today's output file
done_set = set()
if os.path.exists(filename):
    with open(filename, newline='') as f:
        for row in csv.DictReader(f):
            done_set.add((row['player_id'], row['year'], row['surface']))
    print(f'Resuming — skipping {len(done_set)} already-completed tasks')

all_tasks = [(p, y, s) for p in players for y in YEARS for s in SURFACES]
tasks     = [(p, y, s) for p, y, s in all_tasks if (p['player_id'], y, s) not in done_set]
total     = len(tasks)
done      = 0

print(f'Fetching {total} combinations with 20 parallel workers...')

write_lock = threading.Lock()
file_exists = os.path.exists(filename)

with open(filename, 'a', newline='') as out_csv:
    writer = csv.writer(out_csv)
    if not file_exists:
        writer.writerow(['player_id', 'player_slug', 'full_name', 'year', 'surface'] + STAT_COLS)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(fetch_one, p, y, s): None for p, y, s in tasks}
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                with write_lock:
                    writer.writerow(row)
                    out_csv.flush()
            done += 1
            if done % 50 == 0:
                print(f'{done}/{total}')

print('Sorting output by rank / year / surface...')
with open(filename, newline='') as f:
    reader  = csv.DictReader(f)
    headers = reader.fieldnames
    rows    = list(reader)

rows.sort(key=lambda r: (
    rank_map.get(r['player_id'], 9999),
    YEAR_ORDER.get(r['year'], 99),
    SURFACE_ORDER.get(r['surface'], 99),
))

with open(filename, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

print(f'Saved to {filename}')
