"""Image generator: a wrapper around the `higgsfield` CLI.

Minimal P0 surface. The command is assembled here and `--dry-run` prints it verbatim so
the exact invocation can be reviewed before any credits are spent. Model choice follows
P0-SPEC §1: nano_banana_pro for reference and character work, gpt_image_2 for design and
text, text2image_soul_v2 when the profile carries a Soul id.

The CLI owns Higgsfield auth, so no credential is read, built or printed here.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..errors import FactoryError, scrub

BINARY = "higgsfield"
MODEL_DESIGN = "gpt_image_2"
MODEL_REFERENCE = "nano_banana_pro"
MODEL_SOUL = "text2image_soul_v2"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_WAIT = "15m"
MAX_REFERENCES = 3
ASPECTS: tuple[str, ...] = ("1:1", "4:5", "9:16", "16:9", "1.91:1", "3:2", "2:3")
ASPECT_SHAPE = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class ImageJob:
    prompt: str
    model: str = MODEL_DESIGN
    aspect_ratio: str = "4:5"
    references: tuple[str, ...] = ()
    soul_id: str | None = None
    quality: str | None = None
    count: int = 1
    credit_cap: int = 0
    params: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.prompt.strip():
            raise FactoryError("PROMPT_REQUIRED", "image")
        # Shape, not membership: each model declares its own aspect enum and
        # `snap_aspect` has already mapped onto it. A fixed list here would
        # reject ratios the model accepts (`3:4`, `5:4`, `21:9`).
        if not ASPECT_SHAPE.match(self.aspect_ratio):
            raise FactoryError("ASPECT_UNSUPPORTED", self.aspect_ratio[:16])
        if len(self.references) > MAX_REFERENCES:
            # image-rules: more than three references at once and they blend into mush.
            raise FactoryError("TOO_MANY_REFERENCES", str(len(self.references)))
        for reference in self.references:
            if not Path(reference).expanduser().is_file() and "-" not in reference:
                raise FactoryError("REFERENCE_MISSING", Path(reference).name[:40])
        if self.count < 1 or self.count > 20:
            raise FactoryError("COUNT_INVALID", str(self.count))
        if self.model == MODEL_SOUL and not self.soul_id:
            raise FactoryError("SOUL_ID_REQUIRED", self.model)


def pick_model(*, has_soul: bool, has_reference: bool, needs_text: bool) -> str:
    if has_soul:
        return MODEL_SOUL
    if needs_text:
        return MODEL_DESIGN
    if has_reference:
        return MODEL_REFERENCE
    return MODEL_DESIGN


def aspect_value(text: str) -> float:
    """"4:5" -> 0.8. Used to snap a format's aspect onto a model's enum."""
    try:
        width, _, height = str(text).partition(":")
        return float(width) / float(height)
    except (TypeError, ValueError, ZeroDivisionError):
        raise FactoryError("ASPECT_UNSUPPORTED", str(text)[:16]) from None


def model_params(model: str, *, runner: Any = None,
                 timeout: int = 60) -> dict[str, Any]:
    """The model's declared schema, as the CLI reports it."""
    invoke = runner or _subprocess_runner
    return invoke([BINARY, "model", "get", model, "--json"], timeout)


def supported_aspects(model: str, *, runner: Any = None) -> tuple[str, ...]:
    """The model's own aspect_ratio enum, empty when it does not declare one."""
    try:
        schema = model_params(model, runner=runner)
    except FactoryError:
        return ()
    for param in schema.get("params") or []:
        if isinstance(param, dict) and param.get("name") == "aspect_ratio":
            values = param.get("enum")
            if isinstance(values, list):
                return tuple(str(value) for value in values
                             if str(value) != "auto")
    return ()


def snap_aspect(wanted: str, allowed: Sequence[str]) -> str:
    """Closest declared aspect to the one the format asked for.

    A format states the aspect its platform wants (`1.91:1` for an Open Graph
    card); a model declares a fixed enum that may not contain it. Snapping to
    the nearest ratio and cropping to the exact pixel size afterwards beats
    failing the call, and beats silently generating a square.
    """
    if not allowed or wanted in allowed:
        return wanted
    target = aspect_value(wanted)
    return min(allowed, key=lambda value: abs(aspect_value(value) - target))


def _job_flags(job: ImageJob) -> list[str]:
    """The params shared by `generate create` and `generate cost`."""
    flags = ["--prompt", job.prompt, "--aspect_ratio", job.aspect_ratio]
    for reference in job.references:
        flags.extend(["--image", str(Path(reference).expanduser())])
    if job.soul_id:
        flags.extend(["--soul-id", job.soul_id])
    if job.quality:
        flags.extend(["--quality", job.quality])
    for key in sorted(job.params):
        flags.extend([f"--{key}", str(job.params[key])])
    return flags


def build_command(job: ImageJob, *, wait: str = DEFAULT_WAIT,
                  as_json: bool = True) -> list[str]:
    job.validate()
    command = [BINARY, "generate", "create", job.model, *_job_flags(job)]
    command.extend(["--wait", "--wait-timeout", wait])
    if as_json:
        command.append("--json")
    return command


def build_cost_command(job: ImageJob) -> list[str]:
    """`generate cost` takes the same params, minus the wait flags."""
    job.validate()
    return [BINARY, "generate", "cost", job.model, *_job_flags(job), "--json"]


def plan(job: ImageJob, *, wait: str = DEFAULT_WAIT) -> dict[str, Any]:
    """Dry run payload: the exact argv, plus what it would cost and produce."""
    command = build_command(job, wait=wait)
    return {"binary": BINARY,
            "available": shutil.which(BINARY) is not None,
            "model": job.model,
            "count": job.count,
            "credit_cap_per_item": job.credit_cap,
            "aspect_ratio": job.aspect_ratio,
            "references": list(job.references),
            "soul_id": job.soul_id,
            "argv": command,
            "command": " ".join(_quote(part) for part in command),
            "runs": job.count,
            "note": "no network call was made"}


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part or '"' in part else part


def quote(job: ImageJob, *, timeout: int = 120, runner: Any = None) -> float:
    """Credits one run of this job costs, straight from the CLI estimator.

    The per-item cap is checked against this number, so an unreadable answer is
    a hard error rather than an optimistic zero.
    """
    invoke = runner or _subprocess_runner
    payload = invoke(build_cost_command(job), timeout)
    for key in ("credits", "cost", "total_credits"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    raise FactoryError("HIGGSFIELD_COST_UNREADABLE", job.model)


def generate(job: ImageJob, *, wait: str = DEFAULT_WAIT,
             timeout: int = DEFAULT_TIMEOUT_SECONDS,
             runner: Any = None) -> list[dict[str, Any]]:
    """Run the CLI once per requested image and return the parsed job objects."""
    if shutil.which(BINARY) is None and runner is None:
        raise FactoryError("HIGGSFIELD_CLI_MISSING")
    command = build_command(job, wait=wait)
    invoke = runner or _subprocess_runner
    results: list[dict[str, Any]] = []
    for _index in range(job.count):
        results.append(invoke(command, timeout))
    return results


def _subprocess_runner(command: Sequence[str], timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(list(command), capture_output=True, text=True,
                                   timeout=timeout, check=False)
    except FileNotFoundError:
        raise FactoryError("HIGGSFIELD_CLI_MISSING") from None
    except subprocess.TimeoutExpired:
        raise FactoryError("HIGGSFIELD_TIMEOUT") from None
    if completed.returncode != 0:
        raise FactoryError("HIGGSFIELD_FAILED",
                           provider_log(command[0], completed.stderr)) from None
    return parse_output(completed.stdout)


def provider_log(binary: str, stderr: str) -> str:
    """Park a provider failure in a log file and return a reference to it.

    An error code with nothing attached is unactionable, but a raw stderr tail
    in the response is how a credential ends up in a transcript. So the scrubbed
    detail goes to a file under the runtime home and the response carries only
    the filename.
    """
    from ..config import DEFAULT_HOME, HOME_ENV
    import os

    body = scrub(stderr or "")
    base = Path(os.environ.get(HOME_ENV, "").strip()
                or DEFAULT_HOME).expanduser() / "logs"
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{Path(binary).name}-{stamp}.log"
        (base / name).write_text(body + "\n", encoding="utf-8")
    except OSError:
        return "log_unwritable"
    return f"see:logs/{name}"


def parse_output(stdout: str) -> dict[str, Any]:
    body = (stdout or "").strip()
    if not body:
        raise FactoryError("HIGGSFIELD_EMPTY_OUTPUT")
    try:
        parsed = json.loads(body)
    except ValueError:
        raise FactoryError("HIGGSFIELD_OUTPUT_INVALID") from None
    if isinstance(parsed, list):
        if not parsed:
            raise FactoryError("HIGGSFIELD_EMPTY_OUTPUT")
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise FactoryError("HIGGSFIELD_OUTPUT_INVALID")
    return parsed


RESULT_KEYS: tuple[str, ...] = ("result_url", "url", "min_result_url")


def result_paths(payload: dict[str, Any]) -> list[str]:
    """Pull output urls or paths out of a finished job object, shape tolerant.

    `result_url` is the full-size asset and `min_result_url` a small webp
    preview, so the order matters: taking the preview because it was found first
    is how a 2688px render becomes a thumbnail in the brief.
    """
    found: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in found:
            found.append(value)

    for key in RESULT_KEYS:
        add(payload.get(key))
        if found:
            # The first key that answers is the full-size asset; stop before
            # the preview url gets picked up as a second output.
            break
    for key in ("results", "outputs", "assets", "media"):
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                add(entry)
            elif isinstance(entry, dict):
                for inner in ("result_url", "url", "path", "raw_url", "min_url"):
                    if isinstance(entry.get(inner), str) and entry[inner]:
                        add(entry[inner])
                        break
    return found
