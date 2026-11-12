"""Channel-neutral message model. Notifiers render this into their own wire format."""
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlsplit

SAFE_SCHEMES = ("http", "https")


def safe_url(url: str) -> str:
    """Return the URL only if it uses an allowed scheme; otherwise an empty string."""
    url = (url or "").strip()
    try:
        return url if urlsplit(url).scheme.lower() in SAFE_SCHEMES else ""
    except ValueError:
        return ""


@dataclass
class Line:
    text: str
    url: str = ""
    label: str = ""   # short link label, e.g. "example.com/news"


@dataclass
class Message:
    header: Optional[str] = None   # rendered bold where the channel supports it
    lines: List[Line] = field(default_factory=list)
    footer: Optional[str] = None

    def to_plain(self) -> str:
        parts = []
        if self.header:
            parts.append(self.header + "\n")
        for ln in self.lines:
            url = safe_url(ln.url)
            suffix = f" [{ln.label or 'source'}: {url}]" if url else ""
            parts.append(f"• {ln.text}{suffix}")
        if self.footer:
            parts.append(self.footer)
        return "\n".join(parts)
