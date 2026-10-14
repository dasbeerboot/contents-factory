"""Pull a finished asset out of a provider URL and onto disk.

Generated media is only ever a signed URL until something saves it, and those
URLs expire. Every generator funnels through here so an output path in a brief
always means a file that is still there tomorrow.

No credential is ever attached: these are pre-signed result links. The URL is
never echoed on failure either, because a signed link is a bearer token.
"""
from __future__ import annotations

import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..errors import FactoryError

TIMEOUT_SECONDS = 300
CHUNK = 1 << 16
USER_AGENT = "contents-factory/0.1"
MAX_BYTES = 512 * 1024 * 1024
EXTENSIONS: dict[str, str] = {"image/png": ".png", "image/jpeg": ".jpg",
                              "image/webp": ".webp", "video/mp4": ".mp4",
                              "audio/mpeg": ".mp3", "audio/wav": ".wav"}


def extension_for(url: str, content_type: str = "") -> str:
    """Prefer the server's type; fall back to the path, then to .bin."""
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in EXTENSIONS:
        return EXTENSIONS[kind]
    guessed = mimetypes.guess_extension(kind) if kind else None
    if guessed:
        return guessed
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix if suffix and len(suffix) <= 5 else ".bin"


def download(url: str, destination: Path, *,
             timeout: int = TIMEOUT_SECONDS) -> Path:
    """Stream `url` to `destination`, returning the path actually written.

    When `destination` has no suffix the one implied by the response is added,
    so callers can hand in a stem and still get `.png` or `.mp4`.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise FactoryError("ASSET_URL_INVALID")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not target.suffix:
                target = target.with_suffix(
                    extension_for(url, response.headers.get("Content-Type", "")))
            written = 0
            temporary = target.with_name(target.name + ".part")
            with temporary.open("wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_BYTES:
                        handle.close()
                        temporary.unlink(missing_ok=True)
                        raise FactoryError("ASSET_TOO_LARGE", str(MAX_BYTES))
                    handle.write(block)
    except urllib.error.HTTPError as error:
        raise FactoryError(f"HTTP_{int(error.code)}", "asset_download") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FactoryError("ASSET_DOWNLOAD_FAILED") from None
    if written == 0:
        temporary.unlink(missing_ok=True)
        raise FactoryError("ASSET_EMPTY")
    temporary.replace(target)
    return target
