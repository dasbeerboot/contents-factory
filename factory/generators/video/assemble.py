"""Stage 6: the deliverable.

Output contract, enforced by `verify` and not by eye: 1080x1920, 30 fps, h264 +
aac, `faststart` so the first frame plays before the file has finished
downloading, and a duration inside the format's declared band.

Three things happen here that are not just a container change:

* **Upscale.** The take is rendered at 720p because that is what fits the credit
  cap; the deliverable is 1080x1920 because that is what the platforms want.
  Fill then crop, never letterbox.
* **End card hold.** The last frame is frozen for the declared hold so the end
  card is readable. Below roughly 2.5 seconds nobody finishes reading it.
* **Caption burn.** The overlay frames from the captions stage are composited,
  not re-laid out. Layout decisions belong to one stage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import media
from ...errors import FactoryError

WIDTH = 1080
HEIGHT = 1920
FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
CRF = "18"
PRESET = "medium"
HOLD_S = 2.5
DURATION_TOLERANCE_S = 0.6


@dataclass
class VerifyReport:
    violations: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    size: tuple[int, int] = (0, 0)
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    faststart: bool = False

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations),
                "duration_s": self.duration_s, "size": list(self.size),
                "fps": self.fps, "video_codec": self.video_codec,
                "audio_codec": self.audio_codec, "faststart": self.faststart}


def _fill_crop(width: int = WIDTH, height: int = HEIGHT) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1")


def normalise(source: Path, destination: Path, *, width: int = WIDTH,
              height: int = HEIGHT, fps: int = FPS) -> Path:
    """Take -> deliverable geometry, audio kept as-is for now."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    media.ffmpeg(["-i", str(source), "-vf",
                  f"{_fill_crop(width, height)},fps={fps}",
                  "-c:v", VIDEO_CODEC, "-preset", PRESET, "-crf", CRF,
                  "-pix_fmt", "yuv420p", "-c:a", AUDIO_CODEC,
                  "-b:a", AUDIO_BITRATE, "-movflags", "+faststart",
                  str(destination)])
    return destination


def hold_last_frame(source: Path, destination: Path, *, seconds: float = HOLD_S,
                    width: int = WIDTH, height: int = HEIGHT,
                    fps: int = FPS) -> Path:
    """Freeze the final frame for the end card, as silent video."""
    frame = destination.parent / "lastframe.png"
    media.extract_frame(source, frame, at=-0.05)
    media.ffmpeg(["-loop", "1", "-i", str(frame), "-t", f"{seconds:.3f}",
                  "-vf", f"{_fill_crop(width, height)},fps={fps}",
                  "-c:v", VIDEO_CODEC, "-preset", PRESET, "-crf", CRF,
                  "-pix_fmt", "yuv420p", str(destination)])
    return destination


def burn_captions(source: Path, frames_dir: Path, destination: Path, *,
                  fps: int = FPS) -> Path:
    """Composite the caption overlay sequence over the video."""
    pattern = str(Path(frames_dir) / "%05d.png")
    if not any(Path(frames_dir).glob("*.png")):
        raise FactoryError("CAPTION_FRAMES_MISSING")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # shortest=1: the caption sequence must not extend the video it sits on.
    # A track rendered a few frames long would otherwise freeze the last frame
    # for the difference, which then lands inside the end card hold.
    media.ffmpeg(["-i", str(source), "-framerate", str(fps), "-i", pattern,
                  "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                  "-map", "[v]", "-map", "0:a?",
                  "-c:v", VIDEO_CODEC, "-preset", PRESET, "-crf", CRF,
                  "-pix_fmt", "yuv420p", "-c:a", "copy",
                  "-movflags", "+faststart", str(destination)])
    return destination


def mux_audio(source: Path, audio: Path, destination: Path) -> Path:
    """Replace the take's own audio with the narration track."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    media.ffmpeg(["-i", str(source), "-i", str(audio), "-map", "0:v:0",
                  "-map", "1:a:0", "-c:v", "copy", "-c:a", AUDIO_CODEC,
                  "-b:a", AUDIO_BITRATE, "-shortest",
                  "-movflags", "+faststart", str(destination)])
    return destination


def concat(parts: list[Path], destination: Path, *, fps: int = FPS,
           width: int = WIDTH, height: int = HEIGHT) -> Path:
    """Join the pieces into the deliverable.

    The concat **filter**, not the concat demuxer. The demuxer wants every input
    to share stream parameters and does not say so when they do not: joining a
    24 second body whose audio came through from the take with a 2.5 second end
    card carrying a freshly generated silent track produced a 44 second file out
    of 29 seconds of material, because the mismatched timebases desynchronised
    and the container duration took the longer stream.

    The filter resamples and rescales every input to one shape first, so the
    output length is the sum of the inputs by construction.
    """
    if not parts:
        raise FactoryError("NO_PARTS")
    if len(parts) == 1:
        return normalise(parts[0], destination, width=width, height=height,
                         fps=fps)
    destination.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    for index, part in enumerate(parts):
        inputs.extend(["-i", str(part)])
        chains.append(f"[{index}:v]scale={width}:{height},setsar=1,fps={fps}"
                      f"[v{index}]")
        if media.has_audio(part):
            chains.append(f"[{index}:a]aresample=48000,aformat="
                          f"sample_fmts=fltp:channel_layouts=stereo[a{index}]")
        else:
            # A silent card still needs a track, or the concat filter drops the
            # audio of every part before it.
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration="
                          f"{media.duration(part):.3f}[a{index}]")
        labels.append(f"[v{index}][a{index}]")
    chains.append(f"{''.join(labels)}concat=n={len(parts)}:v=1:a=1[v][a]")
    media.ffmpeg([*inputs, "-filter_complex", ";".join(chains),
                  "-map", "[v]", "-map", "[a]",
                  "-c:v", VIDEO_CODEC, "-preset", PRESET, "-crf", CRF,
                  "-pix_fmt", "yuv420p", "-c:a", AUDIO_CODEC,
                  "-b:a", AUDIO_BITRATE, "-movflags", "+faststart",
                  str(destination)])
    return destination


def verify(path: Path, *, duration_band: tuple[float, float] | None = None,
           width: int = WIDTH, height: int = HEIGHT,
           fps: int = FPS) -> VerifyReport:
    """Measure the finished file against the contract."""
    report = VerifyReport()
    report.duration_s = round(media.duration(path), 3)
    report.size = media.dimensions(path)
    report.fps = media.frame_rate(path)
    stream = media.video_stream(path)
    report.video_codec = str(stream.get("codec_name") or "")
    for entry in media.probe(path).get("streams") or []:
        if entry.get("codec_type") == "audio":
            report.audio_codec = str(entry.get("codec_name") or "")
            break
    report.faststart = _faststart(path)

    if report.size != (width, height):
        report.violations.append(
            f"SIZE:{report.size[0]}x{report.size[1]}!={width}x{height}")
    if abs(report.fps - fps) > 0.5:
        report.violations.append(f"FPS:{report.fps:g}!={fps}")
    if report.video_codec != "h264":
        report.violations.append(f"VIDEO_CODEC:{report.video_codec or 'none'}")
    if report.audio_codec != "aac":
        report.violations.append(f"AUDIO_CODEC:{report.audio_codec or 'none'}")
    if not report.faststart:
        report.violations.append("FASTSTART_MISSING")
    if duration_band:
        low, high = float(duration_band[0]), float(duration_band[1])
        if not (low - DURATION_TOLERANCE_S <= report.duration_s
                <= high + DURATION_TOLERANCE_S):
            report.violations.append(
                f"DURATION:{report.duration_s:g}not_in_{low:g}-{high:g}")
    return report


def _faststart(path: Path) -> bool:
    """`moov` ahead of `mdat` in the first bytes is what faststart means."""
    try:
        head = Path(path).open("rb").read(4096)
    except OSError:
        return False
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    if moov == -1:
        return False
    return mdat == -1 or moov < mdat
