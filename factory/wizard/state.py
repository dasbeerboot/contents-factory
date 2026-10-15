"""The wizard's only state file: `<home>/state/wizard.json`.

Owned by this module and nothing else. `factory wizard ...` is the one writer, exactly
like the queue: an agent or a person editing this json by hand is how a half-answered
setup turns into a flow that asks question 3 forever.

Shape:
  {"version": 1, "flow": "setup", "updated_at": "...",
   "flows": {"setup": {"answers": {...}, "asked": [...], "skipped": [...],
                       "notes": {...}, "done": false}, "make": {...}}}

`asked` is the ordered list of question ids that have been answered or skipped, which
is what `back` walks. `notes` is derived work (the profile that was created, the brief
id, the draft, the queue id) so a repeated `next` never regenerates anything.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import FactoryError

VERSION = 1
FLOWS: tuple[str, ...] = ("setup", "make")
STATE_FILENAME = "wizard.json"


def state_path(home: Path) -> Path:
    return home / "state" / STATE_FILENAME


def blank_flow() -> dict[str, Any]:
    return {"answers": {}, "asked": [], "skipped": [], "notes": {}, "done": False}


def blank(flow: str = "setup") -> dict[str, Any]:
    return {"version": VERSION, "flow": flow, "updated_at": utc_now(),
            "flows": {name: blank_flow() for name in FLOWS}}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_flow(flow: str) -> str:
    if flow not in FLOWS:
        raise FactoryError("WIZARD_FLOW_UNKNOWN", str(flow)[:32])
    return flow


def load(home: Path) -> dict[str, Any]:
    path = state_path(home)
    if not path.is_file():
        return blank()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FactoryError("WIZARD_STATE_INVALID", STATE_FILENAME) from None
    if not isinstance(data, dict) or not isinstance(data.get("flows"), dict):
        raise FactoryError("WIZARD_STATE_INVALID", STATE_FILENAME)
    merged = blank(str(data.get("flow") or "setup"))
    for name in FLOWS:
        stored = data["flows"].get(name)
        if not isinstance(stored, dict):
            continue
        record = merged["flows"][name]
        for key, fallback in (("answers", {}), ("notes", {})):
            value = stored.get(key, fallback)
            record[key] = {str(k): v for k, v in value.items()} if isinstance(
                value, dict) else fallback
        for key in ("asked", "skipped"):
            value = stored.get(key)
            record[key] = [str(entry) for entry in value] if isinstance(value,
                                                                       list) else []
        record["done"] = bool(stored.get("done"))
    if merged["flow"] not in FLOWS:
        merged["flow"] = "setup"
    return merged


def save(home: Path, state: dict[str, Any]) -> Path:
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = VERSION
    state["updated_at"] = utc_now()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2,
                                    sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def record(state: dict[str, Any], flow: str) -> dict[str, Any]:
    check_flow(flow)
    return state["flows"][flow]


def reset(state: dict[str, Any], flow: str | None = None) -> dict[str, Any]:
    if flow is None:
        return blank(state.get("flow") or "setup")
    check_flow(flow)
    state["flows"][flow] = blank_flow()
    return state
