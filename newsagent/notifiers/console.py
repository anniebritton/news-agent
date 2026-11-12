"""Prints to stdout. Handy for local runs and dry runs."""
from ..messages import Message
from .base import register


@register("console")
class ConsoleNotifier:
    def __init__(self, channel: dict = None):
        pass

    def missing(self) -> list:
        return []

    async def send(self, message: Message) -> bool:
        print(message.to_plain())
        return True
