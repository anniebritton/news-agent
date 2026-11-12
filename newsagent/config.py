"""Loading and validation of agent config files (agents/<id>.yaml)."""
import os
import string

import yaml

from .errors import ConfigError

REQUIRED_KEYS = ("agent", "feeds", "keywords", "prompts", "notify")
TASK_FIELDS = {"indexed_text", "date_str"}
ITEM_FIELDS = {"group", "subject", "title", "summary"}
BRACE_HINT = "Use '{{' and '}}' for literal braces (for example in a JSON example)."


def normalize_agent_id(value: str) -> str:
    """Accept 'name', 'name.yaml', or 'agents/name.yaml' and return 'name'."""
    value = os.path.basename((value or "").strip())
    for ext in (".yaml", ".yml"):
        if value.endswith(ext):
            value = value[: -len(ext)]
    return value


def load_config(agent_id: str, config_dir: str = "agents") -> dict:
    agent_id = normalize_agent_id(agent_id)
    path = os.path.join(config_dir, f"{agent_id}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No config found for agent '{agent_id}' at {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    validate_config(config)
    return config


def check_template(template, allowed: set, where: str) -> None:
    """Reject templates that would raise at runtime: unknown/positional/attribute fields or stray braces."""
    if not isinstance(template, str):
        raise ConfigError(f"{where} must be a string")
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as e:
        raise ConfigError(f"{where} has unbalanced braces ({e}). {BRACE_HINT}") from e
    for _, name, spec, _ in parsed:
        if name is None:
            continue
        if not name.isidentifier() or "{" in (spec or ""):
            raise ConfigError(f"{where} has an invalid placeholder '{{{name}}}'. {BRACE_HINT}")
        if name not in allowed:
            raise ConfigError(f"{where} uses unknown placeholder '{{{name}}}'. "
                              f"Allowed: {', '.join('{' + a + '}' for a in sorted(allowed))}. {BRACE_HINT}")
    missing = allowed if where.endswith("task") else set()
    for needed in missing:
        if "{" + needed + "}" not in template:
            raise ConfigError(f"{where} must contain {{{needed}}}")
    try:
        template.format(**{k: "x" for k in allowed})
    except (KeyError, IndexError, ValueError) as e:
        raise ConfigError(f"{where} cannot be formatted ({e}). {BRACE_HINT}") from e


def check_templates(config: dict) -> None:
    prompts = config.get("prompts") or {}
    if "task" in prompts:
        check_template(prompts["task"], TASK_FIELDS, "prompts.task")
    messages = config.get("messages") or {}
    if "item_template" in messages:
        check_template(messages["item_template"], ITEM_FIELDS, "messages.item_template")


def validate_config(config) -> None:
    if not isinstance(config, dict):
        raise ConfigError("Config is empty or is not a YAML mapping")
    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        raise ConfigError(f"Config is missing required keys: {', '.join(missing)}")
    if not isinstance(config["agent"], dict) or not config["agent"].get("id"):
        raise ConfigError("agent must be a mapping with an id")
    if not isinstance(config["feeds"], list):
        raise ConfigError("feeds must be a list of URLs")
    if not isinstance(config["prompts"], dict):
        raise ConfigError("prompts must be a mapping")
    notify = config["notify"]
    channels = notify.get("channels") if isinstance(notify, dict) else None
    if not channels or not isinstance(channels, list) or not all(isinstance(c, dict) and c.get("type") for c in channels):
        raise ConfigError("notify.channels must list at least one channel, each with a type")
    if "task" not in config["prompts"]:
        raise ConfigError("prompts.task is required")
    check_templates(config)


def keyword_list(keywords) -> list:
    """Accepts a flat list, or a dict of lists (groups are merged). Returns lowercase terms."""
    if isinstance(keywords, dict):
        flat = [kw for group in keywords.values() for kw in (group or [])]
    else:
        flat = list(keywords or [])
    return [str(kw).lower() for kw in flat]
