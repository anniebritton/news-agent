"""Persistence of processed item IDs, seen URLs and fetch-failure counts, with atomic writes.

IDs and URLs are kept in insertion order so truncation keeps the *newest* entries.
A state file that exists but cannot be parsed is quarantined and the run aborts:
silently starting empty would re-notify everything.
"""
import json
import logging
import os
import threading
import time

from .errors import StateCorruptError

logger = logging.getLogger(__name__)


class StateManager:
    def __init__(self, state_file="processed_news.json", max_items=1000):
        self.state_file = state_file
        self.max_items = max_items
        self._lock = threading.Lock()
        self.processed_ids: dict = {}
        self.seen_urls: dict = {}
        self.fetch_failures: dict = {}
        self.last_run_at = None
        self._load_state()

    def _quarantine(self, reason: str):
        target = f"{self.state_file}.corrupt-{int(time.time())}"
        try:
            os.replace(self.state_file, target)
        except OSError:
            target = self.state_file
        raise StateCorruptError(
            f"State file {self.state_file} is unreadable ({reason}). It was moved to {target}. "
            "Restore it from git history or delete it to start fresh (which re-notifies recent items).")

    @staticmethod
    def _strings(value) -> dict:
        if not isinstance(value, list):
            raise ValueError("expected a list")
        return dict.fromkeys(v for v in value if isinstance(v, str) and v)

    def _load_state(self):
        if not os.path.exists(self.state_file) or os.path.getsize(self.state_file) == 0:
            return
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            if isinstance(data, list):  # legacy format: bare list of IDs
                self.processed_ids = self._strings(data)
                return
            if not isinstance(data, dict):
                raise ValueError("top level must be an object")
            self.processed_ids = self._strings(data.get("ids", []))
            self.seen_urls = self._strings(data.get("urls", []))
            failures = data.get("fetch_failures", {})
            if not isinstance(failures, dict):
                raise ValueError("fetch_failures must be an object")
            self.fetch_failures = {k: v for k, v in failures.items() if isinstance(k, str) and isinstance(v, int)}
            last = data.get("last_run_at")
            self.last_run_at = last if isinstance(last, str) else None
        except (ValueError, OSError) as e:  # json.JSONDecodeError is a ValueError
            self._quarantine(str(e))

    def save_state(self) -> bool:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
                state = {
                    "ids": list(self.processed_ids)[-self.max_items:],
                    "urls": list(self.seen_urls)[-self.max_items:],
                    "fetch_failures": dict(list(self.fetch_failures.items())[-self.max_items:]),
                    "last_run_at": self.last_run_at,
                }
                temp_file = f"{self.state_file}.tmp"
                with open(temp_file, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(temp_file, self.state_file)
                return True
            except Exception as e:
                logger.error(f"Failed to save state to {self.state_file}: {e}")
                return False

    def is_processed(self, item_id: str) -> bool:
        return item_id in self.processed_ids

    def mark_processed(self, item_id: str):
        if item_id:
            self.processed_ids[item_id] = None

    def is_seen_url(self, url: str) -> bool:
        return url in self.seen_urls

    def mark_seen_url(self, url: str):
        if url:
            self.seen_urls[url] = None
