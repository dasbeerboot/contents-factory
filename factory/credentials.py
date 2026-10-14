"""credentials.env access. Values never reach stdout, logs or exceptions."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import FactoryError

LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
CREDENTIALS_FILENAME = "credentials.env"


def credentials_path(home: Path, profile: str) -> Path:
    return home / "profiles" / profile / CREDENTIALS_FILENAME


def load_env(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if line.strip().startswith("#"):
            continue
        match = LINE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value.strip()
    return values


def load_profile_env(home: Path, profile: str) -> dict[str, str]:
    env = load_env(credentials_path(home, profile))
    for key, value in os.environ.items():
        if key.isupper() and key not in env and value.strip():
            env.setdefault(key, value.strip())
    return env


def require(env: dict[str, str], keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if not str(env.get(key, "")).strip()]
    if missing:
        raise FactoryError("ENV_MISSING", ",".join(sorted(missing)))


def env_set(path: Path, key: str, value: str) -> None:
    """Replace or append KEY=value, keeping the file at mode 600."""
    if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
        raise FactoryError("ENV_KEY_INVALID", key[:32])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


REDACTED = "<REDACTED:%s>"


def redacted(key: str) -> str:
    return REDACTED % key
