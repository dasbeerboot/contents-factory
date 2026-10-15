"""Telegram: preview on a phone, confirm, and three kinds of alert. Nothing else.

It is not an entrance to the factory. The agent (Claude Code or Codex) drives
`factory wizard`; Telegram only shows what came out and takes the yes/no.
"""
from . import api, daemon, notify, serve, setup, state

__all__ = ["api", "daemon", "notify", "serve", "setup", "state"]
