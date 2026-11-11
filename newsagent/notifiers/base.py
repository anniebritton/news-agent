"""Notifier protocol and config-driven registry.

To add a channel: write a class with `__init__(self, channel_cfg: dict)` and
`async send(self, message: Message)`, then decorate it with `@register("name")`
(and import its module in notifiers/__init__.py).
"""
from typing import Callable, Dict, List, Protocol, Type

from ..messages import Message


class Notifier(Protocol):
    async def send(self, message: Message): ...


_REGISTRY: Dict[str, Type] = {}


def register(name: str) -> Callable:
    def deco(cls):
        _REGISTRY[name] = cls
        return cls
    return deco


def build_notifiers(config: dict) -> List[Notifier]:
    channels = config.get("notify", {}).get("channels", [])
    notifiers = []
    for ch in channels:
        kind = ch.get("type")
        if kind not in _REGISTRY:
            raise ValueError(f"Unknown notify channel type '{kind}'. Available: {sorted(_REGISTRY)}")
        notifiers.append(_REGISTRY[kind](ch))
    return notifiers
