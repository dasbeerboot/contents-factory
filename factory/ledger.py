"""Cost and publish ledger, one json object per line. Append only.

Anything that costs money goes through `charge()`, which demands an `op`: a key
that names the work, not the moment. A provider job id is the natural one, since
one Higgsfield job is one bill. A row whose `op` is already in the file is not
written again, so re-running a stage, resuming after a crash or replaying a
recovery script charges once. Without that key the ledger counted a single
image job twice and reported 13.0 credits for 6.5 of work (2026-10-14).

`record()` stays for rows that are not charges: stage rollups, publishes, token
refreshes. Those carry `credits=0` and need no key.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import FactoryError

LEDGER_FILENAME = "ledger.jsonl"
TRACKS = ("text", "image", "video", "publish", "token")


@dataclass
class Entry:
    ts: str
    track: str
    kind: str
    model: str = ""
    credits: float = 0.0
    tokens: int = 0
    result: str = "ok"
    profile: str = ""
    platform: str = ""
    ref: str = ""
    op: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ledger_path(home: Path) -> Path:
    return Path(home) / LEDGER_FILENAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_op(home: Path, op: str) -> dict[str, Any] | None:
    """The row already written for this key, if there is one."""
    if not op:
        return None
    for entry in read(home):
        if str(entry.get("op") or "") == op:
            return entry
    return None


def record(home: Path, *, track: str, kind: str, model: str = "", credits: float = 0.0,
           tokens: int = 0, result: str = "ok", profile: str = "", platform: str = "",
           ref: str = "", op: str = "", detail: dict[str, Any] | None = None,
           now: str | None = None) -> Entry:
    """Append one row. With an `op`, a row already carrying it wins and nothing
    is written: the ledger is the record of work done, not of calls made."""
    if op:
        existing = find_op(home, op)
        if existing is not None:
            return Entry(**{key: existing.get(key, getattr(Entry, key, None))
                            for key in ("ts", "track", "kind", "model", "credits",
                                        "tokens", "result", "profile", "platform",
                                        "ref", "op", "detail")})
    entry = Entry(ts=now or utc_now(), track=track, kind=kind, model=model,
                  credits=float(credits), tokens=int(tokens), result=result,
                  profile=profile, platform=platform, ref=ref, op=op,
                  detail=detail or {})
    path = ledger_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(entry.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def charge(home: Path, *, op: str, track: str, kind: str, model: str = "",
           credits: float = 0.0, tokens: int = 0, result: str = "ok",
           profile: str = "", ref: str = "",
           detail: dict[str, Any] | None = None,
           now: str | None = None) -> Entry:
    """The only way a paid call reaches the ledger. `op` is mandatory.

    Making the key a parameter rather than a convention is the point: a call
    site that cannot name the work it is paying for cannot record it, so it
    cannot quietly charge twice either.
    """
    key = str(op or "").strip()
    if not key:
        raise FactoryError("LEDGER_OP_REQUIRED", f"{track}:{kind}"[:40])
    return record(home, track=track, kind=kind, model=model, credits=credits,
                  tokens=tokens, result=result, profile=profile, ref=ref,
                  op=key, detail=detail, now=now)


def read(home: Path) -> Iterator[dict[str, Any]]:
    path = ledger_path(home)
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                yield parsed


def summary(home: Path, *, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    floor = moment.timestamp() - days * 86400
    totals: dict[str, dict[str, float]] = {}
    rows = 0
    for entry in read(home):
        stamp = str(entry.get("ts") or "")
        try:
            when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if when < floor:
            continue
        rows += 1
        bucket = totals.setdefault(str(entry.get("track") or "unknown"),
                                   {"credits": 0.0, "tokens": 0.0, "count": 0.0,
                                    "failures": 0.0})
        bucket["credits"] += float(entry.get("credits") or 0)
        bucket["tokens"] += float(entry.get("tokens") or 0)
        bucket["count"] += 1
        if str(entry.get("result") or "ok") != "ok":
            bucket["failures"] += 1
    return {"days": days, "rows": rows, "tracks": totals}


def render_summary(data: dict[str, Any]) -> str:
    lines = [f"ledger summary, last {data['days']} day(s), {data['rows']} row(s)"]
    for track in sorted(data["tracks"]):
        bucket = data["tracks"][track]
        lines.append(f"  {track:<8} count={int(bucket['count']):<5} "
                     f"credits={bucket['credits']:.1f} tokens={int(bucket['tokens'])} "
                     f"failures={int(bucket['failures'])}")
    return "\n".join(lines)
