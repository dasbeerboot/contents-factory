"""crontab generator for Linux. Lines are fenced by markers so install stays idempotent."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BEGIN = "# >>> contents-factory tick >>>"
END = "# <<< contents-factory tick <<<"
DEFAULT_SCHEDULE = "*/15 * * * *"


def line(*, home: Path, python: str | None = None,
         schedule: str = DEFAULT_SCHEDULE) -> str:
    executable = python or sys.executable
    log = Path(home) / "logs" / "tick.log"
    return (f"{schedule} FACTORY_HOME={home} {executable} -m factory schedule tick "
            f">> {log} 2>&1")


def block(*, home: Path, python: str | None = None,
          schedule: str = DEFAULT_SCHEDULE) -> str:
    return "\n".join([BEGIN, line(home=home, python=python, schedule=schedule), END])


def strip_block(current: str) -> str:
    lines = current.splitlines()
    out: list[str] = []
    inside = False
    for entry in lines:
        if entry.strip() == BEGIN:
            inside = True
            continue
        if entry.strip() == END:
            inside = False
            continue
        if not inside:
            out.append(entry)
    return "\n".join(out).strip()


def merge(current: str, *, home: Path, python: str | None = None,
          schedule: str = DEFAULT_SCHEDULE) -> str:
    base = strip_block(current)
    body = block(home=home, python=python, schedule=schedule)
    return (f"{base}\n{body}\n" if base else f"{body}\n")


def read_crontab() -> str:
    result = _crontab(["-l"])
    return result.stdout if result and result.returncode == 0 else ""


def write_crontab(content: str) -> bool:
    try:
        completed = subprocess.run(["crontab", "-"], input=content, text=True,
                                   capture_output=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def install(*, home: Path, python: str | None = None,
            schedule: str = DEFAULT_SCHEDULE) -> bool:
    (Path(home) / "logs").mkdir(parents=True, exist_ok=True)
    return write_crontab(merge(read_crontab(), home=home, python=python, schedule=schedule))


def remove() -> bool:
    current = read_crontab()
    if BEGIN not in current:
        return False
    stripped = strip_block(current)
    return write_crontab(f"{stripped}\n" if stripped else "")


def status() -> dict[str, object]:
    current = read_crontab()
    return {"installed": BEGIN in current,
            "entries": [entry for entry in current.splitlines()
                        if "factory schedule tick" in entry]}


def _crontab(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["crontab", *arguments], capture_output=True, text=True,
                              timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
