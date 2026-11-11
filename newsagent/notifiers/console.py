"""Prints to stdout. Handy for local runs and dry runs."""
from ..messages import Message
from .base import register


@register("console")
class ConsoleNotifier:
    def __init__(self, channel: dict = None):
        pass

    async def send(self, message: Message):
        print(message.to_plain())
        return {"ok": True}
