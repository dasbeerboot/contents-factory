"""Stage 3: the take.

One take, no rerolls. A failed take is discarded and the backup script goes in;
a rerender needs an explicit decision with the cost named, because rerolling is
where a 150-credit cap turns into a 400-credit one (video-rules §8, §9).

The clip model caps its own duration, so a reel longer than that cap is built as
consecutive takes rather than one impossible call. Each later take starts from
**the last frame of the take before it**, not from a fresh still: generating
twice from the same still makes the background jump back to the beginning at the
seam (STILL-GATE §C, multi-take continuity).

Resolution is a budget lever, not a quality preference. A 24-second reel costs
108 credits at 720p and 288 at 1080p, so the 720p take plus an upscale in
assembly is what fits a per-item cap at all. The format yaml picks via
`limits.clip_resolution`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence  # noqa: F401  (Sequence used in signatures)

from ... import media
from ...errors import FactoryError, scrub
from .. import fetch
from ..image import BINARY, DEFAULT_TIMEOUT_SECONDS, parse_output, result_paths

MODEL = "seedance_2_0"
MAX_TAKE_S = 15
MIN_TAKE_S = 4
RESOLUTION = "720p"
ASPECT = "9:16"
WAIT = "40m"
SEAM_BACKOFF_S = 0.08


@dataclass(frozen=True)
class Take:
    index: int
    duration_s: int
    start_image: Path
    prompt: str

    @property
    def name(self) -> str:
        return f"take{self.index}"


@dataclass
class ClipPlan:
    takes: list[Take] = field(default_factory=list)
    model: str = MODEL
    resolution: str = RESOLUTION
    aspect: str = ASPECT

    @property
    def total_s(self) -> int:
        return sum(take.duration_s for take in self.takes)

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "resolution": self.resolution,
                "aspect": self.aspect, "total_s": self.total_s,
                "takes": [{"index": take.index, "duration_s": take.duration_s,
                           "start_image": str(take.start_image)}
                          for take in self.takes]}


def split_duration(duration_s: float, *, max_take_s: int = MAX_TAKE_S) -> list[int]:
    """Whole-second takes that sum to the requested length, as few as possible."""
    total = int(round(duration_s))
    if total < MIN_TAKE_S:
        raise FactoryError("DURATION_TOO_SHORT", str(total))
    count = -(-total // max_take_s)
    base, extra = divmod(total, count)
    if base < MIN_TAKE_S:
        raise FactoryError("DURATION_SPLIT_INVALID", f"{total}/{count}")
    return [base + (1 if index < extra else 0) for index in range(count)]


def plan(*, prompt: str, duration_s: float, still: Path,
         resolution: str = RESOLUTION, aspect: str = ASPECT,
         max_take_s: int = MAX_TAKE_S,
         prompts: Sequence[str] | None = None) -> ClipPlan:
    """Split the reel into takes. `prompts` gives each take its own rebased text."""
    if not prompt.strip():
        raise FactoryError("PROMPT_REQUIRED", "clip")
    lengths = split_duration(duration_s, max_take_s=max_take_s)
    if prompts is not None and len(prompts) != len(lengths):
        raise FactoryError("TAKE_PROMPT_COUNT", f"{len(prompts)}!={len(lengths)}")
    takes = [Take(index=index, duration_s=length, start_image=Path(still),
                  prompt=(prompts[index - 1].strip() if prompts
                          else prompt.strip()))
             for index, length in enumerate(lengths, 1)]
    return ClipPlan(takes=takes, resolution=resolution, aspect=aspect)


def build_command(take: Take, *, model: str = MODEL,
                  resolution: str = RESOLUTION, aspect: str = ASPECT,
                  wait: str = WAIT, as_json: bool = True) -> list[str]:
    command = [BINARY, "generate", "create", model, "--prompt", take.prompt,
               "--start-image", str(take.start_image),
               "--duration", str(take.duration_s),
               "--resolution", resolution, "--aspect_ratio", aspect]
    command.extend(["--wait", "--wait-timeout", wait])
    if as_json:
        command.append("--json")
    return command


def build_cost_command(take: Take, *, model: str = MODEL,
                       resolution: str = RESOLUTION,
                       aspect: str = ASPECT) -> list[str]:
    return [BINARY, "generate", "cost", model, "--prompt", take.prompt,
            "--duration", str(take.duration_s), "--resolution", resolution,
            "--aspect_ratio", aspect, "--json"]


def _run(command: Sequence[str], timeout: int) -> dict[str, Any]:
    if shutil.which(BINARY) is None:
        raise FactoryError("HIGGSFIELD_CLI_MISSING")
    try:
        done = subprocess.run(list(command), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise FactoryError("HIGGSFIELD_TIMEOUT") from None
    if done.returncode != 0:
        raise FactoryError("HIGGSFIELD_FAILED", scrub(done.stderr[-80:]))
    return parse_output(done.stdout)


def quote(take: Take, *, plan_: ClipPlan, runner: Any = None) -> float:
    invoke = runner or _run
    payload = invoke(build_cost_command(take, model=plan_.model,
                                        resolution=plan_.resolution,
                                        aspect=plan_.aspect), 120)
    for key in ("credits", "cost", "total_credits"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    raise FactoryError("HIGGSFIELD_COST_UNREADABLE", plan_.model)


def render_take(take: Take, destination: Path, *, plan_: ClipPlan,
                timeout: int = DEFAULT_TIMEOUT_SECONDS * 4,
                runner: Any = None) -> dict[str, Any]:
    invoke = runner or _run
    payload = invoke(build_command(take, model=plan_.model,
                                   resolution=plan_.resolution,
                                   aspect=plan_.aspect), timeout)
    urls = [url for url in result_paths(payload)
            if str(url).lower().startswith("http")]
    if not urls:
        raise FactoryError("NO_CLIP_OUTPUT", take.name)
    saved = fetch.download(urls[0], destination)
    return {"take": take.name, "job_id": str(payload.get("id") or ""),
            "path": str(saved), "duration_s": take.duration_s}


def seam_frame(source: Path, destination: Path) -> Path:
    """Last usable frame of a take, for the next take's start image."""
    return media.extract_frame(source, destination, at=-SEAM_BACKOFF_S)


def concat(parts: Sequence[Path], destination: Path) -> Path:
    """Stitch the takes. Re-encoded, because the takes come back independently
    encoded and `-c copy` on mismatched streams produces a file that plays for
    the first take only."""
    if not parts:
        raise FactoryError("NO_CLIP_PARTS")
    if len(parts) == 1:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parts[0], destination)
        return destination
    listing = destination.parent / "concat.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text("".join(f"file '{Path(part).resolve()}'\n"
                               for part in parts), encoding="utf-8")
    media.ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing),
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                  str(destination)])
    return destination


def write_plan(path: Path, plan_: ClipPlan, results: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"plan": plan_.as_dict(), "results": results},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path
