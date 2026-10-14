"""YouTube publisher, ported from saju-threads yt_upload.py, yt_oauth.py and platform_cta.py.

Resumable upload: a snippet+status init POST returns a Location, the bytes go up with PUT.
Uploads land as private. publishAt only takes effect while privacyStatus is private and the
timestamp is in the future, so a past or imminent stamp is dropped rather than turned into an
instant public post. An unverified GCP project keeps uploads private regardless.
"""
from __future__ import annotations

import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, ClassVar

from ..credentials import redacted
from ..errors import FactoryError
from ..spec.queue import QueueItem
from .base import Publisher, PublishRequest, post_form

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CATEGORY_ID = "27"
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
SCHEDULE_MARGIN_SECONDS = 300
DEFAULT_AUDIO_LANGUAGE = "en"


def access_token(env: dict[str, str]) -> str:
    client_id = env.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = env.get("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh = env.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh:
        response = post_form(TOKEN_URL, {"client_id": client_id,
                                         "client_secret": client_secret,
                                         "refresh_token": refresh,
                                         "grant_type": "refresh_token"})
        token = str(response.get("access_token") or "")
        if token:
            return token
        raise FactoryError("TOKEN_REFRESH_FAILED")
    stored = env.get("YOUTUBE_ACCESS_TOKEN", "").strip()
    if stored:
        return stored
    raise FactoryError("ENV_MISSING", "YOUTUBE_REFRESH_TOKEN")


def usable_publish_at(value: str | None, *, now: float | None = None) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"skip", "none", "manual", "-", "auto"}:
        return None
    try:
        epoch = calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        raise FactoryError("PUBLISH_AT_INVALID") from None
    if epoch <= (now if now is not None else time.time()) + SCHEDULE_MARGIN_SECONDS:
        return None
    return text


class YouTubePublisher(Publisher):
    platform: ClassVar[str] = "youtube"
    required_env: ClassVar[tuple[str, ...]] = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                                               "YOUTUBE_REFRESH_TOKEN")
    text_limit: ClassVar[int] = DESCRIPTION_MAX

    def _video_path(self, item: QueueItem) -> Path:
        videos = [entry for entry in item.media if entry.kind == "video"]
        if not videos:
            raise FactoryError("MEDIA_REQUIRED", "youtube")
        return Path(videos[0].path).expanduser()

    def body(self, item: QueueItem, *, now: float | None = None) -> dict[str, Any]:
        first_line = item.text.splitlines()[0] if item.text.strip() else ""
        title = (item.extra.get("title") or first_line)[:TITLE_MAX]
        if not title:
            raise FactoryError("TITLE_REQUIRED", "youtube")
        privacy = item.extra.get("privacy", "private")
        if privacy not in {"private", "public", "unlisted"}:
            raise FactoryError("PRIVACY_INVALID", privacy[:16])
        status: dict[str, Any] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if privacy == "private":
            scheduled = usable_publish_at(item.extra.get("publish_at") or item.scheduled_at,
                                          now=now)
            if scheduled:
                status["publishAt"] = scheduled
        if item.extra.get("ai_generated") == "true":
            status["containsSyntheticMedia"] = True
        tags = [tag for tag in item.extra.get("tags", "").split(",") if tag.strip()]
        return {"snippet": {"title": title,
                            "description": item.text[:DESCRIPTION_MAX],
                            "tags": tags,
                            "categoryId": item.extra.get("category_id", CATEGORY_ID),
                            "defaultAudioLanguage": item.extra.get(
                                "audio_language", DEFAULT_AUDIO_LANGUAGE)},
                "status": status}

    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        path = self._video_path(item)
        size = path.stat().st_size if path.is_file() else 0
        query = urllib.parse.urlencode({"uploadType": "resumable", "part": "snippet,status"})
        return [
            PublishRequest("refresh_token", "POST", TOKEN_URL,
                           {"client_id": redacted("YOUTUBE_CLIENT_ID"),
                            "client_secret": redacted("YOUTUBE_CLIENT_SECRET"),
                            "refresh_token": redacted("YOUTUBE_REFRESH_TOKEN"),
                            "grant_type": "refresh_token"}, "short lived access token"),
            PublishRequest("resumable_init", "POST", f"{UPLOAD_URL}?{query}",
                           {"authorization": redacted("YOUTUBE_ACCESS_TOKEN"),
                            "x_upload_content_length": str(size),
                            "x_upload_content_type": "video/mp4",
                            "body": self.body(item)},
                           "Location header carries the session url"),
            PublishRequest("resumable_put", "PUT", "<resumable_session_url>",
                           {"authorization": redacted("YOUTUBE_ACCESS_TOKEN"),
                            "content_type": "video/mp4",
                            "content_length": str(size),
                            "file": str(path)}, "bytes"),
        ]

    def send(self, item: QueueItem, env: dict[str, str]) -> str:
        path = self._video_path(item)
        if not path.is_file():
            raise FactoryError("MEDIA_FILE_MISSING")
        token = access_token(env)
        size = path.stat().st_size
        body = json.dumps(self.body(item), ensure_ascii=False, sort_keys=True).encode()
        query = urllib.parse.urlencode({"uploadType": "resumable", "part": "snippet,status"})
        init = urllib.request.Request(
            f"{UPLOAD_URL}?{query}", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"})
        try:
            with urllib.request.urlopen(init, timeout=60) as response:
                location = response.headers.get("Location")
        except urllib.error.HTTPError as error:
            raise FactoryError(f"HTTP_{error.code}", "init") from None
        except OSError as error:
            raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
        if not location:
            raise FactoryError("INIT_NO_LOCATION")
        with path.open("rb") as handle:
            payload = handle.read()
        put = urllib.request.Request(
            location, data=payload, method="PUT",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                     "Content-Length": str(size)})
        try:
            with urllib.request.urlopen(put, timeout=600) as response:
                result = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as error:
            raise FactoryError(f"HTTP_{error.code}", "put") from None
        except OSError as error:
            raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
        except ValueError:
            raise FactoryError("BODY_NOT_JSON") from None
        video_id = str(result.get("id") or "")
        if not video_id:
            raise FactoryError("VIDEO_ID_MISSING")
        return video_id
