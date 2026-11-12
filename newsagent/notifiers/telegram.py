"""Telegram channel. Credentials come from env vars whose names are set in config.

Rendering rule: text is truncated BEFORE it is HTML-escaped and wrapped, and rendered HTML is
never sliced, so a message can't end up with a split entity or an unclosed tag (Telegram answers
those with 400, which would otherwise be retried forever).
"""
import html
import logging
import os
from typing import Callable, List

import httpx

from ..messages import Line, Message, safe_url
from ..redact import redact
from .base import register

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
CHUNK_LIMIT = 4000  # headroom under Telegram's hard limit


def units(text: str) -> int:
    """Length in UTF-16 code units (conservative: counts the escaped HTML, not the parsed text)."""
    return len(text.encode("utf-16-le")) // 2


def render_line(text: str, url: str = "", label: str = "") -> str:
    link = f' <a href="{html.escape(url, quote=True)}">[{html.escape(label or "source")}]</a>' if url else ""
    return f"• {html.escape(text)}{link}"


def _shrink(text: str) -> str:
    """Strictly shorter text, ending in an ellipsis once it is long enough to matter."""
    return text[: int(len(text) * 0.8)].rstrip() + "…" if len(text) > 20 else text[:-1]


def _fit_text(raw: str, wrap: Callable[[str], str], limit: int) -> str:
    """Wrap(raw) with raw shortened (pre-escape) until the result fits."""
    text = raw
    while True:
        rendered = wrap(text)
        if units(rendered) <= limit or not text:
            return rendered if units(rendered) <= limit else ""
        text = _shrink(text)


def _fit(ln: Line, limit: int) -> str:
    """Render a line within limit: shorten the text first, then drop the link if it still doesn't fit."""
    url, label = safe_url(ln.url), (ln.label or "source")[:80]
    attempts = [(url, 40), ("", 1)] if url else [("", 1)]   # (link, shortest text worth keeping)
    for link, min_len in attempts:
        text = ln.text or ""
        while True:
            rendered = render_line(text, link, label)
            if units(rendered) <= limit:
                return rendered
            if len(text) <= min_len:
                break
            text = _shrink(text)
    return "•"


def render_chunks(message: Message, limit: int = CHUNK_LIMIT) -> List[str]:
    """Split a message into HTML chunks of at most `limit` UTF-16 units, on line boundaries.
    The header opens the first chunk; the footer closes the last."""
    pieces = []
    if message.header:
        pieces.append(_fit_text(message.header, lambda t: f"<b>{html.escape(t)}</b>\n", limit))
    pieces.extend(_fit(ln, limit) for ln in message.lines)
    if message.footer:
        pieces.append(_fit_text(message.footer, html.escape, limit))

    chunks, current = [], ""
    for piece in filter(None, pieces):
        if current and units(current) + 1 + units(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


@register("telegram")
class TelegramNotifier:
    def __init__(self, channel: dict):
        self._token_env = channel.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        self._chat_env = channel.get("chat_id_env", "TELEGRAM_CHAT_ID")
        self.bot_token = os.getenv(self._token_env, "").strip('"')
        self.chat_id = os.getenv(self._chat_env, "").strip('"')
        if not self.bot_token:
            logger.warning(f"Telegram bot token not set (env var: {self._token_env})")
        if not self.chat_id:
            logger.warning(f"Telegram chat id not set (env var: {self._chat_env})")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def missing(self) -> list:
        return ([f"{self._token_env} (Telegram bot token)"] if not self.bot_token else []) + \
               ([f"{self._chat_env} (Telegram chat id)"] if not self.chat_id else [])

    async def send(self, message: Message) -> bool:
        if not (self.bot_token and self.chat_id):
            logger.warning("Telegram is not fully configured. Skipping.")
            return False
        try:
            async with httpx.AsyncClient() as client:
                for chunk in render_chunks(message):
                    payload = {"chat_id": self.chat_id, "text": chunk, "parse_mode": "HTML",
                               "link_preview_options": {"is_disabled": True}}
                    response = await client.post(f"{self.base_url}/sendMessage", json=payload)
                    if response.status_code != 200:
                        logger.error(f"Telegram error {response.status_code}: "
                                     f"{redact(response.text, [self.bot_token])[:200]}")
                        return False
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {redact(e, [self.bot_token])}")
            return False
