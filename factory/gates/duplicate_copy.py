"""Near duplicate copy against this profile's own queue, published or not.

repeat_guard guards what already went out. This gate also looks at items still waiting in
the queue, because queueing the same caption twice is a duplicate before it ships. The
normalisation, sentence split and n-gram machinery are repeat_guard's, so the two gates
cannot drift apart.

Thresholds (format yaml, else the defaults):
  duplicate_ratio_max   n-gram overlap that counts as a duplicate (default 0.60)
  duplicate_window_days corpus window (default repeat_guard's 30)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import repeat_guard

DUPLICATE_RATIO = float(os.environ.get("FACTORY_DUPLICATE_RATIO") or 0.60)
ALLOW: tuple[bool, str] = (True, "")


@dataclass
class DuplicateReport:
    violations: list[str] = field(default_factory=list)
    compared: int = 0
    worst_ratio: float = 0.0
    worst_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "compared": self.compared,
                "worst_ratio": round(self.worst_ratio, 3), "worst_id": self.worst_id}


def ratio_limit(limits: dict[str, Any] | None = None) -> float:
    value = (limits or {}).get("duplicate_ratio_max")
    if isinstance(value, (int, float)) and 0 < float(value) <= 1:
        return float(value)
    return DUPLICATE_RATIO


def window_days(limits: dict[str, Any] | None = None) -> int:
    value = (limits or {}).get("duplicate_window_days")
    if isinstance(value, int) and value > 0:
        return value
    return repeat_guard.WINDOW_DAYS


def judge(text: str, *, home: Path, profile: str, exclude_id: str | None = None,
          limits: dict[str, Any] | None = None,
          now: float | None = None) -> DuplicateReport:
    body = str(text or "")
    if not repeat_guard.normalize(body):
        return DuplicateReport()
    rows = [row for row in repeat_guard.collect_items(
        home, profile, now=now, window_days=window_days(limits), published_only=False)
        if row["id"] != (exclude_id or "")]
    report = DuplicateReport(compared=len(rows))
    if not rows:
        return report
    limit = ratio_limit(limits)
    own_normal = repeat_guard.normalize(body)
    own_grams = repeat_guard.grams(body)
    own_sentences = {sentence for sentence in repeat_guard.sentences(body)
                     if len(sentence.split()) >= repeat_guard.EXACT_MIN_WORDS}
    for row in rows:
        other = row["text"]
        state = "published" if row["published"] else "queued"
        if repeat_guard.normalize(other) == own_normal:
            report.worst_ratio = 1.0
            report.worst_id = row["id"]
            report.violations.append(f"DUPLICATE_EXACT:{state}:{row['id']}")
            return report
        if own_grams:
            other_grams = repeat_guard.grams(other)
            if other_grams:
                overlap = len(own_grams & other_grams) / float(len(own_grams))
                if overlap > report.worst_ratio:
                    report.worst_ratio = overlap
                    report.worst_id = row["id"]
                if overlap >= limit:
                    report.violations.append(
                        f"DUPLICATE_NGRAM:{state}:{row['id']}:{overlap * 100:.0f}%"
                        f">={limit * 100:.0f}%")
                    return report
        shared = own_sentences & {sentence for sentence in repeat_guard.sentences(other)
                                  if len(sentence.split()) >= repeat_guard.EXACT_MIN_WORDS}
        if shared:
            report.worst_id = report.worst_id or row["id"]
            report.violations.append(f"DUPLICATE_SENTENCE:{state}:{row['id']}")
            return report
    return report


def check_detail(text: str, *, home: Path, profile: str, exclude_id: str | None = None,
                 limits: dict[str, Any] | None = None,
                 now: float | None = None) -> dict[str, Any]:
    """A gate bug must not stop the pipeline, so internals fail open."""
    try:
        return judge(text, home=home, profile=profile, exclude_id=exclude_id,
                     limits=limits, now=now).as_dict()
    except Exception as error:  # noqa: BLE001
        return {"verdict": "PASS", "violations": [], "compared": 0, "worst_ratio": 0.0,
                "worst_id": "", "error": type(error).__name__}


def check(text: str, *, home: Path, profile: str, exclude_id: str | None = None,
          limits: dict[str, Any] | None = None,
          now: float | None = None) -> tuple[bool, str]:
    report = check_detail(text, home=home, profile=profile, exclude_id=exclude_id,
                          limits=limits, now=now)
    if report["verdict"] == "PASS":
        return ALLOW
    return False, str(report["violations"][0])
