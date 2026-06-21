
# # # # # # # # #
#               #
#   FUNCTIONS   #
#               #
# # # # # # # # #

import sqlite3
import os
from curl_cffi import requests
from lxml import html as lhtml
from datetime import datetime


def get_page_tree(url):
    resp = requests.get(url, impersonate='chrome')
    return lhtml.fromstring(resp.content)


def tournaments(year):
    year_url = "https://www.atptour.com/en/scores/results-archive?year=" + year
    year_tree = get_page_tree(year_url)

    tourney_count = len(year_tree.xpath("//ul[contains(@class,'events')]/li"))

    output = []
    for i in range(tourney_count):
        n = i + 1  # XPath is 1-indexed

        # Tournament type from badge image src
        # Note: parentheses around the XPath select the Nth li globally, not the Nth child within each ul
        badge_src_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//div[contains(@class,'event-badge_container')]//img/@src"
        )
        if badge_src_list:
            src = badge_src_list[0].lower()
            if 'unitedcup'  in src: continue  # skip United Cup
            if 'grandslam'  in src: tourney_type = 'Grand Slam'
            elif 'finals'   in src: tourney_type = 'ATP Finals'
            elif '_1000'    in src: tourney_type = 'Masters 1000'
            elif '_500'     in src: tourney_type = 'ATP 500'
            elif '_250'     in src: tourney_type = 'ATP 250'
            elif 'lvr'      in src: tourney_type = 'Laver Cup'
            elif 'nextgen'  in src: tourney_type = 'Next Gen Finals'
            elif 'atpcup'   in src: tourney_type = 'ATP Cup'
            else:                   continue  # skip Davis Cup, Olympics, ITF events
        else:
            tourney_type = ''

        # Tournament name
        name_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//span[@class='name']/text()"
        )
        tourney_name = name_list[0].strip() if name_list else ''

        # Tournament location — strip trailing " |"
        venue_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//span[@class='venue']/text()"
        )
        tourney_location = venue_list[0].strip().rstrip('|').strip() if venue_list else ''

        # Tournament date — format: "29 December, 2023 - 7 January, 2024"
        date_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//span[@class='Date']/text()"
        )
        tourney_date = date_list[0].strip() if date_list else ''

        try:
            parts = tourney_date.split(' - ', 1)
            start_raw, end_raw = parts[0].strip(), parts[1].strip()
            end_date = datetime.strptime(end_raw, '%d %B, %Y')
            if ',' in start_raw:
                start_date = datetime.strptime(start_raw, '%d %B, %Y')
            else:
                try:
                    start_date = datetime.strptime(start_raw + f', {end_date.year}', '%d %B, %Y')
                except ValueError:
                    start_date = datetime(end_date.year, end_date.month, int(start_raw))
            start_iso = start_date.strftime('%Y-%m-%d')
            end_iso   = end_date.strftime('%Y-%m-%d')
        except Exception:
            start_iso = end_iso = None

        # Tournament URL, slug, id — new format: /en/tournaments/slug/id/overview
        url_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//a[contains(@class,'tournament__profile')]/@href"
        )
        if url_list:
            parts = url_list[0].split('/')
            tourney_id  = parts[4] if len(parts) > 4 else ''
            tourney_url = 'https://www.atptour.com' + url_list[0]
        else:
            tourney_id  = ''
            tourney_url = ''

        tourney_year_id = str(year) + '-' + tourney_id
        output.append((
            tourney_year_id, tourney_type, tourney_name,
            tourney_id, tourney_location,
            start_iso, end_iso, tourney_url,
        ))

    print(year + '    ' + str(tourney_count))
    return output


# # # # # # # # # # #
#                   #
#   MAIN ROUTINE    #
#                   #
# # # # # # # # # # #

start_year = input('Enter start year: ')
end_year = input('Enter end year: ')

print('')
print('Year    Tournaments')
print('----    -----------')

tourney_data = []
for h in range(int(start_year), int(end_year) + 1):
    tourney_data += tournaments(str(h))

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'atp.db')
con = sqlite3.connect(db_path)
cur = con.cursor()

cur.execute('''
    CREATE TABLE IF NOT EXISTS tournaments (
        tourney_year_id  TEXT PRIMARY KEY,
        tourney_type     TEXT,
        tourney_name     TEXT,
        tourney_id       TEXT,
        tourney_location TEXT,
        start_date       TEXT,
        end_date         TEXT,
        tourney_url      TEXT
    )
''')

cur.executemany('''
    INSERT OR REPLACE INTO tournaments
        (tourney_year_id, tourney_type, tourney_name, tourney_id,
         tourney_location, start_date, end_date, tourney_url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
''', tourney_data)

con.commit()
con.close()

print(f'\nWrote {len(tourney_data)} rows to {db_path}')
