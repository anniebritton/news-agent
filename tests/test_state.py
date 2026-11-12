import json
import os

import pytest

from newsagent import StateCorruptError
from newsagent.state import StateManager, reset_state_file, state_path


def test_roundtrip_and_empty_file(tmp_path):
    f = str(tmp_path / "s.json")
    open(f, "w").close()
    sm = StateManager(f)
    assert sm.last_run_at is None
    sm.mark_processed("a"); sm.mark_seen_url("u"); sm.last_run_at = "2024-01-01T00:00:00"
    sm.fetch_failures["x"] = 2
    assert sm.save_state()
    sm2 = StateManager(f)
    assert sm2.is_processed("a") and sm2.is_seen_url("u") and sm2.last_run_at and sm2.fetch_failures == {"x": 2}


def test_truncation_keeps_the_newest_entries(tmp_path):
    sm = StateManager(str(tmp_path / "s.json"), max_items=3)
    for i in range(10):
        sm.mark_processed(f"id{i}")
        sm.mark_seen_url(f"u{i}")
    sm.save_state()
    data = json.load(open(sm.state_file))
    assert data["ids"] == ["id7", "id8", "id9"]
    assert data["urls"] == ["u7", "u8", "u9"]


def test_legacy_list_format(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(["a", "b"]))
    sm = StateManager(str(f))
    assert sm.is_processed("a") and sm.is_processed("b")


@pytest.mark.parametrize("content", ["{not json", "null", '"text"', '{"ids": "oops"}', '{"urls": 5}',
                                     '{"fetch_failures": []}', '{"feed_failures": []}'])
def test_corrupt_state_aborts_every_time_and_leaves_the_file_alone(tmp_path, content):
    f = tmp_path / "s.json"
    f.write_text(content)
    for _ in range(3):   # a second run must not silently start from empty state
        with pytest.raises(StateCorruptError) as exc:
            StateManager(str(f))
        assert "--reset-state" in str(exc.value)
    assert f.read_text() == content
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]   # nothing moved aside


def test_non_string_entries_are_ignored(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"ids": ["ok", 5, None, ""], "urls": [["x"], "u"], "last_run_at": 12}))
    sm = StateManager(str(f))
    assert list(sm.processed_ids) == ["ok"] and list(sm.seen_urls) == ["u"] and sm.last_run_at is None


def test_two_agents_isolated(tmp_path):
    a, b = StateManager(str(tmp_path / "a.json")), StateManager(str(tmp_path / "b.json"))
    a.mark_processed("x"); a.save_state()
    assert not StateManager(str(tmp_path / "b.json")).is_processed("x")
    assert StateManager(str(tmp_path / "a.json")).is_processed("x")


def test_reset_state_file_backs_up_a_corrupt_file_and_writes_empty_state(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{broken")
    backup = reset_state_file(str(f))
    assert backup and backup.startswith(str(f) + ".corrupt-") and open(backup).read() == "{broken"
    sm = StateManager(str(f))                       # loads cleanly now
    assert not sm.processed_ids and not sm.seen_urls and sm.last_run_at


def test_reset_state_file_backs_up_a_valid_file_as_bak_and_handles_no_file(tmp_path):
    f = tmp_path / "s.json"
    sm = StateManager(str(f)); sm.mark_processed("a"); sm.save_state()
    assert ".bak-" in reset_state_file(str(f))
    assert reset_state_file(str(tmp_path / "new" / "x.json")) is None and (tmp_path / "new" / "x.json").exists()


def test_state_path():
    assert state_path("abc") == os.path.join("state", "abc_processed_news.json")


def test_feed_failure_counts_roundtrip_and_reset_clears_them(tmp_path):
    f = str(tmp_path / "s.json")
    sm = StateManager(f); sm.feed_failures["https://a.test/"] = 4; sm.save_state()
    assert StateManager(f).feed_failures == {"https://a.test/": 4}
    reset_state_file(f)
    assert StateManager(f).feed_failures == {}
