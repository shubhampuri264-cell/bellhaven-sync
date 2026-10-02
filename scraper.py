"""Scrape every community from the Bellhaven website.

Two things the site does that a naive scraper gets wrong:
  1. The directory is paginated, and asking for a page past the end returns the
     last page again. So we follow the "Next" link and stop when it is gone.
  2. Not every community is in the directory. One is only linked from the home
     page. So we collect community links from every page we visit.
"""
import re
from dataclasses import dataclass, asdict, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

COMMUNITY_PATH = re.compile(r"^/communities/([a-z0-9-]+)$")
CITY_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(-\d{4})?$")


class ScrapeError(Exception):
    """The site did not look the way we expect. Better to stop than to guess."""


@dataclass
class Location:
    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: list = field(default_factory=list)
    administrator: str = ""
    phone: str = ""

    def to_dict(self):
        return asdict(self)


def make_session():
    """A requests session that retries on network blips and 5xx responses."""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _get(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _community_slugs(soup):
    """All community slugs linked from one page, in page order."""
    slugs = []
    for link in soup.find_all("a", href=True):
        match = COMMUNITY_PATH.match(urlparse(link["href"]).path)
        if match:
            slugs.append(match.group(1))
    return slugs


def discover_slugs(session, base_url):
    """Walk the home page, the about page, and every directory page.

    Returns (slugs, listed_count). listed_count is the number the directory
    itself claims ("34 communities listed"), used later as a sanity check.
    """
    slugs = []
    for path in ("/", "/about"):
        slugs += _community_slugs(_get(session, base_url + path))

    listed_count = None
    url, visited = base_url + "/communities", set()
    while url and url not in visited:
        visited.add(url)
        soup = _get(session, url)
        slugs += _community_slugs(soup)
        claim = re.search(r"(\d+)\s+communities listed", soup.get_text(" "))
        if claim:
            listed_count = int(claim.group(1))
        next_link = soup.find("a", string=re.compile(r"Next"))
        url = urljoin(url, next_link["href"]) if next_link else None

    return list(dict.fromkeys(slugs)), listed_count  # dedupe, keep order


def parse_detail(html, slug, url):
    """Turn one community page into a Location."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    fields = {dt.get_text(strip=True): dt.find_next_sibling("dd") for dt in soup.select("dl.detail dt")}
    address = fields.get("Address")
    if not heading or address is None:
        raise ScrapeError(f"{url}: missing name or address")

    lines = list(address.stripped_strings)  # street, then "City, ST 12345"
    city_line = CITY_LINE.match(lines[-1]) if len(lines) >= 2 else None
    if not city_line:
        raise ScrapeError(f"{url}: cannot read address {lines!r}")

    def text(label):
        return fields[label].get_text(strip=True) if label in fields else ""

    offerings = fields.get("Care Offerings")
    return Location(
        slug=slug,
        url=url,
        name=heading.get_text(strip=True),
        street=" ".join(lines[:-1]),
        city=city_line["city"],
        state=city_line["state"],
        zip=city_line["zip"],
        care_offerings=[b.get_text(strip=True) for b in offerings.select(".badge")] if offerings else [],
        administrator=text("Administrator"),
        phone=text("Phone"),
    )


def scrape_locations(session=None, base_url=None):
    """Return (locations, listed_count)."""
    session = session or make_session()
    base_url = base_url or config.BASE_URL
    slugs, listed_count = discover_slugs(session, base_url)
    if not slugs:
        raise ScrapeError("no community links found on the website")

    locations = []
    for slug in slugs:
        url = f"{base_url}/communities/{slug}"
        response = session.get(url, timeout=30)
        response.raise_for_status()
        locations.append(parse_detail(response.text, slug, url))

    if listed_count and len(locations) < listed_count:
        raise ScrapeError(f"directory claims {listed_count} communities but only {len(locations)} were scraped")
    return locations, listed_count


if __name__ == "__main__":
    found, listed = scrape_locations()
    print(f"{len(found)} locations scraped (directory lists {listed})")
    for loc in found:
        print(f"  {loc.name} | {loc.street}, {loc.city}, {loc.state} {loc.zip} | {', '.join(loc.care_offerings)}")
