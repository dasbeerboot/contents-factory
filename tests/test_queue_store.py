from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.queue.store import QueueStore, new_item_id
from tests.conftest import make_item


def test_new_item_id_shape() -> None:
    moment = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    identifier = new_item_id(now=moment)
    assert identifier.startswith("q_20261015120000_")
    assert len(identifier) == len("q_20261015120000_abcd")


def test_add_list_get(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    assert store.day_path("2026-10-15").is_file()
    assert [row.id for row in store.list()] == [item.id]
    assert store.get(item.id).text == item.text


def test_add_rejects_duplicate(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.add(make_item())
    with pytest.raises(FactoryError) as error:
        store.add(make_item())
    assert error.value.code == "QUEUE_ID_DUPLICATE"


def test_get_unknown_raises(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    with pytest.raises(FactoryError) as error:
        store.get("q_20261015120000_zzzz")
    assert error.value.code == "QUEUE_ITEM_MISSING"
    with pytest.raises(FactoryError) as bad:
        store.get("nope")
    assert bad.value.code == "QUEUE_ID_INVALID"


def test_approve_arm_requires_reason_and_approval(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    with pytest.raises(FactoryError) as needs_approval:
        store.arm(item.id, "owner said go ahead")
    assert needs_approval.value.code == "ARM_NEEDS_APPROVAL"
    store.approve(item.id)
    with pytest.raises(FactoryError) as needs_reason:
        store.arm(item.id, " ")
    assert needs_reason.value.code == "ARM_REASON_REQUIRED"
    armed = store.arm(item.id, "owner approved in chat")
    assert armed.armed is True
    assert armed.arm_reason == "owner approved in chat"


def test_unapprove_disarms(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    store.approve(item.id)
    store.arm(item.id, "owner approved in chat")
    back = store.unapprove(item.id)
    assert (back.approved, back.armed, back.arm_reason) == (False, False, None)


def test_disarm_keeps_approval(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    store.approve(item.id)
    store.arm(item.id, "owner approved in chat")
    disarmed = store.disarm(item.id)
    assert disarmed.approved is True and disarmed.armed is False


def test_reschedule_moves_day_file(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    moved = store.reschedule(item.id, "2026-10-16T23:00:00Z", carried=True)
    assert moved.carry_count == 1
    assert store.list(day="2026-10-15") == []
    assert [row.id for row in store.list(day="2026-10-16")] == [item.id]


def test_mark_published_and_counts(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    store.approve(item.id)
    store.arm(item.id, "owner approved in chat")
    published = store.mark_published(item.id, "17891234",
                                     now=datetime(2026, 10, 15, 12, 1, 0,
                                                  tzinfo=timezone.utc))
    assert published.published is True
    assert published.published_id == "17891234"
    assert published.published_at == "2026-10-15T12:01:00Z"
    assert published.armed is False
    assert store.published_count(profile="_example", platform="threads",
                                 day="2026-10-15") == 1
    with pytest.raises(FactoryError) as error:
        store.approve(item.id)
    assert error.value.code == "QUEUE_ALREADY_PUBLISHED"


def test_record_error_increments_attempts(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item())
    store.record_error(item.id, "HTTP_429")
    again = store.record_error(item.id, "HTTP_429")
    assert again.attempts == 2
    assert again.last_error == "HTTP_429"


def test_purge_only_published_by_default(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    first = store.add(make_item())
    second = store.add(make_item(id="q_20261015130000_cd34",
                                 scheduled_at="2026-10-15T13:00:00Z"))
    store.approve(first.id)
    store.arm(first.id, "owner approved in chat")
    store.mark_published(first.id, "1")
    assert store.purge(before="2026-10-16T00:00:00Z") == 1
    assert [row.id for row in store.list()] == [second.id]
    assert store.purge(before="2026-10-16T00:00:00Z", published_only=False) == 1
    assert store.list() == []
    assert not store.day_path("2026-10-15").exists()


def test_upcoming_window(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.add(make_item())
    store.add(make_item(id="q_20261101120000_ef56",
                        scheduled_at="2026-11-01T12:00:00Z"))
    upcoming = store.upcoming(now=datetime(2026, 10, 15, 11, 0, 0, tzinfo=timezone.utc))
    assert [row.scheduled_at for row in upcoming] == ["2026-10-15T12:00:00Z"]


def test_broken_day_file_is_reported(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.day_path("2026-10-15").write_text("{not json", encoding="utf-8")
    with pytest.raises(FactoryError) as error:
        store.list(day="2026-10-15")
    assert error.value.code == "QUEUE_FILE_INVALID"


def test_written_file_is_sorted_json(isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.add(make_item(id="q_20261015130000_cd34",
                        scheduled_at="2026-10-15T13:00:00Z"))
    store.add(make_item())
    raw = json.loads(store.day_path("2026-10-15").read_text(encoding="utf-8"))
    assert [row["scheduled_at"] for row in raw["items"]] == ["2026-10-15T12:00:00Z",
                                                             "2026-10-15T13:00:00Z"]
