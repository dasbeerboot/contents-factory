"""Telegram Bot API transport.

The token is a URL path segment on every call, so it is the one secret that can leak
through an exception message or a log line. Three rules follow from that and are
enforced here rather than at every call site:

* the URL is built inside `Client` and never returned, printed or stored;
* an HTTP failure becomes a code-form `FactoryError` carrying the status and, when
  Telegram said something useful, its numeric `error_code` only;
* `urllib` exception text never reaches the caller, because an invalid-header error
  puts the whole request line into `str(exc)`.

Transport is injectable so tests can assert the exact payload a preview would send
without a network call: a transport takes `(method, params, attachment)` and returns
the parsed Telegram response.
"""
from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import FactoryError, http_code, scrub

BASE_URL = "https://api.telegram.org"
TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 300
# Bot uploads stop at 50MB. The headroom covers the multipart envelope, which is
# counted too, so a file that only just fits is not rejected after the upload.
UPLOAD_MAX = 47_000_000
# sendPhoto caps at 10MB; a larger image has to travel as a document.
PHOTO_MAX = 9_500_000
# callback_data over 64 bytes makes the button silently inert, so it is refused
# before the message is sent rather than discovered when a tap does nothing.
CALLBACK_MAX = 64
CAPTION_MAX = 1024
TEXT_MAX = 4096

TOKEN_SHAPE = re.compile(r"^[0-9]{5,16}:[A-Za-z0-9_-]{30,}$")


@dataclass(frozen=True)
class Attachment:
    """One file to upload. `field` is the Telegram form field: photo, video, document."""

    field: str
    path: Path

    def filename(self) -> str:
        return self.path.name

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


class Transport(Protocol):
    def __call__(self, method: str, params: dict[str, str],
                 attachment: Attachment | None) -> dict:
        ...


def check_token(token: str) -> str:
    """Shape check only. A wrong-looking token is caught before it travels."""
    text = str(token or "").strip()
    if not text:
        raise FactoryError("TELEGRAM_TOKEN_MISSING")
    if not TOKEN_SHAPE.match(text):
        raise FactoryError("TELEGRAM_TOKEN_INVALID")
    return text


def multipart(fields: dict[str, str], attachment: Attachment,
              blob: bytes) -> tuple[str, bytes]:
    boundary = f"----contentsfactory{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        if value is None:
            continue
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{key}"\r\n\r\n').encode("utf-8")
        body += str(value).encode("utf-8") + b"\r\n"
    guessed = mimetypes.guess_type(attachment.filename())[0] or "application/octet-stream"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="{attachment.field}"; filename="{attachment.filename()}"\r\n'
             f"Content-Type: {guessed}\r\n\r\n").encode("utf-8")
    body += blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", bytes(body)


class Client:
    """One bot. `transport` is the seam tests replace; nothing else holds the token."""

    def __init__(self, token: str, *, transport: Transport | None = None,
                 base_url: str = BASE_URL) -> None:
        self.token = check_token(token)
        self.base_url = base_url.rstrip("/")
        self._transport = transport or self._http

    def call(self, method: str, **params: object) -> dict:
        return self.send(method, dict(params))

    def send(self, method: str, params: dict[str, object],
             attachment: Attachment | None = None) -> dict:
        """Raises on a not-ok response. The check lives here rather than in the
        network path so an injected transport obeys the same contract."""
        encoded = {key: encode(value) for key, value in params.items()
                   if value is not None}
        return check_response(self._transport(method, encoded, attachment), method)

    # ---------- the real network path ----------

    def _http(self, method: str, params: dict[str, str],
              attachment: Attachment | None) -> dict:
        if attachment is None:
            return self._request(method, urllib.parse.urlencode(params).encode("utf-8"),
                                 {"Content-Type": "application/x-www-form-urlencoded"},
                                 TIMEOUT_SECONDS)
        try:
            blob = attachment.path.read_bytes()
        except OSError:
            raise FactoryError("TELEGRAM_FILE_UNREADABLE",
                               attachment.filename()[:40]) from None
        content_type, body = multipart(params, attachment, blob)
        return self._request(method, body, {"Content-Type": content_type},
                             UPLOAD_TIMEOUT_SECONDS)

    def _request(self, method: str, body: bytes, headers: dict[str, str],
                 timeout: int) -> dict:
        url = f"{self.base_url}/bot{self.token}/{method}"
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise FactoryError(http_code(error.code),
                               f"{method}:{error_code(error)}") from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            # Never `str(error)`: an invalid-header failure carries the whole
            # request line, token included, in its message.
            raise FactoryError("TELEGRAM_UNREACHABLE",
                               f"{method}:{type(error).__name__}") from None
        if not isinstance(payload, dict):
            raise FactoryError("TELEGRAM_RESPONSE_INVALID", method)
        return payload


def check_response(payload: object, method: str) -> dict:
    if not isinstance(payload, dict):
        raise FactoryError("TELEGRAM_RESPONSE_INVALID", method)
    if payload.get("ok") is not True:
        code = payload.get("error_code")
        raise FactoryError("TELEGRAM_REJECTED",
                           f"{method}:{code}" if code else method)
    return payload


def encode(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def error_code(error: urllib.error.HTTPError) -> str:
    """Telegram's numeric error_code, or the scrubbed shape of whatever came back."""
    try:
        body = json.load(error)
    except Exception:  # noqa: BLE001 - a body we cannot parse must not mask the status
        return "?"
    if isinstance(body, dict) and body.get("error_code") is not None:
        return str(body["error_code"])[:8]
    return scrub(str(body))[:24] or "?"


def callback_data(action: str, item_id: str) -> str:
    """`cf:<action>:<item>`, refused when the 64 byte button limit would be passed."""
    data = f"cf:{action}:{item_id}"
    if len(data.encode("utf-8")) > CALLBACK_MAX:
        raise FactoryError("TELEGRAM_CALLBACK_TOO_LONG", action[:8])
    return data
