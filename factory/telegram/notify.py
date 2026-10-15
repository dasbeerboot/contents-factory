"""What is allowed to buzz a phone, and what is not.

A pipeline that reports every success trains its owner to ignore it, and then the one
message that mattered is ignored too. So the policy is a closed list rather than a
log level:

  🚨 immediate   a publish failed, a delivery is unconfirmed, a token is about to die
  📊 once a day  yesterday published, today scheduled, confirmations waiting
  (silence)      everything else, successes included

`classify` is the only place that decides, and `messages_for` is the only thing the
serve loop and the tick send. A new event type is silent until it is listed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import i18n
from ..publishers.base import failure_mode
from ..queue.store import QueueStore
from ..scheduler.tick import TickReport

ALERT = "alert"
SUMMARY = "summary"
QUIET = "quiet"

ALERT_SIGIL = "\U0001f6a8"
SUMMARY_SIGIL = "\U0001f4ca"

# Only these reach a phone unprompted. Anything absent is silence by default.
ALERT_KINDS: frozenset[str] = frozenset({
    "publish_failed", "delivery_unknown", "token_expiry", "serve_crashed",
})
SUMMARY_KINDS: frozenset[str] = frozenset({"daily_summary"})


@dataclass(frozen=True)
class Message:
    kind: str
    text: str

    def sigil(self) -> str:
        return ALERT_SIGIL if classify(self.kind) == ALERT else SUMMARY_SIGIL

    def render(self) -> str:
        return f"{self.sigil()} {self.text}"


def classify(kind: str) -> str:
    name = str(kind or "")
    if name in ALERT_KINDS:
        return ALERT
    if name in SUMMARY_KINDS:
        return SUMMARY
    return QUIET


def allowed(kind: str) -> bool:
    return classify(kind) != QUIET


def messages_for(kind: str, text: str) -> list[Message]:
    """One event in, zero or one message out. Quiet events produce nothing."""
    return [Message(kind, text)] if allowed(kind) else []


def from_tick(report: TickReport, *, lang: str = "en") -> list[Message]:
    """Alerts a tick earned. A published item is deliberately not one of them."""
    translate = i18n.Translator(lang)
    out: list[Message] = []
    for item_id, code in report.errors:
        if failure_mode(code) == "unknown":
            out.extend(messages_for("delivery_unknown",
                                    translate("telegram.alert.delivery_unknown",
                                              item_id=item_id, code=code)))
        else:
            out.extend(messages_for("publish_failed",
                                    translate("telegram.alert.publish_failed",
                                              item_id=item_id, code=code)))
    for warning in report.warnings:
        # token_refresh already rendered these in ui_lang; the policy only decides
        # whether they travel.
        out.extend(messages_for("token_expiry", warning))
    return out


@dataclass(frozen=True)
class Digest:
    published_yesterday: int
    scheduled_today: int
    awaiting_confirmation: int

    def as_dict(self) -> dict[str, int]:
        return {"published_yesterday": self.published_yesterday,
                "scheduled_today": self.scheduled_today,
                "awaiting_confirmation": self.awaiting_confirmation}


def digest(store: QueueStore, *, now: datetime) -> Digest:
    moment = now.astimezone(timezone.utc)
    today = moment.strftime("%Y-%m-%d")
    yesterday = (moment - timedelta(days=1)).strftime("%Y-%m-%d")
    published = len([item for item in store.list(day=yesterday) if item.published])
    scheduled = len([item for item in store.list(day=today) if not item.published])
    waiting = len([item for item in store.list(published=False) if not item.approved])
    return Digest(published, scheduled, waiting)


def digest_message(data: Digest, *, lang: str = "en") -> list[Message]:
    text = i18n.get("telegram.summary.body", lang, **data.as_dict())
    return messages_for("daily_summary", text)


def due(summary_at: str, *, now: datetime, last_sent_day: str) -> bool:
    """True when the configured UTC time has passed today and nothing went out yet."""
    moment = now.astimezone(timezone.utc)
    today = moment.strftime("%Y-%m-%d")
    if last_sent_day == today:
        return False
    hour, _, rest = str(summary_at or "").partition(":")
    try:
        target = moment.replace(hour=int(hour), minute=int(rest[:2] or 0),
                                second=0, microsecond=0)
    except ValueError:
        return False
    return moment >= target
