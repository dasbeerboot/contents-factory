"""Frame-analysis primitives shared by the limb, background and identity gates.

Ported from the reel pipeline's calibrated gates. Deliberately no OpenCV and no
SciPy: ffmpeg samples the frames, Pillow decodes them and numpy does the rest,
which keeps the gates installable anywhere ffmpeg already is.

Two conventions run through all of it:

* **Analysis width.** Frames are scaled to a fixed analysis width before
  anything is measured, and every reported pixel number is then normalised back
  to a 1080-wide frame. A threshold calibrated on one take therefore means the
  same thing on a 720p take and on a 1080p final.
* **Skin before shape.** The person is found by colour, not by a face detector.
  The `(R-G) > (G-B)` term is the part that matters: a plain YCrCb skin box also
  selects warm pavement and yellow road paint, which then track as a perfectly
  steady "arm".

numpy and Pillow are optional dependencies (`video` extra). Without them the
gates raise `GATE_DEPS_MISSING` rather than silently passing.
"""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from .. import media
from ..errors import FactoryError

ANALYSIS_WIDTH = 540
REFERENCE_WIDTH = 1080
SKIN_RG_OVER_GB = 1.0
SAMPLE_FPS = 4.0


def numpy_module() -> Any:
    try:
        import numpy
    except ModuleNotFoundError:
        raise FactoryError("GATE_DEPS_MISSING", "numpy") from None
    return numpy


def image_module() -> Any:
    try:
        from PIL import Image
    except ModuleNotFoundError:
        raise FactoryError("GATE_DEPS_MISSING", "pillow") from None
    return Image


def load_rgb(path: Path) -> Any:
    numpy = numpy_module()
    return numpy.asarray(image_module().open(path).convert("RGB"))


@contextmanager
def sampled(source: Path, *, fps: float = SAMPLE_FPS,
            width: int = ANALYSIS_WIDTH) -> Iterator[list[Path]]:
    """Frames of `source` at `fps`, cleaned up afterwards."""
    with tempfile.TemporaryDirectory(prefix="factory-frames-") as scratch:
        yield media.sample_frames(Path(source), Path(scratch), fps=fps,
                                  width=width)


def erode(mask: Any, iterations: int = 1) -> Any:
    for _ in range(iterations):
        shrunk = mask.copy()
        shrunk[1:, :] &= mask[:-1, :]
        shrunk[:-1, :] &= mask[1:, :]
        shrunk[:, 1:] &= mask[:, :-1]
        shrunk[:, :-1] &= mask[:, 1:]
        shrunk[0, :] = False
        shrunk[-1, :] = False
        shrunk[:, 0] = False
        shrunk[:, -1] = False
        mask = shrunk
    return mask


def dilate(mask: Any, iterations: int = 1) -> Any:
    for _ in range(iterations):
        grown = mask.copy()
        grown[1:, :] |= mask[:-1, :]
        grown[:-1, :] |= mask[1:, :]
        grown[:, 1:] |= mask[:, :-1]
        grown[:, :-1] |= mask[:, 1:]
        mask = grown
    return mask


def label(mask: Any) -> tuple[Any, int]:
    """Run-length connected components, 8-neighbour. Returns (labels, count)."""
    numpy = numpy_module()
    height, width = mask.shape
    if not mask.any():
        return numpy.zeros((height, width), numpy.int32), 0
    padded = numpy.zeros((height, width + 2), numpy.int8)
    padded[:, 1:-1] = mask
    edges = numpy.diff(padded, axis=1)
    start_rows, start_cols = numpy.nonzero(edges == 1)
    _end_rows, end_cols = numpy.nonzero(edges == -1)
    runs = list(zip(start_rows.tolist(), start_cols.tolist(), end_cols.tolist()))
    count = len(runs)
    parent = list(range(count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    row_start: dict[int, int] = {}
    for index, (row, _start, _end) in enumerate(runs):
        row_start.setdefault(row, index)
    for index, (row, col_start, col_end) in enumerate(runs):
        cursor = row_start.get(row - 1)
        if cursor is None:
            continue
        while cursor < count and runs[cursor][0] == row - 1:
            above_start, above_end = runs[cursor][1], runs[cursor][2]
            if above_start <= col_end and col_start <= above_end:
                union(index, cursor)
            if above_start > col_end:
                break
            cursor += 1

    roots: dict[int, int] = {}
    out = numpy.zeros((height, width), numpy.int32)
    for index, (row, col_start, col_end) in enumerate(runs):
        root = find(index)
        tag = roots.setdefault(root, len(roots) + 1)
        out[row, col_start:col_end] = tag
    return out, len(roots)


@dataclass(frozen=True)
class Blob:
    tag: int
    area: int
    cx: float
    cy: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1

    @property
    def fill(self) -> float:
        return self.area / float(max(self.width * self.height, 1))

    def bbox(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)


def blobs(labels: Any, count: int) -> list[Blob]:
    """Components sorted by area, largest first."""
    if count == 0:
        return []
    numpy = numpy_module()
    height, width = labels.shape
    rows, cols = numpy.nonzero(labels)
    tags = labels[rows, cols]
    areas = numpy.bincount(tags, minlength=count + 1)
    sum_x = numpy.bincount(tags, weights=cols, minlength=count + 1)
    sum_y = numpy.bincount(tags, weights=rows, minlength=count + 1)
    big = max(height, width) + 1
    x_min = numpy.full(count + 1, big, numpy.int64)
    y_min = numpy.full(count + 1, big, numpy.int64)
    x_max = numpy.full(count + 1, -1, numpy.int64)
    y_max = numpy.full(count + 1, -1, numpy.int64)
    numpy.minimum.at(x_min, tags, cols)
    numpy.minimum.at(y_min, tags, rows)
    numpy.maximum.at(x_max, tags, cols)
    numpy.maximum.at(y_max, tags, rows)
    found: list[Blob] = []
    for tag in range(1, count + 1):
        area = int(areas[tag])
        if area == 0:
            continue
        found.append(Blob(tag=tag, area=area, cx=float(sum_x[tag] / area),
                          cy=float(sum_y[tag] / area), x0=int(x_min[tag]),
                          y0=int(y_min[tag]), x1=int(x_max[tag]),
                          y1=int(y_max[tag])))
    found.sort(key=lambda blob: -blob.area)
    return found


def channels(frame: Any) -> tuple[Any, ...]:
    numpy = numpy_module()
    values = frame.astype(numpy.float32)
    red, green, blue = values[..., 0], values[..., 1], values[..., 2]
    luma = 0.299 * red + 0.587 * green + 0.114 * blue
    chroma_r = (red - luma) * 0.713 + 128.0
    chroma_b = (blue - luma) * 0.564 + 128.0
    return (red, green, blue, luma, chroma_r, chroma_b,
            values.max(2), values.min(2))


def skin_mask(frame: Any) -> Any:
    """Skin, with the redness term that rejects warm pavement and road paint."""
    red, green, blue, luma, chroma_r, chroma_b, high, low = channels(frame)
    return ((chroma_r >= 138) & (chroma_r <= 175) & (chroma_b >= 77)
            & (chroma_b <= 127) & (luma >= 50) & (red > 60) & (green > 30)
            & (blue > 15) & (red > green + 12) & (green > blue)
            & ((high - low) > 25)
            & ((red - green) > SKIN_RG_OVER_GB * (green - blue)))


def cloth_mask(frame: Any, skin: Any) -> Any:
    """Low-saturation non-skin that is not sky: the torso, dark or light."""
    _red, _green, _blue, luma, _cr, _cb, high, low = channels(frame)
    return (~skin) & ((high - low) < 32) & (luma < 205)


def grey(frame: Any) -> Any:
    _red, _green, _blue, luma, *_rest = channels(frame)
    return luma


def median_smooth(series: Any, window: int) -> Any:
    """Running median. Removes per-frame segmentation flicker without touching a
    real limb swing, which is slow and hundreds of pixels wide."""
    numpy = numpy_module()
    if window <= 1 or len(series) < 3:
        return series
    out = numpy.empty_like(series)
    half = window // 2
    for index in range(len(series)):
        segment = series[max(0, index - half):min(len(series), index + half + 1)]
        out[index] = numpy.median(segment, axis=0)
    return out


def runs_of(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """Index ranges [start, stop) where `flags` is true."""
    found: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            found.append((start, index))
            start = None
    if start is not None:
        found.append((start, len(flags)))
    return found


def scale_factor(analysis_width: int = ANALYSIS_WIDTH) -> float:
    return REFERENCE_WIDTH / float(analysis_width)


def contact_sheet(frames: Sequence[Path], destination: Path, *,
                  boxes: Sequence[Sequence[tuple[int, int, int, int]]] = (),
                  columns: int = 12, caption: str = "") -> Path:
    """A strip of the sampled frames with any boxes drawn on, for eyeballing.

    The automated verdict is never the whole gate: video-rules requires a human
    to look at the arm sheet and the frame strip before a take goes out.
    """
    image_module_ = image_module()
    try:
        from PIL import ImageDraw
    except ModuleNotFoundError:
        raise FactoryError("GATE_DEPS_MISSING", "pillow") from None
    if not frames:
        raise FactoryError("NO_FRAMES")
    thumbnails = [image_module_.open(path).convert("RGB") for path in frames]
    cell_width = max(1, thumbnails[0].width // 2)
    cell_height = max(1, thumbnails[0].height // 2)
    rows = -(-len(thumbnails) // columns)
    header = 26 if caption else 0
    sheet = image_module_.new("RGB", (cell_width * columns,
                                      cell_height * rows + header), (16, 16, 18))
    pen = ImageDraw.Draw(sheet)
    for index, thumbnail in enumerate(thumbnails):
        scale_x = cell_width / thumbnail.width
        scale_y = cell_height / thumbnail.height
        cell = thumbnail.resize((cell_width, cell_height))
        x = (index % columns) * cell_width
        y = header + (index // columns) * cell_height
        sheet.paste(cell, (x, y))
        for box in (boxes[index] if index < len(boxes) else ()):
            pen.rectangle([x + int(box[0] * scale_x), y + int(box[1] * scale_y),
                           x + int(box[2] * scale_x), y + int(box[3] * scale_y)],
                          outline=(255, 90, 90), width=2)
    if caption:
        pen.text((8, 8), caption[:200], fill=(230, 230, 230))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)
    return destination
