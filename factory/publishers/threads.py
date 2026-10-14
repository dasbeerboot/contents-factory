"""Threads publisher, ported from saju-threads publish.sh and threads_delivery.py.

Two stages: create a container, then threads_publish it. IMAGE and VIDEO containers need
a public URL because Threads fetches the media server side; local paths cannot be uploaded.
VIDEO containers are polled for FINISHED before publishing and topic_tag is omitted there.
"""
from __future__ import annotations

import time
from typing import Any, ClassVar

from ..credentials import redacted
from ..errors import FactoryError
from ..spec.queue import QueueItem
from .base import Publisher, PublishRequest, get_json, post_form

API_BASE = "https://graph.threads.net/v1.0"
TEXT_LIMIT = 500
POLL_TRIES = 10
POLL_WAIT_SECONDS = 20
CONTAINER_SETTLE_SECONDS = 3


class ThreadsPublisher(Publisher):
    platform: ClassVar[str] = "threads"
    required_env: ClassVar[tuple[str, ...]] = ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
    text_limit: ClassVar[int] = TEXT_LIMIT

    def _user_id(self, env: dict[str, str]) -> str:
        user_id = str(env.get("THREADS_USER_ID", "")).strip()
        if not user_id:
            return "<THREADS_USER_ID>"
        if not user_id.isdigit():
            raise FactoryError("ACCOUNT_ID_INVALID")
        return user_id

    def _media_kind(self, item: QueueItem) -> str:
        if not item.media:
            return "TEXT"
        return "VIDEO" if item.media[0].kind == "video" else "IMAGE"

    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        user_id = self._user_id(env)
        token = redacted("THREADS_ACCESS_TOKEN")
        media_type = self._media_kind(item)
        create: dict[str, Any] = {"media_type": media_type, "access_token": token}
        if media_type == "TEXT":
            create["text"] = item.text
            topic = item.extra.get("topic_tag", "")
            if topic:
                create["topic_tag"] = topic
        else:
            public = item.public_media()
            if not public:
                raise FactoryError("MEDIA_PUBLIC_URL_REQUIRED", media_type)
            create["text"] = item.text
            create["image_url" if media_type == "IMAGE" else "video_url"] = public[0]
        requests = [PublishRequest("create_container", "POST", f"{API_BASE}/{user_id}/threads",
                                   create, f"{media_type} container")]
        if media_type != "TEXT":
            requests.append(PublishRequest(
                "poll_container", "GET", f"{API_BASE}/<creation_id>",
                {"fields": "status,error_message", "access_token": token},
                f"poll until FINISHED, up to {POLL_TRIES} rounds"))
        requests.append(PublishRequest(
            "publish", "POST", f"{API_BASE}/{user_id}/threads_publish",
            {"creation_id": "<creation_id>", "access_token": token},
            "parent post"))
        for index, reply in enumerate(self._replies(item), 1):
            requests.append(PublishRequest(
                f"reply_{index}_container", "POST", f"{API_BASE}/{user_id}/threads",
                {"media_type": "TEXT", "text": reply, "reply_to_id": "<previous_post_id>",
                 "access_token": token}, "self reply chain"))
            requests.append(PublishRequest(
                f"reply_{index}_publish", "POST", f"{API_BASE}/{user_id}/threads_publish",
                {"creation_id": f"<reply_{index}_creation_id>", "access_token": token}, ""))
        return requests

    def _replies(self, item: QueueItem) -> list[str]:
        raw = item.extra.get("replies", "")
        replies = [line for line in raw.split("\n") if line.strip()]
        for reply in replies:
            if len(reply) > TEXT_LIMIT:
                raise FactoryError("REPLY_TOO_LONG", str(len(reply)))
        return replies

    def send(self, item: QueueItem, env: dict[str, str]) -> str:
        user_id = self._user_id(env)
        token = env["THREADS_ACCESS_TOKEN"]
        media_type = self._media_kind(item)
        fields: dict[str, str] = {"media_type": media_type, "text": item.text,
                                  "access_token": token}
        if media_type == "TEXT":
            topic = item.extra.get("topic_tag", "")
            if topic:
                fields["topic_tag"] = topic
        else:
            public = item.public_media()
            if not public:
                raise FactoryError("MEDIA_PUBLIC_URL_REQUIRED", media_type)
            fields["image_url" if media_type == "IMAGE" else "video_url"] = public[0]
        created = post_form(f"{API_BASE}/{user_id}/threads", fields)
        creation_id = str(created.get("id") or "")
        if not creation_id.isdigit():
            raise FactoryError("CONTAINER_ID_INVALID")
        if media_type != "TEXT":
            self._await_container(creation_id, token)
        else:
            time.sleep(CONTAINER_SETTLE_SECONDS)
        published = post_form(f"{API_BASE}/{user_id}/threads_publish",
                              {"creation_id": creation_id, "access_token": token})
        post_id = str(published.get("id") or "")
        if not post_id.isdigit():
            raise FactoryError("POST_ID_INVALID")
        self._send_replies(item, user_id, token, post_id)
        return post_id

    def _await_container(self, creation_id: str, token: str) -> None:
        for _ in range(POLL_TRIES):
            status = get_json(f"{API_BASE}/{creation_id}",
                              {"fields": "status,error_message", "access_token": token})
            state = str(status.get("status") or "")
            if state == "FINISHED":
                return
            if state in {"ERROR", "EXPIRED"}:
                raise FactoryError("CONTAINER_FAILED", state)
            time.sleep(POLL_WAIT_SECONDS)
        raise FactoryError("CONTAINER_IN_PROGRESS")

    def _send_replies(self, item: QueueItem, user_id: str, token: str, parent: str) -> None:
        for reply in self._replies(item):
            container = post_form(f"{API_BASE}/{user_id}/threads",
                                  {"media_type": "TEXT", "text": reply,
                                   "reply_to_id": parent, "access_token": token})
            creation_id = str(container.get("id") or "")
            if not creation_id.isdigit():
                raise FactoryError("CONTAINER_ID_INVALID")
            time.sleep(CONTAINER_SETTLE_SECONDS)
            published = post_form(f"{API_BASE}/{user_id}/threads_publish",
                                  {"creation_id": creation_id, "access_token": token})
            parent = str(published.get("id") or "")
            if not parent.isdigit():
                raise FactoryError("POST_ID_INVALID")
