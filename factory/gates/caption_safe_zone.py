"""Caption safe zone for image and video text.

Geometry only: this gate takes the text boxes a renderer reports and judges them against
the zone from the format yaml (`safe_zone_px` / `center_safe_zone_px`, falling back to the
measured 1080x1920 defaults). It does not read pixels, so no PIL, no ffmpeg.

A violation means the frame is reassembled, not that the text is trimmed.
TODO (P2): once the video renderer exists, feed it the drawn box per caption line so this
runs automatically instead of on hand supplied coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920
SAFE_X = (200, 880)
SAFE_Y = (290, 1430)
MAX_TEXT_WIDTH = 640


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int
    label: str = ""

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "x": self.x, "y": self.y,
                "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Zone:
    x_min: int = SAFE_X[0]
    x_max: int = SAFE_X[1]
    y_min: int = SAFE_Y[0]
    y_max: int = SAFE_Y[1]
    max_text_width: int = MAX_TEXT_WIDTH

    def scaled(self, frame: tuple[int, int]) -> "Zone":
        """Scale the zone to a frame other than 1080x1920, keeping proportions."""
        scale_x = frame[0] / FRAME_WIDTH
        scale_y = frame[1] / FRAME_HEIGHT
        return Zone(x_min=int(self.x_min * scale_x), x_max=int(self.x_max * scale_x),
                    y_min=int(self.y_min * scale_y), y_max=int(self.y_max * scale_y),
                    max_text_width=int(self.max_text_width * scale_x))

    def as_dict(self) -> dict[str, int]:
        return {"x_min": self.x_min, "x_max": self.x_max, "y_min": self.y_min,
                "y_max": self.y_max, "max_text_width": self.max_text_width}


@dataclass
class SafeZoneReport:
    violations: list[str] = field(default_factory=list)
    zone: dict[str, int] = field(default_factory=dict)
    frame: tuple[int, int] = (FRAME_WIDTH, FRAME_HEIGHT)
    boxes: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "zone": dict(self.zone),
                "frame": list(self.frame), "boxes": self.boxes}


ZONE_KEYS = ("safe_zone_px", "center_safe_zone_px")


def declared_zone(limits: dict[str, Any] | None) -> dict[str, Any] | None:
    for key in ZONE_KEYS:
        raw = (limits or {}).get(key)
        if isinstance(raw, dict) and {"x_min", "x_max", "y_min", "y_max"} <= set(raw):
            return raw
    return None


def zone_from_limits(limits: dict[str, Any] | None) -> Zone:
    """Read safe_zone_px / center_safe_zone_px out of a format yaml limits block."""
    raw = declared_zone(limits)
    if raw is None:
        return Zone()
    width = int((limits or {}).get("max_text_width_px") or MAX_TEXT_WIDTH)
    return Zone(x_min=int(raw["x_min"]), x_max=int(raw["x_max"]),
                y_min=int(raw["y_min"]), y_max=int(raw["y_max"]),
                max_text_width=width)


def frame_from_limits(limits: dict[str, Any] | None) -> tuple[int, int]:
    width = (limits or {}).get("width_px")
    height = (limits or {}).get("height_px")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return FRAME_WIDTH, FRAME_HEIGHT


def parse_boxes(raw: Iterable[Any]) -> list[Box]:
    """Accept dicts or 4-tuples so a renderer, a yaml file or a CLI flag can all feed in."""
    boxes: list[Box] = []
    for index, entry in enumerate(raw, 1):
        if isinstance(entry, Box):
            boxes.append(entry)
            continue
        if isinstance(entry, dict):
            missing = {"x", "y", "width", "height"} - set(entry)
            if missing:
                raise ValueError(f"box {index} missing {sorted(missing)}")
            boxes.append(Box(x=int(entry["x"]), y=int(entry["y"]),
                             width=int(entry["width"]), height=int(entry["height"]),
                             label=str(entry.get("label") or f"box{index}")))
            continue
        if isinstance(entry, (list, tuple)) and len(entry) == 4:
            boxes.append(Box(x=int(entry[0]), y=int(entry[1]), width=int(entry[2]),
                             height=int(entry[3]), label=f"box{index}"))
            continue
        raise ValueError(f"box {index} is not a mapping or a 4-tuple")
    return boxes


def check_box(box: Box, zone: Zone) -> list[str]:
    label = box.label or "box"
    violations: list[str] = []
    if box.width <= 0 or box.height <= 0:
        violations.append(f"{label}:EMPTY_BOX")
        return violations
    if box.x < zone.x_min:
        violations.append(f"{label}:LEFT_OUT:{box.x}<{zone.x_min}")
    if box.right > zone.x_max:
        violations.append(f"{label}:RIGHT_OUT:{box.right}>{zone.x_max}")
    if box.y < zone.y_min:
        violations.append(f"{label}:TOP_OUT:{box.y}<{zone.y_min}")
    if box.bottom > zone.y_max:
        violations.append(f"{label}:BOTTOM_OUT:{box.bottom}>{zone.y_max}")
    if box.width > zone.max_text_width:
        violations.append(f"{label}:WIDTH_OVER:{box.width}>{zone.max_text_width}")
    return violations


def check(boxes: Sequence[Any], *, limits: dict[str, Any] | None = None,
          zone: Zone | None = None,
          frame: tuple[int, int] | None = None) -> SafeZoneReport:
    """Judge every text box. An empty box list is a FAIL: nothing to verify is not a pass."""
    resolved_frame = frame or frame_from_limits(limits)
    base = zone or zone_from_limits(limits)
    # Scaling exists to carry the measured 1080x1920 defaults onto another frame size.
    # A zone the caller passed in, or one the format yaml states in its own pixels, is
    # already in that frame's coordinates: rescaling it would silently shrink the zone
    # (a 1080x1350 card would get the 9:16 zone multiplied by 0.70 on the y axis).
    explicit = zone is not None or declared_zone(limits) is not None
    scaled = base if explicit or resolved_frame == (FRAME_WIDTH, FRAME_HEIGHT) \
        else base.scaled(resolved_frame)
    try:
        parsed = parse_boxes(boxes)
    except ValueError as error:
        return SafeZoneReport(violations=[f"BOX_INVALID:{error}"[:80]],
                              zone=scaled.as_dict(), frame=resolved_frame)
    if not parsed:
        return SafeZoneReport(violations=["NO_BOXES_SUPPLIED"], zone=scaled.as_dict(),
                              frame=resolved_frame)
    violations: list[str] = []
    for box in parsed:
        violations.extend(check_box(box, scaled))
    return SafeZoneReport(violations=violations, zone=scaled.as_dict(),
                          frame=resolved_frame, boxes=len(parsed))
