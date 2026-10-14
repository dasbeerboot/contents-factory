"""Length, emoji, hashtag and link limits. Thresholds come from the format yaml limits block.

Definitions match the source repo's copy lint so two layers never disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
HASHTAG = re.compile(r"#\w+")
LINK = re.compile(r"https?://")
EM_DASH = "—"

DEFAULTS: dict[str, Any] = {
    "max_chars": 0,
    "min_chars": 0,
    "max_emoji": -1,
    "max_hashtags": -1,
    "max_links": -1,
    "forbid_em_dash": False,
}


@dataclass
class LimitReport:
    violations: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "counts": dict(self.counts)}


def resolve(limits: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    for key, value in (limits or {}).items():
        if key in merged:
            merged[key] = value
    return merged


def check(text: str, limits: dict[str, Any] | None = None) -> LimitReport:
    body = str(text or "")
    rules = resolve(limits)
    counts = {"chars": len(body),
              "emoji": len(EMOJI.findall(body)),
              "hashtags": len(HASHTAG.findall(body)),
              "links": len(LINK.findall(body)),
              "em_dash": body.count(EM_DASH)}
    violations: list[str] = []
    if rules["max_chars"] and counts["chars"] > int(rules["max_chars"]):
        violations.append(f"CHARS_OVER:{counts['chars']}>{rules['max_chars']}")
    if rules["min_chars"] and counts["chars"] < int(rules["min_chars"]):
        violations.append(f"CHARS_UNDER:{counts['chars']}<{rules['min_chars']}")
    if int(rules["max_emoji"]) >= 0 and counts["emoji"] > int(rules["max_emoji"]):
        violations.append(f"EMOJI_OVER:{counts['emoji']}>{rules['max_emoji']}")
    if int(rules["max_hashtags"]) >= 0 and counts["hashtags"] > int(rules["max_hashtags"]):
        violations.append(f"HASHTAGS_OVER:{counts['hashtags']}>{rules['max_hashtags']}")
    if int(rules["max_links"]) >= 0 and counts["links"] > int(rules["max_links"]):
        violations.append(f"LINKS_OVER:{counts['links']}>{rules['max_links']}")
    if rules["forbid_em_dash"] and counts["em_dash"]:
        violations.append(f"EM_DASH:{counts['em_dash']}")
    return LimitReport(violations=violations, counts=counts)


def check_blocks(blocks: list[tuple[str, str]],
                 limits: dict[str, Any] | None = None) -> LimitReport:
    merged = LimitReport()
    for label, text in blocks:
        report = check(text, limits)
        merged.violations.extend(f"{label}:{violation}" for violation in report.violations)
        for key, value in report.counts.items():
            merged.counts[key] = merged.counts.get(key, 0) + value
    return merged
