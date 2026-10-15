"""Desktop launcher: one double-click that runs `factory start`.

The target user does not open a terminal. The whole entry point is a file on the
desktop named 콘텐츠공장, and everything it does is `cd <repo> && uv run factory start`.

Three things decide the shape of the generated script, and all three come from
having watched this fail:

* **`uv` is looked up at run time, not baked in.** Recording the absolute path
  found at create time breaks when uv is reinstalled; relying only on `PATH`
  breaks because a double-clicked `.bat` gets the registry `PATH` and a
  double-clicked `.command` gets a login shell that may never have sourced the
  profile that added `~/.local/bin`. So the script tries `PATH` first and falls
  back to the recorded absolute path.
* **The window has to stay open on failure.** A launcher that flashes and
  vanishes is indistinguishable from a launcher that did nothing, so a non-zero
  exit waits for a keypress.
* **The `.bat` body is ASCII.** `cmd.exe` reads a batch file in the console
  codepage, which on a Korean Windows install is 949; Korean text inside the
  file would arrive mojibake. The *filename* is Korean (NTFS is UTF-16, that is
  fine) and the script switches the console to UTF-8 so that `factory`'s own
  Korean output renders.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import repo_root
from .errors import FactoryError
from .osinfo import (detect_os, expand, home_dir, normalize, path_prefix,
                     which)

NAME = "콘텐츠공장"
# A macOS `.command` is what Finder double-click hands to Terminal; `.sh` has no
# such association, so it is only the Linux fallback.
SUFFIXES: dict[str, str] = {"macos": ".command", "windows": ".bat", "linux": ".sh"}
# Honoured so tests never touch the real desktop, and because a Windows desktop
# is frequently redirected into OneDrive where `~/Desktop` does not exist.
DESKTOP_ENV = "FACTORY_DESKTOP"
START_COMMAND = ("run", "factory", "start")


def desktop_dir() -> Path:
    override = os.environ.get(DESKTOP_ENV, "").strip()
    if override:
        return expand(override)
    return home_dir() / "Desktop"


def launcher_path(os_name: str | None = None, *, name: str = NAME) -> Path:
    target = normalize(os_name) if os_name else detect_os()
    return desktop_dir() / f"{name}{SUFFIXES[target]}"


def uv_fallback(os_name: str | None = None) -> Path:
    """Where the official uv installer puts the binary when `PATH` has lost it."""
    target = normalize(os_name) if os_name else detect_os()
    binary = "uv.exe" if target == "windows" else "uv"
    return home_dir() / ".local" / "bin" / binary


def uv_reference(os_name: str | None = None) -> str:
    """The absolute path the script falls back to: the live one if uv is on PATH."""
    found = which("uv")
    return found or str(uv_fallback(os_name))


def script(*, repo: Path, os_name: str | None = None, uv: str | None = None) -> str:
    target = normalize(os_name) if os_name else detect_os()
    fallback = uv or uv_reference(target)
    if target == "windows":
        return _batch(repo=repo, fallback=fallback,
                      path=path_prefix(target, home="%USERPROFILE%"))
    return _shell(repo=repo, fallback=fallback,
                  path=path_prefix(target, home="$HOME"))


def _shell(*, repo: Path, fallback: str, path: str = "") -> str:
    return f"""#!/bin/sh
# contents-factory launcher. Rebuild with: uv run factory launcher create
# Double-clicking this opens Terminal and starts the content factory.
cd "{repo}" || {{
  echo "contents-factory folder is gone: {repo}"
  printf 'Press return to close. '
  read -r _ignored
  exit 1
}}
# A double-clicked .command gets a login shell that may never have sourced the
# profile that added ~/.local/bin, and factory start needs uv, the agent, node and
# ffmpeg. Prepending the usual directories is cheaper than guessing which one broke.
PATH="{path}:$PATH"
export PATH
UV="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV" ]; then UV="{fallback}"; fi
if [ ! -x "$UV" ]; then
  echo "uv not found. Reinstall with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  printf 'Press return to close. '
  read -r _ignored
  exit 1
fi
"$UV" {" ".join(START_COMMAND)}
status=$?
if [ "$status" -ne 0 ]; then
  echo ""
  echo "factory start exited with $status"
  printf 'Press return to close. '
  read -r _ignored
fi
exit "$status"
"""


def _batch(*, repo: Path, fallback: str, path: str = "") -> str:
    """ASCII only: cmd.exe reads this file in the console codepage, not UTF-8."""
    body = f"""@echo off
rem contents-factory launcher. Rebuild with: uv run factory launcher create
rem This file stays ASCII on purpose; chcp switches the console to UTF-8 so the
rem factory's own Korean output renders.
chcp 65001 >nul
cd /d "{repo}"
if errorlevel 1 (
  echo contents-factory folder is gone: {repo}
  pause
  exit /b 1
)
rem Explorer hands this file the registry PATH, which can be missing the npm
rem prefix and %LOCALAPPDATA% entries that uv and the agent install into.
set "PATH={path};%PATH%"
set "FACTORY_UV=uv"
where uv >nul 2>nul || set "FACTORY_UV={fallback}"
"%FACTORY_UV%" {" ".join(START_COMMAND)}
if errorlevel 1 (
  echo.
  echo factory start failed.
  pause
  exit /b 1
)
"""
    return body.replace("\n", "\r\n")


def create(*, repo: Path | None = None, os_name: str | None = None,
           name: str = NAME, uv: str | None = None,
           dry_run: bool = False) -> dict[str, object]:
    """Write the desktop launcher. Idempotent: an existing file is overwritten."""
    target_os = normalize(os_name) if os_name else detect_os()
    directory = repo or repo_root()
    path = launcher_path(target_os, name=name)
    body = script(repo=directory, os_name=target_os, uv=uv)
    plan: dict[str, object] = {"action": "create", "os": target_os,
                               "path": str(path), "repo": str(directory),
                               "script": body, "dry_run": dry_run}
    if dry_run:
        return plan
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the CRLF the batch body already carries; a shell
        # script has none to preserve.
        path.write_text(body, encoding="utf-8", newline="")
    except OSError:
        raise FactoryError("LAUNCHER_WRITE_FAILED", path.name) from None
    if target_os != "windows":
        # Finder refuses to run a `.command` that is not executable.
        try:
            path.chmod(0o755)
        except OSError:
            plan["warning"] = "LAUNCHER_CHMOD_FAILED"
    plan["created"] = True
    return plan


def remove(*, os_name: str | None = None, name: str = NAME) -> dict[str, object]:
    target_os = normalize(os_name) if os_name else detect_os()
    path = launcher_path(target_os, name=name)
    existed = path.exists()
    if existed:
        try:
            path.unlink()
        except OSError:
            raise FactoryError("LAUNCHER_REMOVE_FAILED", path.name) from None
    return {"action": "remove", "os": target_os, "path": str(path),
            "removed": existed}


def status(*, os_name: str | None = None, name: str = NAME) -> dict[str, object]:
    target_os = normalize(os_name) if os_name else detect_os()
    path = launcher_path(target_os, name=name)
    exists = path.exists()
    executable = bool(exists and os.access(path, os.X_OK))
    return {"os": target_os, "path": str(path), "installed": exists,
            "executable": executable if target_os != "windows" else True,
            "uv": which("uv") or "", "uv_fallback": str(uv_fallback(target_os))}
