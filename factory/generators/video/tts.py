"""Stage 4: narration, with character-level timestamps.

ElevenLabs `with-timestamps` is used rather than plain synthesis because the
timestamps are what makes word-synced captions possible without a second
transcription pass. Word boundaries fall out of the character times.

Voice settings come from the profile's `voice.json` and the key from its
`credentials.env`; the model id matters (`eleven_v3` — the older multilingual
model is what sounded robotic for off-camera lines). Nothing about which voice
this is appears in the code.

Synthesis is cached on a hash of voice, model, settings and text, so re-running
the stage after a caption tweak costs nothing. The character budget is checked
before the first call, because ElevenLabs bills characters whether or not the
resulting audio is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ... import media
from ...errors import FactoryError

API = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_v3"
OUTPUT_FORMAT = "mp3_44100_192"
TIMEOUT_SECONDS = 300
SAMPLE_RATE = 44100
GAP_S = 0.30


@dataclass(frozen=True)
class Voice:
    voice_id: str
    model: str = DEFAULT_MODEL
    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, voice: Any) -> "Voice":
        """Read `voice.json`. The id is required; everything else has a default."""
        voice_id = str(getattr(voice, "voice_id", "") or "").strip()
        if not voice_id:
            raise FactoryError("VOICE_ID_MISSING")
        settings = {"stability": float(getattr(voice, "stability", 0.45) or 0.45),
                    "style": float(getattr(voice, "style", 0.3) or 0.0),
                    "similarity_boost": 0.75, "use_speaker_boost": True}
        return cls(voice_id=voice_id,
                   model=str(getattr(voice, "model", "") or DEFAULT_MODEL),
                   settings=settings)

    def digest(self, text: str) -> str:
        key = "|".join([self.voice_id, self.model,
                        json.dumps(self.settings, sort_keys=True), text])
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def describe(self) -> dict[str, Any]:
        """Safe for a report: model and settings, never the id."""
        return {"model": self.model, "settings": dict(self.settings)}


def character_count(lines: Sequence[str]) -> int:
    return sum(len(line) for line in lines)


def check_budget(lines: Sequence[str], *, cap: int) -> int:
    used = character_count(lines)
    if cap and used > cap:
        raise FactoryError("TTS_CHARS_EXCEEDED", f"{used}>{cap}")
    return used


def _post(url: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise FactoryError(f"HTTP_{int(error.code)}", "elevenlabs_tts") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise FactoryError("TTS_REQUEST_FAILED") from None


def synthesize(text: str, directory: Path, *, voice: Voice,
               api_key: str, poster: Any = None) -> dict[str, Any]:
    """One line of narration. A cache hit costs no characters and no request."""
    if not text.strip():
        raise FactoryError("TTS_TEXT_EMPTY")
    directory.mkdir(parents=True, exist_ok=True)
    digest = voice.digest(text)
    audio = directory / f"line_{digest}.mp3"
    alignment = directory / f"line_{digest}.json"
    cached = audio.is_file() and alignment.is_file()
    if not cached:
        if not api_key:
            raise FactoryError("ENV_MISSING", "ELEVENLABS_API_KEY")
        send = poster or _post
        body = send(f"{API}/v1/text-to-speech/{voice.voice_id}/with-timestamps"
                    f"?output_format={OUTPUT_FORMAT}",
                    {"text": text, "model_id": voice.model,
                     "voice_settings": voice.settings,
                     "output_format": OUTPUT_FORMAT}, api_key)
        encoded = body.get("audio_base64")
        if not isinstance(encoded, str) or not encoded:
            raise FactoryError("TTS_AUDIO_MISSING")
        audio.write_bytes(base64.b64decode(encoded))
        align = body.get("alignment") or body.get("normalized_alignment") or {}
        alignment.write_text(json.dumps(align), encoding="utf-8")
    align = json.loads(alignment.read_text(encoding="utf-8"))
    ends = align.get("character_end_times_seconds") or []
    length = (round(float(ends[-1]), 3) if ends
              else round(media.duration(audio), 3))
    return {"text": text, "digest": digest, "audio": str(audio),
            "alignment": str(alignment), "duration_s": length,
            "cached": cached, "chars": len(text)}


def word_times(alignment: dict[str, Any], *,
               offset: float = 0.0) -> list[tuple[float, float]]:
    """Character times collapsed to word spans, shifted by `offset`."""
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    spans: list[list[float]] = []
    current: list[float] | None = None
    for index, character in enumerate(characters):
        if index >= len(starts) or index >= len(ends):
            break
        if str(character).strip() == "":
            if current:
                spans.append(current)
                current = None
            continue
        if current is None:
            current = [float(starts[index]), float(ends[index])]
        else:
            current[1] = float(ends[index])
    if current:
        spans.append(current)
    return [(round(start + offset, 3), round(end + offset, 3))
            for start, end in spans]


def build_narration(results: Sequence[dict[str, Any]], destination: Path, *,
                    gap_s: float = GAP_S) -> dict[str, Any]:
    """Concatenate the lines with a fixed gap, and report where each one landed.

    The gap is why offsets are tracked: captions and the viewer-voice mixer both
    need to know what second a given line starts at in the finished track.
    """
    if not results:
        raise FactoryError("TTS_NO_LINES")
    destination.parent.mkdir(parents=True, exist_ok=True)
    silence = destination.parent / "gap.wav"
    media.ffmpeg(["-f", "lavfi", "-i",
                  f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", f"{gap_s:.3f}",
                  "-c:a", "pcm_s16le", str(silence)])
    parts: list[Path] = []
    offsets: list[float] = []
    cursor = 0.0
    for index, result in enumerate(results):
        converted = destination.parent / f"part_{index:02d}.wav"
        media.ffmpeg(["-i", result["audio"], "-t", f"{result['duration_s']:.3f}",
                      "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
                      str(converted)])
        offsets.append(round(cursor, 3))
        cursor += float(result["duration_s"])
        parts.append(converted)
        if index < len(results) - 1:
            parts.append(silence)
            cursor += gap_s
    listing = destination.parent / "concat.txt"
    listing.write_text("".join(f"file '{part.resolve()}'\n" for part in parts),
                       encoding="utf-8")
    media.ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing),
                  "-c:a", "pcm_s16le", str(destination)])
    return {"path": str(destination), "offsets": offsets,
            "duration_s": round(media.duration(destination), 3),
            "gap_s": gap_s}


def collect_word_times(results: Sequence[dict[str, Any]],
                       offsets: Sequence[float]) -> list[dict[str, Any]]:
    """Per line: its text, offset and word spans on the narration timeline."""
    out: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        alignment = json.loads(Path(result["alignment"]).read_text(encoding="utf-8"))
        offset = float(offsets[index]) if index < len(offsets) else 0.0
        out.append({"line": index, "text": result["text"], "offset_s": offset,
                    "word_times": [list(span) for span in
                                   word_times(alignment, offset=offset)]})
    return out
