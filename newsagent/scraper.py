"""Scans feed pages, extracts links, and pre-filters them by keyword."""
import asyncio
import logging
import re
from typing import Dict, List

from .config import keyword_list
from .fetcher import NewsFetcher
from .state import StateManager

logger = logging.getLogger(__name__)


class NewsScraper:
    """Finds new, deduplicated links whose titles match any configured keyword.
    An empty keyword list disables filtering (every new link is a candidate)."""

    def __init__(self, fetcher: NewsFetcher, state: StateManager, keywords):
        self.fetcher = fetcher
        self.state = state
        self.keywords = keyword_list(keywords)

    async def get_new_items(self, feeds: List[str]) -> List[Dict[str, str]]:
        unique_links = {}
        seen_titles = set()
        contents = await asyncio.gather(*(self.fetcher.fetch_url(url) for url in feeds))

        for content in contents:
            for title, link in re.findall(r"\[(.*?)\]\((https?://.*?)\)", content):
                link = link.strip().split("#")[0].rstrip("/")
                title = title.strip()
                normalized_title = re.sub(r"[^\w\s]", "", title).lower()

                if (not self.state.is_seen_url(link)
                        and link not in unique_links
                        and normalized_title not in seen_titles
                        and (not self.keywords or any(kw in title.lower() for kw in self.keywords))):
                    unique_links[link] = title
                    seen_titles.add(normalized_title)
                    self.state.mark_seen_url(link)

        return [{"title": t, "url": u} for u, t in unique_links.items()]
