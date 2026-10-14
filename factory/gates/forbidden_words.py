"""Forbidden words: a global list plus the profile's forbidden.txt.

The global list stays claim shaped rather than topic shaped, because the factory is
persona agnostic: guarantees, cures and absolute numbers are a platform and regulatory
risk for any niche. Topic specific bans belong in the profile file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

GLOBAL_FORBIDDEN: tuple[str, ...] = (
    "guaranteed results",
    "100% guaranteed",
    "cures",
    "miracle cure",
    "fda approved",
    "doctor recommended",
    "risk free",
    "get rich quick",
    "no side effects",
    "효과 보장",
    "완치",
    "부작용 없음",
)


@dataclass
class ForbiddenReport:
    hits: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hits

    def as_dict(self) -> dict[str, object]:
        return {"verdict": "PASS" if self.ok else "FAIL", "hits": list(self.hits)}


def vocabulary(profile_words: Iterable[str] = (),
               extra: Iterable[str] = ()) -> tuple[str, ...]:
    merged: list[str] = []
    for word in list(GLOBAL_FORBIDDEN) + list(profile_words) + list(extra):
        text = str(word).strip()
        if text and text.lower() not in {entry.lower() for entry in merged}:
            merged.append(text)
    return tuple(merged)


def check(text: str, profile_words: Iterable[str] = (),
          extra: Iterable[str] = ()) -> ForbiddenReport:
    haystack = str(text or "").lower()
    hits = [word for word in vocabulary(profile_words, extra) if word.lower() in haystack]
    return ForbiddenReport(hits=hits)


def check_blocks(blocks: list[tuple[str, str]], profile_words: Iterable[str] = (),
                 extra: Iterable[str] = ()) -> ForbiddenReport:
    merged = ForbiddenReport()
    for label, text in blocks:
        report = check(text, profile_words, extra)
        merged.hits.extend(f"{label}:{hit}" for hit in report.hits)
    return merged
