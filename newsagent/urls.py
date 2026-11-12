"""URL normalisation shared by the scraper, state, and agent."""
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

_TRACKING = ("utm_",)
_TRACKING_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid"}
# Everything else (including < > " whitespace, control and non-ASCII characters) is percent-encoded.
_URL_SAFE = "/:?#[]@!$&'()*+,;=%-._~"


def encode_url(text: str) -> str:
    """Percent-encode any character that is not a plain URL character. Idempotent (keeps existing %XX)."""
    return quote(text or "", safe=_URL_SAFE)


def canonical_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment, tracking params and trailing slash."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(_TRACKING) and k.lower() not in _TRACKING_EXACT]
    path = encode_url(parts.path.rstrip("/"))
    netloc = encode_url(parts.netloc.lower())
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))
