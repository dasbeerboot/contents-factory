"""Audio gate: transcribe the finished take and measure what was actually said.

Three things fail here, and all three have been shipped before by someone who
only watched the frames:

* **Pace.** Effective words per minute over the spoken spans, not over the whole
  file, so end-card silence does not make a rushed take look calm. Outside the
  format's band it fails.
* **Fidelity.** The transcript against the approved script. A take where the
  model dropped or invented a sentence fails even when it sounds fine.
* **Delivery tics.** Filler words and immediately repeated words, counted.

The transcript is always made from the current take. A stale transcript lying
next to a new render is how a bad take passes, so the audio is hashed and a
transcript whose hash does not match is discarded rather than reused.

Transcription: ElevenLabs `scribe_v1` by default; `whisper` on PATH is used when
no key is configured, so the gate still runs offline.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .. import media
from ..errors import FactoryError

API = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL = "scribe_v1"
TIMEOUT_SECONDS = 600
FILLERS: frozenset[str] = frozenset({"uh", "um", "erm", "ah", "eh", "hmm"})
WPM_BAND = (150.0, 185.0)
FIDELITY_MIN = 0.95
WORD = re.compile(r"[a-z0-9']+")


@dataclass
class SttReport:
    violations: list[str] = field(default_factory=list)
    text: str = ""
    words: int = 0
    speaking_s: float = 0.0
    duration_s: float = 0.0
    wpm: float = 0.0
    fidelity: float = 0.0
    fillers: int = 0
    repeats: int = 0
    engine: str = ""
    band: tuple[float, float] = WPM_BAND

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "words": self.words,
                "speaking_s": self.speaking_s, "duration_s": self.duration_s,
                "wpm": self.wpm, "fidelity": self.fidelity,
                "fillers": self.fillers, "repeats": self.repeats,
                "engine": self.engine, "band": list(self.band),
                "text": self.text[:400]}


def _normalise(text: str) -> list[str]:
    body = str(text or "").lower().replace("’", "'")
    return WORD.findall(body)


def audio_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _multipart(url: str, fields: dict[str, str],
               filename: str, payload: bytes, key: str) -> dict[str, Any]:
    boundary = "----" + uuid.uuid4().hex
    crlf = b"\r\n"
    body = b""
    for name, value in fields.items():
        body += (b"--" + boundary.encode() + crlf
                 + f'Content-Disposition: form-data; name="{name}"'.encode()
                 + crlf + crlf + str(value).encode() + crlf)
    body += (b"--" + boundary.encode() + crlf
             + f'Content-Disposition: form-data; name="file"; '
               f'filename="{filename}"'.encode() + crlf
             + b"Content-Type: audio/mpeg" + crlf + crlf + payload + crlf)
    body += b"--" + boundary.encode() + b"--" + crlf
    request = urllib.request.Request(
        url, data=body,
        headers={"xi-api-key": key,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise FactoryError(f"HTTP_{int(error.code)}", "speech_to_text") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise FactoryError("STT_REQUEST_FAILED") from None


def transcribe_elevenlabs(audio: Path, *, api_key: str,
                          language: str = "eng") -> dict[str, Any]:
    body = _multipart(API, {"model_id": MODEL,
                            "timestamps_granularity": "word",
                            "language_code": language},
                      Path(audio).name, Path(audio).read_bytes(), api_key)
    body["engine"] = f"elevenlabs:{MODEL}"
    return body


def transcribe_whisper(audio: Path) -> dict[str, Any]:
    """Offline fallback. Word timings come from whisper's json output."""
    if shutil.which("whisper") is None:
        raise FactoryError("STT_ENGINE_MISSING", "whisper")
    with tempfile.TemporaryDirectory() as scratch:
        done = subprocess.run(["whisper", str(audio), "--model", "base",
                               "--output_format", "json", "--word_timestamps",
                               "True", "--output_dir", scratch],
                              capture_output=True, text=True, check=False,
                              timeout=TIMEOUT_SECONDS)
        if done.returncode != 0:
            raise FactoryError("STT_REQUEST_FAILED", "whisper")
        found = sorted(Path(scratch).glob("*.json"))
        if not found:
            raise FactoryError("STT_REQUEST_FAILED", "whisper_no_output")
        parsed = json.loads(found[0].read_text(encoding="utf-8"))
    words: list[dict[str, Any]] = []
    for segment in parsed.get("segments") or []:
        for word in segment.get("words") or []:
            words.append({"text": str(word.get("word") or "").strip(),
                          "start": word.get("start"), "end": word.get("end"),
                          "type": "word"})
    return {"text": str(parsed.get("text") or ""), "words": words,
            "engine": "whisper:base"}


def transcript_for(source: Path, destination: Path, *, api_key: str = "",
                   language: str = "eng") -> dict[str, Any]:
    """Fresh transcript for this exact take, cached on the audio's digest."""
    audio = media.extract_audio(Path(source),
                                Path(destination).with_suffix(".mp3"))
    digest = audio_digest(audio)
    if Path(destination).is_file():
        try:
            cached = json.loads(Path(destination).read_text(encoding="utf-8"))
        except ValueError:
            cached = {}
        if isinstance(cached, dict) and cached.get("audio_digest") == digest:
            return cached
    body = (transcribe_elevenlabs(audio, api_key=api_key, language=language)
            if api_key else transcribe_whisper(audio))
    body["audio_digest"] = digest
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text(json.dumps(body, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    return body


def spoken_words(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [word for word in (body.get("words") or [])
            if isinstance(word, dict)
            and str(word.get("type") or "word") == "word"
            and word.get("start") is not None]


def speaking_seconds(words: Sequence[dict[str, Any]], *, pad: float = 0.05,
                     merge_gap: float = 0.35) -> float:
    """Total time actually spoken: word spans merged over short gaps."""
    spans: list[list[float]] = []
    for word in words:
        try:
            start, end = float(word["start"]) - pad, float(word["end"]) + pad
        except (KeyError, TypeError, ValueError):
            continue
        if spans and start - spans[-1][1] <= merge_gap:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([max(0.0, start), end])
    return round(sum(end - start for start, end in spans), 3)


def caption_lines(transcript: Path) -> list[dict[str, Any]]:
    """Transcript -> the shape the captions stage wants, split at long pauses."""
    try:
        body = json.loads(Path(transcript).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FactoryError("STT_TRANSCRIPT_MISSING",
                           Path(transcript).name[:40]) from None
    words = spoken_words(body if isinstance(body, dict) else {})
    if not words:
        raise FactoryError("STT_TRANSCRIPT_EMPTY")
    lines: list[dict[str, Any]] = []
    current: dict[str, Any] = {"text": "", "word_times": []}
    previous_end = None
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        if previous_end is not None and start - previous_end > 0.6 and current["text"]:
            lines.append(current)
            current = {"text": "", "word_times": []}
        text = str(word.get("text") or "").strip()
        current["text"] = f"{current['text']} {text}".strip()
        current["word_times"].append([round(start, 3), round(end, 3)])
        previous_end = end
    if current["text"]:
        lines.append(current)
    for index, line in enumerate(lines):
        line["line"] = index
        line["offset_s"] = line["word_times"][0][0]
    return lines


def check(source: Path, *, transcript: Path, script_text: str = "",
          limits: dict[str, Any] | None = None, api_key: str = "",
          language: str = "eng") -> SttReport:
    """One verdict for pace, fidelity and delivery tics on the current take."""
    rules = limits or {}
    band = rules.get("wpm") or list(WPM_BAND)
    try:
        low, high = float(band[0]), float(band[1])
    except (TypeError, ValueError, IndexError):
        low, high = WPM_BAND
    report = SttReport(band=(low, high))
    body = transcript_for(Path(source), Path(transcript), api_key=api_key,
                          language=language)
    report.engine = str(body.get("engine") or "")
    report.text = str(body.get("text") or "")
    words = spoken_words(body)
    heard = _normalise(report.text)
    report.words = len(heard)
    report.duration_s = round(media.duration(Path(source)), 3)
    report.speaking_s = (speaking_seconds(words) if words
                         else report.duration_s)
    if report.speaking_s > 0 and report.words:
        report.wpm = round(report.words / report.speaking_s * 60.0, 1)
    report.fillers = sum(1 for word in heard if word in FILLERS)
    report.repeats = sum(1 for index in range(1, len(heard))
                         if heard[index] == heard[index - 1])

    if not report.words:
        report.violations.append("NO_SPEECH")
        return report
    if report.wpm > high:
        report.violations.append(f"WPM_HIGH:{report.wpm:g}>{high:g}")
    if report.wpm < low:
        report.violations.append(f"WPM_LOW:{report.wpm:g}<{low:g}")
    if script_text.strip():
        wanted = _normalise(script_text)
        report.fidelity = round(
            difflib.SequenceMatcher(None, wanted, heard).ratio(), 4)
        floor = float(rules.get("stt_fidelity_min") or FIDELITY_MIN)
        if report.fidelity < floor:
            report.violations.append(
                f"FIDELITY_LOW:{report.fidelity:.3f}<{floor:g}")
    if report.fillers > int(rules.get("fillers_max") or 2):
        report.violations.append(f"FILLERS:{report.fillers}")
    if report.repeats > int(rules.get("repeats_max") or 1):
        report.violations.append(f"WORD_REPEATS:{report.repeats}")
    return report
