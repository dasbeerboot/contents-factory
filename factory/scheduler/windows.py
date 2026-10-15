r"""Windows Task Scheduler registration for the tick, via `schtasks`.

`/SC MINUTE /MO <n>` is the counterpart of launchd's StartInterval: a repeating
interval rather than a wall-clock cron line, so a run that takes longer than the
interval cannot be silently skipped by a clock change.

Two Windows specifics drive the shape of the action:

* **A task carries no environment.** `FACTORY_HOME` has to travel with the
  command, so the action is a `cmd /c set ... && ...` line rather than a bare
  executable. There is no `EnvironmentVariables` dictionary to put it in.
* **`pythonw.exe` rather than `python.exe`.** The console host flashes a window
  on every run otherwise, every fifteen minutes, forever. `uv run` is used when
  the interpreter came from a uv-managed virtualenv, because that is what the
  user installed with.

A task also starts in `%SystemRoot%\System32`, so the action changes directory
first: `uv run factory` resolves the project from the working directory, and even
`-m factory` wants the repo on hand for the vendored node scripts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

from ..config import repo_root
from ..osinfo import which

TASK_NAME = "contents-factory-tick"
DEFAULT_INTERVAL_SECONDS = 900
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1439
TICK_ARGUMENTS: tuple[str, ...] = ("schedule", "tick")


def interval_minutes(seconds: int = DEFAULT_INTERVAL_SECONDS) -> int:
    """schtasks counts whole minutes, and refuses 0 or a full day."""
    minutes = int(seconds) // 60
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, minutes))


def tick_executable(python: str | None = None) -> list[str]:
    """The interpreter part of the action, windowless where possible."""
    if python:
        return [str(python), "-m", "factory"]
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        return [str(windowless), "-m", "factory"]
    uv = which("uv")
    if uv and _in_uv_venv(executable):
        return [uv, "run", "factory"]
    return [str(executable), "-m", "factory"]


def _in_uv_venv(executable: Path) -> bool:
    return (executable.parent.parent / "pyvenv.cfg").is_file()


def quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def action(*, home: Path, python: str | None = None,
           working_directory: Path | None = None,
           arguments: Sequence[str] = TICK_ARGUMENTS,
           log_name: str = "tick") -> str:
    """The `/TR` string: cd, set the home, run the command, append to the log."""
    log = Path(home) / "logs" / f"{log_name}.log"
    program = " ".join(quote(part) for part in tick_executable(python))
    directory = Path(working_directory or repo_root())
    return (f'cmd /c cd /d "{directory}" && set "FACTORY_HOME={home}" && '
            f'{program} {" ".join(arguments)} >> "{log}" 2>&1')


def create_command(*, home: Path, python: str | None = None,
                   interval: int = DEFAULT_INTERVAL_SECONDS,
                   task: str = TASK_NAME,
                   working_directory: Path | None = None,
                   arguments: Sequence[str] = TICK_ARGUMENTS,
                   log_name: str = "tick") -> list[str]:
    return ["schtasks", "/Create", "/TN", task, "/TR",
            action(home=home, python=python, working_directory=working_directory,
                   arguments=arguments, log_name=log_name),
            "/SC", "MINUTE", "/MO", str(interval_minutes(interval)), "/F"]


def delete_command(task: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Delete", "/TN", task, "/F"]


def query_command(task: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Query", "/TN", task, "/FO", "LIST"]


def install(*, home: Path, python: str | None = None,
            interval: int = DEFAULT_INTERVAL_SECONDS,
            task: str = TASK_NAME,
            arguments: Sequence[str] = TICK_ARGUMENTS,
            log_name: str = "tick") -> bool:
    (Path(home) / "logs").mkdir(parents=True, exist_ok=True)
    result = _schtasks(create_command(home=home, python=python, interval=interval,
                                      task=task, arguments=arguments,
                                      log_name=log_name))
    return result is not None and result.returncode == 0


def remove(task: str = TASK_NAME) -> bool:
    result = _schtasks(delete_command(task))
    return result is not None and result.returncode == 0


def status(task: str = TASK_NAME) -> dict[str, object]:
    result = _schtasks(query_command(task))
    installed = result is not None and result.returncode == 0
    lines = [line.strip() for line in (result.stdout if result else "").splitlines()
             if line.strip()] if installed else []
    return {"runner": "schtasks", "task": task, "installed": installed,
            "available": which("schtasks") is not None,
            "entries": lines[:12]}


def _schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=60,
                              check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
