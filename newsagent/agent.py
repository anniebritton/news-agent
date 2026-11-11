"""Orchestrator: scrape -> extract -> dedupe -> format, driven entirely by a config dict."""
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .extractor import Extractor
from .fetcher import NewsFetcher
from .messages import Line
from .llm import LLMProvider, build_provider
from .scraper import NewsScraper
from .state import StateManager

logger = logging.getLogger(__name__)

DEFAULT_ITEM_TEMPLATE = "{group}, {subject}, {summary}"


class NewsAgent:
    def __init__(self, config: dict, llm: Optional[LLMProvider] = None, state_dir: str = "state"):
        self.config = config
        meta = config.get("agent", {})
        self.agent_id = meta.get("id", "unknown")
        self.name = meta.get("name", self.agent_id)

        settings = config.get("settings", {})
        self.default_window_hours = settings.get("time_window_hours", 8.0)
        self.max_summary_length = settings.get("max_summary_length", 180)
        self.item_template = config.get("messages", {}).get("item_template", DEFAULT_ITEM_TEMPLATE)

        self.llm = llm or build_provider(config)
        state_file = os.path.join(state_dir, f"{self.agent_id}_processed_news.json")
        self.fetcher = NewsFetcher()
        self.state = StateManager(state_file, settings.get("max_state_items", 1000))
        self.scraper = NewsScraper(self.fetcher, self.state, config.get("keywords", []))
        self.extractor = Extractor(
            self.llm, self.fetcher, config.get("prompts", {}),
            batch_size=settings.get("batch_size", 4),
            require_subject=settings.get("require_subject", True),
        )
        self.feeds = config.get("feeds", [])

    def _compact(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if len(clean) <= self.max_summary_length:
            return clean
        return clean[:self.max_summary_length - 1].rstrip() + "…"

    @staticmethod
    def _source_label(url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "") or "source"
            parts = [p for p in parsed.path.split("/") if p][:2]
            return f"{domain}/{'/'.join(parts)}" if parts else domain
        except Exception:
            return "source"

    def format_item(self, item: dict) -> Line:
        text = self.item_template.format(
            group=item.get("group") or "N/A",
            subject=item.get("subject") or "N/A",
            title=item.get("title", ""),
            summary=self._compact(item.get("summary", "")),
        )
        text = re.sub(r"\s+", " ", text).strip().rstrip(",")
        url = item.get("source_url", "")
        return Line(text=text, url=url, label=self._source_label(url) if url else "")

    async def run_round(self) -> Tuple[List[Line], float]:
        logger.info(f"Starting round for {self.name}...")
        now = datetime.now()
        window_hours = self.default_window_hours

        if self.state.last_run_at:
            try:
                last = datetime.fromisoformat(self.state.last_run_at)
                window_hours = (now - last).total_seconds() / 3600 + 0.05
            except Exception:
                pass

        if not self.feeds:
            logger.warning("No feeds configured.")
            return [], window_hours

        candidates = await self.scraper.get_new_items(self.feeds)
        if not candidates:
            logger.info("No new matching links found in feeds.")
            self._finish(now)
            return [], window_hours

        items = await self.extractor.extract(candidates)
        summaries = []
        for item in items:
            item_id = item.get("unique_slug")
            if not item_id or self.state.is_processed(item_id):
                continue
            pub = item.get("published_at", "")
            if len(pub) >= 10:
                try:
                    age = (now - datetime.strptime(pub[:16], "%Y-%m-%d %H:%M")).total_seconds() / 3600
                    if age > window_hours:
                        logger.info(f"Skipping old item ({age:.1f}h > {window_hours:.1f}h): {item['title']}")
                        continue
                except Exception:
                    pass
            self.state.mark_processed(item_id)
            summaries.append(self.format_item(item))

        logger.info(f"Generated {len(summaries)} new summaries.")
        self._finish(now)
        return summaries, window_hours

    def _finish(self, now: datetime):
        self.state.last_run_at = now.isoformat()
        self.state.save_state()

    async def close(self):
        await self.fetcher.close()
