"""Scans feed pages, extracts links, and pre-filters them by keyword.

The scraper only reads state; it never marks anything as seen. The agent does that
after a candidate has actually been processed, so a failed run loses nothing.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .config import keyword_list
from .fetcher import NewsFetcher
from .state import StateManager
from .urls import canonical_url

logger = logging.getLogger(__name__)

# Bounded quantifiers keep matching linear on hostile input.
LINK_RE = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^)\s]{1,2000})\)")


@dataclass
class FeedReport:
    url: str
    fetched: bool
    links: int = 0      # markdown links found on the page
    matched: int = 0    # links whose title matched a keyword
    new: int = 0        # matched links not seen before and not duplicates


class NewsScraper:
    """Finds new, deduplicated links whose titles match any configured keyword.
    An empty keyword list disables filtering (every new link is a candidate)."""

    def __init__(self, fetcher: NewsFetcher, state: StateManager, keywords):
        self.fetcher = fetcher
        self.state = state
        self.keywords = keyword_list(keywords)

    async def inspect(self, feeds: List[str]) -> Tuple[List[FeedReport], List[Dict[str, str]]]:
        contents = await asyncio.gather(*(self.fetcher.fetch_url(url) for url in feeds))
        unique_links: Dict[str, str] = {}
        seen_titles = set()
        reports = []

        for feed, content in zip(feeds, contents):
            report = FeedReport(url=feed, fetched=content is not None)
            reports.append(report)
            for title, link in LINK_RE.findall(content or ""):
                report.links += 1
                link = canonical_url(link)
                title = title.strip()
                if not self.keywords or any(kw in title.lower() for kw in self.keywords):
                    report.matched += 1
                else:
                    continue
                normalized_title = re.sub(r"[^\w\s]", "", title).lower()
                if (link and not self.state.is_seen_url(link)
                        and link not in unique_links
                        and normalized_title not in seen_titles):
                    unique_links[link] = title
                    seen_titles.add(normalized_title)
                    report.new += 1

        return reports, [{"title": t, "url": u} for u, t in unique_links.items()]

    async def get_new_items(self, feeds: List[str]) -> List[Dict[str, str]]:
        _, items = await self.inspect(feeds)
        return items
