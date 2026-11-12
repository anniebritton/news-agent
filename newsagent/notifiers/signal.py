"""Signal channel via a self-hosted signal-cli-rest-api server
(https://github.com/bbernhard/signal-cli-rest-api). Signal has no official bot API."""
import logging
import os

import httpx

from ..messages import Message
from ..redact import redact
from .base import register

logger = logging.getLogger(__name__)


@register("signal")
class SignalNotifier:
    def __init__(self, channel: dict):
        self._envs = (channel.get("api_url_env", "SIGNAL_API_URL"), channel.get("sender_env", "SIGNAL_SENDER"),
                      channel.get("recipients_env", "SIGNAL_RECIPIENTS"))
        self.api_url = os.getenv(self._envs[0], "").strip('"').rstrip("/")
        self.sender = os.getenv(self._envs[1], "").strip('"')
        recipients = os.getenv(self._envs[2], "").strip('"')
        self.recipients = [r.strip() for r in recipients.split(",") if r.strip()]  # numbers or group IDs
        if not (self.api_url and self.sender and self.recipients):
            logger.warning("Signal channel is not fully configured (api url / sender / recipients)")

    def missing(self) -> list:
        values = (self.api_url, self.sender, self.recipients)
        return [f"{name} (Signal)" for name, value in zip(self._envs, values) if not value]

    async def send(self, message: Message) -> bool:
        if not (self.api_url and self.sender and self.recipients):
            logger.warning("Signal is not configured. Skipping.")
            return False
        payload = {"message": message.to_plain(), "number": self.sender, "recipients": self.recipients}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.api_url}/v2/send", json=payload)
                if response.status_code >= 300:
                    logger.error(f"Signal error {response.status_code}")
                    return False
                return True
        except Exception as e:
            logger.error(f"Failed to send Signal message: {redact(e, [self.api_url])}")
            return False
