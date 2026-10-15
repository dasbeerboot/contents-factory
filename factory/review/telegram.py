"""Telegram review channel: show one queue item on a phone and ask to publish it.

Telegram is not an entrance. It does two things and refuses the third:

1. preview what was made (text inline, image as a photo, video as a playable video);
2. ask "publish?" with three buttons.

It never generates, never interviews and never writes queue state: the buttons are
handled by `factory telegram serve`, which calls the same queue functions the CLI
does.

Two upload rules are ported from the shortform pipeline and are not negotiable:

* `sendVideo` carries `width`, `height` and `supports_streaming`. Without them the
  phone shows a grey file row instead of a player, and a vertical clip gets boxed
  into a landscape frame.
* A document attachment is not a preview. Over the 50MB bot ceiling the clip is
  re-encoded down and labelled as a reduced preview, because the point is to watch
  it on the phone, not to download it.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

from .. import i18n, media
from ..errors import FactoryError
from ..spec.queue import QueueItem
from ..telegram import api

# Telegram uses these only to shape the player box, and every vertical format this
# factory makes is 9:16, so the declared pair stays constant even when a reduced
# preview was encoded smaller.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
PREVIEW_SUFFIX = ".preview.mp4"
PREVIEW_HEIGHT = 1280
PREVIEW_CRF = "32"

ACTIONS: tuple[str, ...] = ("ok", "no", "ed")
BUTTON_KEYS: dict[str, str] = {"ok": "telegram.button.approve",
                               "no": "telegram.button.reject",
                               "ed": "telegram.button.edit"}


@dataclass
class Payload:
    """One Telegram call. `file` is a local path, uploaded as `field`."""

    method: str
    params: dict[str, object]
    file: Path | None = None
    field: str = ""

    def attachment(self) -> api.Attachment | None:
        return api.Attachment(field=self.field, path=self.file) if self.file else None

    def as_dict(self) -> dict[str, object]:
        return {"method": self.method,
                "params": dict(self.params),
                "file": str(self.file) if self.file else None,
                "field": self.field}


@dataclass
class Preview:
    payloads: list[Payload] = field(default_factory=list)
    reduced: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"reduced": self.reduced, "note": self.note,
                "payloads": [entry.as_dict() for entry in self.payloads]}


def markup(item_id: str, *, lang: str = "en") -> dict[str, object]:
    """The three buttons. `callback_data` length is checked before anything is sent."""
    translate = i18n.Translator(lang)
    return {"inline_keyboard": [[{"text": translate(BUTTON_KEYS[action]),
                                  "callback_data": api.callback_data(action, item_id)}
                                 for action in ACTIONS]]}


def header(item: QueueItem, *, lang: str = "en", reduced: bool = False) -> str:
    """Platform, slot and kind on one line. No secrets, no paths."""
    translate = i18n.Translator(lang)
    line = translate("telegram.preview.header", platform=item.platform,
                     slot=item.scheduled_at, kind=item.kind, profile=item.profile)
    if reduced:
        line = f"{line}\n{translate('telegram.preview.reduced')}"
    return line


def pre(text: str) -> str:
    """Text in a <pre> block so hooks, line breaks and spacing survive the phone."""
    stripped = (text or "").strip()
    return f"<pre>{html.escape(stripped)}</pre>" if stripped else ""


def body(item: QueueItem) -> str:
    return pre(item.text or "")


def caption(item: QueueItem, *, lang: str = "en", reduced: bool = False,
            limit: int = api.CAPTION_MAX) -> tuple[str, str]:
    """(caption, overflow). A body too long for a caption travels as its own message."""
    head = header(item, lang=lang, reduced=reduced)
    text = body(item)
    if not text:
        return head, ""
    combined = f"{head}\n{text}"
    if len(combined) <= limit:
        return combined, ""
    return head, text


def media_entry(item: QueueItem) -> Path | None:
    for entry in item.media:
        path = Path(entry.path).expanduser()
        if path.is_file():
            return path
    return None


def preview_path(source: Path) -> Path:
    return source.with_suffix(source.suffix + PREVIEW_SUFFIX)


def encode_preview(source: Path, destination: Path | None = None) -> Path:
    """Shrink a clip under the upload ceiling. Reduced quality, same aspect."""
    target = destination or preview_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    media.ffmpeg(["-i", str(source), "-vf", f"scale=-2:{PREVIEW_HEIGHT}",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", PREVIEW_CRF,
                  "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                  str(target)])
    return target


def build(item: QueueItem, *, chat_id: str, lang: str = "en",
          source: Path | None = None, reduced: bool = False) -> Preview:
    """Payloads for one item, without touching the network or ffmpeg."""
    buttons = markup(item.id, lang=lang)
    file_path = source if source is not None else media_entry(item)
    if item.kind == "text" or file_path is None:
        text = f"{header(item, lang=lang)}\n{body(item)}".strip()
        return Preview([Payload("sendMessage",
                                {"chat_id": chat_id, "text": text[:api.TEXT_MAX],
                                 "parse_mode": "HTML",
                                 "disable_web_page_preview": True,
                                 "reply_markup": buttons})])
    head, overflow = caption(item, lang=lang, reduced=reduced)
    payloads: list[Payload] = []
    if overflow:
        # The buttons ride on the media message, so this one is text only.
        payloads.append(Payload("sendMessage",
                                {"chat_id": chat_id, "text": overflow[:api.TEXT_MAX],
                                 "parse_mode": "HTML",
                                 "disable_web_page_preview": True}))
    shared: dict[str, object] = {"chat_id": chat_id, "caption": head,
                                 "parse_mode": "HTML", "reply_markup": buttons}
    size = file_path.stat().st_size if file_path.is_file() else 0
    if item.kind == "video":
        payloads.append(Payload("sendVideo",
                                {**shared, "width": VIDEO_WIDTH,
                                 "height": VIDEO_HEIGHT,
                                 "supports_streaming": True},
                                file=file_path, field="video"))
    elif size and size >= api.PHOTO_MAX:
        # sendPhoto would be rejected over 10MB; the picture still has to be seen.
        payloads.append(Payload("sendDocument", shared, file=file_path,
                                field="document"))
    else:
        payloads.append(Payload("sendPhoto", shared, file=file_path, field="photo"))
    return Preview(payloads, reduced=reduced)


def plan(item: QueueItem, *, chat_id: str, lang: str = "en") -> Preview:
    """What a real send would do, including whether a re-encode would be needed."""
    source = media_entry(item)
    if item.kind == "video" and source is not None and source.stat().st_size >= api.UPLOAD_MAX:
        built = build(item, chat_id=chat_id, lang=lang, source=preview_path(source),
                      reduced=True)
        built.note = "PREVIEW_REENCODE_REQUIRED"
        return built
    return build(item, chat_id=chat_id, lang=lang, source=source)


def send_preview(item: QueueItem, *, client: api.Client, chat_id: str,
                 lang: str = "en") -> dict[str, object]:
    """Send the preview and the three buttons. Returns the message ids that landed."""
    chat = str(chat_id or "").strip()
    if not chat:
        raise FactoryError("TELEGRAM_CHAT_MISSING", item.id)
    source = media_entry(item)
    reduced = False
    if item.kind == "video" and source is not None:
        if source.stat().st_size >= api.UPLOAD_MAX:
            source = encode_preview(source)
            reduced = True
            if source.stat().st_size >= api.UPLOAD_MAX:
                # Still too big: a document would not play on the phone, and a
                # silent drop would leave the item unconfirmable. Say so instead.
                raise FactoryError("TELEGRAM_PREVIEW_TOO_LARGE", item.id)
    built = build(item, chat_id=chat, lang=lang, source=source, reduced=reduced)
    sent: list[int] = []
    for payload in built.payloads:
        response = client.send(payload.method, payload.params, payload.attachment())
        result = response.get("result")
        if isinstance(result, dict) and result.get("message_id") is not None:
            sent.append(int(result["message_id"]))
    return {"item": item.id, "reduced": reduced, "message_ids": sent,
            "methods": [payload.method for payload in built.payloads]}


@dataclass(frozen=True)
class Draft:
    """A made thing that is not queued yet: what the wizard shows at the draft step.

    No buttons ride on this. There is no queue item to approve, and a button whose
    `callback_data` named a brief would be a second way to publish — the whole point of
    this channel is that it has exactly one.
    """

    ref: str
    kind: str = "text"
    profile: str = ""
    platform: str = ""
    text: str = ""
    media: Path | None = None
    note: str = ""


def draft_header(draft: Draft, *, lang: str = "en", reduced: bool = False) -> str:
    translate = i18n.Translator(lang)
    line = translate("telegram.preview.draft_header", profile=draft.profile or "-",
                     platform=draft.platform or "-", kind=draft.kind, ref=draft.ref)
    if draft.note:
        line = f"{line}\n{draft.note}"
    if reduced:
        line = f"{line}\n{translate('telegram.preview.reduced')}"
    return line


def build_draft(draft: Draft, *, chat_id: str, lang: str = "en",
                source: Path | None = None, reduced: bool = False) -> Preview:
    """Payloads for a pre-queue draft. Same media rules as an item, no reply_markup."""
    head = draft_header(draft, lang=lang, reduced=reduced)
    file_path = source if source is not None else draft.media
    if draft.kind == "text" or file_path is None or not Path(file_path).is_file():
        text = f"{head}\n{pre(draft.text)}".strip()
        return Preview([Payload("sendMessage",
                                {"chat_id": chat_id, "text": text[:api.TEXT_MAX],
                                 "parse_mode": "HTML",
                                 "disable_web_page_preview": True})])
    path = Path(file_path)
    shared: dict[str, object] = {"chat_id": chat_id, "caption": head[:api.CAPTION_MAX],
                                 "parse_mode": "HTML"}
    size = path.stat().st_size
    if draft.kind == "video":
        payload = Payload("sendVideo",
                          {**shared, "width": VIDEO_WIDTH, "height": VIDEO_HEIGHT,
                           "supports_streaming": True}, file=path, field="video")
    elif size >= api.PHOTO_MAX:
        payload = Payload("sendDocument", shared, file=path, field="document")
    else:
        payload = Payload("sendPhoto", shared, file=path, field="photo")
    return Preview([payload], reduced=reduced)


def send_draft(draft: Draft, *, client: api.Client, chat_id: str,
               lang: str = "en") -> dict[str, object]:
    """Show a draft on the phone. Nothing here changes queue state."""
    chat = str(chat_id or "").strip()
    if not chat:
        raise FactoryError("TELEGRAM_CHAT_MISSING", draft.ref)
    source = draft.media
    reduced = False
    if draft.kind == "video" and source is not None and Path(source).is_file():
        if Path(source).stat().st_size >= api.UPLOAD_MAX:
            source = encode_preview(Path(source))
            reduced = True
            if source.stat().st_size >= api.UPLOAD_MAX:
                raise FactoryError("TELEGRAM_PREVIEW_TOO_LARGE", draft.ref)
    built = build_draft(draft, chat_id=chat, lang=lang, source=source, reduced=reduced)
    sent: list[int] = []
    for payload in built.payloads:
        response = client.send(payload.method, payload.params, payload.attachment())
        result = response.get("result")
        if isinstance(result, dict) and result.get("message_id") is not None:
            sent.append(int(result["message_id"]))
    return {"ref": draft.ref, "reduced": reduced, "message_ids": sent,
            "methods": [payload.method for payload in built.payloads]}
