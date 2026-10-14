"""launchd agent generator for macOS. Ported shape from com.saju.publish.plist.

StartInterval rather than cron so a wedged run cannot be skipped silently: launchd will not
start a new instance while the previous one lives, and tick itself is idempotent.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.contents-factory.tick"
START_INTERVAL_SECONDS = 900


def agent_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def definition(*, home: Path, python: str | None = None, label: str = LABEL,
               interval: int = START_INTERVAL_SECONDS,
               working_directory: Path | None = None) -> dict[str, object]:
    executable = python or sys.executable
    logs = Path(home) / "logs"
    return {
        "Label": label,
        "ProgramArguments": [executable, "-m", "factory", "schedule", "tick"],
        "StartInterval": int(interval),
        "RunAtLoad": False,
        "WorkingDirectory": str(working_directory or Path.cwd()),
        "EnvironmentVariables": {"FACTORY_HOME": str(home)},
        "StandardOutPath": str(logs / "tick.log"),
        "StandardErrorPath": str(logs / "tick.err"),
        "ProcessType": "Background",
    }


def render(definition_data: dict[str, object]) -> bytes:
    return plistlib.dumps(definition_data, sort_keys=True)


def install(*, home: Path, python: str | None = None, label: str = LABEL,
            interval: int = START_INTERVAL_SECONDS, load: bool = True) -> Path:
    target = agent_path(label)
    target.parent.mkdir(parents=True, exist_ok=True)
    (Path(home) / "logs").mkdir(parents=True, exist_ok=True)
    target.write_bytes(render(definition(home=home, python=python, label=label,
                                        interval=interval)))
    if load:
        _launchctl(["bootout", f"gui/{_uid()}/{label}"], tolerate=True)
        _launchctl(["bootstrap", f"gui/{_uid()}", str(target)], tolerate=True)
    return target


def remove(label: str = LABEL, *, unload: bool = True) -> bool:
    target = agent_path(label)
    if unload:
        _launchctl(["bootout", f"gui/{_uid()}/{label}"], tolerate=True)
    if target.exists():
        target.unlink()
        return True
    return False


def status(label: str = LABEL) -> dict[str, object]:
    target = agent_path(label)
    loaded = False
    result = _launchctl(["print", f"gui/{_uid()}/{label}"], tolerate=True)
    if result is not None and result.returncode == 0:
        loaded = True
    return {"label": label, "plist": str(target), "installed": target.exists(),
            "loaded": loaded}


def _uid() -> int:
    """launchd is macOS only; a forced `--runner launchd` elsewhere must not crash."""
    import os

    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else -1


def _launchctl(arguments: list[str], *,
               tolerate: bool = False) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["launchctl", *arguments], capture_output=True, text=True,
                              timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        if tolerate:
            return None
        raise
