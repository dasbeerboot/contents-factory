"""Review channels: terminal is the default, telegram is the phone.

`factory.toml review = "telegram"` picks which one the skills reach for. The telegram
channel previews and asks; it never generates and never writes queue state itself.
"""
from . import telegram, terminal

__all__ = ["telegram", "terminal"]
