"""Reusable building blocks for config-driven news agents that notify via Telegram, Signal, etc."""
from .agent import NewsAgent, RoundResult
from .config import load_config, validate_config
from .errors import ConfigError, StateCorruptError
from .messages import Line, Message

__all__ = ["NewsAgent", "RoundResult", "Line", "Message", "load_config", "validate_config",
           "ConfigError", "StateCorruptError"]
