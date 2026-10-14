"""Host facts: which OS this is, where the home lives, how to find a binary.

macOS, Windows and Linux are all supported, and the places they differ are small
enough to name: the scheduler (launchd / Task Scheduler / cron), the way a
credentials file is locked down (`chmod` vs `icacls`), and the `.exe` suffix on
every external binary. Everything that branches on the OS branches here, so a
new platform is one table rather than a grep.

The configured OS wins over the detected one: a user who answered the setup
question is telling us something `platform.system()` cannot, and a wrong answer
should be visible in `factory doctor` rather than silently overridden.
"""
from __future__ import annotations

import getpass
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from .errors import FactoryError

OsName = Literal["macos", "windows", "linux"]
OS_CHOICES: tuple[str, ...] = ("macos", "windows", "linux")

# Windows resolves a bare name against PATHEXT; shutil.which already honours that
# when the suffix list comes from the environment, but an explicit list keeps the
# lookup identical whichever OS the test runs on.
# No .ps1: PowerShell scripts are not executable by CreateProcess, so finding one
# would report a binary as present and then fail at exec time.
EXECUTABLE_SUFFIXES: tuple[str, ...] = ("", ".exe", ".cmd", ".bat")

SCHEDULERS: dict[str, str] = {"macos": "launchd", "windows": "schtasks",
                              "linux": "cron"}

INSTALL_HINTS: dict[str, dict[str, str]] = {
    "ffmpeg": {
        "macos": "brew install ffmpeg",
        "windows": "winget install --id Gyan.FFmpeg",
        "linux": "apt install ffmpeg",
        "url": "https://ffmpeg.org/download.html",
    },
    "ffprobe": {
        "macos": "brew install ffmpeg",
        "windows": "winget install --id Gyan.FFmpeg",
        "linux": "apt install ffmpeg",
        "url": "https://ffmpeg.org/download.html",
    },
    "node": {
        "macos": "brew install node",
        "windows": "winget install --id OpenJS.NodeJS.LTS",
        "linux": "apt install nodejs",
        "url": "https://nodejs.org/en/download",
    },
    "uv": {
        "macos": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "windows": 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"',
        "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "url": "https://docs.astral.sh/uv/getting-started/installation/",
    },
    "higgsfield": {
        "macos": "curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/"
                 "main/install.sh | sh",
        "windows": "see the CLI install page (PowerShell)",
        "linux": "curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/"
                 "main/install.sh | sh",
        "url": "https://github.com/higgsfield-ai/cli",
    },
    "aside": {
        # The aside CLI ships a Windows installer, but the browser it drives is
        # macOS 15+ only, so the aside Naver backend is macOS only.
        "macos": "https://aside.com/download (macOS 15.0 or later)",
        "windows": "unsupported: the aside browser is macOS only, use the "
                   "playwright backend",
        "linux": "unsupported: the aside browser is macOS only, use the "
                 "playwright backend",
        "url": "https://docs.aside.com/help/get-started",
    },
}


def detect_os() -> str:
    """What `platform.system()` says, mapped onto our three names."""
    system = platform.system().strip().lower()
    if system == "darwin":
        return "macos"
    if system == "windows" or system.startswith("cygwin") or system.startswith("mingw"):
        return "windows"
    return "linux"


def normalize(name: str | None) -> str:
    text = str(name or "").strip().lower()
    if text in OS_CHOICES:
        return text
    if text == "darwin":
        return "macos"
    if text in {"win32", "win", "nt"}:
        return "windows"
    raise FactoryError("OS_UNKNOWN", text[:32] or "empty")


def home_dir() -> Path:
    """`~` for this user. `Path.home()` is `%USERPROFILE%` on Windows."""
    return Path.home()


def home_display(os_name: str | None = None) -> str:
    """How to write the runtime home in docs for this OS."""
    target = normalize(os_name) if os_name else detect_os()
    if target == "windows":
        return r"%USERPROFILE%\.contents-factory"
    return "~/.contents-factory"


def expand(path: str | Path) -> Path:
    """`~` expansion that works the same on every OS."""
    text = str(path)
    if text in {"~", "~/", "~\\"}:
        return home_dir()
    if text.startswith("~/") or text.startswith("~\\"):
        return home_dir() / text[2:]
    return Path(text).expanduser()


def which(name: str) -> str | None:
    """Absolute path to `name`, trying the Windows executable suffixes too."""
    direct = shutil.which(name)
    if direct:
        return direct
    for suffix in EXECUTABLE_SUFFIXES:
        if not suffix:
            continue
        found = shutil.which(f"{name}{suffix}")
        if found:
            return found
    return None


def scheduler_for(os_name: str | None = None) -> str:
    return SCHEDULERS[normalize(os_name) if os_name else detect_os()]


def install_hint(binary: str, os_name: str | None = None) -> str:
    target = normalize(os_name) if os_name else detect_os()
    entry = INSTALL_HINTS.get(binary)
    if not entry:
        return ""
    return f"{entry.get(target, '')}  {entry.get('url', '')}".strip()


def icacls_command(path: Path, *, user: str | None = None) -> list[str]:
    """Windows equivalent of chmod 600: drop inheritance, grant this user only."""
    owner = user or os.environ.get("USERNAME") or _login_name()
    return ["icacls", str(path), "/inheritance:r", "/grant:r", f"{owner}:F"]


def _login_name() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - a nameless account still gets a warning
        return ""


def secure_directory(path: Path, *, os_name: str | None = None) -> str:
    """Owner-only on a directory (a browser profile holding session cookies)."""
    target = normalize(os_name) if os_name else detect_os()
    if target != "windows":
        try:
            Path(path).chmod(0o700)
        except OSError:
            return "DIR_PERMISSION_UNSET"
        return ""
    return secure_file(path, os_name=target)


def secure_file(path: Path, *, os_name: str | None = None) -> str:
    """Restrict a credentials file to its owner. Returns "" or a warning code.

    A failure here is reported, not raised: refusing to write a token because the
    ACL call was unavailable would lose the token, and the file still exists in a
    per-user directory. The caller prints the warning.
    """
    target = normalize(os_name) if os_name else detect_os()
    if target != "windows":
        try:
            Path(path).chmod(0o600)
        except OSError:
            return "FILE_PERMISSION_UNSET"
        return ""
    command = icacls_command(Path(path))
    if not command[-1].split(":")[0]:
        return "FILE_ACL_USER_UNKNOWN"
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "FILE_ACL_UNSET"
    return "" if done.returncode == 0 else "FILE_ACL_UNSET"
