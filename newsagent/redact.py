"""Keep secrets out of logs and exception text."""
import os
import re
from urllib.parse import quote, quote_plus
from typing import Iterable

_TELEGRAM_TOKEN = re.compile(r"bot\d+(?::|%3[Aa])[A-Za-z0-9_-]+")
_SECRET_NAME = re.compile(r"(TOKEN|SECRET|API_KEY|PASSWORD)", re.IGNORECASE)


def known_secrets() -> list:
    """Values of environment variables whose names look like credentials."""
    return [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v.strip('"')) >= 8]


def redact(text: str, extra: Iterable[str] = ()) -> str:
    text = str(text)
    for secret in list(extra) + known_secrets():
        secret = (secret or "").strip('"')
        if len(secret) >= 4:
            for form in {secret, quote(secret, safe=""), quote_plus(secret)}:
                text = text.replace(form, "***")
    return _TELEGRAM_TOKEN.sub("bot***", text)
