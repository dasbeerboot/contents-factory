"""cta_single and duplicate_copy: the two text gates the formats ask for."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from factory.cli import main
from factory.gates import cta_single, duplicate_copy, repeat_guard
from factory.queue.store import QueueStore
from factory.spec.format import FORMATS_ENV, load_format
from tests.conftest import make_item

ONE = {"cta_max": 1}


# ---------- cta_single ----------

def test_single_ask_passes() -> None:
    report = cta_single.check("Three days of debugging came down to one cached header. "
                              "Comment if you have hit this.", ONE)
    assert report.ok is True
    assert report.count == 1
    assert report.matches[0]["position"] == "final"


def test_no_ask_passes() -> None:
    report = cta_single.check("A note about a cached header and nothing else.", ONE)
    assert report.ok is True
    assert report.count == 0


def test_two_asks_fail() -> None:
    report = cta_single.check("Comment below. Also save this.", ONE)
    assert report.ok is False
    assert report.violations == ["CTA_OVER:2>1"]


def test_word_boundary_avoids_false_positives() -> None:
    for sentence in ("I clicked through the logs and found nothing.",
                     "We commented on the pull request and moved on.",
                     "The subscriber count did not move."):
        assert cta_single.check(sentence, ONE).count == 0, sentence


def test_korean_asks_are_counted() -> None:
    report = cta_single.check("캐시 헤더 하나였다. 댓글로 알려줘. 저장해두면 편하다.", ONE)
    assert report.ok is False
    assert report.count == 2


def test_body_cta_is_rejected_when_the_format_forbids_it() -> None:
    limits = {"final_cta_max": 1, "body_cta_allowed": False}
    report = cta_single.check("Save this for later. Then the real body continues.", limits)
    assert report.violations == ["CTA_IN_BODY:1"]
    allowed = cta_single.check("The body comes first. Save this for later.", limits)
    assert allowed.ok is True


def test_format_patterns_replace_the_builtins() -> None:
    limits = {"cta_max": 1, "cta_patterns": ["tell me", "pick one"]}
    assert cta_single.check("Tell me what broke. Pick one.", limits).count == 2
    # "comment" is a builtin, so a supplied list means it no longer counts.
    assert cta_single.check("Comment below.", limits).count == 0


def test_extra_patterns_always_add() -> None:
    report = cta_single.check("Steal the checklist.", ONE,
                              extra_patterns=["steal the checklist"])
    assert report.count == 1


def test_allowed_count_reads_either_key() -> None:
    assert cta_single.allowed_count({"cta_max": 2}) == 2
    assert cta_single.allowed_count({"final_cta_max": 0}) == 0
    assert cta_single.allowed_count({}) == 1


def test_zero_allowance_rejects_any_ask() -> None:
    assert cta_single.check("Comment below.", {"cta_max": 0}).ok is False


def test_blocks_share_one_budget() -> None:
    report = cta_single.check_blocks([("body", "Comment below."),
                                      ("reply1", "Also save this.")], ONE)
    assert report.violations == ["CTA_OVER:2>1"]
    assert {entry["block"] for entry in report.matches} == {"body", "reply1"}


def test_shipped_formats_feed_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORMATS_ENV, raising=False)
    for kind, name in (("text", "threads_post"), ("text", "ig_caption"),
                       ("text", "cardnews_copy"), ("video", "explainer")):
        spec = load_format(kind, name)
        assert "cta_single" in spec.gates
        assert cta_single.allowed_count(spec.limits) == 1
    assert cta_single.check("Comment below. Save this too.",
                            load_format("text", "ig_caption").limits).ok is False


# ---------- duplicate_copy ----------

def seed(home: Path, text: str, *, item_id: str, published: bool,
         profile: str = "_example") -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = home / "queue" / f"{stamp[:10]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    items = []
    if path.is_file():
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
    row = {"id": item_id, "profile": profile, "platform": "threads", "kind": "text",
           "scheduled_at": stamp, "published": published, "text": text}
    if published:
        row["published_at"] = stamp
    items.append(row)
    path.write_text(json.dumps({"day": stamp[:10], "items": items}, ensure_ascii=False),
                    encoding="utf-8")
    repeat_guard._MEMO.clear()


BODY = "Three days of debugging came down to one single cached header value."


def test_empty_corpus_passes(isolated_home: Path) -> None:
    assert duplicate_copy.check(BODY, home=isolated_home, profile="_example") == (True, "")


def test_exact_duplicate_of_a_published_item(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    allowed, reason = duplicate_copy.check(BODY, home=isolated_home, profile="_example")
    assert allowed is False
    assert reason == "DUPLICATE_EXACT:published:q_20261014120000_aaaa"


def test_exact_duplicate_of_a_queued_item(isolated_home: Path) -> None:
    """The point of this gate: repeat_guard only sees published copy, this sees the queue."""
    seed(isolated_home, BODY, item_id="q_20261014130000_bbbb", published=False)
    allowed, reason = duplicate_copy.check(BODY, home=isolated_home, profile="_example")
    assert allowed is False
    assert reason == "DUPLICATE_EXACT:queued:q_20261014130000_bbbb"
    assert repeat_guard.check(BODY, home=isolated_home, profile="_example") == (True, "")


def test_self_is_excluded(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014130000_bbbb", published=False)
    assert duplicate_copy.check(BODY, home=isolated_home, profile="_example",
                                exclude_id="q_20261014130000_bbbb") == (True, "")


def test_other_profiles_are_not_compared(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True,
         profile="other")
    assert duplicate_copy.check(BODY, home=isolated_home, profile="_example") == (True, "")


def test_distinct_copy_passes(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    allowed, _reason = duplicate_copy.check(
        "Disk pressure took the node down before anyone looked at the logs.",
        home=isolated_home, profile="_example")
    assert allowed is True


# Same wording as BODY minus the last word: no identical sentence, but every 6-gram is
# shared, which is what the n-gram rule is for.
NEAR = "Three days of debugging came down to one single cached header."


def test_ngram_overlap_is_reported_with_the_id(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    allowed, reason = duplicate_copy.check(NEAR, home=isolated_home, profile="_example")
    assert allowed is False
    assert reason.startswith("DUPLICATE_NGRAM:published:q_20261014120000_aaaa:")


def test_reworded_copy_below_the_threshold_passes(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    reworded = ("One cached header cost me three days. The fix was a single line of "
                "configuration in the end.")
    allowed, _reason = duplicate_copy.check(reworded, home=isolated_home,
                                            profile="_example")
    assert allowed is True


def test_shared_long_sentence_is_caught(isolated_home: Path) -> None:
    seed(isolated_home, f"Intro line here. {BODY}", item_id="q_20261014120000_aaaa",
         published=True)
    allowed, reason = duplicate_copy.check(
        f"A different opening about retries entirely and nothing else. {BODY}",
        home=isolated_home, profile="_example")
    assert allowed is False
    assert reason.startswith(("DUPLICATE_SENTENCE:published:q_20261014120000_aaaa",
                              "DUPLICATE_NGRAM:published:q_20261014120000_aaaa"))


def test_threshold_comes_from_the_format_limits(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    half = ("Three days of debugging came down to this. Disk pressure, not the cache, "
            "took the node down overnight.")
    strict = duplicate_copy.check(half, home=isolated_home, profile="_example",
                                  limits={"duplicate_ratio_max": 0.1})
    assert strict[0] is False
    loose = duplicate_copy.check(half, home=isolated_home, profile="_example",
                                 limits={"duplicate_ratio_max": 1.0})
    assert loose[0] is True
    assert duplicate_copy.ratio_limit({"duplicate_ratio_max": 0.8}) == 0.8
    assert duplicate_copy.ratio_limit({}) == duplicate_copy.DUPLICATE_RATIO


def test_report_carries_the_comparison_count(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    seed(isolated_home, "Another unrelated line about retries entirely.",
         item_id="q_20261014130000_bbbb", published=False)
    report = duplicate_copy.judge("Fresh copy about disk pressure only.",
                                  home=isolated_home, profile="_example")
    assert report.compared == 2
    assert report.ok is True
    assert 0.0 <= report.worst_ratio < 1.0


def test_gate_failure_opens(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("corpus broken")
    monkeypatch.setattr(duplicate_copy, "judge", explode)
    report = duplicate_copy.check_detail("anything", home=isolated_home,
                                         profile="_example")
    assert report["verdict"] == "PASS"
    assert report["error"] == "RuntimeError"


def test_blank_text_is_not_a_duplicate(isolated_home: Path) -> None:
    seed(isolated_home, BODY, item_id="q_20261014120000_aaaa", published=True)
    assert duplicate_copy.check("   ", home=isolated_home, profile="_example") == (True, "")


# ---------- wiring ----------

def test_gates_are_no_longer_skipped(isolated_home: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gate", "run", "--text", "Comment below. Also save this.",
                 "--gate", "cta_single", "--gate", "duplicate_copy"]) == 3
    out = capsys.readouterr().out
    assert "NOT_IMPLEMENTED" not in out
    assert "CTA_OVER:2>1" in out
    assert "duplicate_copy" in out


def test_gate_run_item_flag_excludes_self(isolated_home: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item(text=BODY))
    assert main(["gate", "run", "--text", BODY, "--gate", "duplicate_copy"]) == 3
    assert "DUPLICATE" in capsys.readouterr().out
    assert main(["gate", "run", "--text", BODY, "--gate", "duplicate_copy",
                 "--item", item.id]) == 0
