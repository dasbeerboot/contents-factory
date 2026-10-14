"""Queue scan and publish decision. Approval is not publication; slots are.

Decision order (publishing-rules §6):
  published            -> ALREADY_PUBLISHED
  not approved         -> NOT_APPROVED
  not armed            -> NOT_ARMED
  before slot          -> NOT_DUE
  past slot + grace    -> late_policy: carry_once once, then drop
  daily cap reached    -> DAILY_CAP
  repeat_guard blocks  -> REPEAT_BLOCK
  otherwise            -> publish
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Protocol, Sequence

from ..spec.queue import QueueItem, utc_stamp

Action = Literal["publish", "skip", "carry", "drop"]
LATE_GRACE_SECONDS = 900


@dataclass(frozen=True)
class Decision:
    item_id: str
    action: Action
    reason: str
    scheduled_at: str | None = None


@dataclass
class TickContext:
    now: datetime
    slots: Sequence[str] = ()
    daily_cap: int = 1
    published_today: int = 0
    late_grace_seconds: int = LATE_GRACE_SECONDS


class RepeatGuard(Protocol):
    def __call__(self, text: str, *, profile: str) -> tuple[bool, str]:
        ...


def parse_slot(slot: str) -> tuple[int, int]:
    hour, rest = slot.split(":", 1)
    return int(hour), int(rest[:2])


def next_slot(slots: Sequence[str], after: datetime) -> datetime | None:
    """Earliest slot strictly after `after`, scanning today then the next 7 days."""
    if not slots:
        return None
    base = after.astimezone(timezone.utc)
    for offset in range(0, 8):
        day = (base + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        for slot in sorted(slots):
            hour, minute = parse_slot(slot)
            candidate = day.replace(hour=hour, minute=minute)
            if candidate > base:
                return candidate
    return None


def decide(item: QueueItem, context: TickContext) -> Decision:
    if item.published:
        return Decision(item.id, "skip", "ALREADY_PUBLISHED")
    if not item.approved:
        return Decision(item.id, "skip", "NOT_APPROVED")
    if not item.armed:
        return Decision(item.id, "skip", "NOT_ARMED")

    due = item.scheduled_dt()
    now = context.now.astimezone(timezone.utc)
    if now < due:
        return Decision(item.id, "skip", "NOT_DUE")

    if (now - due).total_seconds() > context.late_grace_seconds:
        if item.late_policy == "drop":
            return Decision(item.id, "drop", "LATE_DROPPED")
        if item.carry_count >= 1:
            return Decision(item.id, "drop", "LATE_CARRY_EXHAUSTED")
        target = next_slot(context.slots, now)
        if target is None:
            return Decision(item.id, "drop", "LATE_NO_SLOT")
        return Decision(item.id, "carry", "LATE_CARRIED", utc_stamp(target))

    if context.daily_cap and context.published_today >= context.daily_cap:
        return Decision(item.id, "skip", "DAILY_CAP")
    return Decision(item.id, "publish", "DUE")


@dataclass
class TickReport:
    decisions: list[Decision] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        lines = [f"{row.item_id} {row.action.upper()} {row.reason}" for row in self.decisions]
        lines.extend(f"WARN {text}" for text in self.warnings)
        return lines


def run(store, *, now: datetime,
        slots_for: Callable[[QueueItem], Sequence[str]],
        cap_for: Callable[[QueueItem], int],
        publish: Callable[[QueueItem], tuple[bool, str, str | None]],
        guard: RepeatGuard | None = None,
        late_grace_seconds: int = LATE_GRACE_SECONDS) -> TickReport:
    """Walk every unpublished item, apply `decide`, then publish the survivors."""
    report = TickReport()
    moment = now.astimezone(timezone.utc)
    today = moment.strftime("%Y-%m-%d")
    counters: dict[tuple[str, str], int] = {}
    for item in store.list(published=False):
        key = (item.profile, item.platform)
        if key not in counters:
            counters[key] = store.published_count(profile=item.profile,
                                                  platform=item.platform, day=today)
        context = TickContext(now=moment, slots=tuple(slots_for(item)),
                              daily_cap=cap_for(item), published_today=counters[key],
                              late_grace_seconds=late_grace_seconds)
        decision = decide(item, context)
        if decision.action == "publish" and guard is not None and item.text.strip():
            allowed, reason = guard(item.text, profile=item.profile)
            if not allowed:
                decision = Decision(item.id, "skip", f"REPEAT_BLOCK:{reason}"[:120])
                store.record_error(item.id, "REPEAT_BLOCK")
        report.decisions.append(decision)

        if decision.action == "carry" and decision.scheduled_at:
            store.reschedule(item.id, decision.scheduled_at, carried=True)
        elif decision.action == "drop":
            store.unapprove(item.id)
            store.record_error(item.id, decision.reason)
        elif decision.action == "publish":
            ok, code, published_id = publish(item)
            if ok:
                store.mark_published(item.id, published_id, now=moment)
                counters[key] += 1
                report.published.append(item.id)
            else:
                store.record_error(item.id, code)
                report.errors.append((item.id, code))
    return report
