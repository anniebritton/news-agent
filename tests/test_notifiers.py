import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from helpers import cfg
from newsagent import Line, Message
from newsagent.notifiers import ConsoleNotifier, SignalNotifier, TelegramNotifier, build_notifiers
from newsagent.notifiers.telegram import render_chunks
from newsagent.errors import ConfigError

TG = {"type": "telegram", "bot_token_env": "T_TOKEN", "chat_id_env": "T_CHAT"}
MSG = Message(header="Head <1>", lines=[Line("a & b", "https://s.test/x?a=1&b=2", "s.test/x")], footer="foot")


def fake_http(module, status=200, text="{}", error=None):
    resp = MagicMock(status_code=status, text=text); resp.json.return_value = {"ok": True}
    client = MagicMock()
    client.post = AsyncMock(side_effect=error) if error else AsyncMock(return_value=resp)
    ctx = MagicMock(); ctx.__aenter__ = AsyncMock(return_value=client); ctx.__aexit__ = AsyncMock(return_value=False)
    return client, patch(f"newsagent.notifiers.{module}.httpx.AsyncClient", return_value=ctx)


@pytest.fixture
def tg_env(monkeypatch):
    monkeypatch.setenv("T_TOKEN", "123456:SECRETTOKENVALUE"); monkeypatch.setenv("T_CHAT", "-100")


def test_telegram_reads_env(monkeypatch):
    monkeypatch.setenv("T_TOKEN", '"123:abc"'); monkeypatch.setenv("T_CHAT", "-100")
    n = TelegramNotifier(TG)
    assert n.bot_token == "123:abc" and n.chat_id == "-100"


async def test_telegram_not_configured_returns_false(monkeypatch):
    monkeypatch.delenv("T_TOKEN", raising=False); monkeypatch.setenv("T_CHAT", "1")
    assert await TelegramNotifier(TG).send(MSG) is False
    monkeypatch.setenv("T_TOKEN", "1:abc"); monkeypatch.delenv("T_CHAT")
    assert await TelegramNotifier(TG).send(MSG) is False


async def test_telegram_renders_escaped_html_and_disables_previews(tg_env):
    client, ctx = fake_http("telegram")
    with ctx:
        assert await TelegramNotifier(TG).send(MSG) is True
    p = client.post.call_args.kwargs["json"]
    assert p["parse_mode"] == "HTML" and p["link_preview_options"] == {"is_disabled": True}
    assert p["text"].startswith("<b>Head &lt;1&gt;</b>")
    assert '• a &amp; b <a href="https://s.test/x?a=1&amp;b=2">[s.test/x]</a>' in p["text"]


def test_long_message_is_split_on_line_boundaries_without_loss():
    lines = [Line(f"item {i} " + "word " * 40, f"https://s.test/{i}", "s.test") for i in range(80)]
    chunks = render_chunks(Message(header="HEAD", lines=lines, footer="FOOT"))
    assert len(chunks) > 1 and all(len(c) <= 4096 for c in chunks)
    assert chunks[0].startswith("<b>HEAD</b>") and all("<b>HEAD</b>" not in c for c in chunks[1:])
    assert chunks[-1].endswith("FOOT")
    joined = "\n".join(chunks)
    assert all(f"item {i} " in joined for i in range(80)) and joined.count("<a ") == joined.count("</a>") == 80


def test_one_enormous_line_is_shortened_to_fit():
    chunks = render_chunks(Message(lines=[Line("x" * 50_000, "https://s.test/", "s")]))
    assert len(chunks) == 1 and len(chunks[0]) <= 4096 and chunks[0].count("<a ") == chunks[0].count("</a>")


async def test_every_chunk_is_posted_and_all_must_succeed(tg_env):
    msg = Message(header="H", lines=[Line("word " * 200, "https://s.test/", "s") for _ in range(20)])
    client, ctx = fake_http("telegram")
    with ctx:
        assert await TelegramNotifier(TG).send(msg) is True
    assert client.post.await_count > 1


async def test_telegram_http_error_returns_false_and_never_logs_the_token(tg_env, caplog):
    client, ctx = fake_http("telegram", status=401, text="Unauthorized for bot123456:SECRETTOKENVALUE")
    with ctx, caplog.at_level(logging.DEBUG):
        assert await TelegramNotifier(TG).send(MSG) is False
    assert "SECRETTOKENVALUE" not in caplog.text


async def test_exception_text_with_the_token_url_is_redacted(tg_env, caplog):
    err = httpx.HTTPStatusError("Client error '401' for url 'https://api.telegram.org/bot123456:SECRETTOKENVALUE/sendMessage'",
                                request=MagicMock(), response=MagicMock())
    client, ctx = fake_http("telegram", error=err)
    with ctx, caplog.at_level(logging.DEBUG):
        assert await TelegramNotifier(TG).send(MSG) is False
    assert "SECRETTOKENVALUE" not in caplog.text and "***" in caplog.text


def test_unsafe_link_schemes_are_dropped_everywhere():
    bad = Message(lines=[Line("x", "javascript:alert(1)", "evil"), Line("y", "tg://user?id=1", "evil"),
                         Line("z", "data:text/html,hi", "evil"), Line("ok", "https://s.test/", "s")])
    html = "\n".join(render_chunks(bad)); plain = bad.to_plain()
    for scheme in ("javascript", "tg://", "data:"):
        assert scheme not in html and scheme not in plain
    assert 'href="https://s.test/"' in html and "https://s.test/" in plain


@pytest.fixture
def signal_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_API_URL", "http://sig:8080/"); monkeypatch.setenv("SIGNAL_SENDER", "+100")
    monkeypatch.setenv("SIGNAL_RECIPIENTS", "+1, +2")


async def test_signal_posts_plain_text(signal_env):
    client, ctx = fake_http("signal")
    with ctx:
        assert await SignalNotifier({"type": "signal"}).send(MSG) is True
    p = client.post.call_args.kwargs["json"]
    assert client.post.call_args.args[0] == "http://sig:8080/v2/send" and p["recipients"] == ["+1", "+2"]
    assert "https://s.test/x?a=1&b=2" in p["message"]


async def test_signal_failure_and_unconfigured_return_false(signal_env, monkeypatch):
    client, ctx = fake_http("signal", status=500)
    with ctx:
        assert await SignalNotifier({"type": "signal"}).send(MSG) is False
    for v in ("SIGNAL_API_URL", "SIGNAL_SENDER", "SIGNAL_RECIPIENTS"):
        monkeypatch.delenv(v)
    assert await SignalNotifier({"type": "signal"}).send(MSG) is False


async def test_console_prints_and_reports_success(capsys):
    assert await ConsoleNotifier().send(MSG) is True
    assert "a & b" in capsys.readouterr().out


def test_build_notifiers_multiple_and_unknown():
    c = cfg(notify={"channels": [TG, {"type": "console"}]})
    assert [type(n).__name__ for n in build_notifiers(c)] == ["TelegramNotifier", "ConsoleNotifier"]
    with pytest.raises(ConfigError):
        build_notifiers(cfg(notify={"channels": [{"type": "carrier-pigeon"}]}))
