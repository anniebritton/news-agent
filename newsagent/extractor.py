"""Uses an LLM to pick relevant articles out of a batch and structure them into items."""
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List

import yaml

from .fetcher import NewsFetcher
from .llm import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_FORMAT = """\
Return ONLY a valid JSON array. No preamble, no explanation, no markdown fences.
If no items qualify, return an empty array: []

Schema:
[
  {
    "index": <int>,                 // original index from the news list
    "translated_title": "<string>", // concise title in English
    "subject": "<string>",          // the named person/product/entity the item is about
    "group": "<string>",            // optional grouping label (team, vendor, category...)
    "summary": "<string>"           // 1-2 sentences explaining why this matters
  }
]
"""

INVALID_SUBJECTS = {"n/a", "na", "unknown", "unspecified", "unnamed", "not provided"}


class Extractor:
    def __init__(self, llm: LLMProvider, fetcher: NewsFetcher, prompts: dict,
                 batch_size: int = 4, require_subject: bool = True, article_chars: int = 1400):
        self.llm = llm
        self.fetcher = fetcher
        self.prompts = prompts
        self.batch_size = batch_size
        self.require_subject = require_subject
        self.article_chars = article_chars

    def _normalize_subject(self, value) -> str:
        if isinstance(value, list):
            value = " / ".join(str(v).strip() for v in value if str(v).strip())
        subject = str(value or "").strip()
        if not subject or subject.lower() in INVALID_SUBJECTS or len(subject) < 3:
            return ""
        return subject

    async def extract(self, news_items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        if not news_items:
            return []
        texts = await asyncio.gather(*(self.fetcher.fetch_url(i.get("url", "")) for i in news_items))
        blocks = []
        for i, (item, raw) in enumerate(zip(news_items, texts)):
            cleaned = re.sub(r"\s+", " ", raw).strip()
            blocks.append(f"[{i}] TITLE: {item.get('title', '')}\nURL: {item.get('url', '')}\n"
                          f"CONTENT: {cleaned[:self.article_chars]}")
        batches = [blocks[i:i + self.batch_size] for i in range(0, len(blocks), self.batch_size)]
        results = await asyncio.gather(*(self._process_batch(b, news_items) for b in batches))
        return [n for r in results for n in r]

    def _build_prompt(self, indexed_text: str, date_str: str) -> str:
        p = self.prompts
        task = p.get("task", "").format(indexed_text=indexed_text, date_str=date_str)
        scope = yaml.dump(p.get("scope", {}), allow_unicode=True)
        return (f"{p.get('system', '')}\n\n{task}\n\nSCOPE:\n{scope}\n\n"
                f"OUTPUT FORMAT:\n{p.get('output_format', DEFAULT_OUTPUT_FORMAT)}")

    async def _process_batch(self, batch_blocks: List[str], original_items: List[Dict]) -> List[Dict]:
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt = self._build_prompt("\n\n".join(batch_blocks), date_str)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, self.llm.generate, prompt)
        if not text:
            return []
        try:
            start, end = text.find("["), text.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            items = []
            for res in json.loads(text[start:end]):
                idx = res.get("index")
                if idx is None or not 0 <= idx < len(original_items):
                    continue
                source = original_items[idx]
                subject = self._normalize_subject(res.get("subject"))
                if self.require_subject and not subject:
                    continue
                title = res.get("translated_title", source.get("title", "Unknown"))
                items.append({
                    "title": title,
                    "subject": subject,
                    "group": res.get("group", ""),
                    "summary": res.get("summary", ""),
                    "source_url": source.get("url", ""),
                    "published_at": date_str,
                    "unique_slug": re.sub(r"[^\w-]", "", title.lower().replace(" ", "-"))[:50],
                })
            return items
        except Exception as e:
            logger.error(f"Failed to parse batch: {e}")
            return []
