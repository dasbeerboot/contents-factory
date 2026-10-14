"""Phase machine, ported from threads_delivery.py. The invariant: never double post."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.publishers.base import failure_mode
from factory.queue.store import QueueStore, StoreTracker
from factory.scheduler import tick
from tests.conftest import make_item

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
SLOTS = ("12:00Z", "23:00Z")


def armed(**overrides: object):
    base: dict[str, object] = {"approved": True, "armed": True,
                               "arm_reason": "owner approved in chat"}
    base.update(overrides)
    return make_item(**base)


def seeded(home: Path, **overrides: object) -> tuple[QueueStore, str]:
    store = QueueStore(home)
    item = store.add(armed(**overrides))
    return store, item.id


def run(store: QueueStore, publish, *, now: datetime = NOW) -> tick.TickReport:
    return tick.run(store, now=now, slots_for=lambda _row: SLOTS,
                    cap_for=lambda _row: 5, publish=publish)


def test_new_item_starts_ready(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    item = store.get(item_id)
    assert item.phase == "ready"
    assert item.container_id is None
    assert item.content_sha256 == ""


def test_begin_delivery_stamps_the_hash_once(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    started = store.begin_delivery(item_id, now=NOW)
    assert started.phase == "creating"
    assert started.content_sha256 == started.fingerprint()
    assert started.attempts == 1
    store.delivery_failed(item_id, "HTTP_429")
    again = store.begin_delivery(item_id, now=NOW)
    assert again.attempts == 2
    assert again.content_sha256 == started.content_sha256


def test_begin_delivery_refuses_an_in_flight_item(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    with pytest.raises(FactoryError) as error:
        store.begin_delivery(item_id, now=NOW)
    assert error.value.code == "DELIVERY_UNCONFIRMED"


def test_begin_delivery_refuses_changed_content(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.delivery_failed(item_id, "HTTP_400")
    store.mutate(item_id, lambda row: row.model_copy(update={"text": "rewritten body"}))
    with pytest.raises(FactoryError) as error:
        store.begin_delivery(item_id, now=NOW)
    assert error.value.code == "DELIVERY_CONTENT_CHANGED"


def test_container_then_publish_transitions(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    created = store.container_created(item_id, "17891111", now=NOW)
    assert (created.phase, created.container_id) == ("container", "17891111")
    assert created.container_at == "2026-10-15T12:00:00Z"
    publishing = store.begin_publish(item_id, "17891111")
    assert publishing.phase == "publishing"
    published = store.mark_published(item_id, "17892222", now=NOW)
    assert published.phase == "published"
    assert published.published_id == "17892222"


def test_container_expiry_forces_regeneration(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.container_created(item_id, "17891111", now=NOW)
    store.delivery_failed(item_id, "HTTP_429")
    store.mutate(item_id, lambda row: row.model_copy(update={"phase": "container"}))
    fresh = store.begin_delivery(item_id, now=NOW + timedelta(hours=2))
    assert fresh.container_id == "17891111"
    assert fresh.container_regen == 0
    store.delivery_failed(item_id, "HTTP_429")
    store.mutate(item_id, lambda row: row.model_copy(update={"phase": "container"}))
    expired = store.begin_delivery(item_id, now=NOW + timedelta(hours=25))
    assert expired.container_id is None
    assert expired.container_regen == 1


def test_reopen_refuses_when_a_container_exists(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.container_created(item_id, "17891111", now=NOW)
    store.delivery_unknown(item_id, "NETWORK_UNREACHABLE")
    with pytest.raises(FactoryError) as error:
        store.reopen_delivery(item_id, "checked the profile by hand")
    assert error.value.code == "DELIVERY_MAY_BE_PUBLISHED"


def test_reopen_clears_unknown_when_nothing_was_submitted(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.delivery_unknown(item_id, "NETWORK_UNREACHABLE")
    with pytest.raises(FactoryError) as short:
        store.reopen_delivery(item_id, "x")
    assert short.value.code == "REOPEN_REASON_REQUIRED"
    reopened = store.reopen_delivery(item_id, "checked the profile, nothing posted")
    assert reopened.phase == "ready"
    assert reopened.last_error.startswith("reopened:")


def test_failure_mode_classification() -> None:
    assert failure_mode("HTTP_401") == "retry"
    assert failure_mode("MEDIA_PUBLIC_URL_REQUIRED") == "retry"
    assert failure_mode("NAVER_SESSION_MISSING") == "retry"
    assert failure_mode("PUBLISH_NOT_SENT:CATEGORY") == "retry"
    assert failure_mode("CONTAINER_IN_PROGRESS") == "hold"
    assert failure_mode("HTTP_500") == "unknown"
    assert failure_mode("NETWORK_UNREACHABLE") == "unknown"
    assert failure_mode("PUBLISH_OUTCOME_UNCERTAIN:PUBLISH") == "unknown"
    assert failure_mode("SOMETHING_NEW") == "unknown"


def test_tick_parks_unknown_and_never_retries(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    calls: list[str] = []

    def publish(row, _tracker):
        calls.append(row.id)
        return False, "HTTP_502", None

    run(store, publish)
    assert store.get(item_id).phase == "unknown"
    run(store, publish)
    assert calls == [item_id]
    second = run(store, publish)
    assert second.decisions[0].action == "skip"
    assert second.decisions[0].reason == "DELIVERY_UNCONFIRMED:unknown"


def test_tick_retries_a_rejected_item(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    attempts: list[str] = []

    def publish(row, _tracker):
        attempts.append(row.id)
        return False, "HTTP_400", None

    run(store, publish)
    assert store.get(item_id).phase == "ready"
    run(store, publish)
    assert len(attempts) == 2
    assert store.get(item_id).attempts == 2


def test_tick_resumes_a_stored_container(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    seen: list[str | None] = []

    def first(row, tracker):
        tracker.container_created("17891111")
        return False, "CONTAINER_IN_PROGRESS", None

    def second(row, tracker):
        seen.append(tracker.resume_container())
        tracker.begin_publish("17891111")
        return True, "OK", "17892222"

    run(store, first)
    held = store.get(item_id)
    assert held.phase == "container"
    assert held.container_id == "17891111"

    report = run(store, second)
    assert seen == ["17891111"]
    assert report.decisions[0].reason == "RESUME_CONTAINER"
    assert report.decisions[0].resume_container == "17891111"
    assert store.get(item_id).published_id == "17892222"
    assert store.get(item_id).phase == "published"


def test_tick_repairs_a_lost_published_flag(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.mutate(item_id, lambda row: row.model_copy(
        update={"phase": "published", "published_id": "17892222"}))
    calls: list[str] = []
    report = run(store, lambda row, _tracker: (calls.append(row.id), (True, "OK", "x"))[1])
    assert calls == []
    assert report.repaired == [item_id]
    assert report.decisions[0].reason == "PUBLISHED_FLAG_MISSING"
    repaired = store.get(item_id)
    assert repaired.published is True
    assert repaired.published_id == "17892222"


def test_tick_skips_when_content_changed_after_a_send(isolated_home: Path) -> None:
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.delivery_failed(item_id, "HTTP_400")
    store.mutate(item_id, lambda row: row.model_copy(update={"text": "different body"}))
    report = run(store, lambda _row, _tracker: (True, "OK", "1"))
    assert report.decisions[0].reason == "DELIVERY_CONTENT_CHANGED"
    assert store.get(item_id).published is False


def test_threads_send_resumes_without_creating_twice(isolated_home: Path) -> None:
    from factory.publishers import publisher_for
    store, item_id = seeded(isolated_home)
    store.begin_delivery(item_id, now=NOW)
    store.container_created(item_id, "17891111", now=NOW)
    publisher = publisher_for("threads", isolated_home,
                              env={"THREADS_ACCESS_TOKEN": "t", "THREADS_USER_ID": "1789"})
    posted: list[dict[str, str]] = []

    def fake_post_form(url: str, fields: dict[str, str], **_kwargs: object):
        posted.append({"url": url, **fields})
        return {"id": "17892222"}

    import factory.publishers.threads as threads_module
    original = threads_module.post_form
    threads_module.post_form = fake_post_form
    try:
        result = publisher.publish(store.get(item_id),
                                   tracker=StoreTracker(store, item_id))
    finally:
        threads_module.post_form = original
    assert result.ok is True
    assert len(posted) == 1
    assert posted[0]["url"].endswith("/threads_publish")
    assert posted[0]["creation_id"] == "17891111"
    assert store.get(item_id).phase == "publishing"
