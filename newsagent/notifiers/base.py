"""Notifier protocol and config-driven registry.

To add a channel: write a class with `__init__(self, channel_cfg: dict)` and
`async send(self, message: Message) -> bool`, then decorate it with `@register("name")`
(and import its module in notifiers/__init__.py).

`send` must return True only if the message was fully delivered. The agent saves its
state only after at least one channel returns True.

Optionally implement `missing() -> list[str]`: names of required env vars that are not set.
main.py checks it before any LLM call so a missing secret fails fast.
"""
from typing import Callable, Dict, List, Protocol, Type

from ..errors import ConfigError
from ..messages import Message


class Notifier(Protocol):
    async def send(self, message: Message) -> bool: ...


_REGISTRY: Dict[str, Type] = {}


def register(name: str) -> Callable:
    def deco(cls):
        _REGISTRY[name] = cls
        return cls
    return deco


def registered_types() -> List[str]:
    return sorted(_REGISTRY)


def build_notifiers(config: dict) -> List[Notifier]:
    channels = (config.get("notify") or {}).get("channels", [])
    notifiers = []
    for ch in channels:
        kind = ch.get("type")
        if kind not in _REGISTRY:
            raise ConfigError(f"Unknown notify channel type '{kind}'. Available: {sorted(_REGISTRY)}")
        notifiers.append(_REGISTRY[kind](ch))
    return notifiers
