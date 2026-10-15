"""Error dictionary: code -> one sentence, one thing to do, one command.

Every code this project can raise has an entry in `i18n/errors.<lang>.json`, because
a code on its own is a dead end for the person in front of the terminal. The wizard
and the publish path both read this instead of printing the bare code.

Three fields on purpose:
  human    what happened, in one sentence, no jargon
  action   the single next thing to do
  command  the command that does it, or "" when the action is not a command

An unknown code still answers: the fallback names the code and sends the user to
`factory doctor` rather than dropping them.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import FactoryError

CATALOGUE_DIR = Path(__file__).resolve().parent / "i18n"
FALLBACK_LANG = "en"
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
FIELDS: tuple[str, ...] = ("human", "action", "command")
# Codes the ledger and the gates print as a status rather than a failure. They are in
# the dictionary too, so `factory explain` never shrugs.
UNKNOWN_HUMAN = {"ko": "처음 보는 오류 코드예요: {code}.",
                 "en": "This error code is not in the dictionary: {code}."}
UNKNOWN_ACTION = {"ko": "호스트 점검을 돌려 보고, 그대로면 명령 전체를 알려주세요.",
                  "en": "Run the host check, and report the whole command if it repeats."}
UNKNOWN_COMMAND = "factory doctor"


@lru_cache(maxsize=8)
def dictionary(lang: str) -> dict[str, dict[str, str]]:
    path = CATALOGUE_DIR / f"errors.{lang}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for code, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(code, str) or not isinstance(entry, dict):
            continue
        out[code] = {field: str(entry.get(field) or "") for field in FIELDS}
    return out


def codes(lang: str = FALLBACK_LANG) -> list[str]:
    return sorted(dictionary(lang) or dictionary(FALLBACK_LANG))


def known(code: str, lang: str = FALLBACK_LANG) -> bool:
    return code in dictionary(lang) or code in dictionary(FALLBACK_LANG)


def entry(code: str, lang: str = FALLBACK_LANG, *, detail: str = "") -> dict[str, Any]:
    """The dictionary row for one code, with the fallback filled in when absent."""
    if not CODE.match(str(code or "")):
        raise FactoryError("EXPLAIN_CODE_INVALID", str(code or "")[:32])
    found = dictionary(lang).get(code) or dictionary(FALLBACK_LANG).get(code)
    if found is None:
        language = lang if lang in UNKNOWN_HUMAN else FALLBACK_LANG
        found = {"human": UNKNOWN_HUMAN[language].format(code=code),
                 "action": UNKNOWN_ACTION[language],
                 "command": UNKNOWN_COMMAND}
    return {"code": code, "detail": detail, "known": known(code, lang), **found}


def line(code: str, lang: str = FALLBACK_LANG, *, detail: str = "") -> str:
    """One line for a failure path: what happened plus the single next step."""
    row = entry(code, lang, detail=detail)
    text = f"{row['human']} {row['action']}"
    if row["command"]:
        text = f"{text} → {row['command']}"
    return text


def render(code: str, lang: str = FALLBACK_LANG, *, detail: str = "") -> str:
    row = entry(code, lang, detail=detail)
    lines = [f"[{row['code']}]" + (f" {detail}" if detail else ""),
             row["human"], row["action"]]
    if row["command"]:
        lines.append(f"  {row['command']}")
    return "\n".join(lines)
