"""Stage 1: fill the format's prompt slots, then prove nothing else moved.

The format yaml's `prompt_template` is the golden prompt. A proven prompt is
reused verbatim and only its slots change (video-rules §3): location, outfit,
lighting, ambience, dialogue, stage timings, gaze cues. Rewriting a sentence
that already renders well is the single most expensive mistake in this pipeline,
because it is paid for in whole takes.

So this stage does what `gen.py --check` did: it masks every substituted value
out of both the template and the finished prompt, then compares what is left. If
a sentence outside a slot differs, the verdict is FAIL and the render is refused.
The template is the golden copy here, which means there is no separate golden
file to drift away from.

It also sizes the script: words, words per minute, end card hold. A script whose
dialogue cannot physically fit the requested duration fails before a credit is
spent, not after the take comes back rushed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...errors import FactoryError

SLOT = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
MASK = "\x00SLOT\x00"
WORD = re.compile(r"[A-Za-z0-9''’-]+")
STAGE_HEADER = re.compile(r"\[Stage\s+\d+\s*\|\s*[\d.]+\s*-\s*[\d.]+s\]")
DEFAULT_WPM = 165
MIN_VALUE_LENGTH = 3
ENDCARD_HOLD_S = 2.5


@dataclass
class ScriptReport:
    prompt: str = ""
    violations: list[str] = field(default_factory=list)
    slots_used: list[str] = field(default_factory=list)
    spoken_words: int = 0
    duration_s: float = 0.0
    speaking_s: float = 0.0
    wpm: float = 0.0
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations),
                "slots_used": list(self.slots_used),
                "spoken_words": self.spoken_words,
                "duration_s": self.duration_s,
                "speaking_s": self.speaking_s,
                "wpm": self.wpm,
                "lines": list(self.lines)}


def slot_names(template: str) -> list[str]:
    return list(dict.fromkeys(SLOT.findall(template or "")))


def render(template: str, values: dict[str, Any]) -> str:
    """Substitute every slot. A slot with no value is an error, not an empty gap."""
    missing: list[str] = []

    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)
        if value is None or str(value).strip() == "":
            missing.append(name)
            return match.group(0)
        return str(value).strip()

    filled = SLOT.sub(swap, template or "")
    if missing:
        raise FactoryError("SLOT_MISSING", ",".join(sorted(set(missing)))[:120])
    return filled


def _normalise(text: str) -> str:
    body = STAGE_HEADER.sub("[Stage]", text or "")
    body = re.sub(r"(?m)^\s+|\s+$", "", body)
    return re.sub(r"\s+", " ", body).strip()


def mask_template(template: str) -> str:
    return _normalise(SLOT.sub(MASK, template or ""))


def mask_rendered(rendered: str, values: dict[str, Any]) -> str:
    """Replace the substituted values back out, longest first so a short value
    cannot eat part of a long one."""
    body = rendered or ""
    candidates = sorted({str(value).strip() for value in values.values()
                         if str(value).strip()},
                        key=len, reverse=True)
    for value in candidates:
        if len(value) < MIN_VALUE_LENGTH:
            continue
        body = body.replace(value, MASK)
    return _normalise(body)


def off_template(rendered: str, template: str,
                 values: dict[str, Any]) -> list[str]:
    """Sentences that differ outside the slots. Empty list means PASS.

    Both sides are masked the same way — the freshly rendered template and the
    prompt actually being sent — rather than masking slot syntax on one side and
    slot values on the other. Masking asymmetrically makes any value too short
    to scan for (a two-letter language tag) look like an edited sentence.
    """
    drafted = render(template, values)
    if rendered.strip() == drafted.strip():
        return []
    golden = mask_rendered(drafted, values)
    actual = mask_rendered(rendered, values)
    if golden == actual:
        return []
    import difflib

    drift = [line for line in difflib.unified_diff(
        golden.split(". "), actual.split(". "), lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    return [f"OFF_TEMPLATE:{len(drift)}_SENTENCE"] + [
        f"DIFF:{line[:90]}" for line in drift[:4]]


def spoken_lines(values: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """Dialogue the host actually says, in order, from the slot values."""
    lines: list[str] = []
    for key in keys:
        raw = values.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            pieces = [str(piece) for piece in raw]
        else:
            pieces = re.split(r"\n+", str(raw))
        for piece in pieces:
            text = piece.strip()
            if text:
                lines.append(text)
    return lines


def count_words(text: str) -> int:
    return len(WORD.findall(text or ""))


def check(*, template: str, prompt: str, values: dict[str, Any],
          duration_s: float, limits: dict[str, Any] | None = None,
          spoken_keys: tuple[str, ...] = ()) -> ScriptReport:
    """Golden-prompt check plus script sizing. One verdict for the whole stage."""
    rules = limits or {}
    report = ScriptReport(prompt=prompt, slots_used=slot_names(template),
                          duration_s=round(float(duration_s), 2))
    report.violations.extend(off_template(prompt, template, values))

    report.lines = spoken_lines(values, spoken_keys)
    report.spoken_words = sum(count_words(line) for line in report.lines)
    hold = float(rules.get("endcard_min_s") or ENDCARD_HOLD_S)
    report.speaking_s = round(max(duration_s - hold, 0.1), 2)
    if report.spoken_words:
        report.wpm = round(report.spoken_words / report.speaking_s * 60.0, 1)

    band = rules.get("wpm") or [DEFAULT_WPM, DEFAULT_WPM]
    try:
        low, high = float(band[0]), float(band[1])
    except (TypeError, ValueError, IndexError):
        low, high = float(DEFAULT_WPM), float(DEFAULT_WPM)
    if not report.lines:
        report.violations.append("NO_SPOKEN_LINES")
    elif report.wpm > high:
        report.violations.append(
            f"SCRIPT_TOO_LONG:{report.wpm:g}wpm>{high:g}")
    elif report.wpm < low:
        report.violations.append(
            f"SCRIPT_TOO_SHORT:{report.wpm:g}wpm<{low:g}")
    return report


def save(path: Path, report: ScriptReport, *, values: dict[str, Any],
         redact: tuple[str, ...] = ()) -> Path:
    """Write script.json. Slot values are content, never credentials, but any
    key a caller flags is dropped rather than persisted."""
    body = {"script": report.as_dict(),
            "values": {key: value for key, value in sorted(values.items())
                       if key not in redact}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path
