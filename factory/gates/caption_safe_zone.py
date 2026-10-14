"""Caption safe zone for image and video text. Stub.

TODO (P1/P2): port the measured zone from saju-shortform gen3/reelv1 caption tooling.
Defaults below are the verified 1080x1920 numbers: x 200..880, y 290..1430, max text
width 640. A violation means the frame is reassembled, not that the text is trimmed.
Implementing this properly needs a renderer that reports the drawn text box, which
arrives with the image and video factories.
"""
from __future__ import annotations

from dataclasses import dataclass

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


def check(box: Box, *, frame: tuple[int, int] = (FRAME_WIDTH, FRAME_HEIGHT)) -> list[str]:
    """Violations for one drawn text box. Geometry only; no rendering yet."""
    violations: list[str] = []
    scale_x = frame[0] / FRAME_WIDTH
    scale_y = frame[1] / FRAME_HEIGHT
    left, right = SAFE_X[0] * scale_x, SAFE_X[1] * scale_x
    top, bottom = SAFE_Y[0] * scale_y, SAFE_Y[1] * scale_y
    if box.x < left:
        violations.append(f"LEFT_OUT:{box.x}<{int(left)}")
    if box.x + box.width > right:
        violations.append(f"RIGHT_OUT:{box.x + box.width}>{int(right)}")
    if box.y < top:
        violations.append(f"TOP_OUT:{box.y}<{int(top)}")
    if box.y + box.height > bottom:
        violations.append(f"BOTTOM_OUT:{box.y + box.height}>{int(bottom)}")
    if box.width > MAX_TEXT_WIDTH * scale_x:
        violations.append(f"WIDTH_OVER:{box.width}>{int(MAX_TEXT_WIDTH * scale_x)}")
    return violations
