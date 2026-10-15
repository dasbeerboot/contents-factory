"""crontab generator for Linux. Lines are fenced by markers so install stays idempotent.

A `name` selects which fenced block is being managed: `tick` is the publisher, and the
telegram confirm poller registers its own under a different name. Without separate
markers installing one job would strip the other.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

TICK_NAME = "tick"
BEGIN = "# >>> contents-factory tick >>>"
END = "# <<< contents-factory tick <<<"
DEFAULT_SCHEDULE = "*/15 * * * *"
DEFAULT_INTERVAL_SECONDS = 900
TICK_ARGUMENTS: tuple[str, ...] = ("schedule", "tick")


def markers(name: str = TICK_NAME) -> tuple[str, str]:
    """`tick` keeps the historical marker text so an existing crontab still matches."""
    if name == TICK_NAME:
        return BEGIN, END
    return (f"# >>> contents-factory {name} >>>",
            f"# <<< contents-factory {name} <<<")


def schedule_for(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> str:
    """`--interval` in seconds as a crontab minute field, clamped to 1..59."""
    minutes = max(1, min(59, int(interval_seconds) // 60))
    return f"*/{minutes} * * * *"


def line(*, home: Path, python: str | None = None,
         schedule: str = DEFAULT_SCHEDULE,
         arguments: Sequence[str] = TICK_ARGUMENTS,
         log_name: str = TICK_NAME) -> str:
    executable = python or sys.executable
    log = Path(home) / "logs" / f"{log_name}.log"
    return (f"{schedule} FACTORY_HOME={home} {executable} -m factory "
            f"{' '.join(arguments)} >> {log} 2>&1")


def block(*, home: Path, python: str | None = None,
          schedule: str = DEFAULT_SCHEDULE, name: str = TICK_NAME,
          arguments: Sequence[str] = TICK_ARGUMENTS) -> str:
    begin, end = markers(name)
    return "\n".join([begin, line(home=home, python=python, schedule=schedule,
                                  arguments=arguments, log_name=name), end])


def strip_block(current: str, name: str = TICK_NAME) -> str:
    begin, end = markers(name)
    lines = current.splitlines()
    out: list[str] = []
    inside = False
    for entry in lines:
        if entry.strip() == begin:
            inside = True
            continue
        if entry.strip() == end:
            inside = False
            continue
        if not inside:
            out.append(entry)
    return "\n".join(out).strip()


def merge(current: str, *, home: Path, python: str | None = None,
          schedule: str = DEFAULT_SCHEDULE, name: str = TICK_NAME,
          arguments: Sequence[str] = TICK_ARGUMENTS) -> str:
    base = strip_block(current, name)
    body = block(home=home, python=python, schedule=schedule, name=name,
                 arguments=arguments)
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
            schedule: str = DEFAULT_SCHEDULE, interval: int | None = None,
            name: str = TICK_NAME,
            arguments: Sequence[str] = TICK_ARGUMENTS) -> bool:
    if interval is not None:
        schedule = schedule_for(interval)
    (Path(home) / "logs").mkdir(parents=True, exist_ok=True)
    return write_crontab(merge(read_crontab(), home=home, python=python,
                               schedule=schedule, name=name, arguments=arguments))


def remove(name: str = TICK_NAME) -> bool:
    current = read_crontab()
    begin, _end = markers(name)
    if begin not in current:
        return False
    stripped = strip_block(current, name)
    return write_crontab(f"{stripped}\n" if stripped else "")


def status(name: str = TICK_NAME,
           arguments: Sequence[str] = TICK_ARGUMENTS) -> dict[str, object]:
    current = read_crontab()
    begin, _end = markers(name)
    needle = f"factory {' '.join(arguments)}"
    return {"installed": begin in current,
            "entries": [entry for entry in current.splitlines() if needle in entry]}


def _crontab(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["crontab", *arguments], capture_output=True, text=True,
                              timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
