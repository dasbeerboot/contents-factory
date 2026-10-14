"""ffmpeg / ffprobe shell helpers, shared by the video stages and the gates.

Every call goes through `run` so a failure becomes a code-form FactoryError with
a scrubbed tail rather than a raw stderr dump, and so there is exactly one place
that knows the binaries might be missing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .errors import FactoryError, scrub
from .osinfo import which

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
DEFAULT_TIMEOUT = 1800


def require_binaries() -> None:
    for binary in (FFMPEG, FFPROBE):
        if which(binary) is None:
            raise FactoryError("FFMPEG_MISSING", binary)


def run(args: Sequence[str], *, binary: str = FFMPEG,
        timeout: int = DEFAULT_TIMEOUT) -> str:
    if which(binary) is None:
        raise FactoryError("FFMPEG_MISSING", binary)
    try:
        done = subprocess.run([which(binary) or binary, *args],
                              capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise FactoryError("FFMPEG_TIMEOUT", binary) from None
    if done.returncode != 0:
        raise FactoryError("FFMPEG_FAILED", scrub(done.stderr[-80:]))
    return done.stdout


def ffmpeg(args: Sequence[str], *, timeout: int = DEFAULT_TIMEOUT) -> str:
    return run(["-y", "-v", "error", *args], binary=FFMPEG, timeout=timeout)


def probe(path: Path) -> dict[str, Any]:
    """Format plus stream metadata for one file."""
    out = run(["-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", str(path)], binary=FFPROBE, timeout=120)
    try:
        parsed = json.loads(out)
    except ValueError:
        raise FactoryError("PROBE_UNREADABLE", Path(path).name[:40]) from None
    if not isinstance(parsed, dict):
        raise FactoryError("PROBE_UNREADABLE", Path(path).name[:40])
    return parsed


def duration(path: Path) -> float:
    data = probe(path).get("format") or {}
    try:
        return float(data.get("duration"))
    except (TypeError, ValueError):
        raise FactoryError("DURATION_UNREADABLE", Path(path).name[:40]) from None


def video_stream(path: Path) -> dict[str, Any]:
    for stream in probe(path).get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream
    raise FactoryError("NO_VIDEO_STREAM", Path(path).name[:40])


def dimensions(path: Path) -> tuple[int, int]:
    stream = video_stream(path)
    try:
        return int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError):
        raise FactoryError("DIMENSIONS_UNREADABLE", Path(path).name[:40]) from None


def frame_rate(path: Path) -> float:
    stream = video_stream(path)
    raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/0")
    try:
        numerator, _, denominator = raw.partition("/")
        return round(float(numerator) / float(denominator or 1), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def has_audio(path: Path) -> bool:
    return any(stream.get("codec_type") == "audio"
               for stream in probe(path).get("streams") or [])


def extract_frame(source: Path, destination: Path, *, at: float,
                  width: int | None = None) -> Path:
    """One frame at `at` seconds. Negative `at` counts back from the end."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    seek = (["-sseof", f"{at:.3f}"] if at < 0 else ["-ss", f"{at:.3f}"])
    scale = ["-vf", f"scale={int(width)}:-2"] if width else []
    ffmpeg([*seek, "-i", str(source), "-frames:v", "1", *scale, str(destination)])
    if not destination.is_file():
        raise FactoryError("FRAME_EXTRACT_FAILED", destination.name[:40])
    return destination


def sample_frames(source: Path, directory: Path, *, fps: float,
                  width: int) -> list[Path]:
    """Decode the whole clip down to `fps` greyscale-friendly pngs, in order."""
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("f_*.png"):
        stale.unlink()
    ffmpeg(["-i", str(source), "-vf", f"fps={fps},scale={int(width)}:-2",
            str(directory / "f_%05d.png")])
    frames = sorted(directory.glob("f_*.png"))
    if not frames:
        raise FactoryError("FRAME_SAMPLE_EMPTY", Path(source).name[:40])
    return frames


def extract_audio(source: Path, destination: Path, *,
                  bitrate: str = "128k") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-i", str(source), "-vn", "-b:a", bitrate, str(destination)])
    return destination
