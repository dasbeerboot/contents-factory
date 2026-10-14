"""Frame-analysis primitives and the pace gate's arithmetic.

The gates themselves need real footage, which tests cannot carry. What is
testable without it is the geometry underneath them — components, the neck cut
that stops a sweater from lengthening a face, and how speaking time is counted —
and that is where the bugs with consequences have been.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.gates import identity, stt_wpm, vision

numpy = pytest.importorskip("numpy")
pytest.importorskip("PIL")


def test_components_are_found_and_sorted_by_size() -> None:
    mask = numpy.zeros((20, 20), bool)
    mask[2:8, 2:8] = True          # 36 px
    mask[12:15, 12:14] = True      # 6 px
    labels, count = vision.label(mask)
    assert count == 2
    found = vision.blobs(labels, count)
    assert [blob.area for blob in found] == [36, 6]
    assert found[0].bbox() == (2, 2, 7, 7)
    assert found[0].fill == 1.0


def test_touching_corners_count_as_one_component() -> None:
    mask = numpy.zeros((10, 10), bool)
    mask[2:4, 2:4] = True
    mask[4:6, 4:6] = True          # diagonally adjacent
    _labels, count = vision.label(mask)
    assert count == 1


def test_an_empty_mask_has_no_components() -> None:
    labels, count = vision.label(numpy.zeros((8, 8), bool))
    assert count == 0
    assert vision.blobs(labels, count) == []


def test_skin_detection_rejects_warm_pavement() -> None:
    """A plain chroma box also selects warm asphalt and yellow road paint, which
    then track as a perfectly steady arm. The redness term is what stops it."""
    skin = numpy.full((4, 4, 3), (205, 150, 125), numpy.uint8)
    paint = numpy.full((4, 4, 3), (200, 180, 60), numpy.uint8)
    assert vision.skin_mask(skin).all()
    assert not vision.skin_mask(paint).any()


def test_runs_of_finds_contiguous_spans() -> None:
    assert vision.runs_of([False, True, True, False, True]) == [(1, 3), (4, 5)]
    assert vision.runs_of([True, True]) == [(0, 2)]
    assert vision.runs_of([]) == []


def test_the_neck_cut_stops_a_skin_toned_top_lengthening_the_face() -> None:
    """Face, narrow neck, then shoulders. The face must end at the neck, not at
    the bottom of whatever the skin mask joined onto it."""
    widths = numpy.array([40, 60, 70, 72, 70, 60,   # head
                          30, 28, 30,               # neck
                          90, 110, 120])            # shoulders
    assert identity.chin_row(widths, 0, 11) == 6


def test_a_face_with_no_neck_dip_keeps_its_own_extent() -> None:
    widths = numpy.array([40, 60, 70, 72, 70, 66, 62])
    assert identity.chin_row(widths, 100, 106) == 106


def test_speaking_time_merges_words_but_not_long_gaps() -> None:
    words = [{"start": 0.0, "end": 0.5}, {"start": 0.6, "end": 1.0},
             {"start": 5.0, "end": 5.4}]
    # The first two merge across a 0.1s gap; the 4s silence does not count.
    assert stt_wpm.speaking_seconds(words, pad=0.0, merge_gap=0.35) == 1.4


def test_a_transcript_splits_into_caption_lines_at_pauses(tmp_path: Path) -> None:
    transcript = tmp_path / "stt.json"
    transcript.write_text(json.dumps({"words": [
        {"text": "one", "start": 0.0, "end": 0.3, "type": "word"},
        {"text": "two", "start": 0.35, "end": 0.6, "type": "word"},
        {"text": "three", "start": 1.5, "end": 1.8, "type": "word"},
    ]}), encoding="utf-8")
    lines = stt_wpm.caption_lines(transcript)
    assert [line["text"] for line in lines] == ["one two", "three"]
    assert lines[1]["offset_s"] == 1.5


def test_an_empty_transcript_is_an_error_not_an_empty_pass(tmp_path: Path) -> None:
    transcript = tmp_path / "stt.json"
    transcript.write_text(json.dumps({"words": []}), encoding="utf-8")
    with pytest.raises(FactoryError) as error:
        stt_wpm.caption_lines(transcript)
    assert error.value.code == "STT_TRANSCRIPT_EMPTY"
