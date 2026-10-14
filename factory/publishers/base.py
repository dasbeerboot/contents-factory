"""Publisher contract. Every publisher can plan a request set without touching the network."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

from ..credentials import load_profile_env, redacted, require
from ..errors import FactoryError, http_code, scrub
from ..spec.queue import QueueItem

TIMEOUT_SECONDS = 60

FailureMode = Literal["retry", "hold", "unknown"]

# Codes that prove nothing reached the platform, so a retry cannot double post.
# Anything not listed is treated as unknown: when in doubt we stop, we never republish.
RETRY_SAFE: frozenset[str] = frozenset({
    "ENV_MISSING", "TEXT_TOO_LONG", "REPLY_TOO_LONG", "TITLE_REQUIRED",
    "CATEGORY_REQUIRED", "TAGS_REQUIRED", "MEDIA_REQUIRED", "MEDIA_FILE_MISSING",
    "MEDIA_PUBLIC_URL_REQUIRED", "MEDIA_PUBLIC_URL_PARTIAL", "CAROUSEL_SIZE_INVALID",
    "PHOTO_COUNT_INVALID", "PRIVACY_INVALID", "PUBLISH_AT_INVALID", "PLATFORM_MISMATCH",
    "ACCOUNT_ID_INVALID", "NAVER_SESSION_MISSING", "NODE_MISSING", "NAVER_SCRIPT_MISSING",
    "DOCUMENT_TOO_LARGE", "IMAGE_UPLOAD_UNSUPPORTED", "JOB_INVALID", "BLOG_ID_INVALID",
    "PUBLISH_MINUTE_NOT_TEN", "TOKEN_REFRESH_FAILED", "MCP_TOKEN_EXPIRED",
    "TIKTOK_NOT_CONNECTED", "PREPARE_NO_PUBLISH_TOKEN", "CONTAINER_FAILED",
    "REMOTE_REJECTED", "HTTP_400", "HTTP_401", "HTTP_403", "HTTP_404", "HTTP_422",
    "HTTP_429",
})
# The container exists and is recorded: hold the phase and let the next tick resume.
HOLD_CODES: frozenset[str] = frozenset({"CONTAINER_IN_PROGRESS"})


def failure_mode(code: str) -> FailureMode:
    text = str(code or "")
    if text in HOLD_CODES:
        return "hold"
    if text in RETRY_SAFE:
        return "retry"
    if text.startswith("PUBLISH_NOT_SENT"):
        return "retry"
    return "unknown"


class DeliveryTracker(Protocol):
    """Lets a container based publisher record progress between the two calls."""

    def resume_container(self) -> str | None:
        ...

    def container_created(self, container_id: str) -> None:
        ...

    def begin_publish(self, container_id: str) -> None:
        ...


class NullTracker:
    """Used by dry runs and by platforms with a single submit call."""

    def resume_container(self) -> str | None:
        return None

    def container_created(self, container_id: str) -> None:
        return None

    def begin_publish(self, container_id: str) -> None:
        return None


@dataclass(frozen=True)
class PublishRequest:
    """One outbound call. `fields` carries redaction placeholders, never real secrets."""

    step: str
    method: str
    url: str
    fields: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "method": self.method, "url": self.url,
                "fields": self.fields, "note": self.note}


@dataclass
class PublishResult:
    ok: bool
    platform: str
    item_id: str
    code: str = "OK"
    published_id: str | None = None
    requests: list[PublishRequest] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "platform": self.platform, "item_id": self.item_id,
                "code": self.code, "published_id": self.published_id,
                "dry_run": self.dry_run,
                "requests": [request.as_dict() for request in self.requests]}

    def render(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


class Publisher(ABC):
    platform: ClassVar[str] = ""
    required_env: ClassVar[tuple[str, ...]] = ()
    text_limit: ClassVar[int] = 0

    def __init__(self, home: Path, *, env: dict[str, str] | None = None) -> None:
        self.home = Path(home)
        self._env_override = env

    def env(self, profile: str) -> dict[str, str]:
        if self._env_override is not None:
            return self._env_override
        return load_profile_env(self.home, profile)

    def token(self, profile: str, key: str) -> str:
        values = self.env(profile)
        require(values, (key,))
        return values[key]

    def check_text(self, item: QueueItem) -> None:
        if self.text_limit and len(item.text) > self.text_limit:
            raise FactoryError("TEXT_TOO_LONG", f"{len(item.text)}>{self.text_limit}")

    @abstractmethod
    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        """Request sequence for this item. No network, no secret values."""

    @abstractmethod
    def send(self, item: QueueItem, env: dict[str, str],
             tracker: DeliveryTracker) -> str:
        """Perform the live calls and return the remote id."""

    def publish(self, item: QueueItem, *, dry_run: bool = False,
                tracker: DeliveryTracker | None = None) -> PublishResult:
        if item.platform != self.platform:
            return PublishResult(False, self.platform, item.id, "PLATFORM_MISMATCH")
        env = self.env(item.profile)
        try:
            self.check_text(item)
            if not dry_run:
                require(env, self.required_env)
            requests = self.plan(item, env)
        except FactoryError as error:
            return PublishResult(False, self.platform, item.id, error.code, dry_run=dry_run)
        if dry_run:
            return PublishResult(True, self.platform, item.id, "DRY_RUN",
                                 requests=requests, dry_run=True)
        try:
            published_id = self.send(item, env, tracker or NullTracker())
        except FactoryError as error:
            return PublishResult(False, self.platform, item.id, error.code, requests=requests)
        return PublishResult(True, self.platform, item.id, "OK", published_id, requests)


def redact(keys: tuple[str, ...]) -> dict[str, str]:
    return {key.lower(): redacted(key) for key in keys}


def post_form(url: str, fields: dict[str, str], *, headers: dict[str, str] | None = None,
              timeout: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode()
    return _request(url, data=data, method="POST",
                    headers={"content-type": "application/x-www-form-urlencoded",
                             **(headers or {})}, timeout=timeout)


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None,
              timeout: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return _request(url, data=data, method="POST",
                    headers={"content-type": "application/json", **(headers or {})},
                    timeout=timeout)


def get_json(url: str, params: dict[str, str] | None = None,
             *, headers: dict[str, str] | None = None,
             timeout: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    target = url if not params else f"{url}?{urllib.parse.urlencode(params)}"
    return _request(target, data=None, method="GET", headers=headers or {}, timeout=timeout)


def _request(url: str, *, data: bytes | None, method: str,
             headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raise FactoryError(http_code(error.code)) from None
    except urllib.error.URLError as error:
        raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
    except OSError as error:
        raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
    try:
        parsed = json.loads(body)
    except ValueError:
        raise FactoryError("BODY_NOT_JSON", scrub(body[:40])) from None
    if not isinstance(parsed, dict):
        raise FactoryError("BODY_NOT_JSON")
    if parsed.get("error"):
        raise FactoryError("REMOTE_REJECTED")
    return parsed
