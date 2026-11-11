"""Reusable building blocks for config-driven news agents that notify via Telegram, Signal, etc.."""
from .agent import NewsAgent
from .messages import Line, Message
from .config import load_config, validate_config

__all__ = ["NewsAgent", "Line", "Message", "load_config", "validate_config"]
