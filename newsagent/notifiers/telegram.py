"""Telegram channel. Credentials come from env vars whose names are set in config."""
import html
import logging
import os

import httpx

from ..messages import Message
from .base import register

logger = logging.getLogger(__name__)


def render_html(message: Message) -> str:
    parts = []
    if message.header:
        parts.append(f"<b>{html.escape(message.header)}</b>\n")
    for ln in message.lines:
        link = f' <a href="{html.escape(ln.url, quote=True)}">[{html.escape(ln.label or "source")}]</a>' if ln.url else ""
        parts.append(f"• {html.escape(ln.text)}{link}")
    if message.footer:
        parts.append(html.escape(message.footer))
    return "\n".join(parts)


@register("telegram")
class TelegramNotifier:
    def __init__(self, channel: dict):
        token_env = channel.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        chat_env = channel.get("chat_id_env", "TELEGRAM_CHAT_ID")
        self.bot_token = os.getenv(token_env, "").strip('"')
        self.chat_id = os.getenv(chat_env, "").strip('"')
        if not self.bot_token:
            logger.warning(f"Telegram bot token not set (env var: {token_env})")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send(self, message: Message):
        if not self.bot_token:
            logger.warning("Telegram bot token is not configured. Skipping.")
            return None
        payload = {"chat_id": self.chat_id, "text": render_html(message), "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.base_url}/sendMessage", json=payload)
                if response.status_code != 200:
                    logger.error(f"Telegram error: {response.text}")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return None
