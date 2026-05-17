"""
update_db.py  —  Incremental update of tennis.db and staging.db.

Phases:
  1. Rankings + Players   — scrape top-500 rankings page; update rank history
  2. Player Career Stats  — re-fetch year='all' + CURRENT_YEAR for all players
  3. Slug population      — derive tourney_slug from tournament_url in DB
  4. Match scores         — scrape tournament result pages for new match codes
  5. Hawkeye fetch        — fetch new match stats into staging.db
  6. Load to DB           — process unloaded staging rows into tennis.db

Usage:
    python update_db.py --cf TOKEN
    python update_db.py --cf TOKEN --dry-run
    python update_db.py --cf TOKEN --year 2025 --workers 10
"""

import argparse, csv, glob, json, math, os, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from lxml import html as lhtml


# -- Constants -----------------------------------------------------------------

CURRENT_YEAR = datetime.today().year

RANKINGS_URL  = 'https://www.atptour.com/en/rankings/singles?rankRange=1-500'
HISTORY_URL   = 'https://www.atptour.com/en/-/www/rank/history/{player_id}?v=1'
STATS_URL_TPL = 'https://www.atptour.com/en/-/www/stats/{player_id}/{year}/{surface}?v=1'
SCORES_URL    = 'https://www.atptour.com/en/scores/archive/{slug}/{event_id}/{year}/results'
HAWKEYE_URL   = 'https://www.atptour.com/-/Hawkeye/MatchStats/Complete/{year}/{event_id}/{match_code}'

SURFACES    = ['all', 'Hard', 'Clay', 'Grass', 'Carpet']
STAT_YEARS  = ['all', str(CURRENT_YEAR)]
STATS_WORKERS = 20

CONSECUTIVE_403_LIMIT = 5

DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
)

STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_match_stats (
    year        TEXT    NOT NULL,
    event_id    TEXT    NOT NULL,
    match_code  TEXT    NOT NULL,
    raw_json    TEXT,
    status      TEXT    NOT NULL,
    http_code   INTEGER,
    fetched_at  TEXT,
    PRIMARY KEY (year, event_id, match_code)
);
"""


# -- CLI -----------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description='Incremental tennis.db updater')
    p.add_argument('--db',            default='../data/tennis.db')
    p.add_argument('--staging',       default='../data/staging.db')
    p.add_argument('--year',          type=int, default=CURRENT_YEAR)
    p.add_argument('--workers',       type=int, default=10)
    p.add_argument('--dry-run',       action='store_true')
    p.add_argument('--cf',            default=None, help='CF_CLEARANCE cookie value')
    p.add_argument('--ua',            default=None, help='User-Agent string')
    p.add_argument('--future-window', type=int, default=30,
                   help='Skip tournaments starting more than N days from today (default 30)')
    p.add_argument('--past-window',   type=int, default=60,
                   help='Skip tournaments whose start_date is more than N days ago (default 60)')
    return p.parse_args()


# -- DB setup ------------------------------------------------------------------

def open_connections(db_path, staging_path):
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')

    staging = sqlite3.connect(staging_path)
    staging.execute('PRAGMA journal_mode=WAL')
    staging.executescript(STAGING_SCHEMA)
    staging.commit()
    return conn, staging


def add_tourney_slug_column(conn):
    try:
        conn.execute('ALTER TABLE tournaments ADD COLUMN tourney_slug TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass


# -- HTTP helpers --------------------------------------------------------------

def make_cf_session(cf_clearance, ua):
    s = requests.Session()
    s.cookies.set('cf_clearance', cf_clearance)
    s.headers.update({'User-Agent': ua})
    return s


def get_page_tree(session, url):
    resp = session.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    return lhtml.fromstring(resp.content)


# -- Helpers copied/adapted from existing scripts ------------------------------

def parse_minutes(s):
    if not s:
        return None
    parts = s.split(':')
    try:
        if len(parts) == 3:
            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            secs = int(parts[0]) * 60 + int(parts[1])
        else:
            return None
        return round(secs / 60, 4)
    except ValueError:
        return None


def g(d, key, field):
    if d is None:
        return None
    inner = d.get(key)
    return None if inner is None else inner.get(field)


def safe(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def extract_stats(stats):
    if not stats:
        return None
    svc = stats.get('ServiceStats') or {}
    ret = stats.get('ReturnStats') or {}
    pts = stats.get('PointStats') or {}
    return {
        'duration_minutes':       parse_minutes(stats.get('Time')),
        'serve_rating':           g(svc, 'ServeRating',                'Number'),
        'aces':                   g(svc, 'Aces',                       'Number'),
        'double_faults':          g(svc, 'DoubleFaults',               'Number'),
        'first_serve_pct':        g(svc, 'FirstServe',                 'Percent'),
        'first_serve_in':         g(svc, 'FirstServe',                 'Dividend'),
        'first_serve_total':      g(svc, 'FirstServe',                 'Divisor'),
        'first_serve_won_pct':    g(svc, 'FirstServePointsWon',        'Percent'),
        'first_serve_won':        g(svc, 'FirstServePointsWon',        'Dividend'),
        'first_serve_won_total':  g(svc, 'FirstServePointsWon',        'Divisor'),
        'second_serve_won_pct':   g(svc, 'SecondServePointsWon',       'Percent'),
        'second_serve_won':       g(svc, 'SecondServePointsWon',       'Dividend'),
        'second_serve_won_total': g(svc, 'SecondServePointsWon',       'Divisor'),
        'bp_saved_pct':           g(svc, 'BreakPointsSaved',           'Percent'),
        'bp_saved':               g(svc, 'BreakPointsSaved',           'Dividend'),
        'bp_faced':               g(svc, 'BreakPointsSaved',           'Divisor'),
        'service_games_played':   g(svc, 'ServiceGamesPlayed',         'Number'),
        'return_rating':          g(ret, 'ReturnRating',                'Number'),
        'first_return_won_pct':   g(ret, 'FirstServeReturnPointsWon',  'Percent'),
        'first_return_won':       g(ret, 'FirstServeReturnPointsWon',  'Dividend'),
        'first_return_total':     g(ret, 'FirstServeReturnPointsWon',  'Divisor'),
        'second_return_won_pct':  g(ret, 'SecondServeReturnPointsWon', 'Percent'),
        'second_return_won':      g(ret, 'SecondServeReturnPointsWon', 'Dividend'),
        'second_return_total':    g(ret, 'SecondServeReturnPointsWon', 'Divisor'),
        'bp_converted_pct':       g(ret, 'BreakPointsConverted',       'Percent'),
        'bp_converted':           g(ret, 'BreakPointsConverted',       'Dividend'),
        'bp_opportunities':       g(ret, 'BreakPointsConverted',       'Divisor'),
        'return_games_played':    g(ret, 'ReturnGamesPlayed',          'Number'),
        'total_svc_pts_won_pct':  g(pts, 'TotalServicePointsWon',      'Percent'),
        'total_svc_pts_won':      g(pts, 'TotalServicePointsWon',      'Dividend'),
        'total_svc_pts':          g(pts, 'TotalServicePointsWon',      'Divisor'),
        'total_ret_pts_won_pct':  g(pts, 'TotalReturnPointsWon',       'Percent'),
        'total_ret_pts_won':      g(pts, 'TotalReturnPointsWon',       'Dividend'),
        'total_ret_pts':          g(pts, 'TotalReturnPointsWon',       'Divisor'),
        'total_pts_won_pct':      g(pts, 'TotalPointsWon',             'Percent'),
        'total_pts_won':          g(pts, 'TotalPointsWon',             'Dividend'),
        'total_pts':              g(pts, 'TotalPointsWon',             'Divisor'),
    }


STAT_COLS = [
    'duration_minutes',
    'serve_rating', 'aces', 'double_faults',
    'first_serve_pct', 'first_serve_in', 'first_serve_total',
    'first_serve_won_pct', 'first_serve_won', 'first_serve_won_total',
    'second_serve_won_pct', 'second_serve_won', 'second_serve_won_total',
    'bp_saved_pct', 'bp_saved', 'bp_faced', 'service_games_played',
    'return_rating',
    'first_return_won_pct', 'first_return_won', 'first_return_total',
    'second_return_won_pct', 'second_return_won', 'second_return_total',
    'bp_converted_pct', 'bp_converted', 'bp_opportunities', 'return_games_played',
    'total_svc_pts_won_pct', 'total_svc_pts_won', 'total_svc_pts',
    'total_ret_pts_won_pct', 'total_ret_pts_won', 'total_ret_pts',
    'total_pts_won_pct', 'total_pts_won', 'total_pts',
]


def insert_stat_row(conn, table, fixed_cols, fixed_vals, stat):
    all_cols = fixed_cols + STAT_COLS
    all_vals = fixed_vals + [stat.get(c) for c in STAT_COLS]
    ph = ', '.join(['?'] * len(all_cols))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(all_cols)}) VALUES ({ph})",
        all_vals,
    )


def parse_match_link(link):
    if not link:
        return None
    parts = link.rstrip('/').split('/')
    try:
        idx = parts.index('archive')
        return parts[idx + 1], parts[idx + 2], parts[idx + 3]
    except (ValueError, IndexError):
        return None


def save_hawkeye_result(staging, year, event_id, match_code, status, raw_json, http_code):
    staging.execute("""
        INSERT OR REPLACE INTO raw_match_stats
            (year, event_id, match_code, raw_json, status, http_code, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (year, event_id, match_code, raw_json, status, http_code,
          datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
    staging.commit()


def fetch_hawkeye_one(year, event_id, match_code, ua):
    url = HAWKEYE_URL.format(year=year, event_id=event_id, match_code=match_code.lower())
    t0 = time.time()
    try:
        resp = requests.get(url, headers={'User-Agent': ua}, timeout=30)
        elapsed = time.time() - t0
        if resp.status_code == 200:
            text = resp.text.strip()
            if text and text != 'null':
                return {'status': 'ok',      'json': text, 'http_code': 200,                'elapsed': elapsed}
            return     {'status': 'no_data', 'json': None, 'http_code': 200,                'elapsed': elapsed}
        return         {'status': 'error',   'json': None, 'http_code': resp.status_code,   'elapsed': elapsed}
    except Exception as exc:
        return         {'status': 'error',   'json': None, 'http_code': None,               'elapsed': time.time() - t0, 'exc': str(exc)}


def process_match(conn, data, known_players):
    """Parse one Hawkeye JSON response, write to all match tables."""
    m = data['Match']
    if m.get('IsDoubles'):
        return 'skipped_doubles'

    p1_id = ((m.get('PlayerTeam1') or {}).get('PlayerId') or '').lower()
    p2_id = ((m.get('PlayerTeam2') or {}).get('PlayerId') or '').lower()
    if not p1_id or not p2_id or p1_id not in known_players or p2_id not in known_players:
        return 'skipped_unknown'

    t = data['Tournament']
    conn.execute("""
        INSERT OR IGNORE INTO tournaments
            (event_id, event_year, tournament_name, tournament_url, event_type, start_date, end_date, city)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        t['EventId'], t['EventYear'],
        t.get('TournamentName'), t.get('TournamentUrl'), t.get('EventType'),
        (t.get('StartDate') or '').split('T')[0] or None,
        (t.get('EndDate') or '').split('T')[0] or None,
        t.get('TournamentCity'),
    ))
    tourney_id = conn.execute(
        'SELECT id FROM tournaments WHERE event_id=? AND event_year=?',
        (t['EventId'], t['EventYear']),
    ).fetchone()[0]

    rnd = m.get('Round') or {}
    winner_id = (m.get('WinningPlayerId') or '').lower() or None
    conn.execute("""
        INSERT OR IGNORE INTO matches
            (tournament_id, match_code, is_doubles, round_id, round_short, round_long,
             court_name, surface, duration_minutes, match_status, reason, winner_player_id,
             is_qualifier, number_of_sets, date_seq)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        tourney_id, m['MatchId'], 0,
        rnd.get('RoundId'), rnd.get('ShortName'), rnd.get('LongName'),
        m.get('CourtName'),
        data.get('Tournament', {}).get('Court'),
        parse_minutes(m.get('MatchTimeTotal')),
        m.get('MatchStatus'), m.get('Reason'), winner_id,
        1 if m.get('IsQualifier') else 0,
        m.get('NumberOfSets'), m.get('DateSeq'),
    ))
    match_id = conn.execute(
        'SELECT id FROM matches WHERE tournament_id=? AND match_code=?',
        (tourney_id, m['MatchId']),
    ).fetchone()[0]

    for team_key, is_p1 in [('PlayerTeam1', 1), ('PlayerTeam2', 0)]:
        team = m.get(team_key) or {}
        pid = (team.get('PlayerId') or '').lower()
        conn.execute(
            'INSERT OR IGNORE INTO match_players (match_id, player_id, is_player1, seed, entry_status) VALUES (?,?,?,?,?)',
            (match_id, pid, is_p1, team.get('SeedPlayerTeam'), team.get('EntryStatusPlayerTeam')),
        )

    stats_by_pid = {}
    for block in (m.get('PlayerTeam'), m.get('OpponentTeam')):
        if not block:
            continue
        pid = ((block.get('Player') or {}).get('PlayerId') or '').lower()
        if pid:
            stats_by_pid[pid] = {'sets': block.get('SetScores') or [], 'ytd': block.get('YearToDateStats')}

    for pid, pdata in stats_by_pid.items():
        sets = pdata['sets']
        for s in sets:
            if s.get('SetNumber') == 0:
                stat = extract_stats(s.get('Stats'))
                if stat:
                    insert_stat_row(conn, 'match_stats', ['match_id', 'player_id'], [match_id, pid], stat)
                break
        for s in sets:
            if s.get('SetNumber', 0) > 0 and s.get('Stats'):
                stat = extract_stats(s['Stats'])
                if stat:
                    insert_stat_row(conn, 'set_stats',
                        ['match_id', 'player_id', 'set_number', 'set_score', 'tiebreak_score'],
                        [match_id, pid, s['SetNumber'], s.get('SetScore'), s.get('TieBreakScore')],
                        stat)
        ytd = pdata.get('ytd')
        if ytd:
            svc = ytd.get('ServiceRecordStats') or {}
            ret = ytd.get('ReturnRecordStats') or {}
            conn.execute("""
                INSERT OR IGNORE INTO match_ytd_stats (
                    match_id, player_id,
                    aces, double_faults, bp_faced, service_games_played,
                    bp_opportunities, return_games_played,
                    first_serve_pct, first_serve_won_pct, second_serve_won_pct, bp_saved_pct,
                    service_games_won_pct, total_svc_pts_won_pct,
                    first_return_won_pct, second_return_won_pct, bp_converted_pct,
                    return_games_won_pct, return_pts_won_pct, total_pts_won_pct
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                match_id, pid,
                g(svc, 'Aces', 'Number'),                         g(svc, 'DoubleFaults', 'Number'),
                g(svc, 'BreakPointsFaced', 'Number'),             g(svc, 'ServiceGamesPlayed', 'Number'),
                g(ret, 'BreakPointOpportunities', 'Number'),      g(ret, 'ReturnGamesPlayed', 'Number'),
                g(svc, 'FirstServe', 'Percent'),                  g(svc, 'FirstServePointsWon', 'Percent'),
                g(svc, 'SecondServePointsWon', 'Percent'),        g(svc, 'BreakPointsSaved', 'Percent'),
                g(svc, 'ServiceGamesWon', 'Percent'),             g(svc, 'TotalServicePointsWon', 'Percent'),
                g(ret, 'FirstServeReturnPointsWon', 'Percent'),   g(ret, 'SecondServeReturnPointsWon', 'Percent'),
                g(ret, 'BreakPointsConverted', 'Percent'),        g(ret, 'ReturnGamesWon', 'Percent'),
                g(ret, 'ReturnPointsWon', 'Percent'),             g(ret, 'TotalPointsWon', 'Percent'),
            ))

    conn.commit()
    return 'inserted'


# -- Phase 1: Rankings + Players -----------------------------------------------

def scrape_rankings_page(session):
    tree = get_page_tree(session, RANKINGS_URL)
    rows = tree.xpath("//table[contains(@class,'mega-table')]//tr[.//td[contains(@class,'tiny-cell')]]")
    players = []
    for row in rows:
        rank_list = row.xpath(".//td[contains(@class,'tiny-cell')]/text()")
        href_list = row.xpath(".//li[@class='name']/a/@href")
        if not rank_list or not href_list:
            continue
        href = href_list[0]
        parts = href.split('/')
        player_id = parts[4] if len(parts) > 4 else ''
        if not player_id:
            continue
        slug = parts[3] if len(parts) > 3 else ''
        full_name  = slug.replace('-', ' ').title()
        name_parts = full_name.rsplit(' ', 1)
        first_name = name_parts[0] if len(name_parts) == 2 else ''
        last_name  = name_parts[1] if len(name_parts) == 2 else full_name
        players.append({
            'player_id': player_id, 'first_name': first_name,
            'last_name': last_name, 'player_url': 'https://www.atptour.com' + href,
        })
    return players


def fetch_rank_history_delta(player_id, latest_date):
    url = HISTORY_URL.format(player_id=player_id)
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return player_id, None
        data = resp.json()
        rows = [
            (player_id, entry['RankDate'][:10],
             entry.get('SglRollRank'), entry.get('SglRollPoints'),
             entry.get('SglRaceRank'), entry.get('SglRacePoints'))
            for entry in data.get('History', [])
            if entry['RankDate'][:10] > latest_date
        ]
        return player_id, rows
    except Exception:
        return player_id, None


def phase1_rankings(conn, session, workers, dry_run):
    print('\n-- Phase 1: Rankings + Players --')

    player_ids = [r[0] for r in conn.execute('SELECT player_id FROM players')]
    latest_dates = {r[0]: r[1] for r in conn.execute(
        'SELECT player_id, MAX(rank_date) FROM player_rankings GROUP BY player_id'
    )}

    if dry_run:
        print(f'  [DRY RUN] Would scrape rankings page, check for new players')
        print(f'  [DRY RUN] Would fetch rank history for {len(player_ids)} players')
        return player_ids

    players = scrape_rankings_page(session)
    new_players = 0
    for p in players:
        cur = conn.execute(
            'INSERT OR IGNORE INTO players (player_id, first_name, last_name, player_url) VALUES (?,?,?,?)',
            (p['player_id'], p['first_name'], p['last_name'], p['player_url']),
        )
        new_players += cur.rowcount
    conn.commit()
    print(f'  Scraped {len(players)} players, {new_players} new')

    # Refresh player_ids in case new players were added
    player_ids = [r[0] for r in conn.execute('SELECT player_id FROM players')]

    total_new = errors = done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_rank_history_delta, pid, latest_dates.get(pid, '2000-01-01')): pid
            for pid in player_ids
        }
        for fut in as_completed(futures):
            player_id, rows = fut.result()
            done += 1
            if rows is None:
                errors += 1
            elif rows:
                conn.executemany(
                    'INSERT OR IGNORE INTO player_rankings '
                    '(player_id, rank_date, roll_rank, roll_points, race_rank, race_points) '
                    'VALUES (?,?,?,?,?,?)',
                    rows,
                )
                conn.commit()
                total_new += len(rows)
            if done % 100 == 0:
                print(f'  Rank history: {done}/{len(player_ids)} players, {total_new} new rows')

    print(f'  Rank history done: {total_new} new rows, {errors} errors')
    return player_ids


# -- Phase 2: Player Career Stats ---------------------------------------------

def fetch_career_stat(session, player_id, year, surface):
    url = STATS_URL_TPL.format(player_id=player_id, year=year, surface=surface)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        j = resp.json()
        stats = j.get('Stats', {})
        c = {**stats.get('ServiceRecordStats', {}), **stats.get('ReturnRecordStats', {})}
        return (
            player_id, year, surface,
            safe(c.get('Aces')),
            safe(c.get('DoubleFaults')),
            safe(c.get('FirstServePercentage')),
            safe(c.get('FirstServePointsWonPercentage')),
            safe(c.get('SecondServePointsWonPercentage')),
            safe(c.get('BreakPointsFaced')),
            safe(c.get('BreakPointsSavedPercentage')),
            safe(c.get('ServiceGamesPlayed')),
            safe(c.get('ServiceGamesWonPercentage')),
            safe(c.get('ServicePointsWonPercentage')),
            safe(c.get('FirstServeReturnPointsWonPercentage')),
            safe(c.get('SecondServeReturnPointsWonPercentage')),
            safe(c.get('BreakPointsOpportunities')),
            safe(c.get('BreakPointsConvertedPercentage')),
            safe(c.get('ReturnGamesPlayed')),
            safe(c.get('ReturnGamesWonPercentage')),
            safe(c.get('ReturnPointsWonPercentage')),
            safe(c.get('TotalPointsWonPercentage')),
        )
    except Exception:
        return None


def phase2_career_stats(conn, session, player_ids, dry_run):
    print('\n-- Phase 2: Player Career Stats --')
    tasks = [(pid, yr, surf) for pid in player_ids for yr in STAT_YEARS for surf in SURFACES]
    n = len(tasks)
    print(f'  Tasks: {n} ({len(player_ids)} players x {len(STAT_YEARS)} years x {len(SURFACES)} surfaces)')

    if dry_run:
        print(f'  [DRY RUN] Would fetch {n} API calls -> INSERT OR REPLACE')
        return

    done = errors = upserted = 0
    with ThreadPoolExecutor(max_workers=STATS_WORKERS) as pool:
        futures = {pool.submit(fetch_career_stat, session, pid, yr, surf): None
                   for pid, yr, surf in tasks}
        for fut in as_completed(futures):
            row = fut.result()
            done += 1
            if row is None:
                errors += 1
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO player_career_stats (
                        player_id, year, surface,
                        aces, double_faults,
                        first_serve_pct, first_serve_won_pct, second_serve_won_pct,
                        bp_faced, bp_saved_pct,
                        service_games_played, service_games_won_pct, service_pts_won_pct,
                        first_return_won_pct, second_return_won_pct,
                        bp_opportunities, bp_converted_pct,
                        return_games_played, return_games_won_pct,
                        return_pts_won_pct, total_pts_won_pct
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, row)
                upserted += 1
            if done % 500 == 0:
                conn.commit()
                print(f'  {done}/{n} done, {upserted} upserted, {errors} errors')
    conn.commit()
    print(f'  Career stats done: {upserted} upserted, {errors} errors')


# -- Phase 3: Tournament Slug Population --------------------------------------

def phase3_slugs(conn, year, future_window, past_window, dry_run):
    print('\n-- Phase 3: Tournament Slugs --')

    # Populate tourney_slug from tournament_url for rows where it's missing
    missing = conn.execute(
        'SELECT id, tournament_url FROM tournaments WHERE tourney_slug IS NULL'
    ).fetchall()

    # Build slug lookup from tournaments CSV (has slugs for all years including 2026
    # where tournament_url may be NULL in the DB)
    csv_slug_map = {}
    csv_files = sorted(glob.glob('../data/tournaments_*.csv'), reverse=True)
    if csv_files:
        with open(csv_files[0], newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                csv_slug_map[(row.get('tourney_id', ''), row.get('year', ''))] = row.get('tourney_slug', '')

    # Always populate slugs — pure derivation from existing data, no HTTP needed
    # Source 1: derive from tournament_url; Source 2: fall back to CSV
    populated = 0
    for tid, url in missing:
        slug = None
        if url:
            parts = url.rstrip('/').split('/')
            candidate = parts[-3] if len(parts) >= 3 else ''
            if candidate and not candidate.isdigit() and candidate not in ('en', 'tournaments'):
                slug = candidate
        if not slug:
            # Fall back to CSV lookup using event_id
            event_id, event_year = conn.execute(
                'SELECT event_id, event_year FROM tournaments WHERE id=?', (tid,)
            ).fetchone()
            slug = csv_slug_map.get((str(event_id), str(event_year)), '')
        if slug:
            conn.execute('UPDATE tournaments SET tourney_slug=? WHERE id=?', (slug, tid))
            populated += 1
    conn.commit()

    # Get tournaments in the date window for Phase 4
    year_tourneys = conn.execute("""
        SELECT event_id, event_year, tourney_slug FROM tournaments
        WHERE event_year = ?
          AND start_date <= date('now', '+' || ? || ' days')
          AND start_date >= date('now', '-' || ? || ' days')
          AND tourney_slug IS NOT NULL
    """, (year, future_window, past_window)).fetchall()

    print(f'  Populated {populated} missing slugs')
    print(f'  Tournaments in window (past {past_window}d / future {future_window}d): {len(year_tourneys)}')
    return year_tourneys


# -- Phase 4: Match Scores -> Hawkeye Keys -------------------------------------

def get_match_links(session, tourney_slug, event_id, year):
    url = SCORES_URL.format(slug=tourney_slug, event_id=event_id, year=year)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return []
        tree = lhtml.fromstring(resp.content)
        hrefs = tree.xpath('//div[contains(@class,"match-cta")]/a[normalize-space(text())="Stats"]/@href')
        return ['https://www.atptour.com' + href for href in hrefs]
    except Exception:
        return []


def phase4_match_keys(staging, session, year_tourneys, dry_run):
    print('\n-- Phase 4: Match Scores --')

    staging_ok = {(r[0], r[1], r[2]) for r in staging.execute(
        "SELECT year, event_id, match_code FROM raw_match_stats WHERE status='ok'"
    )}
    print(f'  staging.db: {len(staging_ok)} matches already fetched')

    if dry_run:
        print(f'  [DRY RUN] Would scrape {len(year_tourneys)} tournament result pages')
        return []

    all_keys = set()
    for event_id, event_year, tourney_slug in year_tourneys:
        links = get_match_links(session, tourney_slug, event_id, event_year)
        for link in links:
            parsed = parse_match_link(link)
            if parsed:
                all_keys.add(parsed)

    new_keys = [k for k in all_keys if k not in staging_ok]
    print(f'  Found {len(all_keys)} match keys across {len(year_tourneys)} tournaments, {len(new_keys)} new')
    return new_keys


# -- Phase 5: Hawkeye Fetch ----------------------------------------------------

def phase5_hawkeye(staging, new_keys, workers, ua, dry_run):
    print('\n-- Phase 5: Hawkeye Fetch --')

    if dry_run:
        print(f'  [DRY RUN] Would fetch {len(new_keys)} Hawkeye API calls')
        return

    if not new_keys:
        print('  Nothing to fetch.')
        return

    counters = {'ok': 0, 'no_data': 0, 'error': 0}
    consecutive_403s = 0
    n = len(new_keys)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_hawkeye_one, y, e, c, ua): (y, e, c) for y, e, c in new_keys}
        for i, fut in enumerate(as_completed(futures), 1):
            year, event_id, match_code = futures[fut]
            result = fut.result()
            status    = result['status']
            http_code = result.get('http_code')

            counters[status] += 1
            save_hawkeye_result(staging, year, event_id, match_code,
                                status, result.get('json'), http_code)

            consecutive_403s = (consecutive_403s + 1) if http_code == 403 else 0

            if i % 50 == 0 or i == n:
                print(f'  [{i}/{n}] ok={counters["ok"]} no_data={counters["no_data"]} error={counters["error"]}')

            if consecutive_403s >= CONSECUTIVE_403_LIMIT:
                print(f'  WARNING: {CONSECUTIVE_403_LIMIT} consecutive 403s — stopping. Re-run to continue.')
                pool.shutdown(wait=False, cancel_futures=True)
                break

    print(f'  Hawkeye done: ok={counters["ok"]}, no_data={counters["no_data"]}, error={counters["error"]}')


# -- Phase 6: Load staging.db -> tennis.db -------------------------------------

def load_scores_from_csv(conn, dry_run):
    csv_files = sorted(glob.glob('../data/match_scores_*.csv'), reverse=True)
    if not csv_files:
        print('  No match_scores_*.csv found, skipping scores update')
        return
    csv_path = csv_files[0]
    print(f'  Scores CSV: {csv_path}')

    if dry_run:
        print('  [DRY RUN] Would update p1_score/p2_score from CSV')
        return

    import csv as csv_mod
    updated = 0
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv_mod.DictReader(f):
            parsed = parse_match_link(row.get('match_link', ''))
            if not parsed:
                continue
            year, event_id, match_code = parsed
            cur = conn.execute("""
                UPDATE matches SET p1_score = ?, p2_score = ?
                WHERE LOWER(match_code) = LOWER(?)
                  AND tournament_id = (SELECT id FROM tournaments WHERE event_id = ? AND event_year = ?)
            """, (row.get('p1_score'), row.get('p2_score'), match_code, event_id, int(year)))
            updated += cur.rowcount
    conn.commit()
    print(f'  Scores updated: {updated} rows')


def phase6_load(conn, staging, dry_run):
    print('\n-- Phase 6: Load to tennis.db --')

    loaded_keys = {(r[0], r[1], r[2]) for r in conn.execute("""
        SELECT t.event_year, t.event_id, m.match_code
        FROM matches m JOIN tournaments t ON m.tournament_id = t.id
    """)}

    to_load = [
        (yr, eid, mc, rj)
        for yr, eid, mc, rj in staging.execute(
            "SELECT year, event_id, match_code, raw_json FROM raw_match_stats WHERE status='ok'"
        )
        if (yr, eid, mc) not in loaded_keys
    ]

    print(f'  {len(to_load)} staging rows not yet in tennis.db')

    if dry_run:
        print(f'  [DRY RUN] Would process {len(to_load)} matches')
        return

    if not to_load:
        print('  Nothing to load.')
        return

    known_players = {r[0] for r in conn.execute('SELECT player_id FROM players')}
    counters = {'inserted': 0, 'skipped_unknown': 0, 'skipped_doubles': 0, 'error': 0}
    n = len(to_load)

    for i, (yr, eid, mc, raw_json) in enumerate(to_load, 1):
        try:
            result = process_match(conn, json.loads(raw_json), known_players)
            counters[result] += 1
        except Exception:
            counters['error'] += 1

        if i % 50 == 0 or i == n:
            print(f'  [{i}/{n}] inserted={counters["inserted"]} '
                  f'skipped={counters["skipped_unknown"] + counters["skipped_doubles"]} '
                  f'errors={counters["error"]}')

    print(f'  Load done: inserted={counters["inserted"]}, '
          f'skipped_doubles={counters["skipped_doubles"]}, '
          f'skipped_unknown={counters["skipped_unknown"]}, '
          f'errors={counters["error"]}')


# -- Main ----------------------------------------------------------------------

def main():
    args = parse_args()

    cf = args.cf or os.environ.get('CF_CLEARANCE')
    if not cf and not args.dry_run:
        print('ERROR: CF_CLEARANCE required. Pass --cf TOKEN or set CF_CLEARANCE env var.')
        print('Refresh: Chrome DevTools -> Application -> Cookies -> atptour.com -> cf_clearance')
        return

    ua = args.ua or os.environ.get('ATP_USER_AGENT') or DEFAULT_UA

    print(f'tennis.db  : {args.db}')
    print(f'staging.db : {args.staging}')
    print(f'Year       : {args.year}')
    print(f'Workers    : {args.workers} (stats: {STATS_WORKERS})')
    print(f'Window     : -{args.past_window}d to +{args.future_window}d')
    print(f'Dry run    : {args.dry_run}')

    conn, staging = open_connections(args.db, args.staging)
    add_tourney_slug_column(conn)

    session = make_cf_session(cf or '', ua)

    player_ids   = phase1_rankings(conn, session, args.workers, args.dry_run)
    phase2_career_stats(conn, session, player_ids, args.dry_run)
    year_tourneys = phase3_slugs(conn, args.year, args.future_window, args.past_window, args.dry_run)
    new_keys     = phase4_match_keys(staging, session, year_tourneys, args.dry_run)
    phase5_hawkeye(staging, new_keys, args.workers, ua, args.dry_run)
    phase6_load(conn, staging, args.dry_run)
    print('\n-- Scores: Updating p1_score/p2_score from CSV --')
    load_scores_from_csv(conn, args.dry_run)

    conn.close()
    staging.close()
    print('\nDone.')


if __name__ == '__main__':
    main()
