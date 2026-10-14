"""Background gate: FROZEN_BG, MORPH_BG, FRAME_JUMP.

Three failure modes a walking shot has shipped with, all invisible in a frame
strip:

* **FROZEN_BG** — the subject walks but the plate never slides. Median block
  displacement across the background band stays near zero for seconds.
* **MORPH_BG** — the plate slides, but the things in it change shape: leaves and
  branches flow rather than translate. Measured as the edge-map correlation of
  each block *after* its own displacement is compensated. Real parallax keeps
  edges correlated however fast they move; morphing does not. This is the part
  of the gate that is calibrated, and it separated the rejected take from the
  approved references by a wide margin.
* **FRAME_JUMP** — the generator joins its latency windows without a matching
  frame, so for one frame the whole plate snaps sideways. At 4 fps that event is
  one sample in six and simply invisible, so it gets its own full-frame-rate
  pass over the background band's mean absolute difference, judged against the
  clip's *own* median because the baseline is whatever this take's walk is.

Read FROZEN_BG as advisory: on the original calibration set it did not cleanly
separate the rejected take from the approved ones, and the most static-looking
reference measured as the most static. MORPH_BG and FRAME_JUMP carry the gate.

The person is excluded by the face, not by clothing colour: a shirt is white in
one take and dark in another, and "low saturation" also selects the road, which
is exactly the background that has to be kept. The largest skin component's
per-row extent is widened monotonically downward (face, neck, shoulders, torso)
into a centre pillar, and everything outside it above the band line is
background.

The scene-recurrence check from the original gate is deliberately not ported:
its own calibration notes record that the approved take retraced further than
the rejected one, so the companion diagnostic never fails a clip on its own.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import vision
from ..errors import FactoryError
from ..media import FFMPEG, frame_rate

ANALYSIS_WIDTH = 360
BLOCK = 24
SEARCH = 24
TEXTURE_MIN = 8.0
COVERAGE_MIN = 0.80
BAND_BOTTOM = 0.62
PERSON_PAD = 0.085
SKIN_DILATE = 4
MIN_BLOCKS = 6
FROZEN_FLOW = 4.0
MIN_FROZEN_S = 3.0
MORPH_NCC = 0.45
MIN_MORPH_S = 1.0
SMOOTH_WINDOW = 3
SPIKE_BAND_TOP = 0.45
SPIKE_RATIO = 4.0
SPIKE_MAX_FRAMES = 3
SPIKE_LOCAL_WINDOW = 12
SPIKE_MIN_BAND_PX = 200
SPIKE_WIDTH = 160


@dataclass
class BackgroundReport:
    violations: list[str] = field(default_factory=list)
    frozen: list[dict[str, Any]] = field(default_factory=list)
    morph: list[dict[str, Any]] = field(default_factory=list)
    jumps: list[dict[str, Any]] = field(default_factory=list)
    samples: int = 0
    flow_median: float | None = None
    ncc_median: float | None = None
    mad_median: float | None = None
    blocks_median: int = 0
    tail_excluded_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations),
                "frozen": list(self.frozen), "morph": list(self.morph),
                "jumps": list(self.jumps), "samples": self.samples,
                "flow_median_1080px": self.flow_median,
                "ncc_median": self.ncc_median, "mad_median": self.mad_median,
                "blocks_median": self.blocks_median,
                "tail_excluded_s": self.tail_excluded_s}


def sobel(grey: Any) -> Any:
    numpy = vision.numpy_module()
    gradient_x = numpy.zeros_like(grey)
    gradient_y = numpy.zeros_like(grey)
    gradient_x[:, 1:-1] = grey[:, 2:] - grey[:, :-2]
    gradient_y[1:-1, :] = grey[2:, :] - grey[:-2, :]
    return numpy.hypot(gradient_x, gradient_y)


def person_column(frame: Any, *, pad_fraction: float = PERSON_PAD,
                  skin_dilate: int = SKIN_DILATE) -> Any:
    """The centre pillar the person occupies, derived from the face."""
    numpy = vision.numpy_module()
    height, width, _depth = frame.shape
    skin = vision.skin_mask(frame)
    labels, count = vision.label(skin)
    if count == 0:
        return numpy.zeros((height, width), bool)
    found = vision.blobs(labels, count)
    mask = labels == found[0].tag
    rows = numpy.nonzero(mask.any(axis=1))[0]
    low = numpy.full(height, -1.0)
    high = numpy.full(height, -1.0)
    for row in rows:
        columns = numpy.nonzero(mask[row])[0]
        low[row] = columns[0]
        high[row] = columns[-1]
    top = int(rows[0])
    carry_low: float | None = None
    carry_high: float | None = None
    for row in range(top, height):
        if low[row] >= 0:
            carry_low = low[row] if carry_low is None else min(carry_low, low[row])
            carry_high = (high[row] if carry_high is None
                          else max(carry_high, high[row]))
        low[row] = carry_low if carry_low is not None else -1
        high[row] = carry_high if carry_high is not None else -1
    pad = int(round(pad_fraction * width))
    columns = numpy.arange(width)[None, :]
    valid = low >= 0
    left = numpy.where(valid, low - pad, 1e9)[:, None]
    right = numpy.where(valid, high + pad, -1e9)[:, None]
    pillar = (columns >= left) & (columns <= right)
    pillar[:top, :] = pillar[top, :][None, :]
    return pillar | vision.dilate(skin, skin_dilate)


def band_mask(height: int, width: int, bottom: float) -> Any:
    numpy = vision.numpy_module()
    mask = numpy.zeros((height, width), bool)
    mask[:int(height * bottom), :] = True
    mask[:3, :] = False
    mask[:, :3] = False
    mask[:, -3:] = False
    return mask


def block_sum(values: Any, rows: int, columns: int, size: int) -> Any:
    return values[:rows * size, :columns * size].reshape(
        rows, size, columns, size).sum(axis=(1, 3))


def shift(image: Any, dx: int, dy: int) -> Any:
    numpy = vision.numpy_module()
    radius = max(abs(dx), abs(dy))
    if radius == 0:
        return image
    padded = numpy.pad(image, radius, mode="edge")
    return padded[radius + dy:radius + dy + image.shape[0],
                  radius + dx:radius + dx + image.shape[1]]


def match_blocks(current: Any, previous: Any, rows: int, columns: int, *,
                 block: int, search: int) -> Any:
    """Per-block displacement by full SAD search at half resolution."""
    numpy = vision.numpy_module()
    half_current = current[::2, ::2]
    half_previous = previous[::2, ::2]
    height, width = half_current.shape
    radius = max(1, search // 2)
    half_block = max(2, block // 2)
    padded = numpy.pad(half_previous, radius, mode="edge")
    best = numpy.full((rows, columns), numpy.inf, numpy.float32)
    displacement = numpy.zeros((rows, columns, 2), numpy.int32)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            candidate = padded[radius + dy:radius + dy + height,
                               radius + dx:radius + dx + width]
            cost = block_sum(numpy.abs(half_current - candidate), rows, columns,
                             half_block)
            chosen = cost < best
            best = numpy.where(chosen, cost, best)
            displacement[chosen] = (dx * 2, dy * 2)
    return displacement


def edge_ncc(current: Any, previous: Any, displacement: Any, rows: int,
             columns: int, block: int) -> Any:
    """Edge-map correlation per block, after that block's own shift is undone."""
    numpy = vision.numpy_module()
    area = float(block * block)
    sum_current = block_sum(current, rows, columns, block)
    sum_current_sq = block_sum(current * current, rows, columns, block)
    out = numpy.zeros((rows, columns), numpy.float32)
    key = displacement[..., 0].astype(numpy.int64) * 100000 + displacement[..., 1]
    for value in numpy.unique(key):
        chosen = key == value
        dx = int(displacement[..., 0][chosen][0])
        dy = int(displacement[..., 1][chosen][0])
        shifted = shift(previous, dx, dy)
        sum_prev = block_sum(shifted, rows, columns, block)
        sum_prev_sq = block_sum(shifted * shifted, rows, columns, block)
        cross = block_sum(current * shifted, rows, columns, block)
        covariance = cross / area - (sum_current / area) * (sum_prev / area)
        variance_current = numpy.maximum(
            sum_current_sq / area - (sum_current / area) ** 2, 0.0)
        variance_prev = numpy.maximum(
            sum_prev_sq / area - (sum_prev / area) ** 2, 0.0)
        denominator = numpy.sqrt(variance_current * variance_prev)
        out[chosen] = numpy.where(denominator < 1e-6, 0.0,
                                  covariance / numpy.maximum(denominator,
                                                             1e-6))[chosen]
    return out


def quiet_windows(flow: Any, times: Any, threshold: float,
                  min_seconds: float) -> list[dict[str, Any]]:
    """Windows of at least `min_seconds` whose mean flow stays under threshold."""
    numpy = vision.numpy_module()
    hits: list[list[int]] = []
    total = len(flow)
    for start in range(total):
        for stop in range(start + 1, total + 1):
            if times[stop - 1] - times[start] < min_seconds:
                continue
            segment = flow[start:stop]
            if numpy.isnan(segment).any():
                break
            if float(numpy.mean(segment)) < threshold:
                hits.append([start, stop - 1])
            break
    merged: list[list[int]] = []
    for start, stop in hits:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    out: list[dict[str, Any]] = []
    for start, stop in merged:
        while (stop < total - 1 and not numpy.isnan(flow[stop + 1])
               and float(numpy.mean(flow[start:stop + 2])) < threshold):
            stop += 1
        segment = flow[start:stop + 1]
        out.append({"t0": round(float(times[start]), 2),
                    "t1": round(float(times[stop]), 2),
                    "sec": round(float(times[stop] - times[start]), 2),
                    "mean_flow_1080px": round(float(numpy.mean(segment)), 2)})
    return out


def runs_below(series: Any, times: Any, threshold: float,
               min_seconds: float) -> list[dict[str, Any]]:
    numpy = vision.numpy_module()
    spans: list[list[int]] = []
    start: int | None = None
    for index, value in enumerate(series):
        low = (not numpy.isnan(value)) and value < threshold
        if low and start is None:
            start = index
        elif not low and start is not None:
            spans.append([start, index - 1])
            start = None
    if start is not None:
        spans.append([start, len(series) - 1])
    out: list[dict[str, Any]] = []
    for begin, end in spans:
        if times[end] - times[begin] < min_seconds - 1e-9:
            continue
        segment = series[begin:end + 1]
        out.append({"t0": round(float(times[begin]), 2),
                    "t1": round(float(times[end]), 2),
                    "sec": round(float(times[end] - times[begin]), 2),
                    "min": round(float(numpy.nanmin(segment)), 3),
                    "mean": round(float(numpy.nanmean(segment)), 3)})
    return out


def _raw_frames(source: Path, width: int, height: int) -> Iterator[Any]:
    """Every frame, no resampling, as uint8 RGB. Streamed, never all in memory."""
    numpy = vision.numpy_module()
    process = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", str(source), "-vsync", "0",
         "-vf", f"scale={width}:{height}", "-pix_fmt", "rgb24",
         "-f", "rawvideo", "-"], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    size = width * height * 3
    try:
        while True:
            block = process.stdout.read(size) if process.stdout else b""
            if len(block) < size:
                break
            yield numpy.frombuffer(block, numpy.uint8).reshape(height, width, 3)
    finally:
        if process.stdout:
            process.stdout.close()
        process.wait()


def frame_jump_scan(source: Path, *, ratio: float = SPIKE_RATIO,
                    max_frames: int = SPIKE_MAX_FRAMES
                    ) -> tuple[list[dict[str, Any]], float | None]:
    """Single-frame plate teleports, at the clip's real frame rate."""
    numpy = vision.numpy_module()
    fps = frame_rate(Path(source)) or 30.0
    height = max(2, int(round(SPIKE_WIDTH * 16 / 9 / 2)) * 2)
    differences: list[float] = []
    previous = None
    previous_pillar = None
    band = band_mask(height, SPIKE_WIDTH, SPIKE_BAND_TOP)
    for frame in _raw_frames(Path(source), SPIKE_WIDTH, height):
        grey = vision.grey(frame)
        pillar = person_column(frame)
        if previous is not None:
            free = band & ~pillar & ~previous_pillar
            if int(free.sum()) >= SPIKE_MIN_BAND_PX:
                differences.append(float(numpy.abs(grey - previous)[free].mean()))
            else:
                differences.append(float("nan"))
        previous, previous_pillar = grey, pillar
    if len(differences) < 8:
        return [], None
    series = numpy.array(differences, dtype=numpy.float64)
    median = float(numpy.nanmedian(series))
    if not median or numpy.isnan(median):
        return [], None
    prominence = numpy.full(len(series), numpy.nan)
    for index in range(len(series)):
        low = max(0, index - SPIKE_LOCAL_WINDOW)
        high = min(len(series), index + SPIKE_LOCAL_WINDOW + 1)
        local = numpy.concatenate([series[low:index], series[index + 1:high]])
        if local.size:
            prominence[index] = series[index] / max(float(numpy.nanmedian(local)),
                                                    1e-6)
    flagged = [index for index, value in enumerate(prominence)
               if not numpy.isnan(value) and value >= ratio]
    jumps: list[dict[str, Any]] = []
    for start, stop in vision.runs_of([index in set(flagged)
                                       for index in range(len(series))]):
        if stop - start > max_frames:
            continue
        jumps.append({"frame": start + 1, "frames": stop - start,
                      "at_s": round((start + 1) / fps, 3),
                      "mad": round(float(series[start]), 3),
                      "ratio": round(float(prominence[start]), 2)})
    return jumps, round(median, 3)


def check(source: Path, *, fps: float = vision.SAMPLE_FPS,
          limits: dict[str, Any] | None = None,
          skip_frame_jump: bool = False,
          tail_s: float | None = None) -> BackgroundReport:
    """Judge the background. `tail_s` excludes a deliberately frozen end card.

    On an assembled reel the last couple of seconds are a held still, which is
    exactly what FROZEN_BG looks for. Trimming the hold before the search is the
    difference between the gate reporting a defect and reporting the design.
    """
    numpy = vision.numpy_module()
    rules = limits or {}
    frozen_flow = float(rules.get("bg_frozen_flow") or FROZEN_FLOW)
    morph_ncc = float(rules.get("bg_morph_ncc") or MORPH_NCC)
    hold = float(tail_s if tail_s is not None
                 else (rules.get("endcard_hold_s") or 0.0))
    report = BackgroundReport()
    scale = vision.REFERENCE_WIDTH / float(ANALYSIS_WIDTH)

    with vision.sampled(source, fps=fps, width=ANALYSIS_WIDTH) as frames:
        if len(frames) < 3:
            raise FactoryError("NOT_ENOUGH_FRAMES", str(len(frames)))
        greys, edges, pillars = [], [], []
        for path in frames:
            pixels = vision.load_rgb(path)
            grey = vision.grey(pixels)
            greys.append(grey)
            edges.append(sobel(grey))
            pillars.append(person_column(pixels))
    height, width = greys[0].shape
    rows, columns = height // BLOCK, width // BLOCK
    band = band_mask(height, width, float(rules.get("bg_band_bottom")
                                          or BAND_BOTTOM))
    area = float(BLOCK * BLOCK)

    times: list[float] = []
    flows: list[float] = []
    nccs: list[float] = []
    block_counts: list[int] = []
    for index in range(1, len(greys)):
        times.append(round(index / float(fps), 3))
        free = (band & ~pillars[index] & ~pillars[index - 1]).astype(numpy.float32)
        coverage = block_sum(free, rows, columns, BLOCK) / area
        total = block_sum(greys[index], rows, columns, BLOCK)
        total_sq = block_sum(greys[index] * greys[index], rows, columns, BLOCK)
        deviation = numpy.sqrt(numpy.maximum(total_sq / area
                                             - (total / area) ** 2, 0.0))
        usable = (coverage > COVERAGE_MIN) & (deviation > TEXTURE_MIN)
        block_counts.append(int(usable.sum()))
        if usable.sum() < MIN_BLOCKS:
            flows.append(float("nan"))
            nccs.append(float("nan"))
            continue
        displacement = match_blocks(greys[index], greys[index - 1], rows,
                                    columns, block=BLOCK, search=SEARCH)
        correlation = edge_ncc(edges[index], edges[index - 1], displacement,
                               rows, columns, BLOCK)
        dx = displacement[..., 0][usable].astype(numpy.float64)
        dy = displacement[..., 1][usable].astype(numpy.float64)
        flows.append(round(float(numpy.hypot(float(numpy.median(dx)),
                                             float(numpy.median(dy)))) * scale, 2))
        nccs.append(round(float(numpy.median(correlation[usable])), 4))

    if hold > 0:
        from ..media import duration as _duration

        cutoff = _duration(Path(source)) - hold
        keep = [index for index, moment in enumerate(times) if moment <= cutoff]
        times = [times[index] for index in keep]
        flows = [flows[index] for index in keep]
        nccs = [nccs[index] for index in keep]
        block_counts = [block_counts[index] for index in keep]
        report.tail_excluded_s = round(hold, 2)
    if len(times) < 2:
        raise FactoryError("NOT_ENOUGH_SAMPLES", str(len(times)))
    time_series = numpy.array(times)
    flow_series = numpy.array(flows)
    ncc_series = vision.median_smooth(numpy.array(nccs), SMOOTH_WINDOW)
    report.samples = len(times)
    report.blocks_median = int(numpy.median(block_counts)) if block_counts else 0
    if not numpy.isnan(flow_series).all():
        report.flow_median = round(float(numpy.nanmedian(flow_series)), 2)
    if not numpy.isnan(ncc_series).all():
        report.ncc_median = round(float(numpy.nanmedian(ncc_series)), 4)

    report.frozen = quiet_windows(flow_series, time_series, frozen_flow,
                                  float(rules.get("bg_min_frozen_s")
                                        or MIN_FROZEN_S))
    report.morph = runs_below(ncc_series, time_series, morph_ncc,
                              float(rules.get("bg_min_morph_s") or MIN_MORPH_S))
    if not skip_frame_jump:
        report.jumps, report.mad_median = frame_jump_scan(Path(source))

    if report.frozen:
        report.violations.append(
            f"FROZEN_BG:{report.frozen[0]['sec']:g}s@"
            f"{report.frozen[0]['t0']:g}s")
    if report.morph:
        report.violations.append(
            f"MORPH_BG:{report.morph[0]['sec']:g}s:ncc="
            f"{report.morph[0]['min']:g}")
    if report.jumps:
        report.violations.append(
            f"FRAME_JUMP:{len(report.jumps)}@{report.jumps[0]['at_s']:g}s")
    return report
