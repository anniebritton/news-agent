import time
from unittest.mock import AsyncMock, MagicMock

from newsagent.scraper import LINK_RE, NewsScraper
from newsagent.state import StateManager
from newsagent.urls import canonical_url


def scraper(tmp_path, page, keywords=("alpha",)):
    fetcher = MagicMock()
    fetcher.fetch_url = AsyncMock(return_value=page)
    sm = StateManager(str(tmp_path / "s.json"))
    return NewsScraper(fetcher, sm, list(keywords)), sm


async def test_filters_dedupes_and_does_not_touch_state(tmp_path):
    s, sm = scraper(tmp_path, "[Alpha news](https://a.test/1) [Alpha news](https://a.test/2) "
                              "[Other](https://a.test/3) [Alpha again](https://a.test/1#frag)")
    items = await s.get_new_items(["f"])
    assert [i["url"] for i in items] == ["https://a.test/1"]
    assert not sm.is_seen_url("https://a.test/1")  # the agent marks seen, after processing
    assert [i["url"] for i in await s.get_new_items(["f"])] == ["https://a.test/1"]


async def test_skips_seen_urls(tmp_path):
    s, sm = scraper(tmp_path, "[Alpha](https://a.test/1)")
    sm.mark_seen_url("https://a.test/1")
    assert await s.get_new_items(["f"]) == []


async def test_no_keywords_passes_everything(tmp_path):
    s, _ = scraper(tmp_path, "[One](https://a.test/1) [Two](https://a.test/2)", keywords=())
    assert len(await s.get_new_items(["f"])) == 2


async def test_failed_feed_fetch_is_reported_not_fatal(tmp_path):
    s, _ = scraper(tmp_path, None)
    reports, items = await s.inspect(["f"])
    assert items == [] and reports[0].fetched is False and reports[0].links == 0


async def test_inspect_counts(tmp_path):
    s, _ = scraper(tmp_path, "[Alpha](https://a.test/1) [Nope](https://a.test/2)")
    reports, items = await s.inspect(["f"])
    r = reports[0]
    assert (r.links, r.matched, r.new) == (2, 1, 1)


def test_title_cannot_swallow_earlier_link_text():
    text = "[Trusted title](javascript:void) [Click](https://evil.example/x)"
    assert LINK_RE.findall(text) == [("Click", "https://evil.example/x")]


def test_urls_with_encoded_parens_survive():
    assert LINK_RE.findall("[T](https://a.com/p%281%29)") == [("T", "https://a.com/p%281%29")]


def test_link_regex_is_not_catastrophic_on_hostile_input():
    hostile = "[a](https://b " * 5000
    t = time.perf_counter()
    LINK_RE.findall(hostile)
    LINK_RE.findall("[" * 20000)
    LINK_RE.findall("[a](https://" + "x" * 100000)
    assert time.perf_counter() - t < 1.0


def test_canonical_url():
    assert canonical_url("HTTPS://A.test/p/?utm_source=x&id=2#frag") == "https://a.test/p?id=2"
    assert canonical_url("https://a.test/1/") == canonical_url("https://a.test/1#x")
    assert canonical_url("") == ""
