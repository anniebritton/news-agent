"""Orchestrator: scrape -> extract -> dedupe -> format, driven entirely by a config dict.

State is never written during run_round(). The caller delivers the message and then calls
commit(); if delivery fails nothing is consumed and the next run retries the same items.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from .config import check_templates
from .errors import ConfigError, FeedsUnreachable
from .extractor import Extractor, _to_str
from .fetcher import NewsFetcher
from .llm import LLMProvider, build_provider
from .messages import Line
from .scraper import FeedReport, NewsScraper
from .state import StateManager, state_path
from .urls import canonical_url

logger = logging.getLogger(__name__)

DEFAULT_ITEM_TEMPLATE = "{group}, {subject}, {summary}"
MAX_FETCH_FAILURES = 3   # after this many failed fetches a link is given up on


@dataclass
class RoundResult:
    lines: List[Line] = field(default_factory=list)
    window_hours: float = 0.0
    total_batches: int = 0
    failed_batches: int = 0
    fetch_failures: int = 0
    deferred: int = 0       # relevant items held back by max_items_per_message
    feeds_total: int = 0
    feeds_failed: int = 0   # feeds that could not be fetched at all

    @property
    def all_failed(self) -> bool:
        return self.total_batches > 0 and self.failed_batches == self.total_batches

    @property
    def all_feeds_failed(self) -> bool:
        """Every feed was unreachable: an outage, not "no news"."""
        return self.feeds_total > 0 and self.feeds_failed == self.feeds_total


class _LazyLLM:
    """Defers building the provider so --check and --seed work without an API key."""
    def __init__(self, agent: "NewsAgent"):
        self._agent = agent

    def generate(self, prompt: str) -> Optional[str]:
        return self._agent.ensure_llm().generate(prompt)


class NewsAgent:
    def __init__(self, config: dict, llm: Optional[LLMProvider] = None, state_dir: str = "state"):
        check_templates(config)
        self.config = config
        meta = config.get("agent") or {}
        self.agent_id = meta.get("id", "unknown")
        self.name = meta.get("name", self.agent_id)
        self.description = meta.get("description", "")

        settings = config.get("settings") or {}
        self.default_window_hours = settings.get("time_window_hours", 8.0)
        self.max_summary_length = settings.get("max_summary_length", 180)
        self.max_items_per_message = settings.get("max_items_per_message", 40)
        self.item_template = (config.get("messages") or {}).get("item_template", DEFAULT_ITEM_TEMPLATE)

        self._llm = llm
        state_file = state_path(self.agent_id, state_dir)
        self.fetcher = NewsFetcher()
        self.state = StateManager(state_file, settings.get("max_state_items", 1000))
        self.scraper = NewsScraper(self.fetcher, self.state, config.get("keywords", []))
        self.extractor = Extractor(
            _LazyLLM(self), self.fetcher, config.get("prompts") or {},
            batch_size=settings.get("batch_size", 4),
            require_subject=settings.get("require_subject", True),
        )
        self.feeds = config.get("feeds") or []
        self.last_feed_reports: List[FeedReport] = []
        self._reset_pending()

    # -- helpers ---------------------------------------------------------

    def ensure_llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = build_provider(self.config)
        return self._llm

    def _reset_pending(self):
        self._pending_seen: Dict[str, None] = {}
        self._pending_processed: Dict[str, None] = {}
        self._pending_failures: Dict[str, int] = {}
        self._pending_clock: Optional[datetime] = None

    def _compact(self, text) -> str:
        clean = re.sub(r"\s+", " ", _to_str(text))
        if len(clean) <= self.max_summary_length:
            return clean
        return clean[:self.max_summary_length - 1].rstrip() + "…"

    @staticmethod
    def _source_label(url: str) -> str:
        try:
            parsed = urlsplit(url)
            domain = (parsed.hostname or "source").replace("www.", "")
            parts = [p for p in parsed.path.split("/") if p][:2]
            return f"{domain}/{'/'.join(parts)}" if parts else domain
        except ValueError:
            return "source"

    @staticmethod
    def item_id(item: dict) -> str:
        """Stable dedupe key: the canonical source URL, else the title slug. '' if neither is usable."""
        url = canonical_url(_to_str(item.get("source_url")))
        if url:
            return "url:" + url
        slug = _to_str(item.get("unique_slug"))
        return "slug:" + slug if slug.strip("-") else ""

    def format_item(self, item: dict) -> Line:
        text = self.item_template.format(
            group=_to_str(item.get("group")) or "N/A",
            subject=_to_str(item.get("subject")) or "N/A",
            title=_to_str(item.get("title")),
            summary=self._compact(item.get("summary")),
        )
        text = re.sub(r"\s+", " ", text).strip().rstrip(",")
        url = _to_str(item.get("source_url"))
        return Line(text=text, url=url, label=self._source_label(url) if url else "")

    def _window_hours(self, now: datetime) -> float:
        """Hours since the last run (+ a small margin). Falls back to the configured default
        when there was no previous run or its timestamp is unusable (garbage or in the future)."""
        last = self.state.last_run_at
        if last:
            try:
                hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
                if hours >= 0:
                    return hours + 0.05
            except (ValueError, TypeError):
                pass
        return float(self.default_window_hours)

    # -- the round -------------------------------------------------------

    async def check(self, limit: Optional[int] = None):
        """Scrape only. No LLM, no key, no state writes. Returns (feed reports, candidates)."""
        reports, candidates = await self.scraper.inspect(self.feeds)
        self.last_feed_reports = reports
        return reports, (candidates[:limit] if limit else candidates)

    async def seed(self, limit: Optional[int] = None) -> int:
        """Record all current candidates as seen without notifying, so the first real run
        only reports what appears afterwards."""
        reports, candidates = await self.check(limit)
        if reports and not any(r.fetched for r in reports):
            raise FeedsUnreachable("Every feed was unreachable, so there is nothing to seed. Nothing was saved.")
        self._reset_pending()
        for c in candidates:
            self._pending_seen[c["url"]] = None
        self._pending_clock = datetime.now()
        self.commit()
        return len(candidates)

    async def run_round(self, limit: Optional[int] = None) -> RoundResult:
        if self.description:
            logger.info(f"{self.name}: {self.description}")
        logger.info(f"Starting round for {self.name}...")
        self._reset_pending()
        now = datetime.now()
        window = self._window_hours(now)
        result = RoundResult(window_hours=window)

        if not self.feeds:
            logger.warning("No feeds configured.")
            return result

        reports, candidates = await self.check(limit)
        result.feeds_total = len(reports)
        result.feeds_failed = sum(1 for r in reports if not r.fetched)
        if result.feeds_failed:
            logger.warning(f"{result.feeds_failed} of {result.feeds_total} feeds could not be fetched: "
                           + ", ".join(r.url for r in reports if not r.fetched))
        if result.all_feeds_failed:
            return result  # an outage, not "no news": don't advance the clock or claim all is quiet
        if not candidates:
            logger.info("No new matching links found in feeds.")
            if not result.feeds_failed:
                self._pending_clock = now
            return result

        extraction = await self.extractor.extract(candidates)
        result.total_batches = extraction.total_batches
        result.failed_batches = extraction.failed_batches
        result.fetch_failures = len(extraction.fetch_failed_urls)

        for url in extraction.done_urls:
            self._pending_seen[url] = None
        for url in extraction.fetch_failed_urls:
            self._pending_failures[url] = self.state.fetch_failures.get(url, 0) + 1

        for item in extraction.items:
            item_id = self.item_id(item)
            if not item_id or self.state.is_processed(item_id) or item_id in self._pending_processed:
                continue
            published = item.get("published")
            if isinstance(published, datetime):
                age = (now - published).total_seconds() / 3600
                if age > window:
                    logger.info(f"Skipping old item ({age:.1f}h > {window:.1f}h window): {item['title']}")
                    continue
            if len(result.lines) >= self.max_items_per_message:
                # Hold it back: leave its URL unseen so it surfaces on the next run.
                self._pending_seen.pop(item.get("source_url", ""), None)
                result.deferred += 1
                continue
            self._pending_processed[item_id] = None
            result.lines.append(self.format_item(item))

        # Advance the clock only if everything was handled; otherwise retried items could
        # look "too old" next time.
        if not (result.failed_batches or result.fetch_failures or result.feeds_failed):
            self._pending_clock = now
        logger.info(f"Generated {len(result.lines)} new summaries "
                    f"({result.failed_batches}/{result.total_batches} batches failed, "
                    f"{result.fetch_failures} fetches failed, {result.deferred} deferred).")
        return result

    def commit(self) -> bool:
        """Persist everything the last round consumed. Call only after delivery succeeded."""
        for url in self._pending_seen:
            self.state.mark_seen_url(url)
            self.state.fetch_failures.pop(url, None)
        for item_id in self._pending_processed:
            self.state.mark_processed(item_id)
        for url, count in self._pending_failures.items():
            if count >= MAX_FETCH_FAILURES:
                logger.warning(f"Giving up on {url} after {count} failed fetches")
                self.state.mark_seen_url(url)
                self.state.fetch_failures.pop(url, None)
            else:
                self.state.fetch_failures[url] = count
        if self._pending_clock:
            self.state.last_run_at = self._pending_clock.isoformat()
        ok = self.state.save_state()
        self._reset_pending()
        return ok

    async def close(self):
        await self.fetcher.close()
