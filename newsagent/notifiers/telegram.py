"""Telegram channel. Credentials come from env vars whose names are set in config."""
import html
import logging
import os
from typing import List

import httpx

from ..messages import Line, Message, safe_url
from ..redact import redact
from .base import register

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
CHUNK_LIMIT = 4000  # headroom under Telegram's hard limit


def render_line(ln: Line) -> str:
    url = safe_url(ln.url)
    link = f' <a href="{html.escape(url, quote=True)}">[{html.escape(ln.label or "source")}]</a>' if url else ""
    return f"• {html.escape(ln.text)}{link}"


def _fit(ln: Line, limit: int) -> str:
    """Render a line, shortening its text until it fits within limit."""
    rendered = render_line(ln)
    text = ln.text
    while len(rendered) > limit and len(text) > 20:
        text = text[: int(len(text) * 0.8)].rstrip() + "…"
        rendered = render_line(Line(text, ln.url, ln.label))
    return rendered[:limit]


def render_chunks(message: Message, limit: int = CHUNK_LIMIT) -> List[str]:
    """Split a message into HTML chunks of at most `limit` characters, on line boundaries.
    The header opens the first chunk; the footer closes the last."""
    pieces = []
    if message.header:
        pieces.append(f"<b>{html.escape(message.header)}</b>\n"[:limit])
    pieces.extend(_fit(ln, limit) for ln in message.lines)
    if message.footer:
        pieces.append(html.escape(message.footer)[:limit])

    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > limit:
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
        token_env = channel.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        chat_env = channel.get("chat_id_env", "TELEGRAM_CHAT_ID")
        self.bot_token = os.getenv(token_env, "").strip('"')
        self.chat_id = os.getenv(chat_env, "").strip('"')
        if not self.bot_token:
            logger.warning(f"Telegram bot token not set (env var: {token_env})")
        if not self.chat_id:
            logger.warning(f"Telegram chat id not set (env var: {chat_env})")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

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
