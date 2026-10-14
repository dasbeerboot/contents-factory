from __future__ import annotations

import json
import time
from pathlib import Path

from factory.gates import caption_safe_zone, forbidden_words, limits, repeat_guard

PUBLISHED = ("Three days of debugging came down to one single cached header value. "
             "I rejected the queue rewrite because the fix was one line of config.")


def seed_published(home: Path, *, text: str = PUBLISHED, profile: str = "_example",
                   day: str = "2026-10-15", when: float | None = None) -> None:
    moment = when if when is not None else time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))
    path = home / "queue" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))["items"]
    existing.append({"id": f"q_2026101512000{len(existing)}_ab12", "profile": profile,
                     "platform": "threads", "kind": "text", "text": text,
                     "scheduled_at": stamp, "published": True, "published_at": stamp})
    path.write_text(json.dumps({"day": day, "items": existing}, ensure_ascii=False),
                    encoding="utf-8")
    repeat_guard._MEMO.clear()


def test_normalize_and_grams() -> None:
    assert repeat_guard.normalize("Hello,  WORLD! 안녕") == "hello world 안녕"
    assert repeat_guard.grams("a b c d e f g", 6) == {"a b c d e f", "b c d e f g"}
    assert repeat_guard.grams("too short", 6) == set()


def test_sentences_skip_blanks() -> None:
    assert repeat_guard.sentences("One. Two!  ") == ["one", "two"]


def test_empty_corpus_passes(isolated_home: Path) -> None:
    allowed, reason = repeat_guard.check("brand new sentence here",
                                         home=isolated_home, profile="_example")
    assert (allowed, reason) == (True, "")


def test_exact_sentence_blocks(isolated_home: Path) -> None:
    seed_published(isolated_home)
    allowed, reason = repeat_guard.check(
        "Three days of debugging came down to one single cached header value.",
        home=isolated_home, profile="_example")
    assert allowed is False
    assert reason.startswith("exact_sentence")


def test_short_sentence_does_not_block(isolated_home: Path) -> None:
    seed_published(isolated_home, text="Short line only.")
    allowed, _reason = repeat_guard.check("Short line only.", home=isolated_home,
                                          profile="_example")
    assert allowed is True


def test_ngram_overlap_blocks(isolated_home: Path) -> None:
    seed_published(isolated_home)
    candidate = ("I rejected the queue rewrite because the fix was one line of config, "
                 "and three days of debugging came down to one single cached header value")
    report = repeat_guard.judge(candidate, home=isolated_home, profile="_example")
    assert report["verdict"] == "BLOCK"
    assert {rule["rule"] for rule in report["rules"]} & {"ngram_overlap", "exact_sentence"}


def test_other_profile_corpus_is_separate(isolated_home: Path) -> None:
    seed_published(isolated_home, profile="other")
    allowed, _reason = repeat_guard.check(
        "Three days of debugging came down to one single cached header value.",
        home=isolated_home, profile="_example")
    assert allowed is True


def test_closer_repeat_blocks_after_limit(isolated_home: Path) -> None:
    for index in range(3):
        seed_published(isolated_home, text=f"Body number {index} differs entirely. "
                                           "link in bio")
    allowed, reason = repeat_guard.check("Nothing alike at all up here. link in bio",
                                        home=isolated_home, profile="_example")
    assert allowed is False
    assert reason.startswith("closer_repeat")


def test_closer_under_limit_passes(isolated_home: Path) -> None:
    for index in range(2):
        seed_published(isolated_home, text=f"Body number {index} differs entirely. "
                                           "link in bio")
    allowed, _reason = repeat_guard.check("Nothing alike at all up here. link in bio",
                                         home=isolated_home, profile="_example")
    assert allowed is True


def test_closer_match_is_whitespace_bounded(isolated_home: Path) -> None:
    for index in range(4):
        seed_published(isolated_home,
                       text=f"Row {index} is unlike the others. more on my profile")
    allowed, _reason = repeat_guard.check(
        "Just the option. My profile has the rest of the writeup for anyone curious.",
        home=isolated_home, profile="_example")
    assert allowed is True


def test_profile_closers_extend_defaults(isolated_home: Path) -> None:
    for index in range(3):
        seed_published(isolated_home, text=f"Row {index} unlike others. steal the checklist")
    allowed, reason = repeat_guard.check("Fresh opening line here. steal the checklist",
                                        home=isolated_home, profile="_example",
                                        closers=["steal the checklist"])
    assert allowed is False
    assert "steal the checklist" in reason


def test_check_blocks_labels_the_failure(isolated_home: Path) -> None:
    seed_published(isolated_home)
    allowed, reason = repeat_guard.check_blocks(
        [("body", "fresh text"),
         ("reply1", "Three days of debugging came down to one single cached header value.")],
        home=isolated_home, profile="_example")
    assert allowed is False
    assert reason.startswith("reply1 ")


def test_guard_failure_opens(isolated_home: Path, monkeypatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("corpus broken")
    monkeypatch.setattr(repeat_guard, "judge", explode)
    report = repeat_guard.check_detail("anything", home=isolated_home, profile="_example")
    assert report["verdict"] == "PASS"
    assert report["error"] == "RuntimeError"


def test_retry_hint_carries_reason() -> None:
    assert "Rejected because" in repeat_guard.retry_hint("exact_sentence(x)")
    assert "Rejected because" not in repeat_guard.retry_hint()


def test_limits_counts_and_violations() -> None:
    rules = {"max_chars": 20, "max_emoji": 1, "max_hashtags": 1, "max_links": 0,
             "forbid_em_dash": True}
    report = limits.check("way too long a body here #one #two http://x.test — yes", rules)
    assert report.ok is False
    codes = {violation.split(":")[0] for violation in report.violations}
    assert codes == {"CHARS_OVER", "HASHTAGS_OVER", "LINKS_OVER", "EM_DASH"}
    assert report.counts["hashtags"] == 2


def test_limits_pass_and_defaults() -> None:
    assert limits.check("short", {"max_chars": 500}).ok is True
    assert limits.check("anything at all").ok is True


def test_limits_min_chars() -> None:
    report = limits.check("hi", {"min_chars": 10})
    assert report.violations == ["CHARS_UNDER:2<10"]


def test_limits_blocks_aggregate() -> None:
    report = limits.check_blocks([("body", "#a #b"), ("reply1", "#c")],
                                 {"max_hashtags": 1})
    assert report.violations == ["body:HASHTAGS_OVER:2>1"]
    assert report.counts["hashtags"] == 3


def test_forbidden_global_and_profile() -> None:
    report = forbidden_words.check("this is a GAME CHANGER, guaranteed results",
                                   ["game changer"])
    assert report.ok is False
    assert "guaranteed results" in report.hits
    assert report.hits.count("game changer") == 1


def test_forbidden_clean_text_passes() -> None:
    assert forbidden_words.check("plain honest sentence", ["game changer"]).ok is True


def test_forbidden_blocks_labels() -> None:
    report = forbidden_words.check_blocks([("reply1", "완치 보장")], ["완치"])
    assert report.hits == ["reply1:완치"]


def test_caption_safe_zone_passes_inside_the_zone() -> None:
    inside = caption_safe_zone.Box(x=220, y=300, width=600, height=200, label="line1")
    report = caption_safe_zone.check([inside])
    assert report.ok is True
    assert report.boxes == 1
    assert report.zone["x_min"] == 200 and report.zone["y_max"] == 1430


def test_caption_safe_zone_reports_every_edge() -> None:
    report = caption_safe_zone.check([(100, 100, 900, 1800)])
    codes = {entry.split(":")[1] for entry in report.violations}
    assert codes == {"LEFT_OUT", "RIGHT_OUT", "TOP_OUT", "BOTTOM_OUT", "WIDTH_OVER"}
    assert report.ok is False
    assert all(entry.startswith("box1:") for entry in report.violations)


def test_caption_safe_zone_accepts_dicts_and_labels() -> None:
    report = caption_safe_zone.check([
        {"x": 220, "y": 300, "width": 600, "height": 100, "label": "hook"},
        {"x": 150, "y": 300, "width": 600, "height": 100, "label": "cta"}])
    assert report.violations == ["cta:LEFT_OUT:150<200"]


def test_caption_safe_zone_empty_input_is_a_failure() -> None:
    assert caption_safe_zone.check([]).violations == ["NO_BOXES_SUPPLIED"]
    assert caption_safe_zone.check([{"x": 1}]).violations[0].startswith("BOX_INVALID")
    assert caption_safe_zone.check([(1, 2, 0, 0)]).violations == ["box1:EMPTY_BOX"]


def test_caption_safe_zone_reads_the_format_limits() -> None:
    limits_block = {"width_px": 1080, "height_px": 1920,
                    "center_safe_zone_px": {"x_min": 180, "x_max": 900,
                                            "y_min": 300, "y_max": 1500}}
    zone = caption_safe_zone.zone_from_limits(limits_block)
    assert (zone.x_min, zone.x_max, zone.y_min, zone.y_max) == (180, 900, 300, 1500)
    report = caption_safe_zone.check([(185, 310, 600, 100)], limits=limits_block)
    assert report.ok is True


def test_caption_safe_zone_scales_to_another_frame() -> None:
    report = caption_safe_zone.check([(110, 150, 300, 100)],
                                     limits={"width_px": 540, "height_px": 960})
    assert report.frame == (540, 960)
    assert report.zone["x_min"] == 100 and report.zone["x_max"] == 440
    assert report.ok is True
