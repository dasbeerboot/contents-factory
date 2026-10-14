"""One ask per piece. More than one call to action and the reader does nothing.

Patterns come from the format yaml when it supplies `cta_patterns`; otherwise the built-in
list below is used. The built-ins are shapes of asking (comment, save, share, follow, link,
subscribe, sign up) rather than any one brand's wording, so this stays persona agnostic.

Limits read from the format yaml:
  cta_max / final_cta_max   how many CTA sentences are allowed (default 1)
  body_cta_allowed: false   a CTA anywhere but the closing line is a violation
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

DEFAULT_MAX = 1
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+|\n+")

# Each entry is a plain phrase matched case insensitively on word boundaries, plus a few
# regex shapes for imperative openers. Keep them about the ask, never about a product.
DEFAULT_PATTERNS: tuple[str, ...] = (
    "comment",
    "reply",
    "drop a",
    "tag ",
    "save this",
    "share this",
    "send this",
    "follow me",
    "follow for",
    "subscribe",
    "sign up",
    "link in bio",
    "link in my bio",
    "in my profile",
    "on my profile",
    "check my profile",
    "dm me",
    "message me",
    "buy now",
    "order now",
    "click",
    "swipe up",
    "join the",
    "댓글",
    "저장해",
    "공유해",
    "팔로우",
    "구독",
    "신청",
    "프로필 링크",
    "바이오 링크",
    "링크 눌러",
)


@dataclass
class CtaReport:
    violations: list[str] = field(default_factory=list)
    matches: list[dict[str, str]] = field(default_factory=list)
    allowed: int = DEFAULT_MAX

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "count": self.count,
                "allowed": self.allowed, "matches": list(self.matches)}


def patterns(limits: dict[str, Any] | None = None,
             extra: Iterable[str] = ()) -> tuple[str, ...]:
    """Format supplied patterns replace the built-ins; `extra` always adds."""
    supplied = (limits or {}).get("cta_patterns")
    base: list[str] = []
    if isinstance(supplied, list) and supplied:
        base = [str(entry).strip().lower() for entry in supplied if str(entry).strip()]
    else:
        base = list(DEFAULT_PATTERNS)
    for entry in extra:
        text = str(entry).strip().lower()
        if text and text not in base:
            base.append(text)
    return tuple(base)


def allowed_count(limits: dict[str, Any] | None = None) -> int:
    for key in ("cta_max", "final_cta_max"):
        value = (limits or {}).get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return DEFAULT_MAX


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def _expression(pattern: str) -> re.Pattern[str]:
    """Word bounded for Latin phrases so "click" does not fire inside "clicked".

    The trailing guard is only added when the phrase ends in a letter: patterns like
    "tag " end in a space on purpose and must still match "tag the friend".
    """
    text = pattern.strip()
    body = re.escape(text)
    lead = r"(?<![a-z])" if re.match(r"^[a-z]", text) else ""
    trail = r"(?![a-z])" if re.search(r"[a-z]$", text) else ""
    return re.compile(lead + body + trail, re.I)


def sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in SENTENCE_SPLIT.split(str(text or ""))
            if chunk.strip()]


def check(text: str, limits: dict[str, Any] | None = None,
          extra_patterns: Iterable[str] = ()) -> CtaReport:
    allowed = allowed_count(limits)
    active = [( pattern, _expression(pattern))
              for pattern in patterns(limits, extra_patterns)]
    lines = sentences(text)
    matches: list[dict[str, str]] = []
    for index, sentence in enumerate(lines):
        folded = _normalize(sentence)
        for pattern, expression in active:
            if expression.search(folded):
                matches.append({"sentence": sentence[:90], "pattern": pattern,
                                "position": "final" if index == len(lines) - 1 else "body"})
                break
    violations: list[str] = []
    if len(matches) > allowed:
        violations.append(f"CTA_OVER:{len(matches)}>{allowed}")
    body_allowed = (limits or {}).get("body_cta_allowed")
    if body_allowed is False:
        stray = [entry for entry in matches if entry["position"] == "body"]
        if stray:
            violations.append(f"CTA_IN_BODY:{len(stray)}")
    return CtaReport(violations=violations, matches=matches, allowed=allowed)


def check_blocks(blocks: Sequence[tuple[str, str]], limits: dict[str, Any] | None = None,
                 extra_patterns: Iterable[str] = ()) -> CtaReport:
    """Blocks share one budget: two replies each ending in an ask is still two asks."""
    allowed = allowed_count(limits)
    merged = CtaReport(allowed=allowed)
    for label, text in blocks:
        report = check(text, limits, extra_patterns)
        for entry in report.matches:
            merged.matches.append({**entry, "block": label})
    if merged.count > allowed:
        merged.violations.append(f"CTA_OVER:{merged.count}>{allowed}")
    if (limits or {}).get("body_cta_allowed") is False:
        stray = [entry for entry in merged.matches if entry["position"] == "body"]
        if stray:
            merged.violations.append(f"CTA_IN_BODY:{len(stray)}")
    return merged
