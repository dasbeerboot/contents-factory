"""Identity gate: face proportion against the profile's reference image.

The complaint this came from was "the face is too long and the eyes are narrow",
and the measurement that explained it was face length over face width. Eye
aperture barely moved across the whole candidate set; the length-to-width ratio
separated the rejected stills from the approved ones cleanly. So L/W is the
first-order indicator and the threshold is the reference's own ratio plus a
small margin, defaulting to 1.33.

L is hairline to chin. W is the face width measured low — at the eye line plus
forty percent of the distance down to the chin — because that is where a long
face gives itself away.

How this differs from the hand-run original: the region of interest is derived
from the largest skin component instead of being typed in per image. That makes
it runnable in a pipeline, and it means the absolute numbers are not comparable
with the hand-set ones. What is comparable is candidate against reference, both
measured the same way in the same run, which is the comparison the gate makes.
A ratio is never the whole verdict — the identity rules in the profile still ask
for a side-by-side look before anything ships.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import vision
from ..errors import FactoryError

RATIO_MAX = 1.33
WIDTH_AT_FRACTION = 0.40
EYE_LINE_FRACTION = 0.42
MIN_FACE_PIXELS = 400
ANALYSIS_WIDTH = 540
# Relative, not absolute. The hand-run protocol used a fixed +0.05 because a
# typed-in region of interest is repeatable to within a pixel or two; an
# automatic one varies with head tilt, hair edge and ear visibility, so a fixed
# margin that tight rejects the same face twice. 15 percent still separates the
# documented cases: the rejected still measured 20 percent longer than the
# approved one.
MARGIN_FRACTION = 0.15
NECK_WIDTH_FRACTION = 0.72


@dataclass
class Face:
    length: int = 0
    width: int = 0
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    pixels: int = 0

    @property
    def ratio(self) -> float:
        return round(self.length / float(max(self.width, 1)), 3)

    def as_dict(self) -> dict[str, Any]:
        return {"length_px": self.length, "width_px": self.width,
                "ratio": self.ratio, "bbox": list(self.bbox),
                "pixels": self.pixels}


@dataclass
class IdentityReport:
    violations: list[str] = field(default_factory=list)
    candidate: dict[str, Any] = field(default_factory=dict)
    reference: dict[str, Any] = field(default_factory=dict)
    threshold: float = RATIO_MAX
    montage: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations),
                "candidate": dict(self.candidate),
                "reference": dict(self.reference),
                "threshold": self.threshold, "montage": self.montage,
                "note": self.note}


def chin_row(widths: Any, top: int, bottom: int) -> int:
    """Where the face ends, found by the neck rather than by the blob's end.

    The skin mask stops at the collar when the top is dark, but a skin-toned
    top joins face, neck and chest into one component and the measured face
    becomes a torso. Clothing colour must not decide whether a face is long.

    Below the cheekbones the silhouette narrows to the neck and then widens
    again at the shoulders, so the face ends at the first row that drops below a
    fraction of the cheek width. Without that dip the blob already ended at the
    collar and its own bottom is the chin.
    """
    numpy = vision.numpy_module()
    span = bottom - top + 1
    if span < 4:
        return bottom
    upper = widths[:max(2, int(span * 0.6))]
    peak = int(numpy.argmax(upper))
    cheek = float(upper[peak])
    if cheek <= 0:
        return bottom
    for offset in range(peak + 1, span):
        if widths[offset] < cheek * NECK_WIDTH_FRACTION:
            return top + offset
    return bottom


def measure(path: Path) -> Face:
    """Face length and low-face width from the largest skin component."""
    numpy = vision.numpy_module()
    image = vision.image_module().open(path).convert("RGB")
    scale = ANALYSIS_WIDTH / float(max(image.width, 1))
    if scale < 1.0:
        image = image.resize((ANALYSIS_WIDTH,
                              max(2, int(round(image.height * scale)))))
    pixels = numpy.asarray(image)
    height, _width, _depth = pixels.shape
    skin = vision.skin_mask(pixels)
    # Search the upper two thirds: hands and forearms are skin too, and a hand
    # near the chin would otherwise lengthen the measured face.
    skin[int(height * 0.66):, :] = False
    labels, count = vision.label(vision.erode(skin, 1))
    found = vision.blobs(labels, count)
    if not found or found[0].area < MIN_FACE_PIXELS:
        raise FactoryError("FACE_NOT_FOUND", Path(path).name[:40])
    blob = found[0]
    mask = labels == blob.tag
    rows = mask[blob.y0:blob.y1 + 1]
    widths = numpy.array([
        (int(numpy.nonzero(row)[0][-1] - numpy.nonzero(row)[0][0] + 1)
         if row.any() else 0) for row in rows])
    bottom = chin_row(widths, blob.y0, blob.y1)
    length = max(bottom - blob.y0 + 1, 1)
    eye_line = blob.y0 + int(round(length * EYE_LINE_FRACTION))
    row = min(bottom, eye_line + int(round((bottom - eye_line)
                                           * WIDTH_AT_FRACTION)))
    columns = numpy.nonzero(mask[row])[0]
    width = int(columns[-1] - columns[0] + 1) if columns.size else blob.width
    return Face(length=int(length), width=int(width),
                bbox=(blob.x0, blob.y0, blob.x1, int(bottom)),
                pixels=int(blob.area))


def check(candidate: Path, *, reference: str | None = None,
          limits: dict[str, Any] | None = None,
          montage: Path | None = None) -> IdentityReport:
    """Compare the candidate's face proportion with the profile reference's."""
    rules = limits or {}
    threshold = float(rules.get("identity_ratio_max") or RATIO_MAX)
    report = IdentityReport(threshold=threshold)
    face = measure(Path(candidate))
    report.candidate = face.as_dict()

    if reference and Path(reference).expanduser().is_file():
        try:
            reference_face = measure(Path(reference).expanduser())
        except FactoryError:
            report.note = "REFERENCE_FACE_NOT_FOUND"
        else:
            report.reference = reference_face.as_dict()
            threshold = round(reference_face.ratio
                              * (1.0 + float(rules.get("identity_margin")
                                             or MARGIN_FRACTION)), 3)
            report.threshold = threshold
    else:
        report.note = "NO_REFERENCE_IMAGE"

    if face.ratio > threshold:
        report.violations.append(f"FACE_TOO_LONG:{face.ratio:g}>{threshold:g}")
    if montage and report.reference:
        report.montage = str(vision.contact_sheet(
            [Path(reference).expanduser(), Path(candidate)], Path(montage),
            columns=2,
            caption=f"identity ref L/W={report.reference['ratio']} "
                    f"cand L/W={face.ratio} thr={threshold:g}"))
    return report


def check_video(source: Path, *, reference: str | None = None,
                limits: dict[str, Any] | None = None,
                at: tuple[float, ...] = (0.5, 0.5, 0.5),
                montage: Path | None = None) -> IdentityReport:
    """Same check on frames pulled out of a take, worst frame wins.

    Identity drift shows up partway through a clip, so one frame is not enough:
    the frames are sampled across the take and the longest face is the verdict.
    """
    from .. import media
    import tempfile

    duration = media.duration(Path(source))
    marks = [round(duration * fraction, 3)
             for fraction in (0.15, 0.5, 0.85)] if len(at) == 3 else list(at)
    worst: IdentityReport | None = None
    with tempfile.TemporaryDirectory(prefix="factory-identity-") as scratch:
        for index, mark in enumerate(marks):
            frame = Path(scratch) / f"id_{index}.png"
            media.extract_frame(Path(source), frame, at=mark, width=1080)
            try:
                report = check(frame, reference=reference, limits=limits)
            except FactoryError:
                continue
            report.candidate["at_s"] = mark
            if worst is None or report.candidate["ratio"] > worst.candidate["ratio"]:
                if montage and index == len(marks) - 1:
                    report = check(frame, reference=reference, limits=limits,
                                   montage=montage)
                    report.candidate["at_s"] = mark
                worst = report
    if worst is None:
        raise FactoryError("FACE_NOT_FOUND", Path(source).name[:40])
    return worst
