"""Stage 2: the first frame.

The still is the cheapest decision in the pipeline and the most expensive to get
wrong, because the clip model locks whatever the still shows. A locked elbow in
the still stays locked for the whole take (video-rules: "스틸 자세는 영상에서
잠긴다"), and a face that drifted in the still drifts in every frame after it.

Identity comes from the profile, never from the prompt: a Soul id when the
profile carries one, otherwise the profile's reference image. Describing a face
in words is what moves it, so the still prompt describes the scene and the
framing and leaves the face to the reference.

Costs 0.12 credits on a Soul model, which is why the budget check still runs:
cheap is not free, and the per-item cap counts every stage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import FactoryError
from .. import fetch
from .. import image as image_generator

STILL_ASPECT = "9:16"
QUALITY = "2k"
MAX_CANDIDATES = 6


def build_job(*, prompt: str, soul_id: str | None, reference: str | None,
              aspect: str = STILL_ASPECT,
              credit_cap: int = 0) -> image_generator.ImageJob:
    """A Soul job when the profile has an id, a reference job when it has a file."""
    if not prompt.strip():
        raise FactoryError("PROMPT_REQUIRED", "still")
    references: tuple[str, ...] = ()
    if not soul_id:
        if not reference:
            raise FactoryError("IDENTITY_SOURCE_MISSING", "soul_id_or_ref_image")
        if not Path(reference).expanduser().is_file():
            raise FactoryError("REFERENCE_MISSING",
                               Path(reference).name[:40])
        references = (reference,)
    model = image_generator.pick_model(has_soul=bool(soul_id),
                                       has_reference=bool(references),
                                       needs_text=False)
    return image_generator.ImageJob(
        prompt=prompt.strip(), model=model, aspect_ratio=aspect,
        references=references,
        soul_id=soul_id if model == image_generator.MODEL_SOUL else None,
        quality=QUALITY if model == image_generator.MODEL_SOUL else None,
        count=1, credit_cap=credit_cap)


def quote(job: image_generator.ImageJob, *, runner: Any = None) -> float:
    return image_generator.quote(job, runner=runner)


def generate(job: image_generator.ImageJob, destination: Path, *,
             runner: Any = None) -> dict[str, Any]:
    """Render one still and keep it. Returns the provider payload plus the path."""
    payload = image_generator.generate(job, runner=runner)[0]
    urls = [url for url in image_generator.result_paths(payload)
            if str(url).lower().startswith("http")]
    if not urls:
        raise FactoryError("NO_STILL_OUTPUT", job.model)
    saved = fetch.download(urls[0], destination)
    return {"job_id": str(payload.get("id") or ""), "model": job.model,
            "path": str(saved), "candidates": len(urls)}
