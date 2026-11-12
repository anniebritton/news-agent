"""Fetches web content safely and converts HTML to simplified text/markdown.

Safety properties (see tests/test_fetcher.py):
  * only http/https URLs
  * hosts that resolve to private, loopback, link-local, or otherwise non-public
    addresses are refused, on the first request and on every redirect hop
  * redirects are followed manually (max 5)
  * bodies are streamed and truncated at MAX_BYTES
  * only text-like content types are read
  * a failed fetch returns None (never error text that could end up in a prompt)

Known limitation: the address check resolves DNS separately from the connection, so a
hostile DNS server could still answer differently the second time (DNS rebinding).
"""
import asyncio
import ipaddress
import logging
import re
import socket
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .redact import redact

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
DEADLINE_SECONDS = 60.0   # wall-clock limit for one URL including redirects (httpx timeouts are per read)
HTML_TYPES = ("text/html", "application/xhtml+xml")
ALLOWED_TYPES = HTML_TYPES + ("text/plain", "text/xml", "application/xml",
                              "application/rss+xml", "application/atom+xml")
REDIRECT_CODES = (301, 302, 303, 307, 308)

Resolver = Callable[[str, int], Awaitable[List[str]]]


class BlockedURLError(ValueError):
    """The URL points somewhere the fetcher refuses to go."""


async def default_resolver(host: str, port: int) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [i[4][0].split("%")[0] for i in infos]


# IPv6 transition ranges that can embed (and so reach) private IPv4 addresses.
_EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in
                  ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")]


def ip_is_blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6 and any(ip in net for net in _EXTRA_BLOCKED):
        return True
    return (not ip.is_global) or ip.is_multicast


class NewsFetcher:
    def __init__(self, timeout=30.0, max_bytes=MAX_BYTES, max_concurrency=8, deadline=DEADLINE_SECONDS,
                 resolver: Optional[Resolver] = None, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.deadline = deadline
        self.resolver = resolver or default_resolver
        self._transport = transport
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = None

    def _get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                transport=self._transport,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            raise BlockedURLError(f"scheme not allowed: {parts.scheme or '(none)'}")
        host = parts.hostname
        if not host:
            raise BlockedURLError("URL has no host")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = await self.resolver(host, parts.port or (443 if parts.scheme == "https" else 80))
            except OSError as e:
                raise BlockedURLError(f"cannot resolve {host}: {e}") from e
        if not addresses:
            raise BlockedURLError(f"{host} resolved to nothing")
        for address in addresses:
            if ip_is_blocked(address):
                raise BlockedURLError(f"{host} resolves to a non-public address")

    async def fetch_url(self, url: str) -> Optional[str]:
        """Return page text (HTML converted to markdown-ish text), or None on any failure."""
        async with self._sem:
            logger.info(f"Fetching URL: {url}")
            try:
                return await asyncio.wait_for(self._fetch(url), timeout=self.deadline)
            except asyncio.TimeoutError:
                logger.error(f"Giving up on {url}: exceeded the {self.deadline:.0f}s deadline")
            except BlockedURLError as e:
                logger.warning(f"Refusing to fetch {url}: {e}")
            except Exception as e:
                logger.error(f"Error fetching {url}: {redact(e)}")
            return None

    async def _fetch(self, url: str) -> Optional[str]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self.check_url(current)
            async with self._get_client().stream("GET", current) as response:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect without a Location header")
                    current = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and ctype not in ALLOWED_TYPES:
                    logger.warning(f"Skipping {current}: unsupported content type {ctype}")
                    return None
                body = await self._read_capped(response, current)
                try:
                    text = body.decode(response.encoding or "utf-8", errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                if ctype in HTML_TYPES or (not ctype and "<html" in text[:2000].lower()):
                    return self._clean_html(text, base_url=current)
                return text
        raise ValueError("too many redirects")

    async def _read_capped(self, response, url: str) -> bytes:
        chunks, total = [], 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total >= self.max_bytes:
                chunks.append(chunk[: len(chunk) - (total - self.max_bytes)])
                logger.warning(f"Truncated {url} at {self.max_bytes} bytes")
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _clean_html(self, html: str, base_url: str = "") -> str:
        """Extract text, turning http(s) links into [text](url) markdown."""
        soup = BeautifulSoup(html, "lxml")

        for element in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe",
                             "svg", "noscript", "button", "input", "textarea"]):
            element.decompose()
        for noise in soup.select('[class*="ads"], [class*="popup"], [class*="modal"], [class*="social"], '
                                 '[id*="ads"], [id*="popup"], [id*="modal"]'):
            noise.decompose()

        for a in soup.find_all("a", href=True):
            link_text = re.sub(r"\s+", " ", a.get_text()).strip().replace("[", "(").replace("]", ")")
            href = urljoin(base_url, a["href"].strip()) if base_url else a["href"].strip()
            if not link_text:
                continue
            if urlsplit(href).scheme.lower() in ("http", "https"):
                href = href.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
                a.replace_with(f" [{link_text}]({href}) ")
            else:
                a.replace_with(f" {link_text} ")  # javascript:, mailto:, tel: ... keep the words only

        text = soup.get_text(separator="\n")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        return "\n".join(chunk for chunk in chunks if chunk)
