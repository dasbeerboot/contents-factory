"""Resolve a format's prompt slots from the brief, the profile and the clock.

Three sources, in this order:

1. **Derived** — duration, frame size, aspect, language, target wpm, profile
   name, and the stage boundary seconds. These are computed so a format can
   change its length without anyone retyping timings into the answers.
2. **Brief answers** — the interview. `video.th.location` fills `{{location}}`:
   the slot name is the last dot segment. A plural answer that holds several
   lines also fills the numbered singulars, so one `host_lines` answer feeds
   `{{host_line_1}}` through `{{host_line_3}}`.
3. **Aliases and profile fallbacks** — `{{host_identity}}` falls back to the
   `identity` answer, `{{viewer_voice}}` to the profile's voice model. Nothing
   here is persona specific: the values all come out of the profile directory.

Stage boundaries are spread linearly between the opening cue and the end card,
in the order the slots appear in the template. A format that wants different
pacing overrides any of them by answering that slot directly.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

SLOT = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
TIMING = re.compile(r"^[a-z_][a-z0-9_]*_s$")
DEFAULT_WPM = 165
ENDCARD_HOLD_S = 2.5
OPENING_CUE_S = 3.0
ALIASES: dict[str, tuple[str, ...]] = {
    "host_identity": ("identity", "persona", "host"),
    "identity": ("host_identity", "persona"),
    "profile_name": ("profile",),
    "ambience": ("ambient", "sound", "room_tone"),
    "viewer_voice": ("viewer_voice_name", "voice"),
}
RESERVED: tuple[str, ...] = ("duration_s", "end_s")


BRAND_LINE = re.compile(r"^\s*[-*]?\s*([A-Za-z][A-Za-z0-9 _/-]{1,40}?)\s*:\s*(\S.*)$")


def _segment(key: str) -> str:
    return key.rsplit(".", 1)[-1].strip()


def from_brand(brand: str) -> dict[str, str]:
    """`brand.md` bullet lines as slot values: "- End card: ..." -> `end_card`.

    This is how a persona's fixed strings — its end card, its ambience, how it
    describes itself — stay in the profile instead of in the code. Both the
    spaced and underscored spellings are exposed so `{{endcard}}` and
    `{{end_card}}` both resolve.
    """
    out: dict[str, str] = {}
    for line in (brand or "").splitlines():
        match = BRAND_LINE.match(line)
        if not match:
            continue
        label = re.sub(r"[ /-]+", "_", match.group(1).strip().lower())
        value = match.group(2).strip().rstrip(".")
        if not label or not value or len(value) > 400:
            continue
        out.setdefault(label, value)
        out.setdefault(label.replace("_", ""), value)
    return out


def _lines(value: str) -> list[str]:
    return [piece.strip() for piece in re.split(r"[\n;]+", str(value))
            if piece.strip()]


def from_answers(answers: dict[str, str]) -> dict[str, str]:
    """Slot name -> answer, plus numbered singulars for multi-line plurals."""
    out: dict[str, str] = {}
    for key, value in answers.items():
        name = _segment(key)
        if not name:
            continue
        text = str(value).strip()
        out[name] = text
        pieces = _lines(text)
        if len(pieces) > 1 and name.endswith("s"):
            stem = name[:-1]
            for index, piece in enumerate(pieces, 1):
                out.setdefault(f"{stem}_{index}", piece)
    return out


def timing_slots(template: str) -> list[str]:
    """Timing slots in the order the template mentions them, end_s last."""
    seen = [name for name in dict.fromkeys(SLOT.findall(template or ""))
            if TIMING.match(name) and name not in RESERVED]
    return seen


def stage_times(template: str, *, duration_s: float, hold_s: float,
                opening_s: float = OPENING_CUE_S) -> dict[str, float]:
    """Spread the unfixed stage boundaries between the opening and the end card."""
    end_s = round(max(duration_s - hold_s, opening_s + 0.5), 2)
    # A whole number of seconds renders as "24-second", not "24.0-second".
    whole = (int(duration_s) if float(duration_s).is_integer()
             else round(duration_s, 2))
    out: dict[str, float] = {"duration_s": whole, "end_s": end_s}
    names = timing_slots(template)
    if not names:
        return out
    start = min(opening_s, end_s - 0.5)
    span = end_s - start
    step = span / (len(names) + 1)
    for index, name in enumerate(names, 1):
        out[name] = round(start + step * index, 2)
    return out


def derived(*, profile_name: str, content_lang: str, duration_s: float,
            template: str, aspect: str | None,
            limits: dict[str, Any] | None) -> dict[str, Any]:
    rules = limits or {}
    band = rules.get("wpm") or [DEFAULT_WPM, DEFAULT_WPM]
    try:
        wpm = int(round((float(band[0]) + float(band[1])) / 2))
    except (TypeError, ValueError, IndexError):
        wpm = DEFAULT_WPM
    hold = float(rules.get("endcard_min_s") or ENDCARD_HOLD_S)
    values: dict[str, Any] = {
        "profile_name": profile_name,
        "content_lang": content_lang,
        "aspect": aspect or "9:16",
        "width_px": int(rules.get("width_px") or 1080),
        "height_px": int(rules.get("height_px") or 1920),
        "wpm": wpm,
    }
    values.update(stage_times(template, duration_s=duration_s, hold_s=hold))
    return values


def for_takes(base: dict[str, Any], *, template: str, lengths: Sequence[int],
              lines: Sequence[str], limits: dict[str, Any] | None,
              spoken_slots: Sequence[str] = ("hook_line", "body_lines")
              ) -> list[dict[str, Any]]:
    """One value set per take, each rebased to that take's own window.

    A reel longer than the clip model's cap is rendered as consecutive takes.
    Handing every take the whole reel's stage timings makes each one try to
    perform the entire script in half the time, so each take gets its own
    duration, its own stage boundaries and its own share of the dialogue.

    Lines are dealt out in proportion to take length and never split mid-line,
    because a take that ends mid-sentence cannot be cut together.
    """
    rules = limits or {}
    hold = float(rules.get("endcard_min_s") or ENDCARD_HOLD_S)
    total = float(sum(lengths)) or 1.0
    remaining = list(lines)
    shares: list[list[str]] = []
    for index, length in enumerate(lengths):
        if index == len(lengths) - 1:
            shares.append(remaining)
            break
        wanted = max(1, round(len(lines) * (length / total)))
        wanted = min(wanted, max(1, len(remaining) - (len(lengths) - index - 1)))
        shares.append(remaining[:wanted])
        remaining = remaining[wanted:]

    out: list[dict[str, Any]] = []
    for index, (length, share) in enumerate(zip(lengths, shares)):
        values = dict(base)
        # Only the last take carries the end card, so the hold is not reserved
        # in the middle of the reel.
        values.update(stage_times(template, duration_s=float(length),
                                  hold_s=hold if index == len(lengths) - 1 else 0.0))
        values["duration_s"] = int(length)
        spoken = list(share) or [str(base.get(spoken_slots[0]) or "")]
        if len(spoken_slots) >= 2:
            values[spoken_slots[0]] = spoken[0]
            values[spoken_slots[1]] = " ".join(spoken[1:]) or spoken[0]
        else:
            values[spoken_slots[0]] = " ".join(spoken)
        out.append(values)
    return out


def resolve(*, template: str, answers: dict[str, str], profile_name: str,
            content_lang: str, duration_s: float, aspect: str | None,
            limits: dict[str, Any] | None,
            profile_fallbacks: dict[str, str] | None = None) -> dict[str, Any]:
    """Every value the template needs, or as close as the inputs allow.

    Unresolved slots are left out so `script.render` raises `SLOT_MISSING` with
    their names, which is the message a user can act on.
    """
    values = derived(profile_name=profile_name, content_lang=content_lang,
                     duration_s=duration_s, template=template, aspect=aspect,
                     limits=limits)
    answered = from_answers(answers)
    for name, value in answered.items():
        values.setdefault(name, value)
    fallbacks = profile_fallbacks or {}
    for name in SLOT.findall(template or ""):
        if name in values:
            continue
        for alias in ALIASES.get(name, ()):
            if answered.get(alias):
                values[name] = answered[alias]
                break
        if name not in values and fallbacks.get(name):
            values[name] = fallbacks[name]
    return values
