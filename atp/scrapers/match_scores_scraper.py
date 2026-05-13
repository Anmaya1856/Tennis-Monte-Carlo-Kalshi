import csv
import os
import requests
from lxml import html as lhtml

# Paste your cf_clearance cookie value and matching User-Agent here.
# To refresh: open atptour.com in Chrome, F12 → Application → Cookies → copy cf_clearance.
CF_CLEARANCE = 'JzbaomCrOTpO3Vz65.m6R2goO70OJMPIMzs0tRgXZpQ-1777891545-1.2.1.1-kXLjIhNulJ55qqUKoFwbxEFx2nQQvTuWhCoHxhGtjiH7gwgBT5kyflNXfaNoGfjIEArosdCxEocaqjelX92w3HQwVJLTVQGO.J0.TWgkIQBWHleAae7mtkDyXb9Dd7urPKoN67pZisNCEEWXKjH.DZjj1xRfiskSBhTwJCRThwg3w3pZI3qSC1D4tu6DkCenRcOyWCQ7Z6A8Eo5fwcZpxkbW8Gzh_sOpHuUEIBx9TvFXzZwbnbaVlbT4R9Q5qAkfST3cTx2y.0l4bboGld0tngBGUpiT7bg2xCwiQqBaxZOpygZLoN485xgZ_OGbp5bBVw5v1mMZCYalrzUj2EJzyg'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}

OUTPUT_COLS = [
    'tourney_year_id', 'tourney_name', 'tourney_type', 'year',
    'match_header', 'match_link',
    'p1_name', 'p1_id', 'p1_rank',
    'p2_name', 'p2_id', 'p2_rank',
    'winner_name', 'winner_id',
    'p1_score', 'p2_score',
]


def get_page_tree(url):
    resp = requests.get(url, cookies=COOKIES, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    return lhtml.fromstring(resp.content)


def extract_player(stats_item):
    name_nodes = stats_item.xpath('.//div[contains(@class,"name")]/a/text()')
    name = name_nodes[0].strip() if name_nodes else ''

    href_nodes = stats_item.xpath('.//div[contains(@class,"name")]/a/@href')
    player_id = ''
    if href_nodes:
        parts = href_nodes[0].strip('/').split('/')
        # href pattern: en/players/{slug}/{id}/overview
        player_id = parts[3] if len(parts) > 3 else ''

    rank_nodes = stats_item.xpath('.//div[contains(@class,"name")]/span/text()')
    rank = rank_nodes[0].strip().strip('()') if rank_nodes else ''

    is_winner = bool(stats_item.xpath('.//div[contains(@class,"winner")]'))

    # Take only the first span per score-item — the second span (if present) is
    # the tiebreak superscript, which we skip to keep set counts equal for both players.
    # Use hyphen separation so leading zeros (e.g. a "0" set) are never dropped.
    score_parts = []
    for score_item in stats_item.xpath('.//div[contains(@class,"score-item")]'):
        first_span = score_item.xpath('span[1]/text()')
        if first_span:
            score_parts.append(first_span[0].strip())
    score = ';'.join(score_parts)

    return name, player_id, rank, is_winner, score


def scrape_tournament(tourney_year_id, tourney_name, tourney_type, year, tourney_slug, tourney_id):
    url = f'https://www.atptour.com/en/scores/archive/{tourney_slug}/{tourney_id}/{year}/results'
    try:
        tree = get_page_tree(url)
    except Exception as e:
        print(f'  WARNING: could not fetch {url} — {e}')
        return []

    # Match the CSS class "match" exactly (not "match-header", "match-content", etc.)
    match_divs = tree.xpath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' match ')]"
    )

    rows = []
    for match_div in match_divs:
        header_nodes = match_div.xpath('.//div[contains(@class,"match-header")]//strong/text()')
        match_header = header_nodes[0].strip() if header_nodes else ''

        link_nodes = match_div.xpath('.//div[contains(@class,"match-cta")]/a[normalize-space(text())="Stats"]/@href')
        match_link = ('https://www.atptour.com' + link_nodes[0]) if link_nodes else ''

        stats_items = match_div.xpath('.//div[contains(@class,"stats-item")]')
        if len(stats_items) < 2:
            continue

        p1_name, p1_id, p1_rank, p1_winner, p1_score = extract_player(stats_items[0])
        p2_name, p2_id, p2_rank, p2_winner, p2_score = extract_player(stats_items[1])

        if not p1_name or not p2_name:
            continue

        if p1_winner:
            winner_name, winner_id = p1_name, p1_id
        elif p2_winner:
            winner_name, winner_id = p2_name, p2_id
        else:
            winner_name, winner_id = '', ''

        rows.append([
            tourney_year_id, tourney_name, tourney_type, year,
            match_header, match_link,
            p1_name, p1_id, p1_rank,
            p2_name, p2_id, p2_rank,
            winner_name, winner_id,
            p1_score, p2_score,
        ])

    return rows


def load_done_ids(output_file):
    done = set()
    if not os.path.exists(output_file):
        return done
    with open(output_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            done.add(row.get('tourney_year_id', ''))
    return done


def append_rows(rows, output_file):
    with open(output_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def main():
    tournaments_file = input('Tournaments CSV path [../data/tournaments_2023-2026.csv]: ').strip()
    if not tournaments_file:
        tournaments_file = '../data/tournaments_2023-2026.csv'

    output_file = input('Output CSV path [../data/match_scores_2023-2026.csv]: ').strip()
    if not output_file:
        output_file = '../data/match_scores_2023-2026.csv'

    # Write header if output file doesn't exist yet
    if not os.path.exists(output_file):
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(OUTPUT_COLS)

    done_ids = load_done_ids(output_file)
    print(f'\nAlready scraped: {len(done_ids)} tournament(s)')

    with open(tournaments_file, newline='', encoding='utf-8') as f:
        tournaments = list(csv.DictReader(f))

    total = len(tournaments)
    print(f'Total tournaments to process: {total}')
    print('')
    print('tourney_year_id                   matches')
    print('---------------                   -------')

    for tourney in tournaments:
        tid = tourney.get('tourney_year_id', '')
        if tid in done_ids:
            continue

        name      = tourney.get('tourney_name', '')
        ttype     = tourney.get('tourney_type', '')
        year      = tourney.get('year', '')
        slug      = tourney.get('tourney_slug', '')
        tourney_id = tourney.get('tourney_id', '')

        if not slug or not tourney_id or not year:
            print(f'{tid:<35} SKIP (missing slug/id/year)')
            continue

        rows = scrape_tournament(tid, name, ttype, year, slug, tourney_id)
        append_rows(rows, output_file)
        print(f'{tid:<35} {len(rows)}')

    print('\nDone.')


if __name__ == '__main__':
    main()
