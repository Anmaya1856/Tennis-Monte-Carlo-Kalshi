import requests
from lxml import html as lhtml
from datetime import datetime

CF_CLEARANCE = 'JzbaomCrOTpO3Vz65.m6R2goO70OJMPIMzs0tRgXZpQ-1777891545-1.2.1.1-kXLjIhNulJ55qqUKoFwbxEFx2nQQvTuWhCoHxhGtjiH7gwgBT5kyflNXfaNoGfjIEArosdCxEocaqjelX92w3HQwVJLTVQGO.J0.TWgkIQBWHleAae7mtkDyXb9Dd7urPKoN67pZisNCEEWXKjH.DZjj1xRfiskSBhTwJCRThwg3w3pZI3qSC1D4tu6DkCenRcOyWCQ7Z6A8Eo5fwcZpxkbW8Gzh_sOpHuUEIBx9TvFXzZwbnbaVlbT4R9Q5qAkfST3cTx2y.0l4bboGld0tngBGUpiT7bg2xCwiQqBaxZOpygZLoN485xgZ_OGbp5bBVw5v1mMZCYalrzUj2EJzyg'
COOKIES = {'cf_clearance': CF_CLEARANCE}
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'}

url = "https://www.atptour.com/en/scores/results-archive?year=2024"
print("Fetching:", url)
resp = requests.get(url, cookies=COOKIES, headers=HEADERS)
print("Status:", resp.status_code, "| Length:", len(resp.text))

tree = lhtml.fromstring(resp.content)
tourney_count = len(tree.xpath("//ul[contains(@class,'events')]/li"))
print("Tournaments found:", tourney_count)

for n in range(1, 4):
    name  = tree.xpath(f"//ul[contains(@class,'events')]/li[{n}]//span[@class='name']/text()")
    venue = tree.xpath(f"//ul[contains(@class,'events')]/li[{n}]//span[@class='venue']/text()")
    date  = tree.xpath(f"//ul[contains(@class,'events')]/li[{n}]//span[@class='Date']/text()")
    url_s = tree.xpath(f"//ul[contains(@class,'events')]/li[{n}]//a[contains(@class,'tournament__profile')]/@href")
    badge = tree.xpath(f"//ul[contains(@class,'events')]/li[{n}]//div[contains(@class,'event-badge_container')]//img/@src")
    print(f"\n--- [{n}] ---")
    print("name :", name)
    print("venue:", venue)
    print("date :", date)
    print("url  :", url_s)
    print("badge:", badge)

# Try various selectors to see what's there
tests = [
    "//ul[contains(@class,'events')]/li",
    "//ul[@class='events']/li",
    "//*[contains(@class,'events')]",
    "//*[contains(@class,'tournament-info')]",
    "//*[contains(@class,'tourney-result')]",
    "//*[contains(@class,'tourney-title')]",
    "//span[@class='name']",
    "//*[contains(@class,'tournament-list')]",
    "//ul",
    "//li",
]

for xpath in tests:
    result = tree.xpath(xpath)
    print(f"{len(result):4d} matches: {xpath}")

# Print first 3000 chars of body to see structure
body = tree.xpath("//body")[0]
from lxml import etree
body_str = etree.tostring(body, pretty_print=True).decode()[:3000]
print("\n--- BODY EXCERPT ---")
print(body_str)

driver.quit()
