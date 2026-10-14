"""Reader for docs/rules/*.md. Those documents are the writing, image, video and
publishing standards, and the relevant sections go into the LLM system prefix.

Only whole `##` sections are selected, never reflowed, so the prefix stays byte stable
across calls: prompt caching dies on a one byte change.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .config import repo_root
from .errors import FactoryError

RULES_DIRNAME = "rules"
HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)

# Sections that belong in a generation prefix. "목적" is a preamble for human readers and
# "예시" is long; both are dropped so the prefix stays about constraints the model must obey.
DEFAULT_SECTIONS: dict[str, tuple[str, ...]] = {
    "writing": ("절차", "게이트(PASS·FAIL 기준)", "금지"),
    "image": ("절차", "게이트(PASS·FAIL 기준)", "금지"),
    "video": ("절차", "게이트(PASS·FAIL 기준)", "금지"),
    "publishing": ("절차", "금지"),
}
KIND_TO_DOC: dict[str, str] = {"text": "writing", "image": "image", "video": "video"}


def rules_dir() -> Path:
    return repo_root() / "docs" / RULES_DIRNAME


def document_path(name: str) -> Path:
    if not re.match(r"^[a-z]+$", name):
        raise FactoryError("RULES_NAME_INVALID", name[:32])
    return rules_dir() / f"{name}-rules.md"


@lru_cache(maxsize=8)
def read_document(name: str) -> str:
    try:
        return document_path(name).read_text(encoding="utf-8")
    except OSError:
        return ""


def sections(text: str) -> dict[str, str]:
    """Split a rules document into {heading: body}, preserving the body verbatim."""
    found: dict[str, str] = {}
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[match.group(1)] = text[match.end():end].strip("\n")
    return found


def section_names(name: str) -> list[str]:
    return list(sections(read_document(name)))


def prefix(name: str, headings: tuple[str, ...] | None = None) -> str:
    """The selected sections, in document order, with their headings kept."""
    body = read_document(name)
    if not body:
        return ""
    wanted = headings if headings is not None else DEFAULT_SECTIONS.get(name, ())
    parts = [f"## {heading}\n\n{text}"
             for heading, text in sections(body).items()
             if not wanted or heading in wanted]
    return "\n\n".join(parts).strip()


def prefix_for_kind(kind: str, headings: tuple[str, ...] | None = None) -> str:
    name = KIND_TO_DOC.get(kind)
    if name is None:
        return ""
    return prefix(name, headings)
