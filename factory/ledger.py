"""Cost and publish ledger, one json object per line. Append only."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ledger_path(home: Path) -> Path:
    return Path(home) / LEDGER_FILENAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(home: Path, *, track: str, kind: str, model: str = "", credits: float = 0.0,
           tokens: int = 0, result: str = "ok", profile: str = "", platform: str = "",
           ref: str = "", detail: dict[str, Any] | None = None,
           now: str | None = None) -> Entry:
    entry = Entry(ts=now or utc_now(), track=track, kind=kind, model=model,
                  credits=float(credits), tokens=int(tokens), result=result,
                  profile=profile, platform=platform, ref=ref, detail=detail or {})
    path = ledger_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(entry.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return entry


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
