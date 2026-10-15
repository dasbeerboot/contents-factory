"""The confirm loop: long-poll getUpdates, act on the three buttons, stay quiet.

Shape ported from the saju-threads poller, including the parts that were learned the
hard way:

* **One consumer per bot.** `getUpdates` drains the inbox, so two pollers on one token
  steal each other's updates. This loop is the only consumer of its bot.
* **Skip the backlog on first run.** With no offset file the first poll uses `-1` to
  read just the newest update and start after it, so months of old messages are not
  replayed as fresh approvals.
* **At most once.** An update id is marked seen *before* it is handled, so a crash
  mid-handling drops it rather than replaying it. Dropping a tap costs one more tap;
  replaying one could publish something twice.
* **Stale text only.** The age check applies to typed messages, never to button
  presses: a preview legitimately sits on the phone for hours before it is answered.

Every state change goes through `QueueStore`, which is the same code path the CLI
uses. This module writes no queue json of its own.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import i18n
from ..errors import FactoryError
from ..queue.store import QueueStore
from . import notify, state
from .api import Client

CALLBACK = re.compile(r"^cf:(ok|no|ed):(q_[0-9]{14}_[a-z0-9]{4})$")
POLL_TIMEOUT_SECONDS = 50
# A typed line older than this is history, not an instruction.
MAX_MESSAGE_AGE_SECONDS = 900
BACKOFF_SECONDS = 15
EDIT_KEY = "edit_request"
REJECT_CODE = "TELEGRAM_REJECTED"

ALLOWED_UPDATES = ("message", "callback_query")


@dataclass
class Serve:
    """One factory home, one bot, one chat."""

    home: Path
    store: QueueStore
    client: Client
    chat_id: str
    lang: str = "en"
    simple: bool = False
    summary_at: str = "21:00Z"
    log: Callable[[str], None] = field(default=lambda line: None)

    def translate(self, key: str, **values: object) -> str:
        return i18n.get(key, self.lang, **values)

    # ---------- outgoing ----------

    def say(self, text: str) -> None:
        self.client.call("sendMessage", chat_id=self.chat_id, text=text,
                         disable_web_page_preview=True)

    def toast(self, callback_id: str, text: str) -> None:
        if callback_id:
            self.client.call("answerCallbackQuery", callback_query_id=callback_id,
                             text=text[:180])

    def clear_buttons(self, message: dict) -> None:
        """Drop the keyboard after a decision so a second tap has nothing to hit."""
        message_id = message.get("message_id")
        chat = str((message.get("chat") or {}).get("id") or "")
        if not message_id or chat != self.chat_id:
            return
        try:
            self.client.call("editMessageReplyMarkup", chat_id=chat,
                             message_id=message_id,
                             reply_markup={"inline_keyboard": []})
        except FactoryError as error:
            # The decision already landed in the queue; a stale keyboard is cosmetic.
            self.log(f"MARKUP_CLEAR_FAILED {error.code}")

    # ---------- buttons ----------

    def handle_callback(self, callback: dict) -> str:
        message = callback.get("message") or {}
        chat = str((message.get("chat") or {}).get("id") or "")
        callback_id = str(callback.get("id") or "")
        if chat != self.chat_id:
            self.log(f"IGNORED_CHAT {chat[:16]}")
            return "IGNORED_CHAT"
        match = CALLBACK.match(str(callback.get("data") or ""))
        if not match:
            self.toast(callback_id, self.translate("telegram.callback.unknown"))
            return "UNKNOWN_CALLBACK"
        action, item_id = match.group(1), match.group(2)
        try:
            return self._apply(action, item_id, callback_id, message)
        except FactoryError as error:
            self.toast(callback_id, self.translate("telegram.callback.failed",
                                                   code=error.code))
            self.log(f"CALLBACK_FAILED {action} {item_id} {error.code}")
            return f"FAILED:{error.code}"

    def _apply(self, action: str, item_id: str, callback_id: str,
               message: dict) -> str:
        item = self.store.get(item_id)
        if item.published:
            self.toast(callback_id, self.translate("telegram.callback.published"))
            self.clear_buttons(message)
            return "ALREADY_PUBLISHED"
        if action == "ok":
            return self._approve(item_id, callback_id, message)
        if action == "no":
            return self._reject(item_id, callback_id, message)
        return self._ask_for_edit(item_id, callback_id)

    def _approve(self, item_id: str, callback_id: str, message: dict) -> str:
        self.store.approve(item_id)
        if self.simple:
            # Beginner mode: approving is the whole decision, so arming is not a
            # second question. The reason records which chat pressed it.
            self.store.arm(item_id, f"telegram:{self.chat_id}")
        updated = self.store.get(item_id)
        # The toast is the whole acknowledgement. A follow-up message here would be
        # the fourth kind of notification, and the policy allows three.
        self.toast(callback_id, self.translate(
            "telegram.callback.approved_armed" if updated.armed
            else "telegram.callback.approved", slot=updated.scheduled_at))
        self.clear_buttons(message)
        self.log(f"APPROVED {item_id} armed={updated.armed}")
        return "APPROVED_ARMED" if updated.armed else "APPROVED"

    def _reject(self, item_id: str, callback_id: str, message: dict) -> str:
        self.store.unapprove(item_id)
        self.store.disarm(item_id)
        self.store.record_error(item_id, REJECT_CODE)
        self.toast(callback_id, self.translate("telegram.callback.rejected"))
        self.clear_buttons(message)
        self.log(f"REJECTED {item_id}")
        return "REJECTED"

    def _ask_for_edit(self, item_id: str, callback_id: str) -> str:
        state.set_pending_edit(self.home, self.chat_id, item_id)
        self.toast(callback_id, self.translate("telegram.callback.edit_asked"))
        self.say(self.translate("telegram.callback.edit_prompt", item_id=item_id))
        self.log(f"EDIT_WAIT {item_id}")
        return "EDIT_WAIT"

    # ---------- typed lines ----------

    def handle_message(self, message: dict) -> str:
        chat = str((message.get("chat") or {}).get("id") or "")
        text = str(message.get("text") or "").strip()
        if self.stale(message):
            self.log("STALE_MESSAGE")
            return "STALE"
        if chat != self.chat_id:
            self.log(f"IGNORED_CHAT {chat[:16]}")
            return "IGNORED_CHAT"
        if text.split("@")[0] in {"/start", "/help"}:
            self.say(self.translate("telegram.role"))
            return "START"
        item_id = state.pending_edit(self.home, chat)
        if not item_id or not text:
            # No conversation here by design: without a pending "fix this" there is
            # nothing a typed line could mean.
            self.log("NO_PENDING_EDIT")
            return "NOOP"
        return self._record_edit(item_id, text, chat)

    def _record_edit(self, item_id: str, text: str, chat: str) -> str:
        try:
            state.save_edit(self.home, item_id, text)
            self.store.set_extra(item_id, EDIT_KEY, text[:2000])
        except FactoryError as error:
            self.say(self.translate("telegram.callback.failed", code=error.code))
            self.log(f"EDIT_FAILED {item_id} {error.code}")
            return f"FAILED:{error.code}"
        state.clear_pending_edit(self.home, chat)
        self.say(self.translate("telegram.callback.edit_saved", item_id=item_id))
        self.log(f"EDIT_SAVED {item_id}")
        return "EDIT_SAVED"

    @staticmethod
    def stale(message: dict) -> bool:
        sent = message.get("date")
        if not sent:
            return False
        try:
            return (time.time() - float(sent)) > MAX_MESSAGE_AGE_SECONDS
        except (TypeError, ValueError):
            return False

    # ---------- the loop ----------

    def handle_update(self, update: dict) -> str:
        update_id = update.get("update_id")
        if update_id is not None and not state.mark_seen(self.home, f"u{update_id}"):
            self.log(f"DUPLICATE u{update_id}")
            return "DUPLICATE"
        if "callback_query" in update:
            return self.handle_callback(update["callback_query"] or {})
        if "message" in update:
            return self.handle_message(update["message"] or {})
        return "NOOP"

    def poll_once(self, *, timeout: int = POLL_TIMEOUT_SECONDS) -> list[str]:
        offset = state.read_offset(self.home)
        if offset is None:
            return ["BACKLOG_SKIPPED"] if self.skip_backlog() else ["POLL_FAILED"]
        payload = self.client.call("getUpdates", offset=offset, timeout=timeout,
                                   allowed_updates=list(ALLOWED_UPDATES))
        updates = payload.get("result")
        if not isinstance(updates, list):
            return ["POLL_EMPTY"]
        actions: list[str] = []
        for update in updates:
            if not isinstance(update, dict):
                continue
            actions.append(self.handle_update(update))
            if update.get("update_id") is not None:
                state.write_offset(self.home, int(update["update_id"]) + 1)
        return actions

    def skip_backlog(self) -> bool:
        """First run: read only the newest update and start after it."""
        payload = self.client.call("getUpdates", offset=-1, timeout=0,
                                   allowed_updates=list(ALLOWED_UPDATES))
        updates = payload.get("result")
        if not isinstance(updates, list):
            return False
        last = int(updates[-1]["update_id"]) if updates else -1
        state.write_offset(self.home, last + 1 if updates else 0)
        self.log(f"BACKLOG_SKIPPED through={last}")
        return True

    def maybe_digest(self, *, now: datetime | None = None) -> list[str]:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        day = moment.strftime("%Y-%m-%d")
        if not notify.due(self.summary_at, now=moment,
                          last_sent_day=state.summary_sent_on(self.home)):
            return []
        data = notify.digest(self.store, now=moment)
        sent: list[str] = []
        for message in notify.digest_message(data, lang=self.lang):
            self.say(message.render())
            sent.append(message.kind)
        state.mark_summary_sent(self.home, day)
        self.log(f"DIGEST {data.as_dict()}")
        return sent

    def run(self, *, once: bool = False, timeout: int = POLL_TIMEOUT_SECONDS,
            sleep: Callable[[float], None] = time.sleep) -> list[str]:
        actions: list[str] = []
        while True:
            try:
                actions.extend(self.poll_once(timeout=timeout))
                actions.extend(self.maybe_digest())
            except FactoryError as error:
                self.log(f"POLL_FAILED {error.code}")
                if once:
                    return actions + [f"FAILED:{error.code}"]
                sleep(BACKOFF_SECONDS)
            if once:
                return actions
