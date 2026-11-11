"""Loading and validation of agent config files (agents/<id>.yaml)."""
import os
import yaml

REQUIRED_KEYS = ("agent", "feeds", "keywords", "prompts", "notify")


def load_config(agent_id: str, config_dir: str = "agents") -> dict:
    path = os.path.join(config_dir, f"{agent_id}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No config found for agent '{agent_id}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        raise ValueError(f"Config is missing required keys: {', '.join(missing)}")
    if not config["agent"].get("id"):
        raise ValueError("agent.id is required")
    if not config["notify"].get("channels"):
        raise ValueError("notify.channels must list at least one channel")
    task = config["prompts"].get("task", "")
    for placeholder in ("{indexed_text}", "{date_str}"):
        if placeholder not in task:
            raise ValueError(f"prompts.task must contain {placeholder}")


def keyword_list(keywords) -> list:
    """Accepts a flat list, or a dict of lists (groups are merged). Returns lowercase terms."""
    if isinstance(keywords, dict):
        flat = [kw for group in keywords.values() for kw in (group or [])]
    else:
        flat = list(keywords or [])
    return [str(kw).lower() for kw in flat]
