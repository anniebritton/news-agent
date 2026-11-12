import json
import os

import pytest

from newsagent import StateCorruptError
from newsagent.state import StateManager


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
                                     '{"fetch_failures": []}'])
def test_corrupt_state_is_quarantined_and_aborts(tmp_path, content):
    f = tmp_path / "s.json"
    f.write_text(content)
    with pytest.raises(StateCorruptError) as exc:
        StateManager(str(f))
    assert "corrupt" in str(exc.value)
    assert not f.exists()  # moved aside, not left to be overwritten
    assert any(p.name.startswith("s.json.corrupt-") for p in tmp_path.iterdir())


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
