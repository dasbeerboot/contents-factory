"""Budget cap, take splitting and caption layout.

These are the three places where a mistake is expensive rather than merely
wrong: a cap that does not hold spends real credits, a take split that does not
add up produces a reel of the wrong length, and a caption laid out past the safe
zone is only discovered after the render.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory import ledger
from factory.errors import FactoryError
from factory.gates import caption_safe_zone
from factory.generators import budget
from factory.generators.video import clip


def test_the_cap_is_the_tighter_of_format_and_brief(tmp_path: Path) -> None:
    assert budget.for_brief(tmp_path, ref="b_1", format_cap=150,
                            brief_cap=90).cap == 90
    assert budget.for_brief(tmp_path, ref="b_1", format_cap=150,
                            brief_cap=0).cap == 150
    assert not budget.for_brief(tmp_path, ref="b_1", format_cap=0,
                                brief_cap=0).capped


def test_spend_is_read_back_from_the_ledger(tmp_path: Path) -> None:
    """Stages run as separate processes, so the running total cannot be held in
    memory. It has to survive a restart."""
    allowance = budget.for_brief(tmp_path, ref="b_1", format_cap=150)
    assert allowance.spent() == 0.0
    ledger.record(tmp_path, track="video", kind="talking_head", credits=54,
                  ref="b_1")
    ledger.record(tmp_path, track="video", kind="talking_head", credits=54,
                  ref="b_1")
    ledger.record(tmp_path, track="video", kind="talking_head", credits=99,
                  ref="b_other")
    ledger.record(tmp_path, track="image", kind="og", credits=99, ref="b_1")

    fresh = budget.for_brief(tmp_path, ref="b_1", format_cap=150)
    assert fresh.spent() == 108.0
    assert fresh.remaining() == 42.0
    assert fresh.check(40, stage="tts") == 2.0
    with pytest.raises(FactoryError) as error:
        fresh.check(54, stage="take3")
    assert error.value.code == "BUDGET_EXCEEDED"
    assert "left=42" in error.value.detail


def test_takes_sum_to_the_requested_length() -> None:
    assert clip.split_duration(12) == [12]
    assert clip.split_duration(24) == [12, 12]
    assert clip.split_duration(25) == [13, 12]
    assert clip.split_duration(28) == [14, 14]
    for wanted in range(4, 61):
        lengths = clip.split_duration(wanted)
        assert sum(lengths) == wanted
        assert max(lengths) <= clip.MAX_TAKE_S
        assert min(lengths) >= clip.MIN_TAKE_S


def test_a_reel_too_short_for_one_take_is_refused() -> None:
    with pytest.raises(FactoryError) as error:
        clip.split_duration(2)
    assert error.value.code == "DURATION_TOO_SHORT"


def test_each_take_can_carry_its_own_prompt(tmp_path: Path) -> None:
    still = tmp_path / "still.png"
    still.write_bytes(b"0")
    plan = clip.plan(prompt="base", duration_s=24, still=still,
                     prompts=["take one text", "take two text"])
    assert [take.prompt for take in plan.takes] == ["take one text",
                                                    "take two text"]
    with pytest.raises(FactoryError) as error:
        clip.plan(prompt="base", duration_s=24, still=still, prompts=["only one"])
    assert error.value.code == "TAKE_PROMPT_COUNT"


def test_a_margin_format_gets_an_inset_zone_not_the_video_band() -> None:
    """A landscape card declares a uniform margin. Judging it against the
    vertical-video caption band fails every headline that is doing its job."""
    card = {"width_px": 1200, "height_px": 630, "safe_margin_px": 80}
    zone = caption_safe_zone.zone_from_limits(card)
    assert (zone.x_min, zone.x_max, zone.y_min, zone.y_max) == (80, 1120, 80, 550)
    assert zone.native is True

    inside = caption_safe_zone.check([{"x": 200, "y": 150, "width": 800,
                                       "height": 200, "label": "headline"}],
                                     limits=card)
    assert inside.ok
    over = caption_safe_zone.check([{"x": 270, "y": 208, "width": 881,
                                     "height": 154, "label": "headline"}],
                                   limits=card)
    assert not over.ok
    assert "headline:RIGHT_OUT:1151>1120" in over.violations


def test_the_video_band_is_still_used_when_declared() -> None:
    reel = {"width_px": 1080, "height_px": 1920,
            "safe_zone_px": {"x_min": 200, "x_max": 880, "y_min": 290,
                             "y_max": 1430}}
    zone = caption_safe_zone.zone_from_limits(reel)
    assert (zone.x_min, zone.y_max) == (200, 1430)
    # A declared band beats the margin inset, and it is native: a format writes those
    # numbers in its own frame's pixels, so they are never rescaled. On a 9:16 format
    # the scale is 1:1 either way; it only shows up on another aspect.
    assert zone.native is True
    assert caption_safe_zone.check([{"x": 200, "y": 290, "width": 640, "height": 100}],
                                   limits=reel).zone["y_max"] == 1430


def test_a_declared_band_on_a_non_vertical_frame_is_not_rescaled() -> None:
    card = {"width_px": 1080, "height_px": 1350,
            "safe_zone_px": {"x_min": 96, "x_max": 984, "y_min": 96, "y_max": 1254},
            "max_text_width_px": 888}
    report = caption_safe_zone.check([{"x": 96, "y": 472, "width": 851, "height": 345,
                                       "label": "headline"}], limits=card)
    assert report.ok, report.violations
    assert report.zone["y_max"] == 1254
