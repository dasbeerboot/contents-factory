"""`factory telegram setup`: three steps in BotFather, then wait for /start.

The chat id is not asked for. A user who can read their own chat id does not need
this command, and one who cannot would paste the wrong number. Instead the bot waits
for the owner to send it anything and records where that came from, which is also the
only way to be sure the owner can actually reach the bot.

The token is read with `getpass`, verified with `getMe` and written to the profile's
`credentials.env`. It is never echoed, logged or returned: the only thing that comes
back out of here is the bot's username.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import i18n
from ..credentials import credentials_path, env_set
from ..errors import FactoryError
from . import state
from .api import Client, Transport, check_token

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
STEP_KEYS: tuple[str, ...] = ("telegram.setup.step1", "telegram.setup.step2",
                              "telegram.setup.step3")
WAIT_SECONDS = 60
POLL_CHUNK_SECONDS = 10


def guide(lang: str = "en") -> list[str]:
    """The three BotFather steps, in the language the owner is being talked to in."""
    translate = i18n.Translator(lang)
    return [f"{index}. {translate(key)}" for index, key in enumerate(STEP_KEYS, 1)]


@dataclass(frozen=True)
class Identity:
    username: str
    bot_id: str


def verify(token: str, *, transport: Transport | None = None) -> Identity:
    """getMe. Raises before anything is written if the token is not a live bot."""
    client = Client(check_token(token), transport=transport)
    payload = client.call("getMe")
    body = payload.get("result")
    if not isinstance(body, dict) or not body.get("username"):
        raise FactoryError("TELEGRAM_BOT_UNVERIFIED")
    return Identity(str(body["username"])[:64], str(body.get("id") or "")[:24])


def save_token(home: Path, profile: str, token: str) -> str:
    """Writes TELEGRAM_BOT_TOKEN owner-only. Returns "" or a permission warning code."""
    path = credentials_path(Path(home), profile)
    return env_set(path, TOKEN_KEY, check_token(token))


def wait_for_start(home: Path, client: Client, *, seconds: int = WAIT_SECONDS,
                   chunk: int = POLL_CHUNK_SECONDS,
                   clock: Callable[[], float] = time.monotonic) -> str:
    """Poll until the owner sends the bot anything. Returns the chat id, or "".

    The offset is advanced past whatever was consumed here so the serve loop does
    not replay the same /start as a fresh instruction.
    """
    deadline = clock() + max(0, int(seconds))
    offset = state.read_offset(Path(home)) or 0
    while True:
        payload = client.call("getUpdates", offset=offset,
                              timeout=min(chunk, max(0, int(deadline - clock()))),
                              allowed_updates=["message"])
        updates = payload.get("result")
        for update in updates if isinstance(updates, list) else []:
            if not isinstance(update, dict):
                continue
            offset = int(update.get("update_id", offset)) + 1
            message = update.get("message") or {}
            chat = (message.get("chat") or {}) if isinstance(message, dict) else {}
            found = str(chat.get("id") or "")
            if found:
                state.save_chat(Path(home), found,
                                username=str(chat.get("username") or ""))
                state.write_offset(Path(home), offset)
                return found
        state.write_offset(Path(home), offset)
        if clock() >= deadline:
            return ""


def status(home: Path, token: str) -> dict[str, object]:
    """What is wired up, with no token value and no network call."""
    path = Path(home)
    chat = state.chat(path)
    return {
        "token": bool(str(token or "").strip()),
        "chat_id": str(chat.get("chat_id") or ""),
        "chat_saved_at": str(chat.get("saved_at") or ""),
        "offset": state.read_offset(path),
        "pending_edits": state.pending_edits(path),
        "summary_sent_on": state.summary_sent_on(path),
        "state_dir": str(state.directory(path)),
    }
