import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from newsagent import NewsAgent, load_config, validate_config
from newsagent.config import keyword_list
from newsagent.extractor import Extractor
from newsagent.fetcher import NewsFetcher
from newsagent.llm import GeminiProvider
from newsagent import Line, Message
from newsagent.notifiers import ConsoleNotifier, SignalNotifier, TelegramNotifier, build_notifiers
from newsagent.scraper import NewsScraper
from newsagent.state import StateManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_FILES = [f for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".yaml")]


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response


def cfg(**over):
    base = {
        "agent": {"id": "t", "name": "Test"},
        "feeds": ["https://feed.test/"],
        "keywords": ["alpha"],
        "prompts": {"system": "s", "task": "{indexed_text} {date_str}", "scope": {"include": ["x"]}},
        "notify": {"channels": [{"type": "telegram", "bot_token_env": "T_TOKEN", "chat_id_env": "T_CHAT"}]},
        "settings": {"batch_size": 2},
    }
    base.update(over)
    return base


# ---- config -----------------------------------------------------------------

@pytest.mark.parametrize("name", AGENT_FILES)
def test_shipped_configs_valid(name):
    with open(os.path.join(ROOT, "agents", name)) as f:
        text = f.read()
    if name == "TEMPLATE.yaml":
        text = text.replace("<id>", "x").replace("<ID>", "X")
    validate_config(yaml.safe_load(text))


def test_validate_requires_a_channel():
    with pytest.raises(ValueError):
        validate_config(cfg(notify={"channels": []}))


def test_validate_rejects_missing_keys():
    with pytest.raises(ValueError):
        validate_config({"agent": {"id": "a"}})


def test_validate_requires_placeholders():
    c = cfg()
    c["prompts"]["task"] = "no placeholders"
    with pytest.raises(ValueError):
        validate_config(c)


def test_load_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nope_xyz", config_dir=os.path.join(ROOT, "agents"))


def test_keyword_list_forms():
    assert keyword_list(["A", "b"]) == ["a", "b"]
    assert keyword_list({"x": ["A"], "y": ["B"]}) == ["a", "b"]
    assert keyword_list(None) == []


# ---- state ------------------------------------------------------------------

def test_state_roundtrip_and_empty_file(tmp_path):
    f = str(tmp_path / "s.json")
    open(f, "w").close()  # empty file must not crash
    sm = StateManager(f)
    assert sm.last_run_at is None
    sm.mark_processed("a"); sm.mark_seen_url("u"); sm.last_run_at = "2024-01-01T00:00:00"
    sm.save_state()
    sm2 = StateManager(f)
    assert sm2.is_processed("a") and sm2.is_seen_url("u") and sm2.last_run_at


def test_state_truncates(tmp_path):
    sm = StateManager(str(tmp_path / "s.json"), max_items=2)
    for i in range(5):
        sm.mark_processed(str(i))
    sm.save_state()
    assert len(json.load(open(sm.state_file))["ids"]) == 2


# ---- scraper ----------------------------------------------------------------

async def test_scraper_filters_dedupes_and_marks_seen(tmp_path):
    fetcher = MagicMock()
    fetcher.fetch_url = AsyncMock(return_value=(
        "[Alpha news](https://a.test/1) [Alpha news](https://a.test/2) "
        "[Other](https://a.test/3) [Alpha again](https://a.test/1#frag)"))
    sm = StateManager(str(tmp_path / "s.json"))
    items = await NewsScraper(fetcher, sm, ["alpha"]).get_new_items(["f"])
    assert [i["url"] for i in items] == ["https://a.test/1"]
    assert sm.is_seen_url("https://a.test/1")
    # Link 1 is now seen and never returned again. Link 2 was only dropped for its
    # duplicate title in the first run (not marked seen), so it surfaces next time.
    again = await NewsScraper(fetcher, sm, ["alpha"]).get_new_items(["f"])
    assert [i["url"] for i in again] == ["https://a.test/2"]


async def test_scraper_no_keywords_passes_everything(tmp_path):
    fetcher = MagicMock()
    fetcher.fetch_url = AsyncMock(return_value="[One](https://a.test/1) [Two](https://a.test/2)")
    items = await NewsScraper(fetcher, StateManager(str(tmp_path / "s.json")), []).get_new_items(["f"])
    assert len(items) == 2


# ---- extractor --------------------------------------------------------------

def make_extractor(response, **kw):
    fetcher = MagicMock()
    fetcher.fetch_url = AsyncMock(return_value="body text")
    llm = FakeLLM(response)
    return Extractor(llm, fetcher, cfg()["prompts"], **kw), llm


NEWS = [{"title": "T0", "url": "https://x.test/0"}, {"title": "T1", "url": "https://x.test/1"}]


async def test_extractor_parses_and_drops_missing_subject():
    resp = json.dumps([
        {"index": 0, "translated_title": "Hello World", "subject": "Acme", "group": "G", "summary": "S"},
        {"index": 1, "translated_title": "No subject", "subject": "n/a", "summary": "S"},
    ])
    ex, llm = make_extractor("preamble " + resp + " trailer")
    out = await ex.extract(NEWS)
    assert len(out) == 1 and out[0]["subject"] == "Acme"
    assert out[0]["unique_slug"] == "hello-world" and out[0]["source_url"] == "https://x.test/0"
    assert "SCOPE" in llm.prompts[0] and "OUTPUT FORMAT" in llm.prompts[0]


async def test_extractor_subject_optional():
    resp = json.dumps([{"index": 0, "translated_title": "Anon", "subject": "", "summary": "S"}])
    ex, _ = make_extractor(resp, require_subject=False)
    assert len(await ex.extract(NEWS)) == 1


async def test_extractor_handles_llm_failure_and_garbage():
    ex, _ = make_extractor(None)
    assert await ex.extract(NEWS) == []
    ex, _ = make_extractor("not json at all")
    assert await ex.extract(NEWS) == []
    ex, _ = make_extractor("[broken")
    assert await ex.extract(NEWS) == []


async def test_extractor_ignores_out_of_range_index():
    ex, _ = make_extractor(json.dumps([{"index": 9, "translated_title": "x", "subject": "Acme"}]))
    assert await ex.extract(NEWS) == []


# ---- notifiers ---------------------------------------------------------------

TG = {"type": "telegram", "bot_token_env": "T_TOKEN", "chat_id_env": "T_CHAT"}
MSG = Message(header="Head <1>", lines=[Line("a & b", "https://s.test/x?a=1&b=2", "s.test/x")], footer="foot")


def fake_http(module, status=200):
    resp = MagicMock(status_code=status); resp.json.return_value = {"ok": True}
    client = MagicMock(); client.post = AsyncMock(return_value=resp)
    ctx = MagicMock(); ctx.__aenter__ = AsyncMock(return_value=client); ctx.__aexit__ = AsyncMock(return_value=False)
    return client, patch(f"newsagent.notifiers.{module}.httpx.AsyncClient", return_value=ctx)


def test_telegram_reads_env(monkeypatch):
    monkeypatch.setenv("T_TOKEN", '"123:abc"'); monkeypatch.setenv("T_CHAT", "-100")
    n = TelegramNotifier(TG)
    assert n.bot_token == "123:abc" and n.chat_id == "-100"


async def test_telegram_skips_without_token(monkeypatch):
    monkeypatch.delenv("T_TOKEN", raising=False)
    assert await TelegramNotifier(TG).send(MSG) is None


async def test_telegram_renders_escaped_html(monkeypatch):
    monkeypatch.setenv("T_TOKEN", "1:a"); monkeypatch.setenv("T_CHAT", "5")
    client, ctx = fake_http("telegram")
    with ctx:
        await TelegramNotifier(TG).send(MSG)
    p = client.post.call_args.kwargs["json"]
    assert p["parse_mode"] == "HTML"
    assert p["text"].startswith("<b>Head &lt;1&gt;</b>")
    assert '• a &amp; b <a href="https://s.test/x?a=1&amp;b=2">[s.test/x]</a>' in p["text"]


async def test_signal_posts_plain_text(monkeypatch):
    monkeypatch.setenv("SIGNAL_API_URL", "http://sig:8080/"); monkeypatch.setenv("SIGNAL_SENDER", "+100")
    monkeypatch.setenv("SIGNAL_RECIPIENTS", "+1, +2")
    client, ctx = fake_http("signal")
    with ctx:
        await SignalNotifier({"type": "signal"}).send(MSG)
    url = client.post.call_args.args[0]
    p = client.post.call_args.kwargs["json"]
    assert url == "http://sig:8080/v2/send" and p["recipients"] == ["+1", "+2"] and p["number"] == "+100"
    assert "<" not in p["message"].replace("<1>", "") and "https://s.test/x?a=1&b=2" in p["message"]


async def test_signal_skips_when_unconfigured(monkeypatch):
    for v in ("SIGNAL_API_URL", "SIGNAL_SENDER", "SIGNAL_RECIPIENTS"):
        monkeypatch.delenv(v, raising=False)
    assert await SignalNotifier({"type": "signal"}).send(MSG) is None


async def test_console_prints(capsys):
    await ConsoleNotifier().send(MSG)
    assert "a & b" in capsys.readouterr().out


def test_build_notifiers_multiple_and_unknown():
    c = cfg(notify={"channels": [TG, {"type": "console"}]})
    assert [type(n).__name__ for n in build_notifiers(c)] == ["TelegramNotifier", "ConsoleNotifier"]
    with pytest.raises(ValueError):
        build_notifiers(cfg(notify={"channels": [{"type": "carrier-pigeon"}]}))


# ---- agent ------------------------------------------------------------------

def make_agent(tmp_path, llm, **over):
    return NewsAgent(cfg(**over), llm=llm, state_dir=str(tmp_path))


def test_agent_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        NewsAgent(cfg(), state_dir=str(tmp_path))


def test_agent_state_file_per_id(tmp_path):
    a = make_agent(tmp_path, FakeLLM("[]"))
    assert a.state.state_file == os.path.join(str(tmp_path), "t_processed_news.json")


def test_format_item_default_and_custom(tmp_path):
    item = {"group": "G", "subject": "Subj", "summary": "x" * 500, "source_url": "https://www.s.test/a/b/c"}
    a = make_agent(tmp_path, FakeLLM("[]"))
    line = a.format_item(item)
    assert line.text.startswith("G, Subj, ") and "…" in line.text
    assert line.url == "https://www.s.test/a/b/c" and line.label == "s.test/a/b"
    a = make_agent(tmp_path, FakeLLM("[]"), messages={"item_template": "{subject}: {title}"})
    assert a.format_item({"subject": "Z", "title": "T"}).text == "Z: T"


async def test_full_round_and_dedup(tmp_path):
    resp = json.dumps([{"index": 0, "translated_title": "Big News", "subject": "Acme", "group": "G", "summary": "Happened"}])
    agent = make_agent(tmp_path, FakeLLM(resp))
    agent.fetcher.fetch_url = AsyncMock(side_effect=lambda url: "[Alpha story](https://a.test/1)" if url == "https://feed.test/" else "article")
    summaries, _ = await agent.run_round()
    assert len(summaries) == 1 and "Acme" in summaries[0].text and summaries[0].url
    assert agent.state.last_run_at

    # Second run: same link is already seen, so nothing new
    agent2 = make_agent(tmp_path, FakeLLM(resp))
    agent2.fetcher.fetch_url = agent.fetcher.fetch_url
    summaries, _ = await agent2.run_round()
    assert summaries == []


async def test_round_no_feeds(tmp_path):
    summaries, _ = await make_agent(tmp_path, FakeLLM("[]"), feeds=[]).run_round()
    assert summaries == []


# ---- no domain leakage -------------------------------------------------------

def test_no_sports_or_betting_terms_in_repo():
    banned = ["betting", "liiga", "hockey", "goaltender", "sportsbook", "wager", "odds"]
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "venv", ".venv"}
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if fn == "test_newsagent.py":
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding="utf-8").read().lower()
            except (UnicodeDecodeError, OSError):
                continue
            for term in banned:
                assert term not in text, f"'{term}' found in {os.path.relpath(path, ROOT)}"


# ---- gemini provider (SDK mocked) --------------------------------------------

def test_gemini_falls_back_across_models():
    with patch("google.genai.Client") as client_cls:
        models = client_cls.return_value.models
        ok = MagicMock(text="hello")
        models.generate_content.side_effect = [RuntimeError("model gone"), ok]
        p = GeminiProvider("key", models=["m1", "m2"])
        assert p.generate("prompt") == "hello"
        calls = [c.kwargs for c in models.generate_content.call_args_list]
        assert [c["model"] for c in calls] == ["m1", "m2"]
        assert calls[0]["config"] == {"temperature": 0.0} and calls[0]["contents"] == "prompt"


def test_gemini_returns_none_when_all_fail():
    with patch("google.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("x")
        assert GeminiProvider("key", models=["m1"]).generate("p") is None


# ---- dry run -------------------------------------------------------------------

async def test_dry_run_prints_and_does_not_save_state(tmp_path, monkeypatch, capsys):
    import main
    resp = json.dumps([{"index": 0, "translated_title": "Big News", "subject": "Acme", "group": "G", "summary": "Happened"}])
    monkeypatch.chdir(tmp_path)
    os.makedirs("agents")
    with open("agents/dry.yaml", "w") as f:
        yaml.safe_dump(cfg(agent={"id": "dry", "name": "Dry"}), f)
    fake = FakeLLM(resp)
    monkeypatch.setattr("newsagent.agent.build_provider", lambda config: fake)
    monkeypatch.setattr(NewsFetcher, "fetch_url", AsyncMock(
        side_effect=lambda self_url=None, *a, **k: "[Alpha story](https://a.test/1)"))
    await main.run("dry", dry_run=True)
    assert "Acme" in capsys.readouterr().out
    assert not os.path.exists(os.path.join("state", "dry_processed_news.json"))
