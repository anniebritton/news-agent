"""
Fetches raw web content and cleans it for processing.
Converts HTML to simplified text/markdown format and handles HTTP requests.
"""
import httpx
from bs4 import BeautifulSoup
import logging

# logging.basicConfig(level=logging.INFO) # Handled by main.py
logger = logging.getLogger(__name__)

class NewsFetcher:
    """
    Fetches a URL and returns clean content.
    Uses connection pooling via a persistent httpx.AsyncClient.
    """
    def __init__(self, timeout=30.0):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def fetch_url(self, url: str) -> str:
        """
        Fetches the content of a URL and returns it as a simplified string (Markdown-like).
        """
        logger.info(f"Fetching URL: {url}")
        try:
            client = self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type:
                return self._clean_html(response.text, base_url=str(response.url))
            else:
                return response.text
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return f"Error: {e}"

    def _clean_html(self, html: str, base_url: str = "") -> str:
        """
        Cleans HTML content to extract text and links in Markdown format.
        """
        from urllib.parse import urljoin
        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, and known non-content elements
        for element in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "svg", "noscript", "button", "input", "textarea"]):
            element.decompose()

        # Target common ad/social/popup classes and IDs if possible
        for noise in soup.select('[class*="ads"], [class*="popup"], [class*="modal"], [class*="social"], [id*="ads"], [id*="popup"], [id*="modal"]'):
            noise.decompose()

        # Convert links to [text](url)
        for a in soup.find_all('a', href=True):
            link_text = a.get_text().strip()
            href = a['href']
            if link_text and href:
                if base_url:
                    href = urljoin(base_url, href)
                a.replace_with(f" [{link_text}]({href}) ")

        # Get text
        text = soup.get_text(separator="\n")

        # Break into lines and remove leading/trailing whitespace
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = "\n".join(chunk for chunk in chunks if chunk)

        return text
