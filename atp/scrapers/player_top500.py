import sqlite3
import os
from curl_cffi import requests
from lxml import html as lhtml

URL_PREFIX = 'https://www.atptour.com'

resp = requests.get('https://www.atptour.com/en/rankings/singles?rankRange=1-500', impersonate='chrome')
tree = lhtml.fromstring(resp.content)

rows = tree.xpath("//table[contains(@class,'mega-table')]//tr[.//td[contains(@class,'tiny-cell')]]")

data = []

for row in rows:
    rank_list = row.xpath(".//td[contains(@class,'tiny-cell')]/text()")
    href_list = row.xpath(".//li[@class='name']/a/@href")

    if not rank_list or not href_list:
        continue

    try:
        int(rank_list[0].strip())
    except ValueError:
        continue

    href       = href_list[0]
    parts      = href.split('/')
    player_id  = parts[4] if len(parts) > 4 else ''
    slug       = parts[3] if len(parts) > 3 else ''
    full_name  = slug.replace('-', ' ').title()
    name_parts = full_name.rsplit(' ', 1)
    first_name = name_parts[0] if len(name_parts) == 2 else ''
    last_name  = name_parts[1] if len(name_parts) == 2 else full_name
    player_url = URL_PREFIX + href

    data.append((player_id, first_name, last_name, player_url))

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'atp.db')
con = sqlite3.connect(db_path)
cur = con.cursor()

cur.execute('''
    CREATE TABLE IF NOT EXISTS players (
        player_id   TEXT PRIMARY KEY,
        first_name  TEXT,
        last_name   TEXT,
        player_url  TEXT
    )
''')

cur.executemany('''
    INSERT OR REPLACE INTO players (player_id, first_name, last_name, player_url)
    VALUES (?, ?, ?, ?)
''', data)

con.commit()
con.close()

print(f'Wrote {len(data)} players to {db_path}')
