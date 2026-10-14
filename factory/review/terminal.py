"""Terminal review channel: the default. Prints candidates and a gate table."""
from __future__ import annotations

from typing import Any, Sequence

from .. import i18n

RULE = "-" * 72


def gate_table(results: Sequence[tuple[str, bool, str]], *, lang: str = "en") -> str:
    translate = i18n.Translator(lang)
    lines = [translate("gates.header"), RULE]
    for name, ok, reason in results:
        verdict = translate("gates.pass") if ok else translate("gates.fail", reason=reason)
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
