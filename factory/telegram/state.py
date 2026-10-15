"""State under `<home>/state/telegram/`. Sole writer of those files.

Same rule as the queue: nothing hand edits this, because the serve loop reads it on
every poll and a half-written offset replays or swallows updates. Every write is a
temp file plus `os.replace`, so a run that dies mid-write leaves the previous value.

Files:
  chat.json   the chat that is allowed to drive this factory, saved from /start
  offset      the getUpdates cursor; absent means "skip the backlog on first poll"
  seen.json   recently handled update ids, so a redelivered update is a no-op
  pending.json  chats waiting to type one edit line, keyed by chat id
  edits/<item>.txt  the edit line itself, read by the wizard
  summary.json  the day the daily digest last went out
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..errors import FactoryError
from ..spec.queue import QUEUE_ID

CHAT_ID = re.compile(r"^-?[0-9]{1,20}$")
# A redelivered update is the normal case after a crash, so the ring has to outlive
# one poll batch (Telegram sends at most 100) with room to spare.
SEEN_LIMIT = 500


def directory(home: Path) -> Path:
    return Path(home) / "state" / "telegram"


def chat_path(home: Path) -> Path:
    return directory(home) / "chat.json"


def offset_path(home: Path) -> Path:
    return directory(home) / "offset"


def seen_path(home: Path) -> Path:
    return directory(home) / "seen.json"


def pending_path(home: Path) -> Path:
    return directory(home) / "pending.json"


def summary_path(home: Path) -> Path:
    return directory(home) / "summary.json"


def edits_dir(home: Path) -> Path:
    return directory(home) / "edits"


def edit_path(home: Path, item_id: str) -> Path:
    return edits_dir(home) / f"{check_item(item_id)}.txt"


def check_item(item_id: str) -> str:
    if not QUEUE_ID.match(str(item_id or "")):
        raise FactoryError("QUEUE_ID_INVALID", str(item_id)[:32])
    return str(item_id)


def check_chat(chat_id: str) -> str:
    text = str(chat_id or "").strip()
    if not CHAT_ID.match(text):
        raise FactoryError("TELEGRAM_CHAT_INVALID", text[:24] or "empty")
    return text


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".tg-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as target:
            target.write(body)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return path


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- chat ----------

def save_chat(home: Path, chat_id: str, *, username: str = "") -> Path:
    """The chat that pressed /start. Only this chat's buttons are obeyed."""
    payload = {"chat_id": check_chat(chat_id), "saved_at": _stamp(),
               "username": str(username or "")[:64]}
    return _write(chat_path(home), json.dumps(payload, ensure_ascii=False,
                                              indent=2, sort_keys=True) + "\n")


def chat(home: Path) -> dict:
    data = _read_json(chat_path(home), {})
    return data if isinstance(data, dict) else {}


def chat_id(home: Path) -> str:
    value = str(chat(home).get("chat_id") or "")
    return value if CHAT_ID.match(value) else ""


# ---------- getUpdates cursor ----------

def read_offset(home: Path) -> int | None:
    try:
        raw = offset_path(home).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_offset(home: Path, value: int) -> Path:
    return _write(offset_path(home), f"{int(value)}\n")


# ---------- duplicate suppression ----------

def seen(home: Path) -> list[str]:
    data = _read_json(seen_path(home), [])
    return [str(entry) for entry in data] if isinstance(data, list) else []


def mark_seen(home: Path, key: str) -> bool:
    """True when this key is new. False means the update was already handled."""
    text = str(key)
    current = seen(home)
    if text in current:
        return False
    current.append(text)
    _write(seen_path(home), json.dumps(current[-SEEN_LIMIT:], ensure_ascii=False) + "\n")
    return True


# ---------- "fix this" follow up ----------

def set_pending_edit(home: Path, chat: str, item_id: str) -> Path:
    data = _read_json(pending_path(home), {})
    rows = data if isinstance(data, dict) else {}
    rows[check_chat(chat)] = {"item": check_item(item_id), "at": _stamp()}
    return _write(pending_path(home),
                  json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def pending_edit(home: Path, chat: str) -> str:
    data = _read_json(pending_path(home), {})
    rows = data if isinstance(data, dict) else {}
    row = rows.get(str(chat)) or {}
    item = str(row.get("item") or "") if isinstance(row, dict) else ""
    return item if QUEUE_ID.match(item) else ""


def clear_pending_edit(home: Path, chat: str) -> None:
    data = _read_json(pending_path(home), {})
    rows = data if isinstance(data, dict) else {}
    if str(chat) in rows:
        rows.pop(str(chat))
        _write(pending_path(home),
               json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def save_edit(home: Path, item_id: str, text: str) -> Path:
    """The one line the owner typed. The wizard reads this; nothing else writes it."""
    body = str(text or "").strip()
    if not body:
        raise FactoryError("TELEGRAM_EDIT_EMPTY", item_id[:32])
    return _write(edit_path(home, item_id), body[:2000] + "\n")


def read_edit(home: Path, item_id: str) -> str:
    try:
        return edit_path(home, item_id).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pending_edits(home: Path) -> list[str]:
    folder = edits_dir(home)
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("q_*.txt"))


# ---------- daily digest bookkeeping ----------

def summary_sent_on(home: Path) -> str:
    data = _read_json(summary_path(home), {})
    return str(data.get("day") or "") if isinstance(data, dict) else ""


def mark_summary_sent(home: Path, day: str) -> Path:
    payload = {"day": str(day)[:10], "at": _stamp()}
    return _write(summary_path(home),
                  json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
