"""Naver blog publisher. Subprocess wrapper around the vendored node scripts.

Naver has no public write API, so publishing drives a logged-in editor. Two backends
hold that login:

* `aside` — the aside browser, which the user is already signed into. macOS only:
  "Aside supports macOS 15.0 or later" (docs.aside.com/help/get-started).
* `playwright` — a persistent Chromium profile under the runtime home, created once by
  `factory naver login`. Works on Windows and Linux, and on macOS if preferred.

Which one runs comes from `platforms.json` (`naver_blog.backend`); unset follows the
configured OS. Either way, no session means `NAVER_SESSION_MISSING` and the item stays
queued rather than failing permanently — that code is a prompt to log in.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from ..errors import FactoryError
from ..osinfo import detect_os, secure_directory, which
from ..spec.queue import QueueItem
from .base import DeliveryTracker, Publisher, PublishRequest

VENDOR_DIR = Path(__file__).resolve().parent / "naver"
SCRIPT = VENDOR_DIR / "publish.mjs"
LOGIN_SCRIPT = VENDOR_DIR / "login.mjs"
RESULT_MARKER = "__FACTORY_NAVER_RESULT__"
NODE_TIMEOUT_SECONDS = 900
LOGIN_TIMEOUT_SECONDS = 900
BACKENDS: tuple[str, ...] = ("aside", "playwright")
PROFILE_DIRNAME = "naver-profile"
SESSION_CODES = ("NAVER_SESSION_MISSING", "HOST_TAB_MISSING", "EDITOR_TAB_MISSING",
                 "EDITOR_FRAME_TIMEOUT", "ASIDE_UNAVAILABLE", "ASIDE_TIMEOUT")


def default_backend(os_name: str | None = None) -> str:
    """aside where the aside browser can exist, playwright everywhere else."""
    return "aside" if (os_name or detect_os()) == "macos" else "playwright"


def user_data_dir(home: Path) -> Path:
    """Where the playwright backend keeps the Naver session."""
    return Path(home) / "state" / PROFILE_DIRNAME


def node_binary() -> str:
    found = which("node")
    if found is None:
        raise FactoryError("NODE_MISSING", "node")
    return found


def _configured_os() -> str:
    try:
        from ..config import load_config

        return load_config().os
    except Exception:  # noqa: BLE001 - a broken config still gets a sane default
        return detect_os()


def login(home: Path, *, status: bool = False,
          wait_ms: int = 0) -> dict[str, Any]:
    """Open the Naver login window for the playwright backend, or just inspect it.

    The user logs in by hand, in their own window, including any 2FA. Nothing is
    typed for them and no cookie value is read back: the answer is a boolean.
    """
    if not LOGIN_SCRIPT.is_file():
        raise FactoryError("NAVER_SCRIPT_MISSING", "login.mjs")
    directory = user_data_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    # The Chromium profile holds the Naver session cookies, so it is locked down
    # like credentials.env rather than left at the default 0755.
    secure_directory(directory)
    command = [node_binary(), str(LOGIN_SCRIPT), "--user-data-dir", str(directory)]
    if status:
        command.append("--status")
    if wait_ms > 0:
        command.extend(["--wait-ms", str(int(wait_ms))])
    # The window waits for a human, so the subprocess timeout has to outlive the
    # wait the node side was told to do.
    budget = max(LOGIN_TIMEOUT_SECONDS, int(wait_ms / 1000) + 60) if wait_ms else \
        LOGIN_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=budget, check=False)
    except FileNotFoundError:
        raise FactoryError("NODE_MISSING") from None
    except subprocess.TimeoutExpired:
        raise FactoryError("NODE_TIMEOUT") from None
    return NaverBlogPublisher.parse_output(completed.stdout)


class NaverBlogPublisher(Publisher):
    platform: ClassVar[str] = "naver_blog"
    required_env: ClassVar[tuple[str, ...]] = ()
    text_limit: ClassVar[int] = 0

    def blog_id(self, item: QueueItem) -> str:
        """Blog id is a public account name, so it lives in platforms.json."""
        identifier = str(self.settings(item.profile).blog_id or "").strip()
        if not identifier:
            raise FactoryError("BLOG_ID_MISSING", "naver_blog")
        return identifier

    def backend(self, item: QueueItem) -> str:
        chosen = str(self.settings(item.profile).backend or "").strip()
        if not chosen:
            return default_backend(_configured_os())
        if chosen not in BACKENDS:
            raise FactoryError("NAVER_BACKEND_UNKNOWN", chosen[:32])
        return chosen

    def job(self, item: QueueItem, env: dict[str, str]) -> dict[str, Any]:
        blog_id = self.blog_id(item)
        title = item.extra.get("title", "").strip()
        if not title:
            raise FactoryError("TITLE_REQUIRED", "naver_blog")
        category = item.extra.get("category", "").strip()
        if not category:
            raise FactoryError("CATEGORY_REQUIRED", "naver_blog")
        tags = [tag.strip() for tag in item.extra.get("tags", "").split(",") if tag.strip()]
        if not tags:
            raise FactoryError("TAGS_REQUIRED", "naver_blog")
        publish_at = self._publish_at_kst(item)
        markdown_path = item.extra.get("markdown_path", "").strip()
        job: dict[str, Any] = {"blogId": blog_id, "title": title, "category": category,
                               "tags": tags, "publishAt": publish_at,
                               "backend": self.backend(item),
                               "userDataDir": str(user_data_dir(self.home))}
        if markdown_path:
            job["markdownPath"] = str(Path(markdown_path).expanduser())
        else:
            job["markdown"] = item.text
        return job

    def _publish_at_kst(self, item: QueueItem) -> str:
        explicit = item.extra.get("publish_at_kst", "").strip()
        if explicit:
            return explicit
        local = item.scheduled_dt().timestamp() + 9 * 3600
        from datetime import datetime, timezone
        moment = datetime.fromtimestamp(local, tz=timezone.utc)
        minute = moment.minute - (moment.minute % 10)
        return moment.replace(minute=minute).strftime("%Y-%m-%dT%H:%M")

    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        job = self.job(item, env)
        safe = {key: value for key, value in job.items() if key != "markdown"}
        safe["markdown_chars"] = len(str(job.get("markdown") or ""))
        backend = str(job["backend"])
        session = ("aside browser session (macOS only)" if backend == "aside"
                   else f"playwright persistent context: {user_data_dir(self.home)}")
        return [PublishRequest(
            "node_publish", "EXEC", f"node {SCRIPT}",
            {"job": safe, "backend": backend, "session": session,
             "env": {"FACTORY_ASIDE_BIN": os.environ.get("FACTORY_ASIDE_BIN", "aside")}
             if backend == "aside" else {}},
            f"{backend} drives the Naver editor: ensure_editor, inject_document, "
            "validate_editor, publish_panel")]

    def send(self, item: QueueItem, env: dict[str, str],
             tracker: DeliveryTracker) -> str:
        job = self.job(item, env)
        result = self._invoke(job, dry_run=False)
        if not result.get("ok"):
            raise self._failure(str(result.get("code") or "PUBLISH_FAILED"))
        log_no = str(result.get("log_no") or "")
        if not log_no:
            raise FactoryError("LOG_NO_MISSING")
        return log_no

    @staticmethod
    def _failure(raw: str) -> FactoryError:
        """Turn a node result code into a FactoryError whose code survives.

        The node side reports the phase inline (`PUBLISH_NOT_SENT:CATEGORY`), and a
        colon is not legal in a FactoryError code — it used to collapse the whole
        thing to `UNEXPECTED`, which `failure_mode` reads as "might have published".
        The phase belongs in the detail.
        """
        code, _, detail = raw.partition(":")
        if code in SESSION_CODES:
            return FactoryError("NAVER_SESSION_MISSING", detail[:40])
        return FactoryError(code, detail[:40])

    def _invoke(self, job: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        if not SCRIPT.is_file():
            raise FactoryError("NAVER_SCRIPT_MISSING")
        handle, name = tempfile.mkstemp(prefix="factory-naver-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as target:
                json.dump(job, target, ensure_ascii=False, sort_keys=True)
            command = [node_binary(), str(SCRIPT), "--job", name]
            backend = str(job.get("backend") or "").strip()
            if backend:
                command.extend(["--backend", backend])
            if dry_run:
                command.append("--dry-run")
            try:
                completed = subprocess.run(command, capture_output=True, text=True,
                                           timeout=NODE_TIMEOUT_SECONDS, check=False)
            except FileNotFoundError:
                raise FactoryError("NODE_MISSING") from None
            except subprocess.TimeoutExpired:
                raise FactoryError("NODE_TIMEOUT") from None
            return self.parse_output(completed.stdout)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def parse_output(stdout: str) -> dict[str, Any]:
        for line in reversed((stdout or "").splitlines()):
            if line.startswith(RESULT_MARKER):
                try:
                    parsed = json.loads(line[len(RESULT_MARKER):])
                except ValueError:
                    raise FactoryError("NODE_RESULT_INVALID") from None
                if not isinstance(parsed, dict):
                    raise FactoryError("NODE_RESULT_INVALID")
                return parsed
        raise FactoryError("NODE_RESULT_MISSING")
