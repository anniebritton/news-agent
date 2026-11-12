import os

import pytest
import yaml

from helpers import cfg
from newsagent import ConfigError, load_config, validate_config
from newsagent.config import check_template, keyword_list, normalize_agent_id

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_FILES = [f for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".yaml")]


@pytest.mark.parametrize("name", AGENT_FILES)
def test_shipped_configs_valid(name):
    with open(os.path.join(ROOT, "agents", name)) as f:
        text = f.read()
    if name == "TEMPLATE.yaml":
        text = text.replace("<id>", "x").replace("<ID>", "X")
    validate_config(yaml.safe_load(text))


def test_validate_rejects_missing_keys():
    with pytest.raises(ConfigError):
        validate_config({"agent": {"id": "a"}})


def test_validate_requires_a_channel():
    with pytest.raises(ConfigError):
        validate_config(cfg(notify={"channels": []}))


@pytest.mark.parametrize("bad", [None, [], "text"])
def test_validate_rejects_non_mapping_config(bad):
    with pytest.raises(ConfigError):
        validate_config(bad)


def test_validate_null_blocks_give_friendly_error():
    for key in ("agent", "notify", "prompts"):
        c = cfg()
        c[key] = None
        with pytest.raises(ConfigError):
            validate_config(c)


def test_task_must_contain_placeholders():
    c = cfg()
    c["prompts"]["task"] = "no placeholders"
    with pytest.raises(ConfigError, match="indexed_text|date_str"):
        validate_config(c)


def test_literal_braces_in_task_rejected_with_hint():
    c = cfg()
    c["prompts"]["task"] = '{indexed_text} {date_str} Example: {"a": 1}'
    with pytest.raises(ConfigError, match="literal braces"):
        validate_config(c)


def test_doubled_braces_in_task_accepted():
    c = cfg()
    c["prompts"]["task"] = '{indexed_text} {date_str} Example: {{"a": 1}}'
    validate_config(c)


def test_unknown_item_template_placeholder_rejected():
    c = cfg(messages={"item_template": "{subject} {bogus}"})
    with pytest.raises(ConfigError, match="bogus"):
        validate_config(c)


@pytest.mark.parametrize("tpl", ["{0}", "{subject.__class__}", "{subject[0]}", "{", "}"])
def test_dangerous_or_broken_templates_rejected(tpl):
    with pytest.raises(ConfigError):
        check_template(tpl, {"subject"}, "t")


def test_load_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nope_xyz", config_dir=os.path.join(ROOT, "agents"))


def test_load_accepts_filename_forms():
    d = os.path.join(ROOT, "agents")
    assert load_config("example_launches.yaml", d)["agent"]["id"] == "example_launches"
    assert load_config("agents/example_launches.yaml", d)["agent"]["id"] == "example_launches"


def test_invalid_yaml_is_config_error(tmp_path):
    (tmp_path / "bad.yaml").write_text("a: [unclosed")
    with pytest.raises(ConfigError):
        load_config("bad", str(tmp_path))


def test_normalize_agent_id():
    assert normalize_agent_id("foo.yaml") == "foo"
    assert normalize_agent_id("agents/foo.yml") == "foo"
    assert normalize_agent_id("foo") == "foo"


def test_keyword_list_forms():
    assert keyword_list(["A", "b"]) == ["a", "b"]
    assert keyword_list({"x": ["A"], "y": ["B"]}) == ["a", "b"]
    assert keyword_list(None) == []


@pytest.mark.parametrize("settings", [
    {"batch_size": 0}, {"batch_size": "4"}, {"batch_size": True}, {"max_items_per_message": 0},
    {"max_summary_length": 0}, {"max_state_items": -1}, {"time_window_hours": -1}, {"time_window_hours": "8"},
    "not a mapping"])
def test_bad_settings_are_rejected_with_a_clear_message(settings):
    with pytest.raises(ConfigError, match="settings"):
        validate_config(cfg(settings=settings))


def test_good_settings_accepted():
    validate_config(cfg(settings={"batch_size": 1, "max_items_per_message": 5, "time_window_hours": 0,
                                  "max_summary_length": 50, "max_state_items": 10}))


def test_unknown_channel_type_is_rejected_at_validation_time():
    with pytest.raises(ConfigError, match="carrier-pigeon.*Available"):
        validate_config(cfg(notify={"channels": [{"type": "carrier-pigeon"}]}))


def test_format_conversions_are_allowed_and_bad_ones_rejected():
    check_template("{summary!r} {title!s}", {"summary", "title"}, "t")
    with pytest.raises(ConfigError):
        check_template("{summary!x}", {"summary"}, "t")


def test_keywords_key_is_required_but_may_be_empty():
    validate_config(cfg(keywords=[]))
    c = cfg(); del c["keywords"]
    with pytest.raises(ConfigError, match="keywords"):
        validate_config(c)
