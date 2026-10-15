"""`factory wizard ...` — the only writer of `state/wizard.json`.

Output is for a person to read out loud. `--json` is the same payload for an agent that
wants the fields rather than the paragraph; both come from the same call, so the two can
never disagree about which question is on the table.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from .. import explain, i18n
from ..config import Config
from ..errors import FactoryError
from . import engine, state

EXIT_OK = 0
EXIT_ERROR = 3


def build_parser(parent: argparse._SubParsersAction) -> None:
    wizard = parent.add_parser(
        "wizard", help="guided setup and make flows (next/answer/skip/back/status/reset)")
    actions = wizard.add_subparsers(dest="wizard_command", required=True)

    nxt = actions.add_parser("next", help="the one question to ask right now")
    nxt.add_argument("--json", dest="as_json", action="store_true")
    nxt.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="no paid generation; drafts are placeholders")

    answer = actions.add_parser("answer", help="store the user's answer, verbatim")
    answer.add_argument("--id", dest="question_id", required=True)
    answer.add_argument("--value", required=True)
    answer.add_argument("--json", dest="as_json", action="store_true")
    answer.add_argument("--dry-run", dest="dry_run", action="store_true")

    for name, help_text in (("skip", "skip the current question, when it allows it"),
                            ("back", "undo the last answer and ask it again"),
                            ("status", "both flows, what is answered and what is left")):
        single = actions.add_parser(name, help=help_text)
        single.add_argument("--json", dest="as_json", action="store_true")

    reset = actions.add_parser("reset", help="start a flow over")
    reset.add_argument("--flow", choices=list(state.FLOWS),
                       help="omit to reset everything")
    reset.add_argument("--json", dest="as_json", action="store_true")


def _dry_run(args: argparse.Namespace) -> bool:
    if getattr(args, "dry_run", False):
        return True
    return os.environ.get(engine.DRY_RUN_ENV, "").strip() not in {"", "0", "false"}


def run(config: Config, args: argparse.Namespace) -> int:
    command = args.wizard_command
    as_json = bool(getattr(args, "as_json", False))
    if command == "next":
        payload = engine.next_question(config, dry_run=_dry_run(args))
        return _emit(payload, config, as_json, render_prompt)
    if command == "answer":
        payload = engine.answer_question(config, args.question_id, args.value,
                                         dry_run=_dry_run(args))
        code = EXIT_OK if payload.get("ok") else EXIT_ERROR
        _emit(payload, config, as_json, render_answer)
        return code
    if command == "skip":
        return _emit(engine.skip(config), config, as_json, render_simple)
    if command == "back":
        return _emit(engine.back(config), config, as_json, render_simple)
    if command == "status":
        return _emit(engine.status(config), config, as_json, render_status)
    if command == "reset":
        return _emit(engine.reset(config, getattr(args, "flow", None)), config, as_json,
                     render_simple)
    raise FactoryError("WIZARD_COMMAND_UNKNOWN", str(command)[:32])


def _emit(payload: dict[str, Any], config: Config, as_json: bool, renderer) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(renderer(payload, config))
    return EXIT_OK


def render_prompt(payload: dict[str, Any], config: Config) -> str:
    lang = config.ui_lang
    if payload.get("done"):
        return str(payload.get("message") or "")
    lines: list[str] = []
    if payload.get("cost"):
        lines.append(str(payload["cost"]))
    if payload.get("draft"):
        lines.append(i18n.get("wizard.draft.header", lang))
        lines.append("")
        lines.append(str(payload["draft"]))
        lines.append("")
        if payload.get("gates"):
            lines.append(str(payload["gates"]))
    if payload.get("note"):
        lines.append(str(payload["note"]))
    progress = i18n.get("wizard.progress", lang, step=payload.get("step"),
                        total=payload.get("step_total"))
    lines.append(f"{progress}  {payload.get('question')}")
    if payload.get("choices"):
        for entry in payload["choices"]:
            lines.append(f"  - {entry['value']}: {entry['label']}")
    if payload.get("action"):
        lines.append(i18n.get("wizard.connect.action", lang, command=payload["action"]))
        lines.append(i18n.get("wizard.connect.verify", lang, command=payload["verify"]))
        if payload.get("link"):
            lines.append(i18n.get("wizard.connect.link", lang, url=payload["link"]))
        if payload.get("keys"):
            lines.append(i18n.get("wizard.connect.keys", lang,
                                  keys=", ".join(payload["keys"])))
    if payload.get("default"):
        lines.append(i18n.get("wizard.default", lang, value=payload["default"]))
    if payload.get("help"):
        lines.append(str(payload["help"]))
    lines.append(str(payload.get("input") or ""))
    lines.append(str(payload.get("answer_with") or ""))
    return "\n".join(line for line in lines if line is not None)


def render_answer(payload: dict[str, Any], config: Config) -> str:
    lines: list[str] = []
    if not payload.get("ok"):
        lines.append(str(payload.get("reask") or ""))
        lines.append(str(payload.get("question") or ""))
        for entry in payload.get("choices") or []:
            lines.append(f"  - {entry['value']}: {entry['label']}")
        return "\n".join(line for line in lines if line)
    lines.append(str(payload.get("saved") or ""))
    if payload.get("message"):
        lines.append(str(payload["message"]))
    for command in payload.get("ran") or []:
        lines.append(f"  $ {command}")
    return "\n".join(lines)


def render_simple(payload: dict[str, Any], config: Config) -> str:
    return str(payload.get("message") or "")


def render_status(payload: dict[str, Any], config: Config) -> str:
    lang = config.ui_lang
    lines = [i18n.get("wizard.status.header", lang, flow=payload.get("flow"),
                      simple="on" if payload.get("simple") else "off")]
    for name, record in (payload.get("flows") or {}).items():
        lines.append(i18n.get("wizard.status.flow", lang, flow=name,
                              answered=record.get("answered"),
                              remaining=record.get("remaining"),
                              next=record.get("next") or "-",
                              done="done" if record.get("done") else "open"))
        for key, value in (record.get("notes") or {}).items():
            lines.append(f"    {key}: {value}")
    lines.append(str(payload.get("state_file") or ""))
    return "\n".join(lines)


def explain_failure(code: str, config: Config, *, detail: str = "") -> str:
    """Error dictionary line, used by the wizard and publish failure paths."""
    return explain.line(code, config.ui_lang, detail=detail)
