import json
import os

import pytest
import yaml

import main
from helpers import FEED_PAGE, FakeLLM, cfg, llm_items
from newsagent import Message, NewsAgent
from newsagent.fetcher import NewsFetcher

GOOD = llm_items((0, "Big News"))


class Recorder:
    def __init__(self, ok=True):
        self.ok = ok
        self.messages = []

    async def send(self, message):
        self.messages.append(message)
        return self.ok

    @property
    def text(self):
        return "\n".join(m.to_plain() for m in self.messages)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("agents")
    with open("agents/t.yaml", "w") as f:
        yaml.safe_dump(cfg(), f)

    async def fetch(self, url):
        return FEED_PAGE if url == "https://feed.test/" else "article body"
    monkeypatch.setattr(NewsFetcher, "fetch_url", fetch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class Env:
        state = "state/t_processed_news.json"
        rec = Recorder()

        def use(self, llm_response=GOOD, rec=None):
            self.rec = rec or Recorder()
            monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: FakeLLM(llm_response))
            monkeypatch.setattr(main, "build_notifiers", lambda c: [self.rec])
            return self.rec
    return Env()


async def test_success_delivers_and_saves_state(env):
    rec = env.use()
    assert await main.run("t") == 0
    assert "Acme" in rec.text and os.path.exists(env.state)


async def test_delivery_failure_exits_nonzero_and_does_not_save_state(env):
    env.use(rec=Recorder(ok=False))
    assert await main.run("t") == 1
    assert not os.path.exists(env.state)
    rec = env.use()  # channel recovers: the same items are delivered, nothing was lost
    assert await main.run("t") == 0 and "Acme" in rec.text


async def test_state_saved_if_at_least_one_channel_accepts(env, monkeypatch):
    good, bad = Recorder(True), Recorder(False)
    monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: FakeLLM(GOOD))
    monkeypatch.setattr(main, "build_notifiers", lambda c: [bad, good])
    assert await main.run("t") == 0 and os.path.exists(env.state)


async def test_all_llm_batches_failing_notifies_and_exits_nonzero_without_saving(env):
    rec = env.use(llm_response=None)
    assert await main.run("t") == 1
    assert main.LLM_ERROR in rec.text and not os.path.exists(env.state)


async def test_unexpected_error_sends_only_a_generic_message(env, monkeypatch):
    rec = env.use()

    async def boom(self, limit=None):
        raise RuntimeError("https://api.telegram.org/bot123456:SECRETTOKENVALUE leaked SECRET_DB_PASSWORD")
    monkeypatch.setattr(NewsAgent, "run_round", boom)
    assert await main.run("t") == 1
    assert main.GENERIC_ERROR in rec.text
    assert "SECRET" not in rec.text and "RuntimeError" not in rec.text and "123456" not in rec.text
    assert not os.path.exists(env.state)


async def test_nothing_new_sends_status_and_saves(env):
    rec = env.use(llm_response="[]")
    assert await main.run("t") == 0
    assert "No new updates" in rec.text and os.path.exists(env.state)


async def test_status_message_failure_is_loud_but_loses_nothing(env):
    env.use(llm_response="[]", rec=Recorder(ok=False))
    assert await main.run("t") == 1
    assert os.path.exists(env.state)  # nothing was undelivered, so the consumed links are saved


async def test_dry_run_prints_and_never_touches_state(env, capsys):
    env.use()
    assert await main.run("t", dry_run=True) == 0
    assert "Acme" in capsys.readouterr().out and not os.path.exists(env.state)


async def test_check_needs_no_key_and_writes_nothing(env, capsys):
    assert await main.run("t", check=True) == 0
    out = capsys.readouterr().out
    assert "https://a.test/1" in out and "2 candidate" in out and not os.path.exists(env.state)


async def test_check_with_no_links_explains_rss_limitation(env, monkeypatch, capsys):
    async def empty(self, url):
        return "<rss><item/></rss>"
    monkeypatch.setattr(NewsFetcher, "fetch_url", empty)
    assert await main.run("t", check=True) == 1
    assert "RSS" in capsys.readouterr().err


async def test_seed_records_links_without_llm_or_notification(env):
    assert await main.run("t", seed=True) == 0
    state = json.load(open(env.state))
    assert "https://a.test/1" in state["urls"] and state["ids"] == []
    rec = env.use(llm_response="[]")
    await main.run("t")
    assert "No new updates" in rec.text


async def test_limit_caps_candidates(env, capsys):
    await main.run("t", check=True, limit=1)
    assert "1 candidate" in capsys.readouterr().out


async def test_missing_api_key_is_a_friendly_error(env, capsys):
    assert await main.run("t") == 2
    err = capsys.readouterr().err
    assert "GEMINI_API_KEY" in err and "Traceback" not in err


async def test_agent_name_with_extension_and_unknown_agent(env, capsys):
    env.use()
    assert await main.run("t.yaml") == 0
    assert await main.run("agents/t.yaml") == 0
    assert await main.run("nope") == 2 and "No config found" in capsys.readouterr().err


async def test_corrupt_state_aborts_with_a_clear_error(env, capsys):
    os.makedirs("state")
    open(env.state, "w").write("{broken")
    env.use()
    assert await main.run("t") == 3
    assert "corrupt" in capsys.readouterr().err and not env.rec.messages


async def test_broadcast_survives_a_channel_that_raises():
    class Raises:
        async def send(self, m):
            raise RuntimeError("x")
    ok = Recorder()
    assert await main.broadcast([Raises(), ok], Message(header="h")) == 1
