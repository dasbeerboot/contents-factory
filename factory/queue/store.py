"""Queue CRUD over ~/.contents-factory/queue/YYYY-MM-DD.json. Sole writer of those files."""
from __future__ import annotations

import json
import os
import random
import string
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..errors import FactoryError
from ..spec.queue import (EXTRA_KEY, EXTRA_VALUE_MAX, IN_FLIGHT, QUEUE_ID, RESUMABLE,
                          QueueItem, parse_stamp, utc_stamp)

DAY_FILE = "%s.json"
_ALPHABET = string.ascii_lowercase + string.digits


def new_item_id(*, now: datetime | None = None, rng: random.Random | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source = rng or random
    suffix = "".join(source.choice(_ALPHABET) for _ in range(4))
    return f"q_{moment.strftime('%Y%m%d%H%M%S')}_{suffix}"


class QueueStore:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.directory = self.home / "queue"

    def day_path(self, day: str) -> Path:
        return self.directory / (DAY_FILE % day)

    def days(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def _read_day(self, day: str) -> list[QueueItem]:
        path = self.day_path(day)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return []
        except ValueError:
            raise FactoryError("QUEUE_FILE_INVALID", day) from None
        rows = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise FactoryError("QUEUE_FILE_INVALID", day)
        items: list[QueueItem] = []
        for row in rows:
            if not isinstance(row, dict):
                raise FactoryError("QUEUE_FILE_INVALID", day)
            try:
                items.append(QueueItem(**row))
            except ValueError:
                raise FactoryError("QUEUE_ITEM_INVALID", str(row.get("id") or "")[:32]) from None
        return items

    def _write_day(self, day: str, items: Iterable[QueueItem]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"day": day,
                   "items": [item.model_dump(mode="json") for item in items]}
        handle, name = tempfile.mkstemp(prefix=".queue-", dir=str(self.directory))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as target:
                json.dump(payload, target, ensure_ascii=False, indent=2, sort_keys=True)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.day_path(day))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def add(self, item: QueueItem) -> QueueItem:
        day = item.day()
        items = self._read_day(day)
        if any(existing.id == item.id for existing in items):
            raise FactoryError("QUEUE_ID_DUPLICATE", item.id)
        items.append(item)
        items.sort(key=lambda row: (row.scheduled_at, row.id))
        self._write_day(day, items)
        return item

    def list(self, *, day: str | None = None, profile: str | None = None,
             platform: str | None = None, published: bool | None = None) -> list[QueueItem]:
        days = [day] if day else self.days()
        out: list[QueueItem] = []
        for name in days:
            for item in self._read_day(name):
                if profile and item.profile != profile:
                    continue
                if platform and item.platform != platform:
                    continue
                if published is not None and item.published != published:
                    continue
                out.append(item)
        out.sort(key=lambda row: (row.scheduled_at, row.id))
        return out

    def get(self, item_id: str) -> QueueItem:
        if not QUEUE_ID.match(item_id):
            raise FactoryError("QUEUE_ID_INVALID", item_id[:32])
        for item in self.list():
            if item.id == item_id:
                return item
        raise FactoryError("QUEUE_ITEM_MISSING", item_id)

    def mutate(self, item_id: str, change: Callable[[QueueItem], QueueItem]) -> QueueItem:
        current = self.get(item_id)
        updated = change(current.model_copy(deep=True))
        if updated.id != item_id:
            raise FactoryError("QUEUE_ID_IMMUTABLE", item_id)
        old_day, new_day = current.day(), updated.day()
        items = [row for row in self._read_day(old_day) if row.id != item_id]
        if old_day == new_day:
            items.append(updated)
            items.sort(key=lambda row: (row.scheduled_at, row.id))
            self._write_day(old_day, items)
        else:
            self._write_day(old_day, items)
            target = self._read_day(new_day)
            target.append(updated)
            target.sort(key=lambda row: (row.scheduled_at, row.id))
            self._write_day(new_day, target)
        return updated

    def approve(self, item_id: str) -> QueueItem:
        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            item.approved = True
            return item
        return self.mutate(item_id, change)

    def unapprove(self, item_id: str) -> QueueItem:
        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            item.approved = False
            item.armed = False
            item.arm_reason = None
            return item
        return self.mutate(item_id, change)

    def arm(self, item_id: str, reason: str) -> QueueItem:
        text = str(reason or "").strip()
        if len(text) < 4:
            raise FactoryError("ARM_REASON_REQUIRED", item_id)

        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            if not item.approved:
                raise FactoryError("ARM_NEEDS_APPROVAL", item_id)
            item.armed = True
            item.arm_reason = text[:160]
            return item
        return self.mutate(item_id, change)

    def disarm(self, item_id: str) -> QueueItem:
        def change(item: QueueItem) -> QueueItem:
            item.armed = False
            item.arm_reason = None
            return item
        return self.mutate(item_id, change)

    def reschedule(self, item_id: str, scheduled_at: str, *, carried: bool = False) -> QueueItem:
        parse_stamp(scheduled_at)

        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            item.scheduled_at = scheduled_at
            if carried:
                item.carry_count += 1
            return item
        return self.mutate(item_id, change)

    def set_media_url(self, item_id: str, index: int, public_url: str) -> QueueItem:
        """Record where one media entry got hosted. The platforms fetch media server side,
        so this URL is what actually gets posted; it is written here and nowhere else."""
        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            if item.phase != "ready":
                # The fingerprint covers media urls, so swapping one mid delivery would
                # trip DELIVERY_CONTENT_CHANGED later instead of here.
                raise FactoryError("DELIVERY_IN_PROGRESS", item.phase)
            if not 0 <= index < len(item.media):
                raise FactoryError("MEDIA_INDEX_INVALID", str(index))
            if not str(public_url).startswith("https://"):
                raise FactoryError("MEDIA_PUBLIC_URL_INVALID")
            item.media[index].public_url = public_url
            return item
        return self.mutate(item_id, change)

    def set_extra(self, item_id: str, key: str, value: str) -> QueueItem:
        """Write one `extra` field. Used by the review channels to hand a correction
        back to the wizard, which is why it refuses anything already in delivery:
        `extra` is part of the content fingerprint."""
        if not EXTRA_KEY.match(str(key or "")):
            raise FactoryError("EXTRA_KEY_INVALID", str(key)[:32] or "empty")
        text = str(value or "")
        if len(text) > EXTRA_VALUE_MAX:
            raise FactoryError("EXTRA_VALUE_TOO_LONG", key)

        def change(item: QueueItem) -> QueueItem:
            if item.published:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            if item.phase != "ready":
                raise FactoryError("DELIVERY_IN_PROGRESS", item.phase)
            item.extra[str(key)] = text
            return item
        return self.mutate(item_id, change)

    def mark_published(self, item_id: str, published_id: str | None,
                       *, now: datetime | None = None) -> QueueItem:
        def change(item: QueueItem) -> QueueItem:
            item.published = True
            item.published_id = published_id
            item.published_at = utc_stamp(now or datetime.now(timezone.utc))
            item.last_error = None
            item.armed = False
            item.phase = "published"
            return item
        return self.mutate(item_id, change)

    # ---------- delivery phase machine ----------

    def begin_delivery(self, item_id: str, *, now: datetime | None = None) -> QueueItem:
        """ready or container -> creating. Stamps the content hash on first attempt."""
        moment = now or datetime.now(timezone.utc)

        def change(item: QueueItem) -> QueueItem:
            if item.published or item.phase == "published":
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            if item.phase not in RESUMABLE:
                raise FactoryError("DELIVERY_UNCONFIRMED", item.phase)
            if item.content_changed():
                raise FactoryError("DELIVERY_CONTENT_CHANGED", item_id)
            if not item.content_sha256:
                item.content_sha256 = item.fingerprint()
            if item.phase == "container" and item.container_expired(now=moment):
                item.container_id = None
                item.container_at = None
                item.container_regen += 1
            item.phase = "creating"
            item.attempts += 1
            return item
        return self.mutate(item_id, change)

    def container_created(self, item_id: str, container_id: str,
                          *, now: datetime | None = None) -> QueueItem:
        identifier = str(container_id or "").strip()
        if not identifier:
            raise FactoryError("CONTAINER_ID_INVALID", item_id)
        moment = utc_stamp(now or datetime.now(timezone.utc))

        def change(item: QueueItem) -> QueueItem:
            item.phase = "container"
            item.container_id = identifier
            item.container_at = moment
            return item
        return self.mutate(item_id, change)

    def begin_publish(self, item_id: str, container_id: str | None = None) -> QueueItem:
        def change(item: QueueItem) -> QueueItem:
            if container_id:
                item.container_id = str(container_id)
            item.phase = "publishing"
            return item
        return self.mutate(item_id, change)

    def delivery_failed(self, item_id: str, code: str) -> QueueItem:
        """The remote rejected us, so nothing was posted: back to ready for a retry."""
        def change(item: QueueItem) -> QueueItem:
            item.phase = "ready"
            item.last_error = str(code)[:64]
            return item
        return self.mutate(item_id, change)

    def delivery_unknown(self, item_id: str, code: str) -> QueueItem:
        """The response was lost. Never auto retried: only queue reopen clears this."""
        def change(item: QueueItem) -> QueueItem:
            item.phase = "unknown"
            item.last_error = str(code)[:64]
            return item
        return self.mutate(item_id, change)

    def reopen_delivery(self, item_id: str, reason: str) -> QueueItem:
        """Manual recovery, refused when anything suggests the post may exist."""
        text = str(reason or "").strip()
        if len(text) < 4:
            raise FactoryError("REOPEN_REASON_REQUIRED", item_id)

        def change(item: QueueItem) -> QueueItem:
            if item.published or item.published_id:
                raise FactoryError("QUEUE_ALREADY_PUBLISHED", item_id)
            if item.container_id:
                raise FactoryError("DELIVERY_MAY_BE_PUBLISHED", item_id)
            item.phase = "ready"
            item.last_error = f"reopened:{text[:48]}"
            return item
        return self.mutate(item_id, change)

    def resume_container(self, item_id: str) -> str | None:
        item = self.get(item_id)
        return item.container_id if item.phase == "container" else None

    def record_error(self, item_id: str, code: str) -> QueueItem:
        """Bookkeeping for failures outside a delivery attempt (gates, caps, drops)."""
        def change(item: QueueItem) -> QueueItem:
            item.attempts += 1
            item.last_error = str(code)[:64]
            return item
        return self.mutate(item_id, change)

    def purge(self, *, before: str | None = None, published_only: bool = True) -> int:
        removed = 0
        for day in self.days():
            items = self._read_day(day)
            keep = []
            for item in items:
                stale = before is None or item.scheduled_at < before
                if stale and (item.published or not published_only):
                    removed += 1
                    continue
                keep.append(item)
            if len(keep) != len(items):
                if keep:
                    self._write_day(day, keep)
                else:
                    self.day_path(day).unlink(missing_ok=True)
        return removed

    def published_count(self, *, profile: str, platform: str, day: str) -> int:
        return len([item for item in self._read_day(day)
                    if item.published and item.profile == profile
                    and item.platform == platform])

    def upcoming(self, *, now: datetime, days: int = 7) -> list[QueueItem]:
        horizon = now + timedelta(days=days)
        return [item for item in self.list(published=False)
                if now <= item.scheduled_dt() <= horizon]


class StoreTracker:
    """DeliveryTracker bound to one queue item. The store stays the only writer.

    `now` comes from the caller so container timestamps share one clock with the tick
    that is judging container expiry.
    """

    def __init__(self, store: QueueStore, item_id: str,
                 now: datetime | None = None) -> None:
        self.store = store
        self.item_id = item_id
        self.now = now

    def resume_container(self) -> str | None:
        item = self.store.get(self.item_id)
        return item.container_id if item.container_id else None

    def container_created(self, container_id: str) -> None:
        self.store.container_created(self.item_id, container_id, now=self.now)

    def begin_publish(self, container_id: str) -> None:
        self.store.begin_publish(self.item_id, container_id)
