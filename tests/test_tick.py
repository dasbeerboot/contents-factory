from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.queue.store import QueueStore
from factory.scheduler import tick
from tests.conftest import make_item

SLOTS = ("12:00Z", "23:00Z")
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def context(**overrides: object) -> tick.TickContext:
    data: dict[str, object] = {"now": NOW, "slots": SLOTS, "daily_cap": 2,
                               "published_today": 0}
    data.update(overrides)
    return tick.TickContext(**data)


def armed(**overrides: object):
    base = {"approved": True, "armed": True, "arm_reason": "owner approved in chat"}
    base.update(overrides)
    return make_item(**base)


def test_next_slot_same_day() -> None:
    target = tick.next_slot(SLOTS, datetime(2026, 10, 15, 13, 0, 0, tzinfo=timezone.utc))
    assert target == datetime(2026, 10, 15, 23, 0, 0, tzinfo=timezone.utc)


def test_next_slot_rolls_to_tomorrow() -> None:
    target = tick.next_slot(SLOTS, datetime(2026, 10, 15, 23, 30, 0, tzinfo=timezone.utc))
    assert target == datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_next_slot_without_slots() -> None:
    assert tick.next_slot((), NOW) is None


def test_published_is_skipped() -> None:
    decision = tick.decide(armed(published=True), context())
    assert (decision.action, decision.reason) == ("skip", "ALREADY_PUBLISHED")


def test_unapproved_is_skipped() -> None:
    decision = tick.decide(make_item(), context())
    assert (decision.action, decision.reason) == ("skip", "NOT_APPROVED")


def test_approved_but_not_armed_is_skipped() -> None:
    decision = tick.decide(make_item(approved=True), context())
    assert (decision.action, decision.reason) == ("skip", "NOT_ARMED")


def test_before_slot_is_not_due() -> None:
    decision = tick.decide(armed(), context(now=NOW - timedelta(minutes=1)))
    assert (decision.action, decision.reason) == ("skip", "NOT_DUE")


def test_at_slot_publishes() -> None:
    decision = tick.decide(armed(), context())
    assert (decision.action, decision.reason) == ("publish", "DUE")


def test_inside_grace_still_publishes() -> None:
    decision = tick.decide(armed(), context(now=NOW + timedelta(seconds=899)))
    assert decision.action == "publish"


def test_late_carry_once_moves_to_next_slot() -> None:
    decision = tick.decide(armed(), context(now=NOW + timedelta(hours=2)))
    assert (decision.action, decision.reason) == ("carry", "LATE_CARRIED")
    assert decision.scheduled_at == "2026-10-15T23:00:00Z"


def test_late_carry_is_single_use() -> None:
    decision = tick.decide(armed(carry_count=1), context(now=NOW + timedelta(hours=2)))
    assert (decision.action, decision.reason) == ("drop", "LATE_CARRY_EXHAUSTED")


def test_late_drop_policy() -> None:
    decision = tick.decide(armed(late_policy="drop"), context(now=NOW + timedelta(hours=2)))
    assert (decision.action, decision.reason) == ("drop", "LATE_DROPPED")


def test_late_without_slots_drops() -> None:
    decision = tick.decide(armed(), context(now=NOW + timedelta(hours=2), slots=()))
    assert (decision.action, decision.reason) == ("drop", "LATE_NO_SLOT")


def test_daily_cap_blocks() -> None:
    decision = tick.decide(armed(), context(daily_cap=1, published_today=1))
    assert (decision.action, decision.reason) == ("skip", "DAILY_CAP")


def test_zero_cap_means_unlimited() -> None:
    decision = tick.decide(armed(), context(daily_cap=0, published_today=9))
    assert decision.action == "publish"


def test_run_publishes_and_marks(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(armed())
    calls: list[str] = []

    def publish(row):
        calls.append(row.id)
        return True, "OK", "1789"

    report = tick.run(store, now=NOW, slots_for=lambda _row: SLOTS,
                      cap_for=lambda _row: 2, publish=publish)
    assert calls == [item.id]
    assert report.published == [item.id]
    assert store.get(item.id).published_id == "1789"


def test_run_records_publish_failure(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(armed())
    report = tick.run(store, now=NOW, slots_for=lambda _row: SLOTS,
                      cap_for=lambda _row: 2,
                      publish=lambda _row: (False, "HTTP_429", None))
    assert report.errors == [(item.id, "HTTP_429")]
    stored = store.get(item.id)
    assert stored.published is False and stored.last_error == "HTTP_429"


def test_run_applies_repeat_guard_before_publish(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(armed())
    report = tick.run(store, now=NOW, slots_for=lambda _row: SLOTS,
                      cap_for=lambda _row: 2,
                      publish=lambda _row: (True, "OK", "1"),
                      guard=lambda _text, profile: (False, "exact_sentence(x)"))
    assert report.published == []
    assert report.decisions[0].action == "skip"
    assert report.decisions[0].reason.startswith("REPEAT_BLOCK")
    assert store.get(item.id).last_error == "REPEAT_BLOCK"


def test_run_carries_then_drops(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(armed())
    late = NOW + timedelta(hours=2)
    tick.run(store, now=late, slots_for=lambda _row: SLOTS, cap_for=lambda _row: 2,
             publish=lambda _row: (True, "OK", "1"))
    carried = store.get(item.id)
    assert carried.scheduled_at == "2026-10-15T23:00:00Z"
    assert carried.carry_count == 1
    tick.run(store, now=datetime(2026, 10, 16, 2, 0, 0, tzinfo=timezone.utc),
             slots_for=lambda _row: SLOTS, cap_for=lambda _row: 2,
             publish=lambda _row: (True, "OK", "1"))
    dropped = store.get(item.id)
    assert dropped.approved is False and dropped.last_error == "LATE_CARRY_EXHAUSTED"


def test_run_respects_cap_across_items(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.add(armed())
    store.add(armed(id="q_20261015120500_cd34", scheduled_at="2026-10-15T12:05:00Z"))
    report = tick.run(store, now=NOW + timedelta(minutes=6), slots_for=lambda _row: SLOTS,
                      cap_for=lambda _row: 1, publish=lambda _row: (True, "OK", "1"))
    actions = [row.action for row in report.decisions]
    assert actions == ["publish", "skip"]
    assert report.decisions[1].reason == "DAILY_CAP"
