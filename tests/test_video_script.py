"""Slot filling and the golden-prompt check.

The check is the one that pays for itself: an edited sentence in a proven
prompt costs a whole take to discover, so these tests pin down exactly what
counts as an edit and what counts as a slot.
"""
from __future__ import annotations

import pytest

from factory.errors import FactoryError
from factory.generators.video import script, values

TEMPLATE = (
    "The START IMAGE is the first frame for {{host_identity}}.\n"
    "[Generation Goal] Generate a {{duration_s}}-second vertical video, "
    "one continuous take, no cuts.\n"
    "Location: {{location}}\n"
    "[Audio] Dialogue language: {{content_lang}}, about {{wpm}} words per minute.\n"
    "[Stage 2 | 0.3-3s] The host says: {{hook_line}}\n"
    "[Stage 3 | 3-{{midpoint_s}}s] Continue: {{body_lines}}\n"
    "[Stage 5 | {{end_s}}-{{duration_s}}s] End card only: {{endcard}}\n"
)

ANSWERS = {
    "video.th.location": "a quiet street",
    "video.th.hook_line": "You are not tired.",
    "video.th.body_lines": "Do the one small thing.",
    "video.th.endcard": "THE END",
    "video.th.identity": "a warm older sister",
}
LIMITS = {"wpm": [150, 185], "endcard_min_s": 2.5}


def resolved() -> dict[str, object]:
    return values.resolve(template=TEMPLATE, answers=ANSWERS, profile_name="p",
                          content_lang="en", duration_s=24.0, aspect="9:16",
                          limits=LIMITS)


def test_slots_fill_from_answers_profile_and_clock() -> None:
    found = resolved()
    assert found["location"] == "a quiet street"
    # host_identity has no question of its own and falls back to the identity answer
    assert found["host_identity"] == "a warm older sister"
    # A whole number of seconds stays whole, so the prompt reads "24-second".
    assert found["duration_s"] == 24
    assert found["end_s"] == 21.5
    assert found["wpm"] == 168
    assert 3.0 < float(found["midpoint_s"]) < 21.5


def test_a_missing_slot_names_itself() -> None:
    with pytest.raises(FactoryError) as error:
        script.render(TEMPLATE, {"location": "x"})
    assert error.value.code == "SLOT_MISSING"
    assert "host_identity" in error.value.detail


def test_changing_a_slot_value_is_not_an_edit() -> None:
    first = resolved()
    second = dict(first, location="a rooftop at dusk")
    rendered = script.render(TEMPLATE, second)
    assert script.off_template(rendered, TEMPLATE, second) == []


def test_a_short_slot_value_is_masked_on_both_sides() -> None:
    """content_lang is two characters. Masking it on one side only used to read
    as an edited sentence, which failed every prompt in the catalogue."""
    found = resolved()
    assert script.off_template(script.render(TEMPLATE, found), TEMPLATE, found) == []


def test_an_added_sentence_fails() -> None:
    found = resolved()
    rendered = script.render(TEMPLATE, found).replace(
        "no cuts.", "no cuts. Make it cinematic.")
    violations = script.off_template(rendered, TEMPLATE, found)
    assert violations and violations[0].startswith("OFF_TEMPLATE")


def test_a_reworded_sentence_fails() -> None:
    found = resolved()
    rendered = script.render(TEMPLATE, found).replace(
        "one continuous take, no cuts.", "one take without cuts.")
    assert any(entry.startswith("OFF_TEMPLATE")
               for entry in script.off_template(rendered, TEMPLATE, found))


def test_script_sizing_judges_against_the_wpm_band() -> None:
    found = resolved()
    report = script.check(template=TEMPLATE,
                          prompt=script.render(TEMPLATE, found), values=found,
                          duration_s=24.0, limits=LIMITS,
                          spoken_keys=("hook_line", "body_lines"))
    assert report.spoken_words == 9
    assert report.speaking_s == 21.5
    assert "SCRIPT_TOO_SHORT" in ",".join(report.violations)

    long_values = dict(found, body_lines=" ".join(["word"] * 120))
    long_report = script.check(template=TEMPLATE,
                               prompt=script.render(TEMPLATE, long_values),
                               values=long_values, duration_s=24.0, limits=LIMITS,
                               spoken_keys=("hook_line", "body_lines"))
    assert "SCRIPT_TOO_LONG" in ",".join(long_report.violations)


def test_brand_lines_become_slot_values() -> None:
    brand = "# Brand\n\n- End card: SAJU UNNI\n- Ambience: quiet room tone\n"
    found = values.from_brand(brand)
    assert found["end_card"] == "SAJU UNNI"
    assert found["endcard"] == "SAJU UNNI"
    assert found["ambience"] == "quiet room tone"


def test_takes_are_rebased_to_their_own_window() -> None:
    base = resolved()
    lines = ["one two three", "four five six", "seven eight nine"]
    per_take = values.for_takes(base, template=TEMPLATE, lengths=[12, 12],
                                lines=lines, limits=LIMITS)
    assert [entry["duration_s"] for entry in per_take] == [12, 12]
    # Only the last take reserves the end card hold.
    assert per_take[0]["end_s"] == 12.0
    assert per_take[1]["end_s"] == 9.5
    # Every line is dealt out exactly once, and none is split.
    spoken = " ".join(f"{entry['hook_line']} {entry['body_lines']}"
                      for entry in per_take)
    for line in lines:
        assert line in spoken
