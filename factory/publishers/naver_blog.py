"""Naver blog publisher. Subprocess wrapper around the vendored node scripts.

Naver has no public write API, so the vendored publish.mjs drives the logged in editor
through the aside browser. If the session is not there the item stays queued with
NAVER_SESSION_MISSING rather than failing permanently.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from ..errors import FactoryError
from ..spec.queue import QueueItem
from .base import Publisher, PublishRequest

VENDOR_DIR = Path(__file__).resolve().parent / "naver"
SCRIPT = VENDOR_DIR / "publish.mjs"
RESULT_MARKER = "__FACTORY_NAVER_RESULT__"
NODE_TIMEOUT_SECONDS = 900
SESSION_CODES = ("NAVER_SESSION_MISSING", "HOST_TAB_MISSING", "EDITOR_TAB_MISSING",
                 "EDITOR_FRAME_TIMEOUT", "ASIDE_UNAVAILABLE", "ASIDE_TIMEOUT")


class NaverBlogPublisher(Publisher):
    platform: ClassVar[str] = "naver_blog"
    required_env: ClassVar[tuple[str, ...]] = ("NAVER_BLOG_ID",)
    text_limit: ClassVar[int] = 0

    def job(self, item: QueueItem, env: dict[str, str]) -> dict[str, Any]:
        blog_id = str(env.get("NAVER_BLOG_ID", "")).strip() or "<NAVER_BLOG_ID>"
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
                               "tags": tags, "publishAt": publish_at}
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
        return [PublishRequest(
            "node_publish", "EXEC", f"node {SCRIPT}",
            {"job": safe, "env": {"FACTORY_ASIDE_BIN": os.environ.get("FACTORY_ASIDE_BIN", "aside")}},
            "aside browser drives the Naver editor: ensure_editor, inject_document, "
            "validate_editor, publish_panel")]

    def send(self, item: QueueItem, env: dict[str, str]) -> str:
        job = self.job(item, env)
        result = self._invoke(job, dry_run=False)
        if not result.get("ok"):
            code = str(result.get("code") or "PUBLISH_FAILED")
            raise FactoryError("NAVER_SESSION_MISSING" if code in SESSION_CODES else code)
        log_no = str(result.get("log_no") or "")
        if not log_no:
            raise FactoryError("LOG_NO_MISSING")
        return log_no

    def _invoke(self, job: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        if not SCRIPT.is_file():
            raise FactoryError("NAVER_SCRIPT_MISSING")
        handle, name = tempfile.mkstemp(prefix="factory-naver-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as target:
                json.dump(job, target, ensure_ascii=False, sort_keys=True)
            command = ["node", str(SCRIPT), "--job", name]
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
