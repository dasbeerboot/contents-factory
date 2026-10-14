"""Terminal review channel: the default. Prints candidates and a gate table."""
from __future__ import annotations

from typing import Any, Sequence

from .. import i18n

RULE = "-" * 72
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def gate_table(results: Sequence[tuple[str, str, str]], *, lang: str = "en") -> str:
    """results = [(gate name, PASS|FAIL|SKIP, detail)]."""
    translate = i18n.Translator(lang)
    lines = [translate("gates.header"), RULE]
    for name, status, detail in results:
        if status == PASS:
            verdict = translate("gates.pass")
        elif status == SKIP:
            verdict = translate("gates.skip", reason=detail or "NOT_IMPLEMENTED")
        else:
            verdict = translate("gates.fail", reason=detail)
        lines.append(f"{name:<20} {verdict}")
    return "\n".join(lines)


def candidates(outputs: Sequence[dict[str, Any]], *, lang: str = "en") -> str:
    lines: list[str] = []
    for index, output in enumerate(outputs):
        body = str(output.get("text") or output.get("path") or "")
        lines.append(f"[{index}] {body}")
        gates = output.get("gates") or {}
        if gates:
            lines.append(f"    gates: {gates}")
        lines.append(RULE)
    return "\n".join(lines) if lines else "(no candidates)"


def notice(message: str) -> str:
    return f"* {message}"
