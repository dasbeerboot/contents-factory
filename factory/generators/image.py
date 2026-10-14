"""Image generator: a wrapper around the `higgsfield` CLI.

Minimal P0 surface. The command is assembled here and `--dry-run` prints it verbatim so
the exact invocation can be reviewed before any credits are spent. Model choice follows
P0-SPEC §1: nano_banana_pro for reference and character work, gpt_image_2 for design and
text, text2image_soul_v2 when the profile carries a Soul id.

The CLI owns Higgsfield auth, so no credential is read, built or printed here.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
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
        if self.aspect_ratio not in ASPECTS:
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


def build_command(job: ImageJob, *, wait: str = DEFAULT_WAIT,
                  as_json: bool = True) -> list[str]:
    job.validate()
    command = [BINARY, "generate", "create", job.model, "--prompt", job.prompt,
               "--aspect_ratio", job.aspect_ratio]
    for reference in job.references:
        command.extend(["--image", str(Path(reference).expanduser())])
    if job.soul_id:
        command.extend(["--soul-id", job.soul_id])
    if job.quality:
        command.extend(["--quality", job.quality])
    for key in sorted(job.params):
        command.extend([f"--{key}", str(job.params[key])])
    command.extend(["--wait", "--wait-timeout", wait])
    if as_json:
        command.append("--json")
    return command


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
        raise FactoryError("HIGGSFIELD_FAILED", scrub(completed.stderr[-80:]))
    return parse_output(completed.stdout)


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


def result_paths(payload: dict[str, Any]) -> list[str]:
    """Pull output urls or paths out of a finished job object, shape tolerant."""
    found: list[str] = []
    for key in ("results", "outputs", "assets", "media"):
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                found.append(entry)
            elif isinstance(entry, dict):
                for inner in ("url", "path", "raw_url", "min_url"):
                    value = entry.get(inner)
                    if isinstance(value, str) and value:
                        found.append(value)
                        break
    url = payload.get("url")
    if isinstance(url, str) and url and url not in found:
        found.append(url)
    return found
