"""Length, emoji, hashtag, mention and link limits. Thresholds come from the format yaml.

The yaml is canonical, so its key names (`hashtags_max`, `emoji_max`, `external_links`)
are the primary spelling and the older `max_*` names stay as aliases. Keys that describe
something no text check can see (slide counts, pixel sizes, cta_max, domain_terms_max) are
ignored here and belong to their own gates.

A target range is an advisory, not a violation: `target_chars: [120, 900]` guides the
writer, it does not fail the post.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
HASHTAG = re.compile(r"#\w+")
MENTION = re.compile(r"(?<![\w/])@[A-Za-z0-9._]{2,}")
LINK = re.compile(r"https?://")
HEADING_H2 = re.compile(r"^##\s+\S", re.M)
EM_DASH = "—"

DEFAULTS: dict[str, Any] = {
    "max_chars": 0,
    "min_chars": 0,
    "max_emoji": -1,
    "max_hashtags": -1,
    "max_mentions": -1,
    "max_links": -1,
    "max_first_line_words": 0,
    "max_paragraph_chars": 0,
    "min_h2": 0,
    "forbid_em_dash": False,
}

# yaml spelling -> internal key
ALIASES: dict[str, str] = {
    "hashtags_max": "max_hashtags",
    "emoji_max": "max_emoji",
    "mentions_max": "max_mentions",
    "external_links_max": "max_links",
    "first_line_words_max": "max_first_line_words",
    "paragraph_chars_max": "max_paragraph_chars",
    "h2_count_min": "min_h2",
    "chars_max": "max_chars",
    "chars_min": "min_chars",
}
TARGET_KEYS: tuple[str, ...] = ("target_chars", "body_chars_target", "title_chars_target")


@dataclass
class LimitReport:
    violations: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    advisories: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": "PASS" if self.ok else "FAIL",
                "violations": list(self.violations), "counts": dict(self.counts),
                "advisories": list(self.advisories)}


def resolve(limits: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    for key, value in (limits or {}).items():
        target = ALIASES.get(key, key)
        if target == "max_links" and isinstance(value, bool):
            merged["max_links"] = -1 if value else 0
            continue
        if key == "external_links":
            merged["max_links"] = -1 if value else 0
            continue
        if target in merged:
            merged[target] = value
    return merged


def target_range(limits: dict[str, Any] | None) -> tuple[int, int] | None:
    for key in TARGET_KEYS:
        value = (limits or {}).get(key)
        if isinstance(value, list) and len(value) == 2:
            return int(value[0]), int(value[1])
    return None


def check(text: str, limits: dict[str, Any] | None = None) -> LimitReport:
    body = str(text or "")
    rules = resolve(limits)
    paragraphs = [block for block in re.split(r"\n\s*\n", body) if block.strip()]
    first_line = next((line for line in body.splitlines() if line.strip()), "")
    counts = {"chars": len(body),
              "emoji": len(EMOJI.findall(body)),
              "hashtags": len(HASHTAG.findall(body)),
              "mentions": len(MENTION.findall(body)),
              "links": len(LINK.findall(body)),
              "em_dash": body.count(EM_DASH),
              "first_line_words": len(first_line.split()),
              "h2": len(HEADING_H2.findall(body)),
              "paragraphs": len(paragraphs)}
    violations: list[str] = []
    if rules["max_chars"] and counts["chars"] > int(rules["max_chars"]):
        violations.append(f"CHARS_OVER:{counts['chars']}>{rules['max_chars']}")
    if rules["min_chars"] and counts["chars"] < int(rules["min_chars"]):
        violations.append(f"CHARS_UNDER:{counts['chars']}<{rules['min_chars']}")
    for name, key in (("EMOJI", "emoji"), ("HASHTAGS", "hashtags"),
                      ("MENTIONS", "mentions"), ("LINKS", "links")):
        allowed = int(rules[f"max_{key}"])
        if allowed >= 0 and counts[key] > allowed:
            violations.append(f"{name}_OVER:{counts[key]}>{allowed}")
    if rules["max_first_line_words"] and counts["first_line_words"] > int(
            rules["max_first_line_words"]):
        violations.append(f"FIRST_LINE_WORDS_OVER:{counts['first_line_words']}>"
                          f"{rules['max_first_line_words']}")
    if rules["min_h2"] and counts["h2"] < int(rules["min_h2"]):
        violations.append(f"H2_UNDER:{counts['h2']}<{rules['min_h2']}")
    if rules["max_paragraph_chars"]:
        longest = max((len(block) for block in paragraphs), default=0)
        if longest > int(rules["max_paragraph_chars"]):
            violations.append(f"PARAGRAPH_CHARS_OVER:{longest}>"
                              f"{rules['max_paragraph_chars']}")
    if rules["forbid_em_dash"] and counts["em_dash"]:
        violations.append(f"EM_DASH:{counts['em_dash']}")

    advisories: list[str] = []
    window = target_range(limits)
    if window and not window[0] <= counts["chars"] <= window[1]:
        advisories.append(f"CHARS_OFF_TARGET:{counts['chars']}~[{window[0]},{window[1]}]")
    return LimitReport(violations=violations, counts=counts, advisories=advisories)


def check_blocks(blocks: list[tuple[str, str]],
                 limits: dict[str, Any] | None = None) -> LimitReport:
    merged = LimitReport()
    for label, text in blocks:
        report = check(text, limits)
        merged.violations.extend(f"{label}:{violation}" for violation in report.violations)
        merged.advisories.extend(f"{label}:{entry}" for entry in report.advisories)
        for key, value in report.counts.items():
            merged.counts[key] = merged.counts.get(key, 0) + value
    return merged
