"""Stage 5: word-synced captions, laid out inside the safe zone by construction.

The safe zone is not a review note, it is the layout constraint: x 200..880,
y 290..1430 on a 1080x1920 frame, caption text no wider than 640 px. A line that
does not fit is re-wrapped, then shrunk down a font ladder, then split into
consecutive cards — never pushed past the edge and flagged afterwards. The
`caption_safe_zone` gate then re-measures the boxes this stage reports, so the
check runs on the drawn geometry rather than on hand-typed coordinates.

Highlighting is per word, driven by the timestamps from the TTS stage or by the
transcript from the STT gate. Cards are at most two rows, because a third row
reads as a paragraph on a phone.

Rendering needs Pillow for text metrics. It is an optional dependency: without
it this stage fails with `GATE_DEPS_MISSING` instead of guessing at text widths
and producing captions that overflow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ...errors import FactoryError

FRAME = (1080, 1920)
SAFE = {"x_min": 200, "x_max": 880, "y_min": 290, "y_max": 1430}
MAX_TEXT_WIDTH = 640
FONT_LADDER = (72, 64, 56)
LINE_RATIO = 1.30
CENTER_FRACTION = 0.72
MAX_ROWS = 2
FADE_S = 0.25
LEAD_IN_S = 0.20
TAIL_S = 0.40
FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 4),
    ("/System/Library/Fonts/Supplemental/Georgia.ttf", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
)
SHADOW_BLUR = 12
SHADOW_OFFSET = 6
SCRIM_ALPHA = 77
DIM = 190


def _pillow() -> Any:
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ModuleNotFoundError:
        raise FactoryError("GATE_DEPS_MISSING", "pillow") from None
    return Image, ImageDraw, ImageFilter, ImageFont


@dataclass(frozen=True)
class Typeface:
    path: str
    index: int = 0

    @classmethod
    def discover(cls, override: str = "") -> "Typeface":
        """First readable face. A profile may name its own in `brand.md` terms."""
        if override and Path(override).is_file():
            return cls(path=override)
        for path, index in FONT_CANDIDATES:
            if Path(path).is_file():
                return cls(path=path, index=index)
        raise FactoryError("CAPTION_FONT_MISSING")


class Metrics:
    """Text widths for one typeface, memoised per size."""

    def __init__(self, face: Typeface) -> None:
        _image, _draw, _filter, font_module = _pillow()
        self._module = font_module
        self._face = face
        self._cache: dict[int, Any] = {}

    def font(self, size: int) -> Any:
        found = self._cache.get(size)
        if found is None:
            found = self._module.truetype(self._face.path, size,
                                         index=self._face.index)
            self._cache[size] = found
        return found

    def width(self, text: str, size: int) -> int:
        box = self.font(size).getbbox(text)
        return int(box[2] - box[0])


@dataclass
class Card:
    start: float
    end: float
    rows: list[list[str]] = field(default_factory=list)
    size: int = FONT_LADDER[0]
    word_times: list[tuple[float, float]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(" ".join(row) for row in self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "rows": [" ".join(row) for row in self.rows], "size": self.size,
                "word_times": [list(span) for span in self.word_times],
                "text": self.text}


def wrap(text: str, size: int, metrics: Metrics,
         max_width: int = MAX_TEXT_WIDTH) -> list[str]:
    rows: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if metrics.width(candidate, size) <= max_width:
            current = candidate
            continue
        if current:
            rows.append(current)
        current = word
    if current:
        rows.append(current)
    if len(rows) == 2:
        balanced = _balance(text, size, metrics, max_width)
        if balanced:
            rows = balanced
    return rows


def _balance(text: str, size: int, metrics: Metrics,
             max_width: int) -> list[str] | None:
    """Prefer an even two-row split, and prefer breaking after punctuation."""
    words = text.split()
    best: tuple[int, list[str]] | None = None
    for cut in range(1, len(words)):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        width_head, width_tail = metrics.width(head, size), metrics.width(tail, size)
        if width_head > max_width or width_tail > max_width:
            continue
        score = abs(width_head - width_tail)
        if head.rstrip().endswith((",", ".", ";", "?", "!")):
            score -= 60
        if best is None or score < best[0]:
            best = (score, [head, tail])
    return best[1] if best else None


def fit(text: str, metrics: Metrics, *, max_width: int = MAX_TEXT_WIDTH,
        ladder: Sequence[int] = FONT_LADDER,
        max_rows: int = MAX_ROWS) -> tuple[int, list[str], bool]:
    """Largest ladder size whose wrap fits. Third value is whether it fits at all."""
    last: tuple[int, list[str]] = (ladder[-1], [text])
    for size in ladder:
        rows = wrap(text, size, metrics, max_width)
        last = (size, rows)
        if len(rows) <= max_rows and all(
                metrics.width(row, size) <= max_width for row in rows):
            return size, rows, True
    return last[0], last[1], False


def split_long(words: Sequence[str], metrics: Metrics, *,
               max_width: int = MAX_TEXT_WIDTH,
               max_rows: int = MAX_ROWS) -> list[list[str]]:
    """Fewest consecutive chunks that each fit. Greedy, longest chunk first."""
    chunks: list[list[str]] = []
    remaining = list(words)
    while remaining:
        taken = len(remaining)
        while taken > 1:
            _size, _rows, ok = fit(" ".join(remaining[:taken]), metrics,
                                   max_width=max_width, max_rows=max_rows)
            if ok:
                break
            taken -= 1
        chunks.append(remaining[:taken])
        remaining = remaining[taken:]
    return chunks


def build_cards(lines: Sequence[dict[str, Any]], metrics: Metrics, *,
                max_width: int = MAX_TEXT_WIDTH,
                max_rows: int = MAX_ROWS) -> list[Card]:
    """One or more cards per narration line, timed off that line's word spans."""
    cards: list[Card] = []
    for line in lines:
        words = str(line.get("text") or "").split()
        spans = [tuple(span) for span in (line.get("word_times") or [])]
        if not words or not spans:
            continue
        if len(spans) != len(words):
            spans = _rescale(spans, len(words))
        for chunk_words, chunk_spans in zip(
                *_zip_chunks(split_long(words, metrics, max_width=max_width,
                                        max_rows=max_rows), spans)):
            size, rows, _ok = fit(" ".join(chunk_words), metrics,
                                  max_width=max_width, max_rows=max_rows)
            start = max(0.0, chunk_spans[0][0] - LEAD_IN_S)
            cards.append(Card(start=round(start, 3),
                              end=round(chunk_spans[-1][1] + TAIL_S, 3),
                              rows=[row.split() for row in rows], size=size,
                              word_times=list(chunk_spans)))
    for index in range(len(cards) - 1):
        if cards[index].end > cards[index + 1].start:
            cards[index].end = cards[index + 1].start
    return cards


def _zip_chunks(chunks: Sequence[Sequence[str]],
                spans: Sequence[tuple[float, float]]
                ) -> tuple[list[Sequence[str]], list[list[tuple[float, float]]]]:
    out_words: list[Sequence[str]] = []
    out_spans: list[list[tuple[float, float]]] = []
    cursor = 0
    for chunk in chunks:
        taken = spans[cursor:cursor + len(chunk)]
        if not taken:
            break
        out_words.append(chunk)
        out_spans.append(list(taken))
        cursor += len(chunk)
    return out_words, out_spans


def _rescale(spans: Sequence[tuple[float, float]],
             count: int) -> list[tuple[float, float]]:
    """Even spans across the line when the word counts disagree with the aligner."""
    if not spans:
        return []
    start, end = float(spans[0][0]), float(spans[-1][1])
    step = (end - start) / max(count, 1)
    return [(round(start + step * index, 3), round(start + step * (index + 1), 3))
            for index in range(count)]


def block_top(card: Card, *, frame: tuple[int, int] = FRAME,
              center_fraction: float = CENTER_FRACTION,
              zone: dict[str, int] | None = None) -> int:
    """Where this card's text block starts, clamped into the safe zone.

    The band sits low in frame, but a two-row card at the largest size is nearly
    190 px tall and a fixed centre line pushes it past the bottom of the zone.
    Clamping here is what makes the layout safe by construction instead of safe
    on average: the renderer and the box report both call this, so what the gate
    measures is what was drawn.
    """
    safe = zone or SAFE
    _width, height = frame
    block = len(card.rows) * int(round(card.size * LINE_RATIO))
    top = int(height * center_fraction) - block // 2
    top = min(top, int(safe["y_max"]) - block)
    return max(top, int(safe["y_min"]))


def card_boxes(cards: Sequence[Card], metrics: Metrics, *,
               frame: tuple[int, int] = FRAME,
               center_fraction: float = CENTER_FRACTION,
               zone: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The drawn geometry, in the shape `caption_safe_zone` expects."""
    boxes: list[dict[str, Any]] = []
    width, _height = frame
    for index, card in enumerate(cards):
        block = len(card.rows) * int(round(card.size * LINE_RATIO))
        widest = max((metrics.width(" ".join(row), card.size)
                      for row in card.rows), default=0)
        boxes.append({"label": f"card{index}", "x": width // 2 - widest // 2,
                      "y": block_top(card, frame=frame,
                                     center_fraction=center_fraction, zone=zone),
                      "width": widest, "height": block})
    return boxes


def render_card(card: Card, metrics: Metrics, *, active: int | None,
                frame: tuple[int, int] = FRAME,
                center_fraction: float = CENTER_FRACTION,
                zone: dict[str, int] | None = None) -> Any:
    """One RGBA overlay: dark scrim band, drop shadow, dimmed words, lit word."""
    image_module, draw_module, filter_module, _font = _pillow()
    width, height = frame
    font = metrics.font(card.size)
    line_height = int(round(card.size * LINE_RATIO))
    top = block_top(card, frame=frame, center_fraction=center_fraction, zone=zone)
    layer = image_module.new("RGBA", frame, (0, 0, 0, 0))
    shadow = image_module.new("RGBA", frame, (0, 0, 0, 0))
    scrim = image_module.new("RGBA", frame, (0, 0, 0, 0))
    pen, pen_shadow, pen_scrim = (draw_module.Draw(layer),
                                  draw_module.Draw(shadow),
                                  draw_module.Draw(scrim))
    band_top = top - int(card.size * 0.9)
    band_bottom = top + len(card.rows) * line_height + int(card.size * 0.55)
    feather = int(card.size * 1.6)
    middle = (band_top + band_bottom) / 2.0
    half = (band_bottom - band_top) / 2.0
    for y in range(max(0, band_top - feather), min(height, band_bottom + feather)):
        distance = abs(y - middle)
        alpha = (SCRIM_ALPHA if distance <= half
                 else SCRIM_ALPHA * max(0.0, 1.0 - (distance - half) / feather))
        if alpha > 0:
            pen_scrim.line([(0, y), (width, y)], fill=(0, 0, 0, int(alpha)))
    scrim = scrim.filter(filter_module.GaussianBlur(8))
    cursor = 0
    for row_index, row in enumerate(card.rows):
        row_width = metrics.width(" ".join(row), card.size)
        x_start = width // 2 - row_width // 2
        y = top + row_index * line_height
        for word_index, word in enumerate(row):
            offset = (metrics.width(" ".join(row[:word_index]) + " ", card.size)
                      if word_index else 0)
            x = x_start + offset
            pen_shadow.text((x, y + SHADOW_OFFSET), word, font=font,
                            fill=(0, 0, 0, 255))
            lit = active is not None and cursor == active
            pen.text((x, y), word, font=font,
                     fill=(255, 255, 255, 255 if lit else DIM))
            cursor += 1
    shadow = shadow.filter(filter_module.GaussianBlur(SHADOW_BLUR))
    return image_module.alpha_composite(
        image_module.alpha_composite(scrim, shadow), layer)


def render_track(cards: Sequence[Card], directory: Path, *, total_s: float,
                 fps: int, metrics: Metrics, frame: tuple[int, int] = FRAME,
                 center_fraction: float = CENTER_FRACTION,
                 zone: dict[str, int] | None = None) -> dict[str, Any]:
    """A png per output frame. Identical frames are hard-linked, not re-drawn."""
    image_module, _draw, _filter, _font = _pillow()
    frames_dir = directory / "frames"
    cache_dir = directory / "img"
    for target in (frames_dir, cache_dir):
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.iterdir():
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
    blank = cache_dir / "blank.png"
    image_module.new("RGBA", frame, (0, 0, 0, 0)).save(blank)
    cache: dict[tuple[int, int, int], Path] = {}

    def overlay(card_index: int, active: int, step: int) -> Path:
        key = (card_index, active, step)
        found = cache.get(key)
        if found is not None:
            return found
        path = cache_dir / f"c{card_index}_w{active}_s{step}.png"
        image = render_card(cards[card_index], metrics, active=active,
                            frame=frame, center_fraction=center_fraction,
                            zone=zone)
        if step < 10:
            alpha = image.split()[3].point(lambda value, s=step: int(value * s / 10.0))
            image = image.copy()
            image.putalpha(alpha)
        image.save(path)
        cache[key] = path
        return path

    count = int(round(total_s * fps))
    covered = 0
    for number in range(count):
        moment = number / fps
        source = blank
        for card_index, card in enumerate(cards):
            if card.start <= moment < card.end:
                active = -1
                for word_index, (begin, _end) in enumerate(card.word_times):
                    if moment >= begin:
                        active = word_index
                    else:
                        break
                step = (10 if moment >= card.start + FADE_S
                        else max(0, int(((moment - card.start) / FADE_S) * 10)))
                source = overlay(card_index, active, step)
                covered += 1
                break
        (frames_dir / f"{number:05d}.png").hardlink_to(source)
    spoken = sum(card.end - card.start for card in cards)
    return {"frames": count, "frames_dir": str(frames_dir),
            "covered_frames": covered,
            "coverage_pct": round(covered / max(count, 1) * 100, 1),
            "cards": len(cards), "caption_s": round(spoken, 2),
            "unique_overlays": len(cache)}


def save_cards(path: Path, cards: Sequence[Card],
               boxes: Sequence[dict[str, Any]],
               track: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cards": [card.as_dict() for card in cards],
                                "boxes": list(boxes), "track": track,
                                "safe_zone": dict(SAFE)},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_cards(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FactoryError("CARDS_MISSING", Path(path).name[:40]) from None
    if not isinstance(parsed, dict):
        raise FactoryError("CARDS_INVALID", Path(path).name[:40])
    return parsed
