"""Channel-neutral message model. Notifiers render this into their own wire format."""
from dataclasses import dataclass, field
from typing import List, Optional


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
            suffix = f" [{ln.label}: {ln.url}]" if ln.url else ""
            parts.append(f"• {ln.text}{suffix}" if self.lines else ln.text)
        if self.footer:
            parts.append(self.footer)
        return "\n".join(parts)
