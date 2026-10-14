"""FROZEN_LIMB gate: an arm that never moved for the whole take.

The defect this catches is specific. A clip model treats the start image as a
constraint, so an arm that was stretched out or elbow-locked in the still stays
exactly there for twenty-odd seconds while the rest of the frame walks on. It
reads as uncanny immediately and no amount of negative prompting fixes it after
the fact — which is why the still gate rejects locked arms too.

Method, ported from the calibrated original:

1. sample frames, build a skin mask and a cloth mask
2. label components on the **full** frame, not a crop, so chin and neck stay
   attached to the face; a component reaching into the top half is head or
   background and is dropped. What survives is skin detached from the face.
3. express the arm centroid **relative to a body anchor** (torso blob, falling
   back to the head blob) so walking sway and camera drift subtract out
4. FAIL when an arm is continuously present for at least the minimum frozen
   duration and the standard deviation of that relative vector stays under the
   threshold

No arm blob at all is a PASS: an arm out of frame cannot be frozen. Two
suppression rules keep that honest — a stub that only clips the bottom edge, and
a sleeve-bounded upper-arm patch whose outline is shirt plus frame border and
almost no free background, are not arms and are dropped before tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import vision
from ..errors import FactoryError

STD_THRESHOLD = 24.0
MIN_FROZEN_S = 3.0
ERODE_ITERATIONS = 2
ARM_BAND_TOP = 0.60
ARM_SIDE = 0.45
ARM_AREA_MIN = 0.010
ARM_AREA_MAX = 0.45
ARM_FILL_MIN = 0.32
ARM_BBOX_WIDTH_MAX = 0.85
ARM_BBOX_HEIGHT_MAX = 0.95
HEAD_EXCLUDE_TOP = 0.45
HEAD_BAND = 0.50
TORSO_BAND = (0.38, 0.88)
TORSO_SIDE = (0.10, 0.90)
TORSO_AREA_MIN = 0.04
BOTTOM_BAND = 0.12
STUB_HEIGHT_FRACTION = 0.06
SLEEVE_BG_MAX = 0.18
SLEEVE_CLOTH_MIN = 0.25
SMOOTH_WINDOW = 5
SIDES = ("left", "right")


@dataclass
class LimbReport:
    violations: list[str] = field(default_factory=list)
    reason: str = ""
    frozen: list[dict[str, Any]] = field(default_factory=list)
    sides: dict[str, Any] = field(default_factory=dict)
    suppressed: dict[str, int] = field(default_factory=dict)
    samples: int = 0
    threshold: float = STD_THRESHOLD
    min_frozen_s: float = MIN_FROZEN_S
    sheet: str = ""

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "reason": self.reason,
                "frozen": list(self.frozen), "sides": dict(self.sides),
                "suppressed": dict(self.suppressed), "samples": self.samples,
                "threshold_1080px": self.threshold,
                "min_frozen_s": self.min_frozen_s, "sheet": self.sheet}


def ring_composition(mask: Any, cloth: Any) -> tuple[float, float, float]:
    """Outline of a blob split into (cloth, frame edge, free background) shares.

    The blob is padded so the frame border becomes ring pixels: an upper arm cut
    off by the bottom of frame is bounded by sleeve plus border and nothing else,
    while a real forearm hanging in the shot is outlined by background for a
    third of its perimeter or more.
    """
    numpy = vision.numpy_module()
    height, width = mask.shape
    padded = numpy.zeros((height + 2, width + 2), bool)
    padded[1:-1, 1:-1] = mask
    ring = vision.dilate(padded, 1) & ~padded
    rows, cols = numpy.nonzero(ring)
    total = rows.size
    if total == 0:
        return 0.0, 0.0, 1.0
    on_edge = (rows == 0) | (rows == height + 1) | (cols == 0) | (cols == width + 1)
    inner_rows = numpy.clip(rows[~on_edge] - 1, 0, height - 1)
    inner_cols = numpy.clip(cols[~on_edge] - 1, 0, width - 1)
    cloth_count = int(cloth[inner_rows, inner_cols].sum())
    edge_count = int(on_edge.sum())
    return (cloth_count / float(total), edge_count / float(total),
            (total - cloth_count - edge_count) / float(total))


def not_an_arm(mask: Any, cloth: Any, blob: vision.Blob, height: int) -> str:
    """"STUB" or "SLEEVE" when this blob cannot gesture, else an empty string."""
    if blob.y1 < height * (1.0 - BOTTOM_BAND):
        return ""
    if blob.height / float(height) < STUB_HEIGHT_FRACTION:
        return "STUB"
    cloth_share, _edge, background = ring_composition(mask, cloth)
    if background <= SLEEVE_BG_MAX and cloth_share >= SLEEVE_CLOTH_MIN:
        return "SLEEVE"
    return ""


def detect_arms(skin: Any, cloth: Any, height: int, width: int
                ) -> tuple[dict[str, Any], dict[str, str]]:
    mask = vision.dilate(vision.erode(skin, ERODE_ITERATIONS), ERODE_ITERATIONS)
    labels, count = vision.label(mask)
    band_top = int(height * ARM_BAND_TOP)
    region_height, region_width = height - band_top, int(width * ARM_SIDE)
    region_area = float(region_height * region_width)
    head_limit = height * HEAD_EXCLUDE_TOP
    found: dict[str, Any] = {"left": None, "right": None}
    dropped: dict[str, str] = {}
    for blob in vision.blobs(labels, count):
        if blob.y0 < head_limit or blob.cy < band_top:
            continue
        side = ("left" if blob.cx < width * ARM_SIDE else
                "right" if blob.cx > width * (1.0 - ARM_SIDE) else "")
        if not side or found[side] is not None:
            continue
        area_fraction = blob.area / region_area
        if area_fraction < ARM_AREA_MIN or area_fraction > ARM_AREA_MAX:
            continue
        if (blob.width / float(region_width) > ARM_BBOX_WIDTH_MAX
                or blob.height / float(region_height) > ARM_BBOX_HEIGHT_MAX):
            continue
        if blob.fill < ARM_FILL_MIN:
            continue
        why = not_an_arm(labels == blob.tag, cloth, blob, height)
        if why:
            dropped.setdefault(side, why)
            continue
        found[side] = {"x": round(blob.cx, 2), "y": round(blob.cy, 2),
                       "area_frac": round(area_fraction, 4),
                       "bbox": blob.bbox()}
    return found, dropped


def pick_anchor(skin: Any, cloth: Any, height: int,
                width: int) -> dict[str, Any] | None:
    """Body reference point: the torso blob, else the head blob."""
    top, bottom = int(height * TORSO_BAND[0]), int(height * TORSO_BAND[1])
    left, right = int(width * TORSO_SIDE[0]), int(width * TORSO_SIDE[1])
    region = cloth[top:bottom, left:right]
    area = float(region.shape[0] * region.shape[1])
    labels, count = vision.label(vision.erode(region, 2))
    found = vision.blobs(labels, count)
    if found and found[0].area / area >= TORSO_AREA_MIN:
        return {"x": round(found[0].cx + left, 2),
                "y": round(found[0].cy + top, 2), "src": "torso"}
    labels, count = vision.label(vision.erode(skin[:int(height * HEAD_BAND), :], 2))
    found = vision.blobs(labels, count)
    if found:
        return {"x": round(found[0].cx, 2), "y": round(found[0].cy, 2),
                "src": "head"}
    return None


def _spread(segment: Any) -> tuple[float, float, float]:
    numpy = vision.numpy_module()
    sx = float(numpy.std(segment[:, 0]))
    sy = float(numpy.std(segment[:, 1]))
    return float(numpy.hypot(sx, sy)), sx, sy


def quietest_window(times: list[float], relative: Any,
                    min_seconds: float) -> tuple[float, int, int] | None:
    """Lowest-spread window of at least `min_seconds` inside a continuous run."""
    best: tuple[float, int, int] | None = None
    total = len(times)
    for start in range(total):
        for stop in range(start + 1, total + 1):
            if times[stop - 1] - times[start] < min_seconds:
                continue
            spread, _sx, _sy = _spread(relative[start:stop])
            if best is None or spread < best[0]:
                best = (spread, start, stop - 1)
            break
    return best


def expand(relative: Any, low: int, high: int,
           threshold: float) -> tuple[int, int]:
    """Grow the seed window while the whole segment stays under threshold, so
    the report names the real frozen stretch rather than its quietest slice."""
    moved = True
    while moved:
        moved = False
        if low > 0 and _spread(relative[low - 1:high + 1])[0] < threshold:
            low -= 1
            moved = True
        if high < len(relative) - 1 and \
                _spread(relative[low:high + 2])[0] < threshold:
            high += 1
            moved = True
    return low, high


def check(source: Path, *, fps: float = vision.SAMPLE_FPS,
          threshold: float = STD_THRESHOLD,
          min_frozen_s: float = MIN_FROZEN_S,
          sheet: Path | None = None,
          limits: dict[str, Any] | None = None) -> LimbReport:
    numpy = vision.numpy_module()
    rules = limits or {}
    threshold = float(rules.get("limb_std_threshold") or threshold)
    min_frozen_s = float(rules.get("limb_min_frozen_s") or min_frozen_s)
    report = LimbReport(threshold=threshold, min_frozen_s=min_frozen_s)
    scale = vision.scale_factor()
    records: list[dict[str, Any]] = []
    with vision.sampled(source, fps=fps) as frames:
        if not frames:
            raise FactoryError("NO_FRAMES")
        sheet_boxes: list[list[tuple[int, int, int, int]]] = []
        for index, path in enumerate(frames):
            pixels = vision.load_rgb(path)
            height, width, _depth = pixels.shape
            skin = vision.skin_mask(pixels)
            cloth = vision.cloth_mask(pixels, skin)
            arms, dropped = detect_arms(skin, cloth, height, width)
            records.append({"t": round(index / float(fps), 3),
                            "anchor": pick_anchor(skin, cloth, height, width),
                            **arms})
            for why in dropped.values():
                report.suppressed[why] = report.suppressed.get(why, 0) + 1
            sheet_boxes.append([tuple(arms[side]["bbox"]) for side in SIDES
                                if arms[side]])
        report.samples = len(records)
        if sheet:
            report.sheet = str(vision.contact_sheet(
                frames, Path(sheet), boxes=sheet_boxes,
                caption=f"limb_motion thr={threshold:g}px "
                        f"min={min_frozen_s:g}s n={len(records)}"))

    for side in SIDES:
        present = [bool(record[side] and record["anchor"]) for record in records]
        info: dict[str, Any] = {"present_frames": sum(present),
                                "present_frac": round(
                                    sum(present) / float(max(len(records), 1)), 3),
                                "min_window_std_1080px": None,
                                "frozen_windows": []}
        quietest: float | None = None
        for start, stop in vision.runs_of(present):
            if records[stop - 1]["t"] - records[start]["t"] < min_frozen_s:
                continue
            times = [records[index]["t"] for index in range(start, stop)]
            relative = numpy.array(
                [[records[index][side]["x"] - records[index]["anchor"]["x"],
                  records[index][side]["y"] - records[index]["anchor"]["y"]]
                 for index in range(start, stop)], dtype=numpy.float64) * scale
            relative = vision.median_smooth(relative, SMOOTH_WINDOW)
            window = quietest_window(times, relative, min_frozen_s)
            if window is None:
                continue
            spread, low, high = window
            if quietest is None or spread < quietest:
                quietest = spread
            if spread >= threshold:
                continue
            low, high = expand(relative, low, high, threshold)
            final, spread_x, spread_y = _spread(relative[low:high + 1])
            frozen = {"side": side, "t0": times[low], "t1": times[high],
                      "sec": round(times[high] - times[low], 2),
                      "std_1080px": round(final, 2),
                      "std_xy_1080px": [round(spread_x, 2), round(spread_y, 2)]}
            info["frozen_windows"].append(frozen)
            report.frozen.append(frozen)
        info["min_window_std_1080px"] = (None if quietest is None
                                         else round(quietest, 2))
        report.sides[side] = info

    no_arm = all(report.sides[side]["present_frames"] == 0 for side in SIDES)
    if report.frozen:
        report.reason = "FROZEN_LIMB"
        report.violations = [f"FROZEN_LIMB:{entry['side']}:"
                             f"{entry['sec']:g}s:{entry['std_1080px']:g}px"
                             for entry in report.frozen]
    elif no_arm:
        report.reason = ("NO_ARM_BLOB_SUPPRESSED" if report.suppressed
                         else "NO_ARM_BLOB")
    else:
        report.reason = "LIMB_MOVES"
    return report
