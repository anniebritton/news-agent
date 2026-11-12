import asyncio
import socket
import time

import httpx
import pytest

from newsagent.fetcher import NewsFetcher, ip_is_blocked

HOSTS = {"a.test": ["93.184.216.34"], "internal.test": ["10.0.0.5"], "meta.test": ["169.254.169.254"],
         "loop.test": ["127.0.0.1"], "mixed.test": ["93.184.216.34", "192.168.1.9"]}


async def resolver(host, port):
    if host not in HOSTS:
        raise socket.gaierror("no such host")
    return HOSTS[host]


def fetcher(handler, **kw):
    requests = []

    def wrapped(request):
        requests.append(str(request.url))
        return handler(request)
    return NewsFetcher(resolver=resolver, transport=httpx.MockTransport(wrapped), **kw), requests


def html(body):
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body.encode())


async def test_html_is_converted_to_markdown_links():
    f, _ = fetcher(lambda r: html('<html><body><p>Hi <a href="/news/1">First story</a> and '
                                  '<a href="https://b.test/x">Second</a></p></body></html>'))
    text = await f.fetch_url("https://a.test/index")
    assert "[First story](https://a.test/news/1)" in text
    assert "[Second](https://b.test/x)" in text


async def test_parentheses_and_brackets_in_links_are_made_safe():
    f, _ = fetcher(lambda r: html('<html><body><a href="https://b.test/p(1)">Title [draft]</a></body></html>'))
    text = await f.fetch_url("https://a.test/")
    assert "[Title (draft)](https://b.test/p%281%29)" in text


async def test_non_http_links_keep_words_but_no_markdown_link():
    f, _ = fetcher(lambda r: html('<html><body><a href="javascript:alert(1)">Click me</a></body></html>'))
    text = await f.fetch_url("https://a.test/")
    assert "Click me" in text and "javascript" not in text


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data",
    "http://10.1.2.3/", "http://192.168.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://100.64.0.1/", "http://0.0.0.0/", "http://internal.test/", "http://meta.test/",
    "http://loop.test/", "http://mixed.test/", "file:///etc/passwd", "ftp://a.test/x", "gopher://a.test/",
    "http:///nohost",
])
async def test_blocked_targets_are_never_requested(url):
    f, requests = fetcher(lambda r: html("<html>secret</html>"))
    assert await f.fetch_url(url) is None
    assert requests == []


async def test_redirect_to_internal_address_is_blocked():
    def handler(request):
        if request.url.host == "a.test":
            return httpx.Response(302, headers={"location": "http://meta.test/latest/meta-data"})
        return html("<html>INTERNAL-ONLY-DATA</html>")
    f, requests = fetcher(handler)
    assert await f.fetch_url("https://a.test/redir") is None
    assert not any("meta.test" in r for r in requests)


async def test_redirect_to_public_host_is_followed():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return html("<html><body>final</body></html>")
    f, _ = fetcher(handler)
    assert "final" in await f.fetch_url("https://a.test/old")


async def test_redirect_loop_gives_up():
    f, requests = fetcher(lambda r: httpx.Response(302, headers={"location": "/again"}))
    assert await f.fetch_url("https://a.test/x") is None
    assert len(requests) <= 6


async def test_response_is_streamed_and_capped():
    yielded = []

    async def body():
        for i in range(100):
            yielded.append(i)
            yield b"x" * 65536

    f, _ = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=body()),
                   max_bytes=200_000)
    text = await f.fetch_url("https://a.test/big")
    assert len(text) == 200_000
    assert len(yielded) <= 5  # stopped reading long before 6.5 MB


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream", "video/mp4"])
async def test_non_text_content_is_skipped(ctype):
    f, _ = fetcher(lambda r: httpx.Response(200, headers={"content-type": ctype}, content=b"bytes"))
    assert await f.fetch_url("https://a.test/file") is None


async def test_plain_text_and_xml_are_returned_raw():
    f, _ = fetcher(lambda r: httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=b"<rss/>"))
    assert await f.fetch_url("https://a.test/feed") == "<rss/>"


@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_http_errors_return_none_not_error_text(status):
    f, _ = fetcher(lambda r: httpx.Response(status, content=b"nope"))
    assert await f.fetch_url("https://a.test/x") is None


async def test_unresolvable_host_returns_none():
    f, requests = fetcher(lambda r: html("x"))
    assert await f.fetch_url("https://unknown.test/") is None and requests == []


def test_ip_is_blocked_table():
    for blocked in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1",
                    "fe80::1", "fc00::1", "::ffff:10.0.0.1", "100.64.0.1", "224.0.0.1", "0.0.0.0",
                    "64:ff9b::7f00:1", "64:ff9b::a00:1", "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2"):
        assert ip_is_blocked(blocked), blocked
    for allowed in ("93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"):
        assert not ip_is_blocked(allowed), allowed


async def test_a_stalled_server_is_cut_off_by_the_wall_clock_deadline():
    async def stall(request):
        await asyncio.sleep(10)
        return html("<html>late</html>")
    f = NewsFetcher(resolver=resolver, transport=httpx.MockTransport(stall), deadline=0.2)
    start = time.perf_counter()
    assert await f.fetch_url("https://a.test/slow") is None
    assert time.perf_counter() - start < 3


async def test_slow_dripping_body_cannot_outlive_the_deadline():
    async def drip():
        for _ in range(100):
            await asyncio.sleep(0.1)   # each read is fast, so a per-read timeout never fires
            yield b"x" * 10
    f = NewsFetcher(resolver=resolver, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=drip())), deadline=0.5)
    start = time.perf_counter()
    assert await f.fetch_url("https://a.test/drip") is None
    assert time.perf_counter() - start < 3
