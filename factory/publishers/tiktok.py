"""TikTok publisher, ported from saju-threads tiktok_publish.py.

Route: Higgsfield MCP. media_import_url hands the server a public HTTPS URL (no bytes leave
this machine), then tiktok_prepare_publish opens a session whose _meta carries the
publish_token, and tiktok_publish submits with that token. Direct TikTok API is not used:
an unreviewed app can only post privately.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, ClassVar

from ..credentials import redacted
from ..errors import FactoryError, scrub
from ..spec.queue import QueueItem
from .base import DeliveryTracker, Publisher, PublishRequest

MCP_URL = "https://mcp.higgsfield.ai/mcp"
PROTOCOL_VERSION = "2025-06-18"
TITLE_MAX = 150
DESC_MAX = 4000
IMPORT_MAX_BYTES = 50 * 1024 * 1024
PHOTO_MAX = 35
POLL_TRIES = 10
TOKEN_KEYS = ("tiktok_publish_token", "publish_token", "publish_authorization")
_PUBLISH_ID = re.compile(r'publish_id["\']?\s*[:=]\s*["\']?([A-Za-z0-9._~+/=-]{6,})')


class McpClient:
    """JSON-RPC over HTTP with SSE-framed responses, stdlib only."""

    def __init__(self, token: str) -> None:
        self.token = token
        self._id = 0
        self._ready = False
        self.last_meta: dict[str, Any] = {}
        self.last_structured: dict[str, Any] = {}

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                           "params": params or {}}, sort_keys=True).encode()
        request = urllib.request.Request(MCP_URL, data=body, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise FactoryError("MCP_TOKEN_EXPIRED", f"HTTP_{error.code}") from None
            raise FactoryError(f"HTTP_{error.code}") from None
        except OSError as error:
            raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
        frames: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                parsed = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                frames.append(parsed)
        return frames

    def init(self) -> None:
        if self._ready:
            return
        self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                 "clientInfo": {"name": "contents-factory", "version": "1"}})
        self._ready = True

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[Any, str]:
        self.init()
        chunks: list[str] = []
        meta: dict[str, Any] = {}
        structured: dict[str, Any] = {}
        for frame in self._rpc("tools/call", {"name": name, "arguments": arguments}):
            result = frame.get("result") or {}
            for block in result.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text") or ""))
            if isinstance(result.get("_meta"), dict):
                meta.update(result["_meta"])
            if isinstance(result.get("structuredContent"), dict):
                structured.update(result["structuredContent"])
            if frame.get("error"):
                raise FactoryError("MCP_TOOL_ERROR", name[:32])
        self.last_meta, self.last_structured = meta, structured
        text = "\n".join(chunks)
        try:
            return json.loads(text), text
        except ValueError:
            return None, text


def publish_token(raw: str, meta: dict[str, Any], structured: dict[str, Any]) -> str:
    for source in (meta, structured):
        for key in TOKEN_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    match = re.search(r'(?:tiktok_)?publish_token["\']?\s*[:=]\s*["\']?([A-Za-z0-9._~+/=-]{8,})',
                      str(raw or ""))
    return match.group(1) if match else ""


class TikTokPublisher(Publisher):
    platform: ClassVar[str] = "tiktok"
    required_env: ClassVar[tuple[str, ...]] = ("HIGGSFIELD_MCP_TOKEN", "TIKTOK_CONNECTOR_ID")
    text_limit: ClassVar[int] = DESC_MAX

    def _media_type(self, item: QueueItem) -> str:
        if not item.media:
            raise FactoryError("MEDIA_REQUIRED", "tiktok")
        if item.media[0].kind == "video":
            return "VIDEO"
        if len(item.media) > PHOTO_MAX:
            raise FactoryError("PHOTO_COUNT_INVALID", str(len(item.media)))
        return "PHOTO"

    def _caption(self, item: QueueItem) -> tuple[str, str]:
        first_line = item.text.splitlines()[0] if item.text.strip() else ""
        title = (item.extra.get("title") or first_line)[:TITLE_MAX]
        description = item.text[:DESC_MAX]
        if not title:
            raise FactoryError("TITLE_REQUIRED", "tiktok")
        return title, description

    def _caption_fields(self, title: str, description: str, media_type: str) -> dict[str, str]:
        fields = {"title": title}
        if media_type == "PHOTO":
            fields["description"] = description
        return fields

    def _urls(self, item: QueueItem) -> list[str]:
        urls = item.public_media()
        if not urls:
            raise FactoryError("MEDIA_PUBLIC_URL_REQUIRED", "tiktok")
        return urls

    def _prepare_arguments(self, item: QueueItem, env: dict[str, str],
                           urls: list[str], *, redact: bool) -> dict[str, Any]:
        media_type = self._media_type(item)
        title, description = self._caption(item)
        connector = (redacted("TIKTOK_CONNECTOR_ID") if redact
                     else env["TIKTOK_CONNECTOR_ID"])
        arguments: dict[str, Any] = {
            "connector_id": connector,
            "mode": item.extra.get("mode", "DIRECT_POST"),
            "media_type": media_type,
            **self._caption_fields(title, description, media_type),
            "is_aigc": item.extra.get("is_aigc", "true") == "true",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_content_disclosure": {"enabled": False, "your_brand": False,
                                              "branded_content": False},
        }
        if media_type == "PHOTO":
            arguments["photo_images"] = urls
            arguments["photo_cover_index"] = 0
        else:
            arguments["video_url"] = urls[0]
        if arguments["mode"] == "DIRECT_POST":
            arguments["privacy_level"] = item.extra.get("privacy_level", "PUBLIC_TO_EVERYONE")
        return arguments

    def plan(self, item: QueueItem, env: dict[str, str]) -> list[PublishRequest]:
        urls = self._urls(item)
        media_type = self._media_type(item)
        arguments = self._prepare_arguments(item, env, urls, redact=True)
        bearer = redacted("HIGGSFIELD_MCP_TOKEN")
        requests = [PublishRequest(
            "mcp_accounts", "POST", MCP_URL,
            {"method": "tools/call", "name": "tiktok_accounts", "arguments": {},
             "authorization": bearer}, "connector must be active")]
        for index, url in enumerate(urls, 1):
            requests.append(PublishRequest(
                f"media_import_url_{index}", "POST", MCP_URL,
                {"method": "tools/call", "name": "media_import_url",
                 "arguments": {"url": url,
                               "type": "video" if media_type == "VIDEO" else "image"},
                 "authorization": bearer},
                f"server side fetch, cap {IMPORT_MAX_BYTES} bytes"))
        requests.append(PublishRequest(
            "tiktok_prepare_publish", "POST", MCP_URL,
            {"method": "tools/call", "name": "tiktok_prepare_publish",
             "arguments": arguments, "authorization": bearer},
            "publish_token arrives in result._meta.tiktok_publish_token"))
        publish_arguments = dict(arguments)
        publish_arguments["publish_session_id"] = "<publish_session_id>"
        publish_arguments["publish_token"] = redacted("TIKTOK_PUBLISH_TOKEN")
        if media_type == "PHOTO":
            publish_arguments["auto_add_music"] = True
        else:
            publish_arguments["auto_add_music"] = False
        requests.append(PublishRequest(
            "tiktok_publish", "POST", MCP_URL,
            {"method": "tools/call", "name": "tiktok_publish",
             "arguments": publish_arguments, "authorization": bearer}, ""))
        requests.append(PublishRequest(
            "tiktok_publish_status", "POST", MCP_URL,
            {"method": "tools/call", "name": "tiktok_publish_status",
             "arguments": {"publish_id": "<publish_id>"}, "authorization": bearer},
            f"poll up to {POLL_TRIES} rounds"))
        return requests

    def send(self, item: QueueItem, env: dict[str, str],
             tracker: DeliveryTracker) -> str:
        client = McpClient(env["HIGGSFIELD_MCP_TOKEN"])
        accounts, raw = client.call("tiktok_accounts", {})
        if env["TIKTOK_CONNECTOR_ID"] not in str(accounts or raw):
            raise FactoryError("TIKTOK_NOT_CONNECTED")
        media_type = self._media_type(item)
        hosted: list[str] = []
        for url in self._urls(item):
            _, import_raw = client.call(
                "media_import_url",
                {"url": url, "type": "video" if media_type == "VIDEO" else "image"})
            media_id = str((client.last_structured or {}).get("media_id") or "")
            if not media_id:
                raise FactoryError("IMPORT_NO_MEDIA_ID", scrub(import_raw[:40]))
            hosted.append(self._hosted_url(client, media_id, media_type))
        arguments = self._prepare_arguments(item, env, hosted, redact=False)
        _, prepare_raw = client.call("tiktok_prepare_publish", arguments)
        session = str((client.last_structured or {}).get("publish_session_id") or "")
        if not session:
            raise FactoryError("PREPARE_FAIL")
        token = publish_token(prepare_raw, client.last_meta, client.last_structured)
        if not token:
            raise FactoryError("PREPARE_NO_PUBLISH_TOKEN")
        publish_arguments = dict(arguments)
        publish_arguments.update({"publish_session_id": session, "publish_token": token,
                                  "auto_add_music": media_type == "PHOTO"})
        _, publish_raw = client.call("tiktok_publish", publish_arguments)
        identifier = str((client.last_structured or {}).get("publish_id") or "")
        if not identifier:
            match = _PUBLISH_ID.search(publish_raw)
            identifier = match.group(1) if match else ""
        if not identifier:
            raise FactoryError("PUBLISH_FAIL")
        return identifier

    def _hosted_url(self, client: McpClient, media_id: str, media_type: str) -> str:
        kind = "video" if media_type == "VIDEO" else "image"
        cursor: float | None = None
        for _ in range(4):
            arguments: dict[str, Any] = {"type": kind, "size": 50}
            if cursor is not None:
                arguments["cursor"] = cursor
            _, raw = client.call("show_medias", arguments)
            for line in str(raw).splitlines():
                if media_id in line:
                    found = re.search(r'https?://[^\s"\'<>\\,]+', line)
                    if found:
                        return found.group(0).rstrip(".,\"')")
            nxt = re.search(r"next_cursor:\s*([0-9.]+)", str(raw))
            if not nxt:
                break
            cursor = float(nxt.group(1))
        raise FactoryError("IMPORT_NO_HOSTED_URL")
