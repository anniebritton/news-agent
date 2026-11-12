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


async def test_partial_delivery_saves_state_but_exits_with_a_distinct_code(env, monkeypatch, caplog):
    good, bad = Recorder(True), Recorder(False)
    monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: FakeLLM(GOOD))
    monkeypatch.setattr(main, "build_notifiers", lambda c: [bad, good])
    assert await main.run("t") == main.EXIT_PARTIAL == 4
    assert os.path.exists(env.state)                       # at-least-once: the good channel got it, so it is consumed
    assert "PARTIAL DELIVERY" in caplog.text


async def test_all_channels_accepting_is_exit_zero(env, monkeypatch):
    monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: FakeLLM(GOOD))
    monkeypatch.setattr(main, "build_notifiers", lambda c: [Recorder(True), Recorder(True)])
    assert await main.run("t") == 0


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


async def test_missing_api_key_is_a_friendly_error(env, capsys, monkeypatch):
    monkeypatch.setenv("T_TOKEN", "1:abc"); monkeypatch.setenv("T_CHAT", "1")   # channel secrets present
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
    err = capsys.readouterr().err
    assert "corrupt" in err and "--reset-state" in err and not env.rec.messages


async def test_broadcast_survives_a_channel_that_raises():
    class Raises:
        async def send(self, m):
            raise RuntimeError("x")
    ok = Recorder()
    assert await main.broadcast([Raises(), ok], Message(header="h")) == 1


async def test_missing_channel_secret_fails_fast_before_any_llm_call(env, monkeypatch, capsys):
    llm = FakeLLM(GOOD)
    monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: llm)
    monkeypatch.delenv("T_TOKEN", raising=False); monkeypatch.delenv("T_CHAT", raising=False)
    assert await main.run("t") == main.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "T_TOKEN" in err and "T_CHAT" in err
    assert llm.prompts == [] and not os.path.exists(env.state)


async def test_secrets_are_not_required_for_dry_run_check_or_seed(env, monkeypatch):
    monkeypatch.delenv("T_TOKEN", raising=False); monkeypatch.delenv("T_CHAT", raising=False)
    assert await main.run("t", check=True) == 0
    assert await main.run("t", seed=True) == 0
    monkeypatch.setattr(NewsAgent, "ensure_llm", lambda a: FakeLLM("[]"))
    assert await main.run("t", dry_run=True) == 0


async def test_every_feed_down_is_reported_as_an_outage_not_as_no_news(env, monkeypatch):
    rec = env.use(llm_response="[]")

    async def down(self, url):
        return None
    monkeypatch.setattr(NewsFetcher, "fetch_url", down)
    assert await main.run("t") == 1
    assert "None of the configured feeds" in rec.text and "No new updates" not in rec.text
    assert not os.path.exists(env.state)               # clock not advanced, nothing saved


async def test_one_feed_down_adds_a_note_to_the_message(env, monkeypatch):
    with open("agents/t2.yaml", "w") as f:
        yaml.safe_dump(cfg(agent={"id": "t2", "name": "T2"}, feeds=["https://feed.test/", "https://down.test/"]), f)
    rec = env.use(llm_response="[]")

    async def fetch(self, url):
        return None if "down.test" in url else FEED_PAGE
    monkeypatch.setattr(NewsFetcher, "fetch_url", fetch)
    assert await main.run("t2") == 0
    assert "1 of 2 feeds could not be fetched" in rec.text
    assert json.load(open("state/t2_processed_news.json"))["last_run_at"] is None


async def test_reset_state_recovers_from_a_corrupt_file(env, capsys):
    os.makedirs("state")
    open(env.state, "w").write("{broken")
    assert await main.run("t") == 3                           # default stays: abort on corrupt
    open(env.state, "w").write("{broken")                     # (the abort moved it aside; recreate it)
    assert await main.run("t", reset_state=True) == 0
    assert any(n.startswith("t_processed_news.json.corrupt-") for n in os.listdir("state"))
    assert json.load(open(env.state))["ids"] == [] and json.load(open(env.state))["last_run_at"]
    assert "--seed" in capsys.readouterr().out
    rec = env.use()
    assert await main.run("t") == 0 and "Acme" in rec.text   # and the next normal run works


async def test_reset_state_without_a_file_and_with_a_bad_agent(env, capsys):
    assert await main.run("t", reset_state=True) == 0 and os.path.exists(env.state)
    assert await main.run("nope", reset_state=True) == 2


def test_limit_must_be_positive():
    for bad in ("0", "-3"):
        with pytest.raises(SystemExit):
            main.build_parser().parse_args(["--agent", "t", "--limit", bad])
    assert main.build_parser().parse_args(["--agent", "t", "--limit", "5"]).limit == 5


def test_uncaught_errors_print_one_redacted_line_and_no_traceback(monkeypatch, capsys):
    async def boom(*a, **k):
        raise RuntimeError("failed calling https://api.telegram.org/bot123456:SECRETTOKENVALUE/sendMessage")
    monkeypatch.setattr(main, "run", boom)
    assert main.cli(["--agent", "t"]) == 1
    err = capsys.readouterr().err
    assert "SECRETTOKENVALUE" not in err and "Traceback" not in err and len(err.strip().splitlines()) == 1
