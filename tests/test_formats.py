"""The shipped catalogue is canonical: the loader must read all 15 files as written."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.gates import limits
from factory.spec.format import (FORMATS_ENV, FormatSpec, catalogue, formats_root,
                                 load_all, load_format, parse_format)

EXPECTED = {
    "text": ["blog_long", "cardnews_copy", "free", "ig_caption", "threads_post"],
    "image": ["cardnews", "free", "og", "product_shot", "thumbnail"],
    "video": ["explainer", "free", "pov", "talking_head", "textcard"],
}
FREE_FORMATS = {"text/free", "image/free", "video/free"}


@pytest.fixture()
def shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the loader back at factory/formats instead of the test fixture tree."""
    monkeypatch.delenv(FORMATS_ENV, raising=False)


def test_catalogue_has_fifteen(shipped: None) -> None:
    found = catalogue()
    assert {kind: sorted(names) for kind, names in found.items()} == EXPECTED
    assert sum(len(names) for names in found.values()) == 15


def test_every_shipped_format_parses(shipped: None) -> None:
    specs = load_all()
    assert len(specs) == 15
    for key, spec in specs.items():
        kind, name = key.split("/")
        assert spec.kind == kind
        assert spec.name == name
        assert spec.questions, f"{key} has no questions"
        for question in spec.questions:
            assert question.key
            assert question.question_ko
            assert question.question_en


def test_free_formats_omit_name_kind_and_template(shipped: None) -> None:
    for key in sorted(FREE_FORMATS):
        kind, name = key.split("/")
        raw = (formats_root() / kind / f"{name}.yaml").read_text(encoding="utf-8")
        assert "name:" not in raw and "kind:" not in raw
        spec = load_format(kind, name)
        assert spec.is_free is True
        assert spec.prompt_template == ""
        assert spec.gates == []


def test_non_free_formats_carry_a_template(shipped: None) -> None:
    for key, spec in load_all().items():
        if key in FREE_FORMATS:
            continue
        assert spec.prompt_template.strip(), f"{key} has no prompt_template"
        assert spec.gates, f"{key} has no gates"
        assert spec.platforms, f"{key} has no platforms"


def test_video_formats_declare_duration_and_cap(shipped: None) -> None:
    for name in ("explainer", "pov", "talking_head", "textcard"):
        spec = load_format("video", name)
        assert spec.duration_s and len(spec.duration_s) == 2
        assert spec.duration_s[0] <= spec.duration_s[1]
        assert spec.credit_cap_per_item > 0
        assert spec.aspect == "9:16"


def test_safe_zone_matches_the_gate_constants(shipped: None) -> None:
    from factory.gates import caption_safe_zone
    zone = load_format("video", "talking_head").safe_zone()
    assert zone == {"x_min": caption_safe_zone.SAFE_X[0],
                    "x_max": caption_safe_zone.SAFE_X[1],
                    "y_min": caption_safe_zone.SAFE_Y[0],
                    "y_max": caption_safe_zone.SAFE_Y[1]}
    assert load_format("video", "talking_head").frame() == (
        caption_safe_zone.FRAME_WIDTH, caption_safe_zone.FRAME_HEIGHT)


def test_thumbnail_zone_overrides_the_default(shipped: None) -> None:
    assert load_format("image", "thumbnail").safe_zone() == {
        "x_min": 180, "x_max": 900, "y_min": 300, "y_max": 1500}


def test_interview_wording_prefers_yaml_over_key(shipped: None) -> None:
    spec = load_format("text", "threads_post")
    korean = spec.interview("ko")
    english = spec.interview("en")
    assert korean[0]["key"] == english[0]["key"]
    assert korean[0]["question"] != english[0]["question"]
    assert korean[0]["question"].endswith("?")
    assert not korean[0]["question"].startswith("text.")


def test_interview_falls_back_to_key_without_wording() -> None:
    spec = parse_format({"questions": [{"key": "text.x.y"}]}, kind="text", name="free")
    assert spec.interview("ko") == [{"key": "text.x.y", "question": "text.x.y",
                                     "required": "true"}]


def test_path_mismatch_is_rejected() -> None:
    with pytest.raises(FactoryError) as error:
        parse_format({"name": "other", "kind": "text"}, kind="text", name="threads_post")
    assert error.value.code == "FORMAT_PATH_MISMATCH"


def test_bad_duration_is_rejected() -> None:
    with pytest.raises(FactoryError) as error:
        parse_format({"duration_s": [30, 10]}, kind="video", name="free")
    assert error.value.code == "FORMAT_INVALID"


def test_unknown_platform_is_rejected() -> None:
    with pytest.raises(FactoryError) as error:
        parse_format({"platforms": ["mastodon"]}, kind="text", name="free")
    assert error.value.code == "FORMAT_INVALID"


def test_shipped_limits_feed_the_limits_gate(shipped: None) -> None:
    rules = load_format("text", "threads_post").limits
    assert rules["max_chars"] == 500
    assert rules["hashtags_max"] == 3
    assert rules["emoji_max"] == 1
    assert rules["external_links"] is False
    resolved = limits.resolve(rules)
    assert resolved["max_hashtags"] == 3
    assert resolved["max_emoji"] == 1
    assert resolved["max_links"] == 0
    assert resolved["max_first_line_words"] == 7


def test_yaml_spelling_and_legacy_spelling_agree() -> None:
    body = "one two three #a #b 😀 http://x.test"
    modern = limits.check(body, {"hashtags_max": 1, "emoji_max": 0,
                                 "external_links": False})
    legacy = limits.check(body, {"max_hashtags": 1, "max_emoji": 0, "max_links": 0})
    assert modern.violations == legacy.violations
    assert {entry.split(":")[0] for entry in modern.violations} == {
        "HASHTAGS_OVER", "EMOJI_OVER", "LINKS_OVER"}


def test_external_links_true_means_unlimited() -> None:
    assert limits.check("http://a.test http://b.test",
                        {"external_links": True}).ok is True


def test_ig_caption_target_is_advisory_not_a_violation(shipped: None) -> None:
    rules = load_format("text", "ig_caption").limits
    report = limits.check("short caption", rules)
    assert report.ok is True
    assert report.advisories == ["CHARS_OFF_TARGET:13~[120,900]"]


def test_blog_long_structure_limits(shipped: None) -> None:
    rules = load_format("text", "blog_long").limits
    report = limits.check("## One\n\nbody\n\n## Two\n\nbody", rules)
    assert report.counts["h2"] == 2
    assert "H2_UNDER" in str(report.violations) or report.ok
    thin = limits.check("no headings here", rules)
    assert any(entry.startswith("H2_UNDER") for entry in thin.violations)


def test_mentions_are_counted_without_urls(shipped: None) -> None:
    rules = load_format("text", "ig_caption").limits
    report = limits.check("hi @one @two see http://x.test/@three", rules)
    assert report.counts["mentions"] == 2


def test_first_line_words_gate(shipped: None) -> None:
    rules = load_format("text", "threads_post").limits
    long_opener = "one two three four five six seven eight nine\nrest"
    report = limits.check(long_opener, rules)
    assert any(entry.startswith("FIRST_LINE_WORDS_OVER") for entry in report.violations)


def test_paragraph_cap(shipped: None) -> None:
    rules = load_format("text", "blog_long").limits
    cap = int(rules["paragraph_chars_max"])
    report = limits.check("## h\n\n" + "x" * (cap + 10), rules)
    assert any(entry.startswith("PARAGRAPH_CHARS_OVER") for entry in report.violations)


def test_formats_env_override_still_wins(isolated_home: Path) -> None:
    assert os.environ[FORMATS_ENV].endswith("fixtures/formats")
    assert catalogue("text") == {"text": ["threads_post"]}
