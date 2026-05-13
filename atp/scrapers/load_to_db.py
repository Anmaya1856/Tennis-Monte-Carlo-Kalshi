"""
load_to_db.py

Builds tennis.db from:
  - player_rankings_*.csv  → players, player_rankings
  - player_stats_*.csv     → player_career_stats
  - staging.db             → tournaments, matches, match_players,
                             match_stats, set_stats, match_ytd_stats

Only matches where BOTH players exist in the players table are inserted.

Usage:
    python load_to_db.py
    python load_to_db.py --db tennis.db --staging staging.db
"""

import argparse
import glob
import json
import logging
import math
import sqlite3

import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
log = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id  TEXT PRIMARY KEY,
    first_name TEXT,
    last_name  TEXT,
    player_url TEXT
);

CREATE TABLE IF NOT EXISTS player_rankings (
    id             INTEGER PRIMARY KEY,
    player_id      TEXT NOT NULL REFERENCES players(player_id),
    ranking        INTEGER,
    ranking_points INTEGER,
    scraped_date   TEXT,
    UNIQUE(player_id, scraped_date)
);

CREATE TABLE IF NOT EXISTS player_career_stats (
    id                   INTEGER PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES players(player_id),
    year                 TEXT,
    surface              TEXT,
    aces                 REAL,
    double_faults        REAL,
    first_serve_pct      REAL,
    first_serve_won_pct  REAL,
    second_serve_won_pct REAL,
    bp_faced             REAL,
    bp_saved_pct         REAL,
    service_games_played REAL,
    service_games_won_pct REAL,
    service_pts_won_pct  REAL,
    first_return_won_pct  REAL,
    second_return_won_pct REAL,
    bp_opportunities     REAL,
    bp_converted_pct     REAL,
    return_games_played  REAL,
    return_games_won_pct REAL,
    return_pts_won_pct   REAL,
    total_pts_won_pct    REAL,
    UNIQUE(player_id, year, surface)
);

CREATE TABLE IF NOT EXISTS tournaments (
    id               INTEGER PRIMARY KEY,
    event_id         TEXT NOT NULL,
    event_year       INTEGER NOT NULL,
    tournament_name  TEXT,
    tournament_url   TEXT,
    event_type       TEXT,
    start_date       TEXT,
    end_date         TEXT,
    city             TEXT,
    UNIQUE(event_id, event_year)
);

CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY,
    tournament_id    INTEGER NOT NULL REFERENCES tournaments(id),
    match_code       TEXT NOT NULL,
    is_doubles       INTEGER,
    round_id         INTEGER,
    round_short      TEXT,
    round_long       TEXT,
    court_name       TEXT,
    duration_minutes REAL,
    match_status     TEXT,
    winner_player_id TEXT REFERENCES players(player_id),
    is_qualifier     INTEGER,
    number_of_sets   INTEGER,
    date_seq         TEXT,
    UNIQUE(tournament_id, match_code)
);

CREATE TABLE IF NOT EXISTS match_players (
    match_id     INTEGER NOT NULL REFERENCES matches(id),
    player_id    TEXT    NOT NULL REFERENCES players(player_id),
    is_player1   INTEGER NOT NULL,
    seed         TEXT,
    entry_status TEXT,
    PRIMARY KEY(match_id, player_id)
);

CREATE TABLE IF NOT EXISTS match_stats (
    id                      INTEGER PRIMARY KEY,
    match_id                INTEGER NOT NULL REFERENCES matches(id),
    player_id               TEXT    NOT NULL REFERENCES players(player_id),
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
    UNIQUE(match_id, player_id)
);

CREATE TABLE IF NOT EXISTS set_stats (
    id                      INTEGER PRIMARY KEY,
    match_id                INTEGER NOT NULL REFERENCES matches(id),
    player_id               TEXT    NOT NULL REFERENCES players(player_id),
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
    UNIQUE(match_id, player_id, set_number)
);

CREATE TABLE IF NOT EXISTS match_ytd_stats (
    id                    INTEGER PRIMARY KEY,
    match_id              INTEGER NOT NULL REFERENCES matches(id),
    player_id             TEXT    NOT NULL REFERENCES players(player_id),
    aces                  INTEGER,
    double_faults         INTEGER,
    bp_faced              INTEGER,
    service_games_played  INTEGER,
    bp_opportunities      INTEGER,
    return_games_played   INTEGER,
    first_serve_pct       INTEGER,
    first_serve_won_pct   INTEGER,
    second_serve_won_pct  INTEGER,
    bp_saved_pct          INTEGER,
    service_games_won_pct INTEGER,
    total_svc_pts_won_pct INTEGER,
    first_return_won_pct  INTEGER,
    second_return_won_pct INTEGER,
    bp_converted_pct      INTEGER,
    return_games_won_pct  INTEGER,
    return_pts_won_pct    INTEGER,
    total_pts_won_pct     INTEGER,
    UNIQUE(match_id, player_id)
);
"""

# Shared stat columns between match_stats and set_stats (after the fixed id/match_id/player_id cols)
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_minutes(s):
    """'02:15:11' → 135.183 minutes. Returns None if blank."""
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
    """d.get(key, {}).get(field) with None guards."""
    if d is None:
        return None
    inner = d.get(key)
    if inner is None:
        return None
    return inner.get(field)


def safe(v):
    """Convert NaN/None to None, leave everything else as-is."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def extract_stats(stats):
    """
    Extract all STAT_COLS values from a Stats dict (ServiceStats + ReturnStats + PointStats).
    Returns None if stats is None.
    """
    if not stats:
        return None
    svc = stats.get('ServiceStats') or {}
    ret = stats.get('ReturnStats') or {}
    pts = stats.get('PointStats') or {}
    return {
        'duration_minutes':       parse_minutes(stats.get('Time')),
        'serve_rating':           g(svc, 'ServeRating',            'Number'),
        'aces':                   g(svc, 'Aces',                   'Number'),
        'double_faults':          g(svc, 'DoubleFaults',           'Number'),
        'first_serve_pct':        g(svc, 'FirstServe',             'Percent'),
        'first_serve_in':         g(svc, 'FirstServe',             'Dividend'),
        'first_serve_total':      g(svc, 'FirstServe',             'Divisor'),
        'first_serve_won_pct':    g(svc, 'FirstServePointsWon',    'Percent'),
        'first_serve_won':        g(svc, 'FirstServePointsWon',    'Dividend'),
        'first_serve_won_total':  g(svc, 'FirstServePointsWon',    'Divisor'),
        'second_serve_won_pct':   g(svc, 'SecondServePointsWon',   'Percent'),
        'second_serve_won':       g(svc, 'SecondServePointsWon',   'Dividend'),
        'second_serve_won_total': g(svc, 'SecondServePointsWon',   'Divisor'),
        'bp_saved_pct':           g(svc, 'BreakPointsSaved',       'Percent'),
        'bp_saved':               g(svc, 'BreakPointsSaved',       'Dividend'),
        'bp_faced':               g(svc, 'BreakPointsSaved',       'Divisor'),
        'service_games_played':   g(svc, 'ServiceGamesPlayed',     'Number'),
        'return_rating':          g(ret, 'ReturnRating',            'Number'),
        'first_return_won_pct':   g(ret, 'FirstServeReturnPointsWon',  'Percent'),
        'first_return_won':       g(ret, 'FirstServeReturnPointsWon',  'Dividend'),
        'first_return_total':     g(ret, 'FirstServeReturnPointsWon',  'Divisor'),
        'second_return_won_pct':  g(ret, 'SecondServeReturnPointsWon', 'Percent'),
        'second_return_won':      g(ret, 'SecondServeReturnPointsWon', 'Dividend'),
        'second_return_total':    g(ret, 'SecondServeReturnPointsWon', 'Divisor'),
        'bp_converted_pct':       g(ret, 'BreakPointsConverted',   'Percent'),
        'bp_converted':           g(ret, 'BreakPointsConverted',   'Dividend'),
        'bp_opportunities':       g(ret, 'BreakPointsConverted',   'Divisor'),
        'return_games_played':    g(ret, 'ReturnGamesPlayed',      'Number'),
        'total_svc_pts_won_pct':  g(pts, 'TotalServicePointsWon',  'Percent'),
        'total_svc_pts_won':      g(pts, 'TotalServicePointsWon',  'Dividend'),
        'total_svc_pts':          g(pts, 'TotalServicePointsWon',  'Divisor'),
        'total_ret_pts_won_pct':  g(pts, 'TotalReturnPointsWon',   'Percent'),
        'total_ret_pts_won':      g(pts, 'TotalReturnPointsWon',   'Dividend'),
        'total_ret_pts':          g(pts, 'TotalReturnPointsWon',   'Divisor'),
        'total_pts_won_pct':      g(pts, 'TotalPointsWon',         'Percent'),
        'total_pts_won':          g(pts, 'TotalPointsWon',         'Dividend'),
        'total_pts':              g(pts, 'TotalPointsWon',         'Divisor'),
    }


def insert_stat_row(conn, table, fixed_cols, fixed_vals, stat):
    """Insert one row into match_stats or set_stats using shared STAT_COLS."""
    all_cols = fixed_cols + STAT_COLS
    all_vals = fixed_vals + [stat.get(c) for c in STAT_COLS]
    placeholders = ', '.join(['?'] * len(all_cols))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders})",
        all_vals,
    )


# ── CSV loaders ───────────────────────────────────────────────────────────────

def load_players(conn, rankings_path):
    df = pd.read_csv(rankings_path)
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            "INSERT OR IGNORE INTO players (player_id, first_name, last_name, player_url) VALUES (?,?,?,?)",
            (row['player_id'], row['first_name'], row['last_name'], row['player_url']),
        )
        rows += 1
    conn.commit()
    log.info('players        : %d rows loaded', rows)


def load_rankings(conn, rankings_path):
    df = pd.read_csv(rankings_path)
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            "INSERT OR IGNORE INTO player_rankings (player_id, ranking, ranking_points, scraped_date) VALUES (?,?,?,?)",
            (row['player_id'], int(row['ranking']), int(row['ranking_points']), row['scrape_date']),
        )
        rows += 1
    conn.commit()
    log.info('player_rankings: %d rows loaded', rows)


def load_career_stats(conn, stats_path):
    df = pd.read_csv(stats_path)
    # Only load players that exist in the players table
    known = {r[0] for r in conn.execute("SELECT player_id FROM players")}
    rows = skipped = 0
    for _, row in df.iterrows():
        pid = row['player_id']
        if pid not in known:
            skipped += 1
            continue
        conn.execute("""
            INSERT OR IGNORE INTO player_career_stats (
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
        """, (
            pid, row['year'], row['surface'],
            safe(row['Aces']),
            safe(row['DoubleFaults']),
            safe(row['FirstServePercentage']),
            safe(row['FirstServePointsWonPercentage']),
            safe(row['SecondServePointsWonPercentage']),
            safe(row['BreakPointsFaced']),
            safe(row['BreakPointsSavedPercentage']),
            safe(row['ServiceGamesPlayed']),
            safe(row['ServiceGamesWonPercentage']),
            safe(row['ServicePointsWonPercentage']),
            safe(row['FirstServeReturnPointsWonPercentage']),
            safe(row['SecondServeReturnPointsWonPercentage']),
            safe(row['BreakPointsOpportunities']),
            safe(row['BreakPointsConvertedPercentage']),
            safe(row['ReturnGamesPlayed']),
            safe(row['ReturnGamesWonPercentage']),
            safe(row['ReturnPointsWonPercentage']),
            safe(row['TotalPointsWonPercentage']),
        ))
        rows += 1
    conn.commit()
    log.info('player_career_stats: %d rows loaded, %d skipped (player not in rankings)', rows, skipped)


# ── Match processor ───────────────────────────────────────────────────────────

def process_match(conn, data, known_players):
    """
    Parse one Hawkeye JSON response and write to all match tables.
    Returns: 'inserted' | 'skipped_unknown' | 'skipped_doubles'
    """
    m = data['Match']

    if m.get('IsDoubles'):
        return 'skipped_doubles'

    p1_id = ((m.get('PlayerTeam1') or {}).get('PlayerId') or '').lower()
    p2_id = ((m.get('PlayerTeam2') or {}).get('PlayerId') or '').lower()

    if not p1_id or not p2_id:
        return 'skipped_unknown'
    if p1_id not in known_players or p2_id not in known_players:
        return 'skipped_unknown'

    # Tournament
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
        "SELECT id FROM tournaments WHERE event_id=? AND event_year=?",
        (t['EventId'], t['EventYear']),
    ).fetchone()[0]

    # Match
    rnd       = m.get('Round') or {}
    winner_id = (m.get('WinningPlayerId') or '').lower() or None
    conn.execute("""
        INSERT OR IGNORE INTO matches
            (tournament_id, match_code, is_doubles, round_id, round_short, round_long,
             court_name, duration_minutes, match_status, winner_player_id,
             is_qualifier, number_of_sets, date_seq)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        tourney_id, m['MatchId'],
        0,
        rnd.get('RoundId'), rnd.get('ShortName'), rnd.get('LongName'),
        m.get('CourtName'),
        parse_minutes(m.get('MatchTimeTotal')),
        m.get('MatchStatus'), winner_id,
        1 if m.get('IsQualifier') else 0,
        m.get('NumberOfSets'), m.get('DateSeq'),
    ))
    match_id = conn.execute(
        "SELECT id FROM matches WHERE tournament_id=? AND match_code=?",
        (tourney_id, m['MatchId']),
    ).fetchone()[0]

    # match_players
    for team_key, is_p1 in [('PlayerTeam1', 1), ('PlayerTeam2', 0)]:
        team = m.get(team_key) or {}
        pid  = (team.get('PlayerId') or '').lower()
        conn.execute(
            "INSERT OR IGNORE INTO match_players (match_id, player_id, is_player1, seed, entry_status) VALUES (?,?,?,?,?)",
            (match_id, pid, is_p1, team.get('SeedPlayerTeam'), team.get('EntryStatusPlayerTeam')),
        )

    # Build per-player stats map from PlayerTeam / OpponentTeam blocks
    stats_by_pid = {}
    for block in (m.get('PlayerTeam'), m.get('OpponentTeam')):
        if not block:
            continue
        pid = ((block.get('Player') or {}).get('PlayerId') or '').lower()
        if pid:
            stats_by_pid[pid] = {
                'sets': block.get('SetScores') or [],
                'ytd':  block.get('YearToDateStats'),
            }

    for pid, pdata in stats_by_pid.items():
        sets = pdata['sets']

        # match_stats — set 0 only
        for s in sets:
            if s.get('SetNumber') == 0:
                stat = extract_stats(s.get('Stats'))
                if stat:
                    insert_stat_row(conn, 'match_stats', ['match_id', 'player_id'], [match_id, pid], stat)
                break

        # set_stats — sets 1+ where Stats is not null
        for s in sets:
            if s.get('SetNumber', 0) > 0 and s.get('Stats'):
                stat = extract_stats(s['Stats'])
                if stat:
                    insert_stat_row(
                        conn, 'set_stats',
                        ['match_id', 'player_id', 'set_number', 'set_score', 'tiebreak_score'],
                        [match_id, pid, s['SetNumber'], s.get('SetScore'), s.get('TieBreakScore')],
                        stat,
                    )

        # match_ytd_stats
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
                g(svc, 'Aces',                      'Number'),
                g(svc, 'DoubleFaults',               'Number'),
                g(svc, 'BreakPointsFaced',           'Number'),
                g(svc, 'ServiceGamesPlayed',         'Number'),
                g(ret, 'BreakPointOpportunities',    'Number'),
                g(ret, 'ReturnGamesPlayed',          'Number'),
                g(svc, 'FirstServe',                 'Percent'),
                g(svc, 'FirstServePointsWon',        'Percent'),
                g(svc, 'SecondServePointsWon',       'Percent'),
                g(svc, 'BreakPointsSaved',           'Percent'),
                g(svc, 'ServiceGamesWon',            'Percent'),
                g(svc, 'TotalServicePointsWon',      'Percent'),
                g(ret, 'FirstServeReturnPointsWon',  'Percent'),
                g(ret, 'SecondServeReturnPointsWon', 'Percent'),
                g(ret, 'BreakPointsConverted',       'Percent'),
                g(ret, 'ReturnGamesWon',             'Percent'),
                g(ret, 'ReturnPointsWon',            'Percent'),
                g(ret, 'TotalPointsWon',             'Percent'),
            ))

    conn.commit()
    return 'inserted'


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Load staging.db + CSVs into normalized tennis.db')
    parser.add_argument('--db',       default='../data/tennis.db',  help='output SQLite DB (default: ../data/tennis.db)')
    parser.add_argument('--staging',  default='../data/staging.db', help='staging DB from fetch_match_stats.py')
    parser.add_argument('--rankings', default=None,                  help='player_rankings_*.csv (default: most recent in ../data/)')
    parser.add_argument('--stats',    default=None,                  help='player_stats_*.csv (default: most recent in ../data/)')
    args = parser.parse_args()

    # Resolve CSVs
    rankings_path = args.rankings or max(glob.glob('../data/player_rankings_*.csv'))
    stats_path    = args.stats    or max(glob.glob('../data/player_stats_*.csv'))
    print(f'Rankings CSV : {rankings_path}')
    print(f'Stats CSV    : {stats_path}')
    print(f'Staging DB   : {args.staging}')
    print(f'Output DB    : {args.db}')
    print()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()

    # Load CSV data
    load_players(conn, rankings_path)
    load_rankings(conn, rankings_path)
    load_career_stats(conn, stats_path)

    # Load known player set for filtering
    known_players = {r[0] for r in conn.execute("SELECT player_id FROM players")}
    log.info('Known players  : %d', len(known_players))
    print()

    # Process matches from staging DB
    staging = sqlite3.connect(args.staging)
    rows = staging.execute(
        "SELECT year, event_id, match_code, raw_json FROM raw_match_stats WHERE status='ok'"
    ).fetchall()
    staging.close()

    total = len(rows)
    log.info('Matches in staging: %d', total)
    print()

    counters = {'inserted': 0, 'skipped_unknown': 0, 'skipped_doubles': 0, 'error': 0}

    for i, (year, event_id, match_code, raw_json) in enumerate(rows, 1):
        try:
            data   = json.loads(raw_json)
            result = process_match(conn, data, known_players)
            counters[result] += 1
        except Exception as exc:
            counters['error'] += 1
            log.warning('ERR %s/%s/%s: %s', year, event_id, match_code, exc)

        if i % 500 == 0 or i == total:
            log.info('[%d/%d]  inserted=%d  skipped_unknown=%d  skipped_doubles=%d  errors=%d',
                     i, total,
                     counters['inserted'], counters['skipped_unknown'],
                     counters['skipped_doubles'], counters['error'])

    conn.close()
    print()
    print('-' * 55)
    print(f"  Inserted        : {counters['inserted']:,}")
    print(f"  Skipped (player): {counters['skipped_unknown']:,}")
    print(f"  Skipped (doubles): {counters['skipped_doubles']:,}")
    print(f"  Errors          : {counters['error']:,}")
    print('-' * 55)


if __name__ == '__main__':
    main()
