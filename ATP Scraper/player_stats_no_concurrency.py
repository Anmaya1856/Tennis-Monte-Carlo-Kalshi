from scraping import array2csv
import requests
import csv
import glob
import os
from datetime import datetime

CF_CLEARANCE = 'JzbaomCrOTpO3Vz65.m6R2goO70OJMPIMzs0tRgXZpQ-1777891545-1.2.1.1-kXLjIhNulJ55qqUKoFwbxEFx2nQQvTuWhCoHxhGtjiH7gwgBT5kyflNXfaNoGfjIEArosdCxEocaqjelX92w3HQwVJLTVQGO.J0.TWgkIQBWHleAae7mtkDyXb9Dd7urPKoN67pZisNCEEWXKjH.DZjj1xRfiskSBhTwJCRThwg3w3pZI3qSC1D4tu6DkCenRcOyWCQ7Z6A8Eo5fwcZpxkbW8Gzh_sOpHuUEIBx9TvFXzZwbnbaVlbT4R9Q5qAkfST3cTx2y.0l4bboGld0tngBGUpiT7bg2xCwiQqBaxZOpygZLoN485xgZ_OGbp5bBVw5v1mMZCYalrzUj2EJzyg'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}

YEARS    = ['all', '2026', '2025', '2024', '2023']
SURFACES = ['all', 'Grass', 'Clay', 'Hard', 'Carpet']

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

players = []
with open(ranking_files[0], newline='') as f:
    for row in csv.DictReader(f):
        players.append({'player_id': row['player_id'], 'full_name': row['full_name'], 'player_url': row['player_url']})

print(f'Loaded {len(players)} players from {ranking_files[0]}')

scrape_date = datetime.today().strftime('%Y-%m-%d')
data = []
total = len(players) * len(YEARS) * len(SURFACES)
done = 0

for player in players:
    pid   = player['player_id']
    name  = player['full_name']
    slug  = player['player_url'].split('/')[5] if len(player['player_url'].split('/')) > 5 else pid

    for year in YEARS:
        for surface in SURFACES:
            url  = f'https://www.atptour.com/en/-/www/stats/{pid}/{year}/{surface}?v=1'
            resp = requests.get(url, cookies=COOKIES, headers=HEADERS)
            done += 1

            row = [pid, slug, name, year, surface]

            if resp.status_code == 200:
                try:
                    j     = resp.json()
                    stats = j.get('Stats', {})
                    svc   = stats.get('ServiceRecordStats', {})
                    ret   = stats.get('ReturnRecordStats', {})
                    combined = {**svc, **ret}
                    row += [combined.get(col, '') for col in STAT_COLS]
                except Exception:
                    row += ['' for _ in STAT_COLS]
            else:
                row += ['' for _ in STAT_COLS]

            data.append(row)

            if done % 25 == 0:
                print(f'{done}/{total}  {name} {year}/{surface}')

headers = [['player_id', 'player_slug', 'full_name', 'year', 'surface'] + STAT_COLS]
filename = f'player_stats_{scrape_date}.csv'
array2csv(headers + data, filename)
print(f'\nSaved {len(data)} rows to {filename}')
