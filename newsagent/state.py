"""Persistence of processed item IDs, seen URLs and failure counts, with atomic writes.

IDs and URLs are kept in insertion order so truncation keeps the *newest* entries.
A state file that exists but cannot be parsed aborts every run (exit 3) until `--reset-state`:
silently starting empty would re-notify everything.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from .errors import StateCorruptError

logger = logging.getLogger(__name__)


def state_path(agent_id: str, state_dir: str = "state") -> str:
    return os.path.join(state_dir, f"{agent_id}_processed_news.json")


def reset_state_file(path: str, now: Optional[datetime] = None) -> Optional[str]:
    """Recovery for a corrupt (or unwanted) state file: move it aside and write empty state with
    last_run_at set to now. Nothing is re-seeded, so the next run treats current links as new;
    run `--seed` afterwards to avoid that. Returns the backup path, or None if there was no file."""
    backup = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                json.load(f)
            kind = "bak"
        except (ValueError, OSError):
            kind = "corrupt"
        backup = f"{path}.{kind}-{int(time.time())}"
        os.replace(path, backup)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fresh = {"ids": [], "urls": [], "fetch_failures": {}, "feed_failures": {}, "last_run_at": (now or datetime.now()).isoformat()}
    with open(path, "w") as f:
        json.dump(fresh, f, indent=2)
    return backup


class StateManager:
    def __init__(self, state_file="processed_news.json", max_items=1000):
        self.state_file = state_file
        self.max_items = max_items
        self._lock = threading.Lock()
        self.processed_ids: dict = {}
        self.seen_urls: dict = {}
        self.fetch_failures: dict = {}
        self.feed_failures: dict = {}   # feed url -> consecutive runs it could not be fetched
        self.last_run_at = None
        self._load_state()

    def _abort(self, reason: str):
        """Leave the file exactly where it is: every run must keep failing until --reset-state is used,
        otherwise the next run would silently start empty and re-announce everything."""
        raise StateCorruptError(
            f"State file {self.state_file} is unreadable ({reason}). Nothing was changed, and every run will "
            "stop here until you recover with --reset-state (it backs the file up and starts from empty state; "
            "then run --seed to avoid re-announcing). The previous version is also in git history.")

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
            feed_failures = data.get("feed_failures", {})
            if not isinstance(feed_failures, dict):
                raise ValueError("feed_failures must be an object")
            self.feed_failures = {k: v for k, v in feed_failures.items() if isinstance(k, str) and isinstance(v, int)}
            last = data.get("last_run_at")
            self.last_run_at = last if isinstance(last, str) else None
        except (ValueError, OSError) as e:  # json.JSONDecodeError is a ValueError
            self._abort(str(e))

    def save_state(self) -> bool:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
                state = {
                    "ids": list(self.processed_ids)[-self.max_items:],
                    "urls": list(self.seen_urls)[-self.max_items:],
                    "fetch_failures": dict(list(self.fetch_failures.items())[-self.max_items:]),
                    "feed_failures": dict(list(self.feed_failures.items())[-self.max_items:]),
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
