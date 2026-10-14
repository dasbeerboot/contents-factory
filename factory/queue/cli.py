"""factory queue ... — the only writer of queue state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import i18n
from ..config import Config, repo_root
from ..errors import FactoryError
from ..scheduler.tick import next_slot
from ..spec.brief import load_brief
from ..spec.profile import load_profile
from ..spec.queue import Media, QueueItem, parse_stamp, utc_stamp
from .store import QueueStore, new_item_id


def build_parser(parent: argparse._SubParsersAction) -> None:
    queue = parent.add_parser("queue", help="queue add/list/approve/unapprove/arm/disarm/purge")
    actions = queue.add_subparsers(dest="queue_command", required=True)

    add = actions.add_parser("add")
    add.add_argument("--brief")
    add.add_argument("--output", type=int, default=0)
    add.add_argument("--platform", required=True)
    add.add_argument("--profile")
    add.add_argument("--kind", default="text", choices=["text", "image", "video"])
    add.add_argument("--text")
    add.add_argument("--media", action="append", default=[])
    add.add_argument("--public-url", action="append", default=[])
    add.add_argument("--slot", default="next", help="next | YYYY-MM-DDTHH:MM:SSZ")
    add.add_argument("--late-policy", choices=["carry_once", "drop"])

    listing = actions.add_parser("list")
    listing.add_argument("--day")
    listing.add_argument("--profile")
    listing.add_argument("--platform")
    listing.add_argument("--pending", action="store_true")
    listing.add_argument("--json", action="store_true")

    for name in ("approve", "unapprove", "disarm"):
        single = actions.add_parser(name)
        single.add_argument("--item", required=True)

    arm = actions.add_parser("arm")
    arm.add_argument("--item", required=True)
    arm.add_argument("--reason", required=True)

    reopen = actions.add_parser("reopen",
                                help="clear an unknown delivery phase after checking "
                                     "the platform by hand")
    reopen.add_argument("--item", required=True)
    reopen.add_argument("--reason", required=True)

    purge = actions.add_parser("purge")
    purge.add_argument("--before", help="YYYY-MM-DDTHH:MM:SSZ")
    purge.add_argument("--include-unpublished", action="store_true")


def _resolve_slot(raw: str, *, slots: tuple[str, ...], now: datetime) -> str:
    if raw and raw != "next":
        parse_stamp(raw)
        return raw
    target = next_slot(slots, now)
    if target is None:
        raise FactoryError("NO_SLOT_CONFIGURED")
    return utc_stamp(target)


def _item_from_brief(args: argparse.Namespace, home: Path) -> tuple[str, str]:
    brief = load_brief(home, args.brief)
    if args.output >= len(brief.outputs):
        raise FactoryError("BRIEF_OUTPUT_MISSING", str(args.output))
    output = brief.outputs[args.output]
    return output.text or "", output.path or ""


def run(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    store = QueueStore(home)
    translate = i18n.Translator(config.ui_lang)
    now = datetime.now(timezone.utc)

    if args.queue_command == "add":
        profile_name = args.profile or config.default_profile
        profile = load_profile(profile_name, home=home, repo=repo_root())
        platform = profile.platform(args.platform)
        text, media_path = "", ""
        if args.brief:
            text, media_path = _item_from_brief(args, home)
        if args.text:
            text = args.text
        media: list[Media] = []
        paths = list(args.media) or ([media_path] if media_path else [])
        urls = list(args.public_url)
        for index, path in enumerate(paths):
            if not path:
                continue
            kind = "video" if path.lower().endswith((".mp4", ".mov", ".m4v")) else "image"
            media.append(Media(path=path, kind=kind,
                               public_url=urls[index] if index < len(urls) else None))
        item = QueueItem(
            id=new_item_id(now=now),
            brief_id=args.brief,
            profile=profile_name,
            platform=args.platform,
            kind=args.kind,
            text=text,
            media=media,
            scheduled_at=_resolve_slot(args.slot, slots=tuple(platform.slots), now=now),
            late_policy=args.late_policy or platform.late_policy,
        )
        store.add(item)
        print(translate("queue.added", item_id=item.id, platform=item.platform,
                        scheduled_at=item.scheduled_at))
        return 0

    if args.queue_command == "list":
        items = store.list(day=args.day, profile=args.profile, platform=args.platform,
                           published=False if args.pending else None)
        if args.json:
            print(json.dumps([item.model_dump(mode="json") for item in items],
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if not items:
            print("(empty)")
            return 0
        for item in items:
            flags = "".join(["A" if item.approved else "-",
                             "R" if item.armed else "-",
                             "P" if item.published else "-"])
            print(f"{item.id}  {item.scheduled_at}  {item.platform:<10} {flags}  "
                  f"{item.phase:<10} {item.profile}  "
                  f"{item.text[:40].replace(chr(10), ' ')}")
        return 0

    if args.queue_command == "approve":
        print(store.approve(args.item).id)
        return 0
    if args.queue_command == "unapprove":
        print(store.unapprove(args.item).id)
        return 0
    if args.queue_command == "arm":
        item = store.arm(args.item, args.reason)
        print(f"{item.id} armed: {item.arm_reason}")
        return 0
    if args.queue_command == "disarm":
        print(store.disarm(args.item).id)
        return 0
    if args.queue_command == "reopen":
        item = store.reopen_delivery(args.item, args.reason)
        print(translate("queue.reopened", item_id=item.id, reason=args.reason))
        return 0
    if args.queue_command == "purge":
        before = args.before or utc_stamp(now)
        parse_stamp(before)
        removed = store.purge(before=before, published_only=not args.include_unpublished)
        print(f"purged {removed}")
        return 0
    raise FactoryError("QUEUE_COMMAND_UNKNOWN", str(args.queue_command)[:32])
