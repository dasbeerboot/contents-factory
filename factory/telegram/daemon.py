"""Keep the confirm poller alive across reboots, per OS.

The three runners want three different shapes, and the difference is not cosmetic:

* **launchd (macOS)** runs the loop itself, `KeepAlive` so a crash restarts it. Long
  polling is the timer; there is no interval.
* **schtasks (Windows)** and **cron (Linux)** cannot supervise a daemon. They get
  `telegram serve --once`, a single poll that returns, every minute. One getUpdates
  round trip per minute is the same cursor and the same idempotency, just coarser:
  a button press is answered within a minute rather than within a second.

Everything here routes through the existing scheduler modules, so a plist or a
command line is rendered by the same code the tick uses. `plan` renders without
touching the machine, which is what `--dry-run` prints.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..errors import FactoryError
from ..osinfo import scheduler_for
from ..scheduler import cron, launchd, windows

LABEL = "com.contents-factory.telegram"
TASK_NAME = "contents-factory-telegram"
CRON_NAME = "telegram"
LOG_NAME = "telegram"
# Long polling is the loop's own clock; the once-per-minute runners re-enter it.
DAEMON_ARGUMENTS: tuple[str, ...] = ("telegram", "serve")
ONCE_ARGUMENTS: tuple[str, ...] = ("telegram", "serve", "--once")
ONCE_INTERVAL_SECONDS = 60

RUNNERS: tuple[str, ...] = ("launchd", "cron", "schtasks")


def runner_for(os_name: str | None, requested: str = "auto") -> str:
    chosen = str(requested or "auto")
    resolved = scheduler_for(os_name) if chosen == "auto" else chosen
    if resolved not in RUNNERS:
        raise FactoryError("RUNNER_UNKNOWN", resolved[:32])
    return resolved


def plan(*, home: Path, runner: str, python: str | None = None) -> dict[str, object]:
    """What `install` would write, rendered without writing it."""
    executable = python or sys.executable
    if runner == "launchd":
        agent = launchd.definition(home=Path(home), python=executable, label=LABEL,
                                   arguments=DAEMON_ARGUMENTS, keep_alive=True,
                                   log_name=LOG_NAME)
        return {"runner": runner, "label": LABEL,
                "path": str(launchd.agent_path(LABEL)),
                "plist": launchd.render(agent).decode("utf-8"),
                "definition": agent}
    if runner == "schtasks":
        command = windows.create_command(home=Path(home), python=executable,
                                         interval=ONCE_INTERVAL_SECONDS,
                                         task=TASK_NAME, arguments=ONCE_ARGUMENTS,
                                         log_name=LOG_NAME)
        return {"runner": runner, "task": TASK_NAME, "command": command}
    block = cron.block(home=Path(home), python=executable,
                       schedule=cron.schedule_for(ONCE_INTERVAL_SECONDS),
                       name=CRON_NAME, arguments=ONCE_ARGUMENTS)
    return {"runner": runner, "name": CRON_NAME, "crontab": block}


def install(*, home: Path, runner: str, python: str | None = None) -> dict[str, object]:
    if runner == "launchd":
        path = launchd.install(home=Path(home), python=python, label=LABEL,
                               arguments=DAEMON_ARGUMENTS, keep_alive=True,
                               log_name=LOG_NAME)
        return {"runner": runner, "installed": True, "path": str(path)}
    if runner == "schtasks":
        ok = windows.install(home=Path(home), python=python,
                             interval=ONCE_INTERVAL_SECONDS, task=TASK_NAME,
                             arguments=ONCE_ARGUMENTS, log_name=LOG_NAME)
        return {"runner": runner, "installed": ok, "task": TASK_NAME}
    ok = cron.install(home=Path(home), python=python,
                     interval=ONCE_INTERVAL_SECONDS, name=CRON_NAME,
                     arguments=ONCE_ARGUMENTS)
    return {"runner": runner, "installed": ok, "name": CRON_NAME}


def remove(*, runner: str) -> dict[str, object]:
    if runner == "launchd":
        return {"runner": runner, "removed": launchd.remove(LABEL)}
    if runner == "schtasks":
        return {"runner": runner, "removed": windows.remove(TASK_NAME)}
    return {"runner": runner, "removed": cron.remove(CRON_NAME)}


def status(*, runner: str) -> dict[str, object]:
    if runner == "launchd":
        return {"runner": runner, **launchd.status(LABEL)}
    if runner == "schtasks":
        return {"runner": runner, **windows.status(TASK_NAME)}
    return {"runner": runner, **cron.status(CRON_NAME, ONCE_ARGUMENTS)}
