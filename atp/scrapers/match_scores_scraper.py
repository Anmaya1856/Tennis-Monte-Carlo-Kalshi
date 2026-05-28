import argparse
import json
import math
import os
import sqlite3
from datetime import datetime

import requests
from lxml import html as lhtml

CF_CLEARANCE = '3f9WMNEUmIHU4rl0fYMI0kpPjsSgnWKMYhFgE9z4B_c-1779980623-1.2.1.1-TbuX0FSfu6RjwKg2GURc4l69l1B2yXZ4aLF6XTNACBdcI565awpUcYOfaFWuMGVEPGVkPP_nGre5vrxvngsqBT_v4QxS.PwcHlQij5qO0MNUGfjqRO7hTs8xpzaGHYJ9XxTQkFrcCP4oZ_1xt.d3Ng5QWhwW.Z_dmY0XL6r6lqwtHfBIv1H08PFXoUArA.bBn0r5Q.4sI6BwbyGhZAmL3.Gt6dNxTjj.dMqVRY3ZielqC3CdpXnZixzJsa8Tw5bxVIU4eNltS72P.040lGxZNkq.J1IIhotwHqi50beGCwtyElltSs7Vvv2vNfIpy1Bgqx7qnlMIUe4KSHZjFugQWw'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}

HAWKEYE_URL = 'https://www.atptour.com/-/Hawkeye/MatchStats/Complete/{year}/{event_id}/{match_code}'

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    year             TEXT NOT NULL,
    event_id         TEXT NOT NULL,
    match_code       TEXT NOT NULL,
    tourney_year_id  TEXT,
    match_date       TEXT,
    round            TEXT,
    p1_id            TEXT,
    p2_id            TEXT,
    p1_seed          TEXT,
    p2_seed          TEXT,
    winner_id        TEXT,
    p1_score         TEXT,
    p2_score         TEXT,
    match_url        TEXT,
    surface          TEXT,
    duration_minutes REAL,
    court_name       TEXT,
    number_of_sets   INTEGER,
    match_status     TEXT,
    reason           TEXT,
    is_qualifier     INTEGER,
    PRIMARY KEY (year, event_id, match_code),
    FOREIGN KEY (tourney_year_id) REFERENCES tournaments(tourney_year_id),
    FOREIGN KEY (p1_id)  REFERENCES players(player_id),
    FOREIGN KEY (p2_id)  REFERENCES players(player_id)
);

CREATE TABLE IF NOT EXISTS match_stats (
    year                    TEXT NOT NULL,
    event_id                TEXT NOT NULL,
    match_code              TEXT NOT NULL,
    player_id               TEXT NOT NULL,
    duration_minutes        REAL,
    serve_rating            INTEGER,
    aces                    INTEGER,
    double_faults           INTEGER,
    first_serve_pct         INTEGER,
    first_serve_in          INTEGER,
    first_serve_total       INTEGER,
    first_serve_won_pct     INTEGER,
    first_serve_won         INTEGER,
    first_serve_won_total   INTEGER,
    second_serve_won_pct    INTEGER,
    second_serve_won        INTEGER,
    second_serve_won_total  INTEGER,
    bp_saved_pct            INTEGER,
    bp_saved                INTEGER,
    bp_faced                INTEGER,
    service_games_played    INTEGER,
    return_rating           INTEGER,
    first_return_won_pct    INTEGER,
    first_return_won        INTEGER,
    first_return_total      INTEGER,
    second_return_won_pct   INTEGER,
    second_return_won       INTEGER,
    second_return_total     INTEGER,
    bp_converted_pct        INTEGER,
    bp_converted            INTEGER,
    bp_opportunities        INTEGER,
    return_games_played     INTEGER,
    total_svc_pts_won_pct   INTEGER,
    total_svc_pts_won       INTEGER,
    total_svc_pts           INTEGER,
    total_ret_pts_won_pct   INTEGER,
    total_ret_pts_won       INTEGER,
    total_ret_pts           INTEGER,
    total_pts_won_pct       INTEGER,
    total_pts_won           INTEGER,
    total_pts               INTEGER,
    PRIMARY KEY (year, event_id, match_code, player_id)
);

CREATE TABLE IF NOT EXISTS set_stats (
    year                    TEXT NOT NULL,
    event_id                TEXT NOT NULL,
    match_code              TEXT NOT NULL,
    player_id               TEXT NOT NULL,
    set_number              INTEGER NOT NULL,
    set_score               TEXT,
    tiebreak_score          INTEGER,
    duration_minutes        REAL,
    serve_rating            INTEGER,
    aces                    INTEGER,
    double_faults           INTEGER,
    first_serve_pct         INTEGER,
    first_serve_in          INTEGER,
    first_serve_total       INTEGER,
    first_serve_won_pct     INTEGER,
    first_serve_won         INTEGER,
    first_serve_won_total   INTEGER,
    second_serve_won_pct    INTEGER,
    second_serve_won        INTEGER,
    second_serve_won_total  INTEGER,
    bp_saved_pct            INTEGER,
    bp_saved                INTEGER,
    bp_faced                INTEGER,
    service_games_played    INTEGER,
    return_rating           INTEGER,
    first_return_won_pct    INTEGER,
    first_return_won        INTEGER,
    first_return_total      INTEGER,
    second_return_won_pct   INTEGER,
    second_return_won       INTEGER,
    second_return_total     INTEGER,
    bp_converted_pct        INTEGER,
    bp_converted            INTEGER,
    bp_opportunities        INTEGER,
    return_games_played     INTEGER,
    total_svc_pts_won_pct   INTEGER,
    total_svc_pts_won       INTEGER,
    total_svc_pts           INTEGER,
    total_ret_pts_won_pct   INTEGER,
    total_ret_pts_won       INTEGER,
    total_ret_pts           INTEGER,
    total_pts_won_pct       INTEGER,
    total_pts_won           INTEGER,
    total_pts               INTEGER,
    PRIMARY KEY (year, event_id, match_code, player_id, set_number)
);

CREATE TABLE IF NOT EXISTS match_ytd_stats (
    year                    TEXT NOT NULL,
    event_id                TEXT NOT NULL,
    match_code              TEXT NOT NULL,
    player_id               TEXT NOT NULL,
    aces                    INTEGER,
    double_faults           INTEGER,
    bp_faced                INTEGER,
    service_games_played    INTEGER,
    bp_opportunities        INTEGER,
    return_games_played     INTEGER,
    first_serve_pct         INTEGER,
    first_serve_won_pct     INTEGER,
    second_serve_won_pct    INTEGER,
    bp_saved_pct            INTEGER,
    service_games_won_pct   INTEGER,
    total_svc_pts_won_pct   INTEGER,
    first_return_won_pct    INTEGER,
    second_return_won_pct   INTEGER,
    bp_converted_pct        INTEGER,
    return_games_won_pct    INTEGER,
    return_pts_won_pct      INTEGER,
    total_pts_won_pct       INTEGER,
    PRIMARY KEY (year, event_id, match_code, player_id)
);
"""

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


def parse_minutes(s):
    if not s:
        return None
    parts = s.split(':')
    try:
        if len(parts) == 3:
            total_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            total_seconds = int(parts[0]) * 60 + int(parts[1])
        else:
            return None
        return round(total_seconds / 60, 4)
    except ValueError:
        return None


def g(d, key, field):
    if d is None:
        return None
    inner = d.get(key)
    if inner is None:
        return None
    return inner.get(field)


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
        'serve_rating':           g(svc, 'ServeRating',                   'Number'),
        'aces':                   g(svc, 'Aces',                          'Number'),
        'double_faults':          g(svc, 'DoubleFaults',                  'Number'),
        'first_serve_pct':        g(svc, 'FirstServe',                    'Percent'),
        'first_serve_in':         g(svc, 'FirstServe',                    'Dividend'),
        'first_serve_total':      g(svc, 'FirstServe',                    'Divisor'),
        'first_serve_won_pct':    g(svc, 'FirstServePointsWon',           'Percent'),
        'first_serve_won':        g(svc, 'FirstServePointsWon',           'Dividend'),
        'first_serve_won_total':  g(svc, 'FirstServePointsWon',           'Divisor'),
        'second_serve_won_pct':   g(svc, 'SecondServePointsWon',          'Percent'),
        'second_serve_won':       g(svc, 'SecondServePointsWon',          'Dividend'),
        'second_serve_won_total': g(svc, 'SecondServePointsWon',          'Divisor'),
        'bp_saved_pct':           g(svc, 'BreakPointsSaved',              'Percent'),
        'bp_saved':               g(svc, 'BreakPointsSaved',              'Dividend'),
        'bp_faced':               g(svc, 'BreakPointsSaved',              'Divisor'),
        'service_games_played':   g(svc, 'ServiceGamesPlayed',            'Number'),
        'return_rating':          g(ret, 'ReturnRating',                   'Number'),
        'first_return_won_pct':   g(ret, 'FirstServeReturnPointsWon',     'Percent'),
        'first_return_won':       g(ret, 'FirstServeReturnPointsWon',     'Dividend'),
        'first_return_total':     g(ret, 'FirstServeReturnPointsWon',     'Divisor'),
        'second_return_won_pct':  g(ret, 'SecondServeReturnPointsWon',    'Percent'),
        'second_return_won':      g(ret, 'SecondServeReturnPointsWon',    'Dividend'),
        'second_return_total':    g(ret, 'SecondServeReturnPointsWon',    'Divisor'),
        'bp_converted_pct':       g(ret, 'BreakPointsConverted',          'Percent'),
        'bp_converted':           g(ret, 'BreakPointsConverted',          'Dividend'),
        'bp_opportunities':       g(ret, 'BreakPointsConverted',          'Divisor'),
        'return_games_played':    g(ret, 'ReturnGamesPlayed',             'Number'),
        'total_svc_pts_won_pct':  g(pts, 'TotalServicePointsWon',         'Percent'),
        'total_svc_pts_won':      g(pts, 'TotalServicePointsWon',         'Dividend'),
        'total_svc_pts':          g(pts, 'TotalServicePointsWon',         'Divisor'),
        'total_ret_pts_won_pct':  g(pts, 'TotalReturnPointsWon',          'Percent'),
        'total_ret_pts_won':      g(pts, 'TotalReturnPointsWon',          'Dividend'),
        'total_ret_pts':          g(pts, 'TotalReturnPointsWon',          'Divisor'),
        'total_pts_won_pct':      g(pts, 'TotalPointsWon',                'Percent'),
        'total_pts_won':          g(pts, 'TotalPointsWon',                'Dividend'),
        'total_pts':              g(pts, 'TotalPointsWon',                'Divisor'),
    }


def insert_stat_row(conn, table, fixed_cols, fixed_vals, stat):
    all_cols = fixed_cols + STAT_COLS
    all_vals = fixed_vals + [stat.get(c) for c in STAT_COLS]
    placeholders = ', '.join(['?'] * len(all_cols))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders})",
        all_vals,
    )


def parse_match_link(link):
    if not link:
        return None
    parts = link.rstrip('/').split('/')
    try:
        idx = parts.index('archive')
        year, event_id, match_code = parts[idx + 1], parts[idx + 2], parts[idx + 3]
        return year, event_id, match_code
    except (ValueError, IndexError):
        return None


def parse_match_date(raw):
    cleaned = raw.strip().strip('"').strip()
    try:
        return datetime.strptime(cleaned, '%a, %d %B, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return None


def fetch_hawkeye(year, event_id, match_code):
    url = HAWKEYE_URL.format(year=year, event_id=event_id, match_code=match_code.lower())
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            text = resp.text.strip()
            return json.loads(text) if text and text != 'null' else None
    except Exception:
        pass
    return None


def get_page_tree(url):
    resp = requests.get(url, cookies=COOKIES, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    return lhtml.fromstring(resp.content)


def extract_player(stats_item):
    href_nodes = stats_item.xpath('.//div[contains(@class,"name")]/a/@href')
    player_id = ''
    if href_nodes:
        parts = href_nodes[0].strip('/').split('/')
        player_id = parts[3] if len(parts) > 3 else ''

    is_winner = bool(stats_item.xpath('.//div[contains(@class,"winner")]'))

    score_parts = []
    for score_item in stats_item.xpath('.//div[contains(@class,"score-item")]'):
        first_span = score_item.xpath('span[1]/text()')
        if first_span:
            score_parts.append(first_span[0].strip())
    score = ';'.join(score_parts)

    return player_id, is_winner, score


def scrape_tournament(conn, known_players, done_matches, tourney_year_id, tourney_slug, tourney_id, year):
    url = f'https://www.atptour.com/en/scores/archive/{tourney_slug}/{tourney_id}/{year}/results'
    try:
        tree = get_page_tree(url)
    except Exception as e:
        print(f'  WARNING: could not fetch {url} — {e}')
        return 0

    inserted = 0

    # Pass 1: build match_code → date mapping.
    # Overwrite on each accordion item so inner day-sections (later in document order)
    # win over the outer container's first-date assignment.
    date_by_match_code = {}
    for item in tree.xpath("//div[contains(@class,'atp_accordion-item')]"):
        match_date = None
        for raw in item.xpath('.//h4//text()'):
            match_date = parse_match_date(raw)
            if match_date:
                break
        if not match_date:
            continue
        for href in item.xpath('.//div[contains(@class,"match-cta")]/a[normalize-space(text())="Stats"]/@href'):
            parsed = parse_match_link('https://www.atptour.com' + href)
            if parsed:
                _, _, mc = parsed
                date_by_match_code[mc] = match_date

    # Pass 2: process each unique match div across the whole page.
    seen_match_codes = set()
    for match_div in tree.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' match ')]"):
        link_nodes = match_div.xpath(
            './/div[contains(@class,"match-cta")]/a[normalize-space(text())="Stats"]/@href'
        )
        if not link_nodes:
            continue
        match_url = 'https://www.atptour.com' + link_nodes[0]

        parsed = parse_match_link(match_url)
        if not parsed:
            continue
        m_year, event_id, match_code = parsed

        if match_code in seen_match_codes:
            continue
        seen_match_codes.add(match_code)

        stats_items = match_div.xpath('.//div[contains(@class,"stats-item")]')
        if len(stats_items) < 2:
            continue

        p1_id, p1_winner, p1_score = extract_player(stats_items[0])
        p2_id, p2_winner, p2_score = extract_player(stats_items[1])

        if not p1_id or not p2_id:
            continue
        if p1_id not in known_players or p2_id not in known_players:
            continue

        if (m_year, event_id, match_code) in done_matches:
            continue

        data = fetch_hawkeye(m_year, event_id, match_code)
        if not data:
            continue

        m   = data.get('Match') or {}
        t   = data.get('Tournament') or {}
        rnd = m.get('Round') or {}

        winner_id = (m.get('WinningPlayerId') or '').lower() or None

        pt1 = m.get('PlayerTeam1') or {}
        pt2 = m.get('PlayerTeam2') or {}
        pt1_id = (pt1.get('PlayerId') or '').lower()
        pt2_id = (pt2.get('PlayerId') or '').lower()

        p1_seed = p2_seed = None
        for pt, pid in [(pt1, pt1_id), (pt2, pt2_id)]:
            if pid == p1_id:
                p1_seed = pt.get('SeedPlayerTeam')
            elif pid == p2_id:
                p2_seed = pt.get('SeedPlayerTeam')

        conn.execute(
            '''INSERT OR IGNORE INTO matches
                (year, event_id, match_code, tourney_year_id, match_date,
                 round, p1_id, p2_id, p1_seed, p2_seed, winner_id,
                 p1_score, p2_score, match_url, surface, duration_minutes,
                 court_name, number_of_sets, match_status, reason, is_qualifier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (m_year, event_id, match_code, tourney_year_id, date_by_match_code.get(match_code),
             rnd.get('LongName'), p1_id, p2_id, p1_seed, p2_seed, winner_id,
             p1_score, p2_score, match_url, t.get('Court'),
             parse_minutes(m.get('MatchTimeTotal')), m.get('CourtName'),
             m.get('NumberOfSets'), m.get('MatchStatus'), m.get('Reason'),
             1 if m.get('IsQualifier') else 0),
        )

        stats_by_pid = {}
        for block in (m.get('PlayerTeam'), m.get('OpponentTeam')):
            if not block:
                continue
            player = block.get('Player') or {}
            pid = (player.get('PlayerId') or '').lower()
            if pid:
                stats_by_pid[pid] = {
                    'sets': block.get('SetScores') or [],
                    'ytd':  block.get('YearToDateStats'),
                }

        for pid, pdata in stats_by_pid.items():
            sets = pdata['sets']

            for s in sets:
                if s.get('SetNumber') == 0:
                    stat = extract_stats(s.get('Stats'))
                    if stat:
                        insert_stat_row(
                            conn, 'match_stats',
                            ['year', 'event_id', 'match_code', 'player_id'],
                            [m_year, event_id, match_code, pid],
                            stat,
                        )
                    break

            for s in sets:
                if s.get('SetNumber', 0) > 0 and s.get('Stats'):
                    stat = extract_stats(s['Stats'])
                    if stat:
                        insert_stat_row(
                            conn, 'set_stats',
                            ['year', 'event_id', 'match_code', 'player_id',
                             'set_number', 'set_score', 'tiebreak_score'],
                            [m_year, event_id, match_code, pid,
                             s['SetNumber'], s.get('SetScore'), s.get('TieBreakScore')],
                            stat,
                        )

            ytd = pdata.get('ytd')
            if ytd:
                svc = ytd.get('ServiceRecordStats') or {}
                ret = ytd.get('ReturnRecordStats') or {}
                conn.execute(
                    '''INSERT OR IGNORE INTO match_ytd_stats
                        (year, event_id, match_code, player_id,
                         aces, double_faults, bp_faced, service_games_played,
                         bp_opportunities, return_games_played,
                         first_serve_pct, first_serve_won_pct, second_serve_won_pct,
                         bp_saved_pct, service_games_won_pct, total_svc_pts_won_pct,
                         first_return_won_pct, second_return_won_pct, bp_converted_pct,
                         return_games_won_pct, return_pts_won_pct, total_pts_won_pct)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (m_year, event_id, match_code, pid,
                     g(svc, 'Aces',                       'Number'),
                     g(svc, 'DoubleFaults',                'Number'),
                     g(svc, 'BreakPointsFaced',            'Number'),
                     g(svc, 'ServiceGamesPlayed',          'Number'),
                     g(ret, 'BreakPointOpportunities',     'Number'),
                     g(ret, 'ReturnGamesPlayed',           'Number'),
                     g(svc, 'FirstServe',                  'Percent'),
                     g(svc, 'FirstServePointsWon',         'Percent'),
                     g(svc, 'SecondServePointsWon',        'Percent'),
                     g(svc, 'BreakPointsSaved',            'Percent'),
                     g(svc, 'ServiceGamesWon',             'Percent'),
                     g(svc, 'TotalServicePointsWon',       'Percent'),
                     g(ret, 'FirstServeReturnPointsWon',   'Percent'),
                     g(ret, 'SecondServeReturnPointsWon',  'Percent'),
                     g(ret, 'BreakPointsConverted',        'Percent'),
                     g(ret, 'ReturnGamesWon',              'Percent'),
                     g(ret, 'ReturnPointsWon',             'Percent'),
                     g(ret, 'TotalPointsWon',              'Percent')),
                )

        conn.commit()
        done_matches.add((m_year, event_id, match_code))
        inserted += 1

    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.path.join(os.path.dirname(__file__), '..', 'data', 'atp.db'))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    conn.commit()

    known_players = {r[0] for r in conn.execute('SELECT player_id FROM players')}
    print(f'Loaded {len(known_players)} known players')

    tournaments = conn.execute(
        "SELECT tourney_year_id, tourney_url, tourney_id, SUBSTR(tourney_year_id, 1, 4) AS year FROM tournaments"
    ).fetchall()

    done_matches = {
        (r[0], r[1], r[2])
        for r in conn.execute('SELECT year, event_id, match_code FROM matches')
    }
    if done_matches:
        print(f'Resuming — {len(done_matches)} match(es) already in DB, will skip them')

    total_inserted = 0
    for tourney_year_id, tourney_url, tourney_id, year in tournaments:
        url_parts  = tourney_url.rstrip('/').split('/')
        tourney_slug = url_parts[5] if len(url_parts) > 5 else ''
        if not tourney_slug or not tourney_id or not year:
            print(f'{tourney_year_id:<35} SKIP (missing slug/id/year)')
            continue

        n = scrape_tournament(conn, known_players, done_matches, tourney_year_id, tourney_slug, tourney_id, year)
        total_inserted += n
        print(f'{tourney_year_id:<35} {n} matches')

    conn.close()
    print(f'\nDone. {total_inserted} matches inserted total.')


if __name__ == '__main__':
    main()
