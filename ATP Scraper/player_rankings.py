from scraping import array2csv
import requests
from lxml import html as lhtml
from datetime import datetime

# Paste your cf_clearance cookie value and matching User-Agent here.
# To refresh: open atptour.com in Chrome, F12 → Application → Cookies → copy cf_clearance.
CF_CLEARANCE = 'JzbaomCrOTpO3Vz65.m6R2goO70OJMPIMzs0tRgXZpQ-1777891545-1.2.1.1-kXLjIhNulJ55qqUKoFwbxEFx2nQQvTuWhCoHxhGtjiH7gwgBT5kyflNXfaNoGfjIEArosdCxEocaqjelX92w3HQwVJLTVQGO.J0.TWgkIQBWHleAae7mtkDyXb9Dd7urPKoN67pZisNCEEWXKjH.DZjj1xRfiskSBhTwJCRThwg3w3pZI3qSC1D4tu6DkCenRcOyWCQ7Z6A8Eo5fwcZpxkbW8Gzh_sOpHuUEIBx9TvFXzZwbnbaVlbT4R9Q5qAkfST3cTx2y.0l4bboGld0tngBGUpiT7bg2xCwiQqBaxZOpygZLoN485xgZ_OGbp5bBVw5v1mMZCYalrzUj2EJzyg'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES    = {'cf_clearance': CF_CLEARANCE}
HEADERS    = {'User-Agent': USER_AGENT}
URL_PREFIX = 'https://www.atptour.com'

resp = requests.get('https://www.atptour.com/en/rankings/singles?rankRange=1-500', cookies=COOKIES, headers=HEADERS)
tree = lhtml.fromstring(resp.content)

rows = tree.xpath("//table[contains(@class,'mega-table')]//tr[.//td[contains(@class,'tiny-cell')]]")

scrape_date = datetime.today().strftime('%Y-%m-%d')
data = []

for row in rows:
    rank_list   = row.xpath(".//td[contains(@class,'tiny-cell')]/text()")
    points_list = row.xpath(".//td[contains(@class,'small-cell')]//a/text()")
    href_list   = row.xpath(".//li[@class='name']/a/@href")

    if not rank_list or not href_list:
        continue

    try:
        ranking = int(rank_list[0].strip())
    except ValueError:
        continue

    try:
        ranking_points = int(points_list[0].strip().replace(',', '')) if points_list else None
    except ValueError:
        ranking_points = None

    href       = href_list[0]
    parts      = href.split('/')
    slug       = parts[3] if len(parts) > 3 else ''
    player_id  = parts[4] if len(parts) > 4 else ''
    full_name  = slug.replace('-', ' ').title()
    name_parts = full_name.rsplit(' ', 1)
    first_name = name_parts[0] if len(name_parts) == 2 else ''
    last_name  = name_parts[1] if len(name_parts) == 2 else full_name
    player_url = URL_PREFIX + href

    data.append([ranking, ranking_points, full_name, first_name, last_name, player_url, player_id, scrape_date])

headers = [['ranking', 'ranking_points', 'full_name', 'first_name', 'last_name', 'player_url', 'player_id', 'scrape_date']]
filename = f'player_rankings_{scrape_date}.csv'
array2csv(headers + data, filename)
print(f'Saved {len(data)} players to {filename}')
