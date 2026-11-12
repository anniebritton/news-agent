"""Keep secrets out of logs and exception text."""
import os
import re
from typing import Iterable

_TELEGRAM_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_SECRET_NAME = re.compile(r"(TOKEN|SECRET|API_KEY|PASSWORD)", re.IGNORECASE)


def known_secrets() -> list:
    """Values of environment variables whose names look like credentials."""
    return [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v.strip('"')) >= 8]


def redact(text: str, extra: Iterable[str] = ()) -> str:
    text = str(text)
    for secret in list(extra) + known_secrets():
        secret = (secret or "").strip('"')
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    return _TELEGRAM_TOKEN.sub("bot***", text)
