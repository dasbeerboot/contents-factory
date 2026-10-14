"""Instagram publisher, ported from saju-threads ig_publish.sh and ig_preview.sh.

Three shapes: single image, carousel (child containers then a CAROUSEL parent) and REELS.
Containers are polled on status_code until FINISHED, then media_publish takes creation_id.
"""
from __future__ import annotations

import time
from typing import Any, ClassVar

from ..credentials import redacted
from ..errors import FactoryError
from ..spec.queue import QueueItem
from .base import Publisher, PublishRequest, get_json, post_form

IG_HOST = "https://graph.instagram.com"
IG_API_VERSION = "v23.0"
CAPTION_LIMIT = 2200
CAROUSEL_MIN = 2
CAROUSEL_MAX = 10
POLL_TRIES = 8
POLL_WAIT_SECONDS = 30


class InstagramPublisher(Publisher):
    platform: ClassVar[str] = "instagram"
    required_env: ClassVar[tuple[str, ...]] = ("IG_ACCESS_TOKEN", "IG_USER_ID")
    text_limit: ClassVar[int] = CAPTION_LIMIT

    def _node(self, env: dict[str, str], path: str) -> str:
        user_id = str(env.get("IG_USER_ID", "")).strip() or "<IG_USER_ID>"
        return f"{IG_HOST}/{IG_API_VERSION}/{user_id}/{path}"

    def _shape(self, item: QueueItem) -> str:
        explicit = item.extra.get("ig_shape", "")
        if explicit in {"image", "carousel", "reels"}:
            return explicit
        if not item.media:
            raise FactoryError("MEDIA_REQUIRED", "instagram")
        if item.media[0].kind == "video":
            return "reels"
        return "carousel" if len(item.media) >= CAROUSEL_MIN else "image"

    def _urls(self, item: QueueItem) -> list[str]:
        urls = item.public_media()
        if not urls:
            raise FactoryError("MEDIA_PUBLIC_URL_REQUIRED", "instagram")
        if len(urls) != len(item.media):
            raise FactoryError("MEDIA_PUBLIC_URL_PARTIAL", f"{len(urls)}/{len(item.media)}")
        return urls

    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        token = redacted("IG_ACCESS_TOKEN")
        shape = self._shape(item)
        urls = self._urls(item)
        requests: list[PublishRequest] = []
        if shape == "reels":
            fields: dict[str, Any] = {"media_type": "REELS", "video_url": urls[0],
                                      "caption": item.text, "share_to_feed": "true",
                                      "access_token": token}
            cover = item.extra.get("cover_url", "")
            if cover:
                fields["cover_url"] = cover
            if item.extra.get("ai_generated") == "true":
                fields["is_ai_generated"] = "true"
            requests.append(PublishRequest("create_reels", "POST", self._node(env, "media"),
                                           fields, "reels container"))
        elif shape == "carousel":
            if not CAROUSEL_MIN <= len(urls) <= CAROUSEL_MAX:
                raise FactoryError("CAROUSEL_SIZE_INVALID", str(len(urls)))
            for index, url in enumerate(urls, 1):
                requests.append(PublishRequest(
                    f"create_child_{index}", "POST", self._node(env, "media"),
                    {"image_url": url, "is_carousel_item": "true", "access_token": token},
                    "carousel child"))
            requests.append(PublishRequest(
                "create_carousel", "POST", self._node(env, "media"),
                {"media_type": "CAROUSEL", "children": "<child_ids_joined_by_comma>",
                 "caption": item.text, "access_token": token}, "carousel parent"))
        else:
            requests.append(PublishRequest(
                "create_image", "POST", self._node(env, "media"),
                {"image_url": urls[0], "caption": item.text, "access_token": token},
                "single image container"))
        requests.append(PublishRequest(
            "poll_container", "GET", f"{IG_HOST}/{IG_API_VERSION}/<container_id>",
            {"fields": "status_code,status", "access_token": token},
            f"poll until FINISHED, up to {POLL_TRIES} rounds"))
        requests.append(PublishRequest(
            "media_publish", "POST", self._node(env, "media_publish"),
            {"creation_id": "<container_id>", "access_token": token}, ""))
        requests.append(PublishRequest(
            "permalink", "GET", f"{IG_HOST}/{IG_API_VERSION}/<media_id>",
            {"fields": "permalink", "access_token": token}, "receipt only"))
        return requests

    def send(self, item: QueueItem, env: dict[str, str]) -> str:
        token = env["IG_ACCESS_TOKEN"]
        shape = self._shape(item)
        urls = self._urls(item)
        if shape == "reels":
            fields = {"media_type": "REELS", "video_url": urls[0], "caption": item.text,
                      "share_to_feed": "true", "access_token": token}
            cover = item.extra.get("cover_url", "")
            if cover:
                fields["cover_url"] = cover
            if item.extra.get("ai_generated") == "true":
                fields["is_ai_generated"] = "true"
            container = self._container(env, fields)
        elif shape == "carousel":
            children = [self._container(env, {"image_url": url, "is_carousel_item": "true",
                                              "access_token": token}) for url in urls]
            container = self._container(env, {"media_type": "CAROUSEL",
                                              "children": ",".join(children),
                                              "caption": item.text, "access_token": token})
        else:
            container = self._container(env, {"image_url": urls[0], "caption": item.text,
                                              "access_token": token})
        self._await_container(container, token)
        published = post_form(self._node(env, "media_publish"),
                              {"creation_id": container, "access_token": token})
        media_id = str(published.get("id") or "")
        if not media_id:
            raise FactoryError("MEDIA_ID_MISSING")
        return media_id

    def _container(self, env: dict[str, str], fields: dict[str, str]) -> str:
        response = post_form(self._node(env, "media"), fields)
        container = str(response.get("id") or "")
        if not container:
            raise FactoryError("CONTAINER_ID_INVALID")
        return container

    def _await_container(self, container: str, token: str) -> None:
        for _ in range(POLL_TRIES):
            status = get_json(f"{IG_HOST}/{IG_API_VERSION}/{container}",
                              {"fields": "status_code,status", "access_token": token})
            code = str(status.get("status_code") or "")
            if code == "FINISHED":
                return
            if code in {"ERROR", "EXPIRED"}:
                raise FactoryError("CONTAINER_FAILED", code)
            time.sleep(POLL_WAIT_SECONDS)
        raise FactoryError("CONTAINER_IN_PROGRESS")
