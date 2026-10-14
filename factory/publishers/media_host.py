"""Public URL hosting. Threads, Instagram and the TikTok import route all fetch media
server side, so local paths have to become public HTTPS URLs first. Supabase storage is the
default; bring your own host by filling media[].public_url before queueing."""
from __future__ import annotations

import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..credentials import redacted, require
from ..errors import FactoryError
from ..spec.queue import Media

DEFAULT_BUCKET = "contents-factory"
REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
UPLOAD_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class HostPlan:
    method: str
    url: str
    headers: dict[str, str]
    file: str
    public_url: str

    def as_dict(self) -> dict[str, str | dict[str, str]]:
        return {"method": self.method, "url": self.url, "headers": self.headers,
                "file": self.file, "public_url": self.public_url}


def _base(env: dict[str, str]) -> str:
    return str(env.get("SUPABASE_URL", "")).strip().rstrip("/") or "<SUPABASE_URL>"


def bucket(env: dict[str, str]) -> str:
    return str(env.get("SUPABASE_BUCKET", "")).strip() or DEFAULT_BUCKET


def remote_path(profile: str, item_id: str, filename: str) -> str:
    safe = filename.replace("/", "_")
    return f"{profile}/{item_id}/{safe}"


def plan(media: Media, *, profile: str, item_id: str, env: dict[str, str]) -> HostPlan:
    path = Path(media.path).expanduser()
    target = remote_path(profile, item_id, path.name)
    base, store = _base(env), bucket(env)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return HostPlan(
        method="POST",
        url=f"{base}/storage/v1/object/{store}/{target}",
        headers={"authorization": redacted("SUPABASE_SERVICE_ROLE_KEY"),
                 "content-type": content_type, "x-upsert": "true"},
        file=str(path),
        public_url=f"{base}/storage/v1/object/public/{store}/{target}")


def upload(media: Media, *, profile: str, item_id: str, env: dict[str, str]) -> str:
    if media.public_url:
        return media.public_url
    require(env, REQUIRED_ENV)
    path = Path(media.path).expanduser()
    if not path.is_file():
        raise FactoryError("MEDIA_FILE_MISSING")
    prepared = plan(media, profile=profile, item_id=item_id, env=env)
    content_type = prepared.headers["content-type"]
    request = urllib.request.Request(
        prepared.url, data=path.read_bytes(), method="POST",
        headers={"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
                 "Content-Type": content_type, "x-upsert": "true"})
    try:
        with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:
            if response.status not in (200, 201):
                raise FactoryError(f"HTTP_{response.status}", "storage")
    except urllib.error.HTTPError as error:
        raise FactoryError(f"HTTP_{error.code}", "storage") from None
    except OSError as error:
        raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
    return prepared.public_url
