"""Persistence of processed item IDs and seen URLs, with atomic writes."""
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)


class StateManager:
    def __init__(self, state_file="processed_news.json", max_items=1000):
        self.state_file = state_file
        self.max_items = max_items
        self._lock = threading.Lock()
        self.processed_ids, self.seen_urls, self.last_run_at = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                if os.path.getsize(self.state_file) == 0:
                    return set(), set(), None
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):  # legacy format: bare list of IDs
                    return set(data), set(), None
                return (
                    set(data.get("ids", [])),
                    set(data.get("urls", [])),
                    data.get("last_run_at"),
                )
            except Exception as e:
                logger.error(f"Error loading state from {self.state_file}: {e}")
        return set(), set(), None

    def save_state(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
                state = {
                    "ids": list(self.processed_ids)[-self.max_items:],
                    "urls": list(self.seen_urls)[-self.max_items:],
                    "last_run_at": self.last_run_at,
                }
                temp_file = f"{self.state_file}.tmp"
                with open(temp_file, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(temp_file, self.state_file)
            except Exception as e:
                logger.error(f"Failed to save state to {self.state_file}: {e}")

    def is_processed(self, item_id: str) -> bool:
        return item_id in self.processed_ids

    def mark_processed(self, item_id: str):
        if item_id:
            self.processed_ids.add(item_id)

    def is_seen_url(self, url: str) -> bool:
        return url in self.seen_urls

    def mark_seen_url(self, url: str):
        if url:
            self.seen_urls.add(url)
