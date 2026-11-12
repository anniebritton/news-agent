"""URL normalisation shared by the scraper, state, and agent."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = ("utm_",)
_TRACKING_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid"}


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
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
