
# # # # # # # # #
#               #
#   FUNCTIONS   #
#               #
# # # # # # # # #

from scraping import array2csv
import requests
from lxml import html as lhtml
from datetime import datetime

# Paste your cf_clearance cookie value and matching User-Agent here.
# To refresh: open atptour.com in Chrome, F12 → Application → Cookies → copy cf_clearance.
# The cookie typically lasts 30 minutes to a few hours per session.
CF_CLEARANCE = 'JzbaomCrOTpO3Vz65.m6R2goO70OJMPIMzs0tRgXZpQ-1777891545-1.2.1.1-kXLjIhNulJ55qqUKoFwbxEFx2nQQvTuWhCoHxhGtjiH7gwgBT5kyflNXfaNoGfjIEArosdCxEocaqjelX92w3HQwVJLTVQGO.J0.TWgkIQBWHleAae7mtkDyXb9Dd7urPKoN67pZisNCEEWXKjH.DZjj1xRfiskSBhTwJCRThwg3w3pZI3qSC1D4tu6DkCenRcOyWCQ7Z6A8Eo5fwcZpxkbW8Gzh_sOpHuUEIBx9TvFXzZwbnbaVlbT4R9Q5qAkfST3cTx2y.0l4bboGld0tngBGUpiT7bg2xCwiQqBaxZOpygZLoN485xgZ_OGbp5bBVw5v1mMZCYalrzUj2EJzyg'
USER_AGENT   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': USER_AGENT}


def get_page_tree(url):
    resp = requests.get(url, cookies=COOKIES, headers=HEADERS)
    return lhtml.fromstring(resp.content)


def tournaments(year):
    year_url = "https://www.atptour.com/en/scores/results-archive?year=" + year
    year_tree = get_page_tree(year_url)

    tourney_count = len(year_tree.xpath("//ul[contains(@class,'events')]/li"))

    output = []
    for i in range(tourney_count):
        n = i + 1  # XPath is 1-indexed
        tourney_order = n

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
            else:                   tourney_type = 'undefined'
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

        tourney_year = int(year)
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
            tourney_start_day, tourney_start_month, tourney_start_year = start_date.day, start_date.month, start_date.year
            tourney_end_day,   tourney_end_month,   tourney_end_year   = end_date.day,   end_date.month,   end_date.year
        except Exception:
            tourney_start_day = tourney_start_month = tourney_start_year = ''
            tourney_end_day   = tourney_end_month   = tourney_end_year   = ''

        # Tournament URL, slug, id — new format: /en/tournaments/slug/id/overview
        url_list = year_tree.xpath(
            f"(//ul[contains(@class,'events')]/li)[{n}]//a[contains(@class,'tournament__profile')]/@href"
        )
        if url_list:
            tourney_url_suffix = url_list[0]
            parts = tourney_url_suffix.split('/')
            tourney_slug = parts[3] if len(parts) > 3 else ''
            tourney_id   = parts[4] if len(parts) > 4 else ''
        else:
            tourney_url_suffix = ''
            tourney_slug = ''
            tourney_id = ''

        tourney_surface = ''  # not available on results-archive page

        tourney_year_id = str(year) + '-' + tourney_id
        output.append([
            tourney_year_id, tourney_order, tourney_type,
            tourney_name, tourney_id, tourney_slug,
            tourney_location, tourney_date, year,
            tourney_start_day, tourney_start_month, tourney_start_year,
            tourney_end_day, tourney_end_month, tourney_end_year,
            tourney_surface,
            tourney_url_suffix,
        ])

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

headers = [['tourney_year_id', 'tourney_order', 'tourney_type', 'tourney_name',
            'tourney_id', 'tourney_slug', 'tourney_location', 'tourney_date', 'year',
            'tourney_start_day', 'tourney_start_month', 'tourney_start_year',
            'tourney_end_day', 'tourney_end_month', 'tourney_end_year',
            'tourney_surface', 'tourney_url_suffix']]

tourney_data = []
for h in range(int(start_year), int(end_year) + 1):
    year = str(h)
    tourney_data += tournaments(year)

filename = 'tournaments_' + start_year + '-' + end_year + '.csv'
array2csv(headers + tourney_data, filename)
