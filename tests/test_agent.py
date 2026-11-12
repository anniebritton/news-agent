import json
import os
from datetime import datetime, timedelta

import pytest

from helpers import FakeLLM, cfg, hours_ago, llm_items, make_agent
from newsagent import ConfigError, NewsAgent
from newsagent.extractor import ExtractionResult
from newsagent.state import StateManager


async def test_round_does_not_write_state_until_commit(tmp_path):
    agent = make_agent(tmp_path, FakeLLM(llm_items((0, "Big News"))))
    result = await agent.run_round()
    assert len(result.lines) == 1 and "Acme" in result.lines[0].text and result.lines[0].url == "https://a.test/1"
    assert not os.path.exists(agent.state.state_file)
    assert agent.commit()
    again = make_agent(tmp_path, FakeLLM(llm_items((0, "Big News"))))
    assert (await again.run_round()).lines == []  # both links are now seen


async def test_uncommitted_round_is_retried_in_full(tmp_path):
    """Delivery failed -> caller never commits -> the same items come back."""
    first = await make_agent(tmp_path, FakeLLM(llm_items((0, "Big News")))).run_round()
    second = await make_agent(tmp_path, FakeLLM(llm_items((0, "Big News")))).run_round()
    assert len(first.lines) == len(second.lines) == 1


async def test_failed_llm_batch_does_not_consume_urls(tmp_path):
    agent = make_agent(tmp_path, FakeLLM(None))
    result = await agent.run_round()
    assert result.all_failed and result.lines == []
    agent.commit()
    retry = make_agent(tmp_path, FakeLLM(llm_items((0, "Big News"))))
    assert len((await retry.run_round()).lines) == 1  # article was NOT lost


async def test_failed_llm_does_not_advance_the_clock(tmp_path):
    agent = make_agent(tmp_path, FakeLLM(None))
    await agent.run_round()
    agent.commit()
    assert agent.state.last_run_at is None


async def test_only_failed_batches_are_retried(tmp_path):
    def llm(prompt):  # batch_size 1: one call per article; fail the one about /2
        return None if "a.test/2" in prompt else llm_items((1, "Two"))
    agent = make_agent(tmp_path, FakeLLM(llm), settings={"batch_size": 1})
    result = await agent.run_round()
    assert (result.total_batches, result.failed_batches) == (2, 1) and not result.all_failed
    agent.commit()
    assert agent.state.is_seen_url("https://a.test/1") and not agent.state.is_seen_url("https://a.test/2")


async def test_fetch_failures_are_retried_then_given_up(tmp_path):
    for attempt in range(1, 4):
        agent = make_agent(tmp_path, FakeLLM("[]"), fail={"https://a.test/1"})
        await agent.run_round()
        seen_before = agent.state.is_seen_url("https://a.test/1")
        agent.commit()
        assert not seen_before
    assert StateManager(agent.state.state_file).is_seen_url("https://a.test/1")  # gave up after 3


async def test_window_is_enforced_on_published_dates(tmp_path):
    resp = llm_items(
        {"index": 0, "translated_title": "Old", "subject": "Acme", "published": hours_ago(10)},
        {"index": 1, "translated_title": "Fresh", "subject": "Acme", "published": hours_ago(1)})
    result = await make_agent(tmp_path, FakeLLM(resp), settings={"time_window_hours": 8}).run_round()
    assert [ln.url for ln in result.lines] == ["https://a.test/2"]


async def test_items_without_a_published_date_are_not_window_filtered(tmp_path):
    result = await make_agent(tmp_path, FakeLLM(llm_items((0, "Undated")))).run_round()
    assert len(result.lines) == 1


async def test_window_uses_time_since_last_run_and_ignores_bad_timestamps(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"), settings={"time_window_hours": 8})
    now = datetime.now()
    agent.state.last_run_at = (now - timedelta(hours=2)).isoformat()
    assert 2.0 < agent._window_hours(now) < 2.2
    for bad in ((now + timedelta(days=365)).isoformat(), "garbage", "2999-01-01T00:00:00"):
        agent.state.last_run_at = bad
        assert agent._window_hours(now) == 8.0, bad


def fake_extraction(*items):
    async def extract(candidates):
        return ExtractionResult(items=list(items), done_urls={c["url"] for c in candidates}, total_batches=1)
    return extract


def raw_item(url="https://a.test/1", title="Story", **kw):
    return {"title": title, "subject": "Acme", "group": "G", "summary": "S", "source_url": url,
            "published": None, "unique_slug": title.lower(), **kw}


async def test_already_processed_items_are_not_resent(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"))
    agent.state.mark_processed("url:https://a.test/1")
    agent.extractor.extract = fake_extraction(raw_item("https://a.test/1", "Different title this time"))
    assert (await agent.run_round()).lines == []


async def test_same_story_with_a_new_title_is_deduped_by_url(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"))
    agent.extractor.extract = fake_extraction(raw_item("https://a.test/1/?utm_source=x", "Title A"),
                                              raw_item("https://a.test/1", "Title B"))
    assert len((await agent.run_round()).lines) == 1


async def test_long_identical_title_prefixes_do_not_collide(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"))
    prefix = "A" * 80
    agent.extractor.extract = fake_extraction(raw_item("https://a.test/1", prefix + "x"),
                                              raw_item("https://a.test/2", prefix + "y"))
    assert len((await agent.run_round()).lines) == 2


async def test_items_without_a_usable_id_are_skipped(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"))
    agent.extractor.extract = fake_extraction(raw_item("", "!!!", unique_slug=""), raw_item("", "?", unique_slug="-"))
    assert (await agent.run_round()).lines == []


async def test_message_cap_defers_the_rest_to_the_next_run(tmp_path):
    items = [raw_item(f"https://a.test/{i}", f"Story {i}") for i in (1, 2)]
    agent = make_agent(tmp_path, FakeLLM("[]"), settings={"max_items_per_message": 1})
    agent.extractor.extract = fake_extraction(*items)
    result = await agent.run_round()
    assert len(result.lines) == 1 and result.deferred == 1
    agent.commit()
    assert agent.state.is_seen_url("https://a.test/1") and not agent.state.is_seen_url("https://a.test/2")


def test_format_item_default_and_custom(tmp_path):
    item = {"group": "G", "subject": "Subj", "summary": "x" * 500, "source_url": "https://www.s.test/a/b/c"}
    a = make_agent(tmp_path, FakeLLM("[]"))
    line = a.format_item(item)
    assert line.text.startswith("G, Subj, ") and "…" in line.text
    assert line.url == "https://www.s.test/a/b/c" and line.label == "s.test/a/b"
    a = make_agent(tmp_path, FakeLLM("[]"), messages={"item_template": "{subject}: {title}"})
    assert a.format_item({"subject": "Z", "title": "T"}).text == "Z: T"


def test_format_item_tolerates_non_string_values(tmp_path):
    a = make_agent(tmp_path, FakeLLM("[]"))
    line = a.format_item({"group": None, "subject": ["A", "B"], "summary": ["x", "y"], "source_url": None})
    assert "A / B" in line.text and "x / y" in line.text and line.url == ""


def test_source_label_uses_hostname_not_userinfo():
    label = NewsAgent._source_label("https://good.com@evil.com/x/y")
    assert label == "evil.com/x/y" and "good.com" not in label


async def test_check_is_keyless_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = make_agent(tmp_path, None)
    reports, candidates = await agent.check()
    assert len(candidates) == 2 and reports[0].links == 2
    assert not os.path.exists(agent.state.state_file)
    assert len((await agent.check(limit=1))[1]) == 1


async def test_seed_marks_links_seen_without_an_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = make_agent(tmp_path, None)
    assert await agent.seed() == 2
    fresh = make_agent(tmp_path, None)
    assert fresh.state.is_seen_url("https://a.test/1") and fresh.state.last_run_at
    assert (await fresh.check())[1] == []


def test_missing_api_key_is_a_config_error_at_use_not_construction(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = NewsAgent(cfg(), state_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        agent.ensure_llm()


def test_state_file_per_id(tmp_path):
    a = make_agent(tmp_path, FakeLLM("[]"))
    assert a.state.state_file == os.path.join(str(tmp_path), "t_processed_news.json")


async def test_no_feeds(tmp_path):
    assert (await make_agent(tmp_path, FakeLLM("[]"), feeds=[]).run_round()).lines == []


def test_bad_template_in_programmatic_config_fails_fast(tmp_path):
    with pytest.raises(ConfigError):
        NewsAgent(cfg(messages={"item_template": "{nope}"}), llm=FakeLLM("[]"), state_dir=str(tmp_path))


async def test_all_feeds_unreachable_is_an_outage_not_a_quiet_day(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"), pages={"https://feed.test/": None})
    agent.fetcher.fetch_url = lambda url: _none()
    result = await agent.run_round()
    assert result.all_feeds_failed and result.feeds_failed == 1 and result.lines == []
    agent.commit()
    assert agent.state.last_run_at is None       # the clock must not advance


async def _none():
    return None


async def test_partial_feed_outage_warns_and_does_not_advance_the_clock(tmp_path, caplog):
    agent = make_agent(tmp_path, FakeLLM("[]"), feeds=["https://feed.test/", "https://down.test/"])
    real = agent.fetcher.fetch_url

    async def fetch(url):
        return None if "down.test" in url else await real(url)
    agent.fetcher.fetch_url = fetch
    result = await agent.run_round()
    assert (result.feeds_failed, result.feeds_total) == (1, 2) and not result.all_feeds_failed
    assert "down.test" in caplog.text
    agent.commit()
    assert agent.state.last_run_at is None
    healthy = make_agent(tmp_path / "ok", FakeLLM("[]"))
    await healthy.run_round(); healthy.commit()
    assert healthy.state.last_run_at                # control: a healthy run does advance it


async def test_seed_refuses_when_every_feed_is_unreachable(tmp_path):
    from newsagent.errors import FeedsUnreachable
    agent = make_agent(tmp_path, None)
    agent.fetcher.fetch_url = lambda url: _none()
    with pytest.raises(FeedsUnreachable):
        await agent.seed()
    assert not os.path.exists(agent.state.state_file)


async def test_a_down_feed_with_no_candidates_elsewhere_still_does_not_advance_the_clock(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"), pages={"https://feed.test/": "no links here"},
                       feeds=["https://feed.test/", "https://down.test/"])
    real = agent.fetcher.fetch_url

    async def fetch(url):
        return None if "down.test" in url else await real(url)
    agent.fetcher.fetch_url = fetch
    result = await agent.run_round()
    assert result.feeds_failed == 1 and result.lines == []
    agent.commit()
    assert agent.state.last_run_at is None


def two_feeds(tmp_path, down_before):
    agent = make_agent(tmp_path, FakeLLM("[]"), feeds=["https://feed.test/", "https://down.test/"])
    agent.state.feed_failures["https://down.test/"] = down_before
    real = agent.fetcher.fetch_url

    async def fetch(url):
        return None if "down.test" in url else await real(url)
    agent.fetcher.fetch_url = fetch
    return agent


async def test_a_recently_failing_feed_holds_the_clock_and_its_count_grows(tmp_path):
    agent = two_feeds(tmp_path, down_before=3)
    result = await agent.run_round()
    assert (result.feeds_failed, result.feeds_chronic, result.feeds_transient) == (1, 0, 1)
    agent.commit()
    assert agent.state.last_run_at is None and agent.state.feed_failures == {"https://down.test/": 4}


async def test_a_chronically_dead_feed_stops_holding_the_clock_and_is_named(tmp_path, caplog):
    agent = two_feeds(tmp_path, down_before=4)            # this run is its 5th failure in a row
    result = await agent.run_round()
    assert (result.feeds_failed, result.feeds_chronic, result.feeds_transient) == (1, 1, 0)
    assert "https://down.test/" in caplog.text and "permanently dead" in caplog.text
    agent.commit()
    assert agent.state.last_run_at                         # the clock advances again
    assert agent.state.feed_failures == {"https://down.test/": 5}


async def test_a_feed_that_recovers_has_its_failure_count_cleared(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"), feeds=["https://feed.test/"])
    agent.state.feed_failures["https://feed.test/"] = 3
    await agent.run_round(); agent.commit()
    assert agent.state.feed_failures == {}


async def test_total_outage_is_still_an_outage_even_if_every_feed_is_chronic(tmp_path):
    agent = make_agent(tmp_path, FakeLLM("[]"))
    agent.state.feed_failures["https://feed.test/"] = 9
    agent.fetcher.fetch_url = lambda url: _none()
    assert (await agent.run_round()).all_feeds_failed
