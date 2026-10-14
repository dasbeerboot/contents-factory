"""Long lived token refresh, ported from saju-threads scripts/token_refresh.sh.

Threads and Instagram tokens live 60 days and must be refreshed, not exchanged: the
generator token is already long lived, so th_refresh_token / ig_refresh_token is the only
endpoint used. A failed refresh is the single most common outage in the source system, so
the result is always reported. Warnings start 7 days before expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import i18n
from ..credentials import credentials_path, env_set, load_env
from ..errors import FactoryError
from ..publishers.base import get_json

THREADS_URL = "https://graph.threads.net/refresh_access_token"
INSTAGRAM_URL = "https://graph.instagram.com/refresh_access_token"
TOKEN_LIFETIME_DAYS = 60
WARN_DAYS = 7

PLATFORM_TOKENS: dict[str, tuple[str, str, str, str]] = {
    "threads": ("THREADS_ACCESS_TOKEN", "THREADS_TOKEN_ISSUED_AT", THREADS_URL,
                "th_refresh_token"),
    "instagram": ("IG_ACCESS_TOKEN", "IG_TOKEN_ISSUED_AT", INSTAGRAM_URL,
                  "ig_refresh_token"),
}


@dataclass(frozen=True)
class ExpiryStatus:
    platform: str
    profile: str
    issued_at: str | None
    days_left: int | None
    warn: bool

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "profile": self.profile,
                "issued_at": self.issued_at, "days_left": self.days_left,
                "warn": self.warn}


def expiry_status(home: Path, profile: str, platform: str,
                  *, now: datetime | None = None) -> ExpiryStatus:
    keys = PLATFORM_TOKENS.get(platform)
    if keys is None:
        raise FactoryError("PLATFORM_UNKNOWN", platform[:32])
    token_key, issued_key, _url, _grant = keys
    env = load_env(credentials_path(home, profile))
    if not env.get(token_key):
        return ExpiryStatus(platform, profile, None, None, False)
    issued = str(env.get(issued_key) or "").strip()
    if not issued:
        return ExpiryStatus(platform, profile, None, None, True)
    try:
        when = datetime.strptime(issued[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return ExpiryStatus(platform, profile, issued, None, True)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    days_left = TOKEN_LIFETIME_DAYS - int((moment - when).total_seconds() // 86400)
    return ExpiryStatus(platform, profile, issued, days_left, days_left <= WARN_DAYS)


def warnings(home: Path, profile: str, *, lang: str = "en",
             now: datetime | None = None) -> list[str]:
    translate = i18n.Translator(lang)
    out: list[str] = []
    for platform in PLATFORM_TOKENS:
        status = expiry_status(home, profile, platform, now=now)
        if not status.warn:
            continue
        out.append(translate("token.expiry_warning", platform=platform, profile=profile,
                             days=status.days_left if status.days_left is not None else 0))
    return out


def refresh(home: Path, profile: str, platform: str, *, dry_run: bool = False,
            fetch: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    keys = PLATFORM_TOKENS.get(platform)
    if keys is None:
        raise FactoryError("PLATFORM_UNKNOWN", platform[:32])
    token_key, issued_key, url, grant = keys
    path = credentials_path(home, profile)
    env = load_env(path)
    current = str(env.get(token_key) or "").strip()
    if not current:
        raise FactoryError("ENV_MISSING", token_key)
    if dry_run:
        return {"platform": platform, "profile": profile, "dry_run": True,
                "request": {"method": "GET", "url": url,
                            "params": {"grant_type": grant,
                                       "access_token": f"<REDACTED:{token_key}>"}},
                "writes": [f"{path}:{token_key}", f"{path}:{issued_key}"]}
    caller = fetch or (lambda target, params: get_json(target, params))
    response = caller(url, {"grant_type": grant, "access_token": current})
    token = str(response.get("access_token") or "")
    if not token:
        raise FactoryError("TOKEN_REFRESH_FAILED", platform)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    env_set(path, token_key, token)
    permission = env_set(path, issued_key, moment.strftime("%Y-%m-%d"))
    result = {"platform": platform, "profile": profile, "dry_run": False,
              "issued_at": moment.strftime("%Y-%m-%d"),
              "expires_in_days": TOKEN_LIFETIME_DAYS}
    if permission:
        # This path is unattended (the tick calls it), so a file that could not be
        # locked down has to be reported rather than dropped on the floor.
        result["warning"] = permission
    return result
