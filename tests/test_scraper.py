import pytest

from scraper import ScrapeError, discover_slugs, parse_detail

DETAIL = """
<h1>Bellhaven Meadows of Findlay</h1>
<dl class="detail">
  <dt>Address</dt><dd>1800 N Blanchard St<br>Findlay, OH 45840</dd>
  <dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span><span class="badge">Memory Support</span></dd>
  <dt>Administrator</dt><dd>Sam Pruitt</dd>
  <dt>Phone</dt><dd>(231) 533-2969</dd>
</dl>"""


def test_parse_detail_reads_every_field():
    loc = parse_detail(DETAIL, "findlay", "https://example.test/communities/findlay")
    assert (loc.name, loc.street, loc.city, loc.state, loc.zip) == (
        "Bellhaven Meadows of Findlay", "1800 N Blanchard St", "Findlay", "OH", "45840")
    assert loc.care_offerings == ["Assisted Living", "Memory Support"]
    assert loc.administrator == "Sam Pruitt"


def test_parse_detail_refuses_a_page_without_an_address():
    with pytest.raises(ScrapeError):
        parse_detail("<h1>Somewhere</h1>", "x", "https://example.test/communities/x")


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSite:
    """Two directory pages. Like the real site, a page past the end repeats the last page."""
    PAGES = {
        "/": '<a href="/communities/hidden-one">New this year</a>',
        "/about": "<p>About us</p>",
        "/communities": '<p>3 communities listed</p><a href="/communities/a">A</a><a href="/communities?page=2">Next &rarr;</a>',
        "/communities?page=2": '<p>3 communities listed</p><a href="/communities/b">B</a><a href="/communities/a">A</a>',
    }

    def __init__(self):
        self.requested = []

    def get(self, url, timeout=None):
        path = url.replace("https://example.test", "")
        self.requested.append(path)
        return FakeResponse(self.PAGES.get(path, self.PAGES["/communities?page=2"]))


def test_discover_follows_next_links_and_finds_pages_outside_the_directory():
    site = FakeSite()
    slugs, listed = discover_slugs(site, "https://example.test")
    assert slugs == ["hidden-one", "a", "b"]  # home page link found, duplicate "a" dropped
    assert listed == 3
    assert site.requested.count("/communities?page=2") == 1  # stopped when Next disappeared
