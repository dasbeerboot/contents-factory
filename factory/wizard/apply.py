"""What the wizard does with an answer: it runs the ordinary `factory` commands.

Nothing here writes a profile, a brief or a queue file. Every answer lands through the
same argv a person would type, in process, so the wizard can never drift from the CLI
contract and `--json` output can show exactly which commands ran on the user's behalf.

The one exception is `factory.toml`, written through `config.write_keys`, which is that
file's owner.
"""
from __future__ import annotations

import io
import json
import re
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..config import Config, config_targets, write_keys
from ..errors import FactoryError
from ..spec.format import catalogue, format_path, load_format
from ..spec.profile import PLATFORMS
from . import questions, templates

QUEUE_ID = re.compile(r"\bq_\d{14}_[a-z0-9]{4}\b")
BRIEF_ID = re.compile(r"\bb_\d{14}_[A-Za-z0-9]{1,12}\b")
CODE_LINE = re.compile(r"^([A-Z][A-Z0-9_]{1,63})")

# Where each kind goes when the user never said. First enabled platform wins.
PLATFORM_BY_KIND: dict[str, tuple[str, ...]] = {
    "text": ("threads", "instagram", "naver_blog"),
    "image": ("instagram", "threads", "naver_blog", "youtube", "tiktok"),
    "video": ("tiktok", "youtube", "instagram"),
}
# Format per (kind, platform). Never asked: a beginner has no way to choose between
# `og` and `thumbnail`, and both answers are implied by where it is going.
FORMAT_BY_TARGET: dict[tuple[str, str], str] = {
    ("text", "threads"): "threads_post",
    ("text", "instagram"): "ig_caption",
    ("text", "naver_blog"): "blog_long",
    ("text", "tiktok"): "threads_post",
    ("text", "youtube"): "threads_post",
    ("image", "instagram"): "free",
    ("image", "threads"): "free",
    ("image", "naver_blog"): "og",
    ("image", "youtube"): "thumbnail",
    ("image", "tiktok"): "thumbnail",
    ("video", "tiktok"): "textcard",
    ("video", "youtube"): "textcard",
    ("video", "instagram"): "textcard",
    ("video", "threads"): "textcard",
    ("video", "naver_blog"): "textcard",
}
# Rough wall clock for the cost note. The credit figure comes from the format itself.
RENDER_MINUTES = "2~5"


@dataclass
class Ran:
    """One `factory ...` invocation the wizard made for the user."""

    argv: tuple[str, ...]
    code: int
    out: str = ""
    err: str = ""

    @property
    def command(self) -> str:
        # Quoted so the line can be pasted back into a terminal: half of these carry a
        # sentence the user typed, and an unquoted sentence is a different command.
        return "factory " + " ".join(shlex.quote(part) for part in self.argv)

    def error_code(self) -> str:
        match = CODE_LINE.match(self.err.strip())
        return match.group(1) if match else ""


@dataclass
class Result:
    ran: list[Ran] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)

    def commands(self) -> list[str]:
        return [entry.command for entry in self.ran]


def run_cli(argv: Sequence[str], *, stdin: str | None = None,
            check: bool = True) -> Ran:
    """Run one factory command in this process and keep its output.

    `cli.main` is imported late: the CLI imports the wizard to register its parser, so
    importing it at module scope would be a cycle.
    """
    from .. import cli  # noqa: PLC0415 - deliberate late import, see docstring

    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
    finally:
        sys.stdin = saved
    entry = Ran(tuple(argv), int(code), out.getvalue(), err.getvalue())
    if check and entry.code != 0:
        raise FactoryError(entry.error_code() or "UNEXPECTED", entry.argv[0])
    return entry


# --- setup ---------------------------------------------------------------------

def record_os(config: Config, os_name: str, result: Result) -> None:
    result.ran.append(run_cli(["init", "--os", os_name]))


def record_config(config: Config, values: dict[str, object], result: Result) -> None:
    for path in config_targets(config):
        write_keys(path, values)
    result.notes["config"] = ",".join(sorted(values))


def create_profile(config: Config, *, name: str, intro: str, tone: str,
                   result: Result) -> str:
    """`profile add` then `profile template`: the two commands, in that order."""
    existing = run_cli(["profile", "list"])
    result.ran.append(existing)
    taken = [entry for entry in existing.out.split() if entry]
    profile = questions.profile_slug(name, taken)
    if profile not in taken:
        result.ran.append(run_cli(["profile", "add", profile]))
    result.ran.append(run_cli(["profile", "template", "--profile", profile,
                               "--brand", name, "--intro", intro, "--tone", tone]))
    result.notes["profile"] = profile
    return profile


def enable_platforms(config: Config, *, profile: str, platforms: Sequence[str],
                     slots: Sequence[str], result: Result) -> None:
    for platform in [name for name in PLATFORMS if name in platforms]:
        argv = ["profile", "platform", "--profile", profile, "--platform", platform,
                "--enable"]
        for slot in slots:
            argv.extend(["--slot", slot])
        result.ran.append(run_cli(argv))
    result.notes["slots"] = ",".join(slots)


def slots_for_answer(answer: str, custom: str) -> list[str]:
    """The named slot answers, converted from local wall clock to UTC."""
    if answer == "custom":
        return questions.parse_local_times(custom)
    hour, minute = questions.SLOT_HOURS.get(answer, (12, 0))
    return [questions.to_utc_slot(hour, minute)]


# --- make ----------------------------------------------------------------------

def enabled_platforms(config: Config, profile: str) -> list[str]:
    shown = run_cli(["profile", "show", profile])
    try:
        data = json.loads(shown.out)
    except ValueError:
        raise FactoryError("PROFILE_INVALID", profile[:32]) from None
    blocks = data.get("platforms") if isinstance(data, dict) else {}
    return [name for name in PLATFORMS
            if isinstance(blocks, dict) and bool((blocks.get(name) or {}).get("enabled"))]


def pick_platform(kind: str, enabled: Sequence[str]) -> str:
    for platform in PLATFORM_BY_KIND.get(kind, ()):
        if platform in enabled:
            return platform
    if enabled:
        return enabled[0]
    raise FactoryError("WIZARD_NO_PLATFORM", kind[:16])


def pick_format(kind: str, platform: str) -> str:
    """The format for this kind and platform. Never a question to the user.

    The table is the answer when that format exists. It can be absent (a trimmed
    formats directory), so the fallback is any format in the catalogue that names this
    platform, then `free`, then whatever is there.
    """
    named = FORMAT_BY_TARGET.get((kind, platform))
    if named and format_path(kind, named).is_file():
        return named
    available = catalogue(kind).get(kind, [])
    for name in available:
        try:
            spec = load_format(kind, name)
        except FactoryError:
            continue
        if platform in spec.platforms:
            return name
    if "free" in available:
        return "free"
    if available:
        return available[0]
    raise FactoryError("FORMAT_MISSING", f"{kind}/{platform}")


def credit_note(kind: str, format_name: str) -> int:
    if kind == "text":
        return 0
    try:
        return int(load_format(kind, format_name).credit_cap_per_item)
    except FactoryError:
        return 0


def make_brief(config: Config, *, kind: str, format_name: str, profile: str,
               platform: str, topic: str, references: Sequence[str],
               result: Result) -> str:
    answers = {"platforms": [platform],
               "prompt_draft": topic,
               "prompt_final": topic,
               "references": [reference for reference in references if reference],
               "answers": {"topic": topic}}
    ran = run_cli(["brief", "new", "--kind", kind, "--format", format_name,
                   "--profile", profile, "--answers", "-"],
                  stdin=json.dumps(answers, ensure_ascii=False))
    result.ran.append(ran)
    match = BRIEF_ID.search(ran.out)
    if not match:
        raise FactoryError("BRIEF_MISSING", "new")
    result.notes["brief"] = match.group(0)
    return match.group(0)


def revise_brief(config: Config, *, brief_id: str, correction: str,
                 result: Result) -> None:
    """A "고쳐줘" answer edits `prompt_final` and keeps the brief, per the skill rules."""
    ran = run_cli(["brief", "revise", "--brief", brief_id, "--note", correction])
    result.ran.append(ran)
    result.notes["revised"] = brief_id


def generate(config: Config, *, kind: str, brief_id: str, dry_run: bool,
             result: Result) -> str:
    argv = ["gen", kind, "--brief", brief_id]
    if dry_run:
        argv.append("--dry-run")
    ran = run_cli(argv, check=not dry_run)
    result.ran.append(ran)
    if dry_run:
        return ""
    return chosen_text(config, brief_id)


def chosen_text(config: Config, brief_id: str) -> str:
    from ..spec.brief import load_brief  # noqa: PLC0415 - late, see run_cli

    brief = load_brief(config.home_path, brief_id)
    for output in brief.outputs:
        if output.text:
            return output.text
        if output.path:
            return output.path
    return ""


@dataclass
class Gates:
    """One gate run: whether it passed, the table to show, and the codes that caught."""

    ok: bool
    table: str
    failures: tuple[str, ...] = ()

    def first_failure(self) -> str:
        return self.failures[0] if self.failures else ""


def gate(config: Config, *, brief_id: str = "", text: str = "", profile: str = "",
         format_name: str = "", kind: str = "text", result: Result) -> Gates:
    """Run the gates once and get both forms.

    `--json` rather than the table: the media gates transcribe and sample frames, so
    running them twice to get a readable table and a parseable code is a real cost. The
    table is rendered here with the same renderer the CLI uses.
    """
    from ..review import terminal  # noqa: PLC0415 - late, see run_cli

    argv = ["gate", "run", "--json"]
    if brief_id:
        argv.extend(["--brief", brief_id])
    else:
        argv.extend(["--text", text, "--profile", profile, "--kind", kind])
        if format_name:
            argv.extend(["--format", format_name])
    ran = run_cli(argv, check=False)
    result.ran.append(ran)
    try:
        rows = json.loads(ran.out or "[]")
    except ValueError:
        rows = []
    if not isinstance(rows, list):
        rows = []
    results = [(str(row.get("gate")), str(row.get("status")), str(row.get("detail")))
               for row in rows if isinstance(row, dict)]
    failures = tuple(f"{name}: {detail}" if detail else name
                     for name, status, detail in results if status == terminal.FAIL)
    return Gates(ok=ran.code == 0,
                 table=terminal.gate_table(results, lang=config.ui_lang),
                 failures=failures)


def queue_add(config: Config, *, profile: str, platform: str, kind: str,
              brief_id: str, text: str, slot: str, result: Result) -> str:
    argv = ["queue", "add", "--platform", platform, "--profile", profile,
            "--kind", kind, "--slot", slot]
    if brief_id and not text:
        argv.extend(["--brief", brief_id, "--output", "0"])
    else:
        argv.extend(["--text", text])
    ran = run_cli(argv)
    result.ran.append(ran)
    match = QUEUE_ID.search(ran.out)
    if not match:
        raise FactoryError("QUEUE_ITEM_MISSING", "add")
    result.notes["item"] = match.group(0)
    return match.group(0)


def approve_and_arm(config: Config, *, item_id: str, reason: str,
                    result: Result) -> None:
    """Simple mode only. The reason is the user's own words, never invented here."""
    result.ran.append(run_cli(["queue", "approve", "--item", item_id]))
    result.ran.append(run_cli(["queue", "arm", "--item", item_id,
                               "--reason", reason]))


def slot_value(answer: str, *, now: datetime | None = None) -> str:
    """`now` becomes this minute in UTC; the tick's 15 minute grace picks it up."""
    if answer == "next":
        return "next"
    if answer.startswith("date:"):
        return answer.split(":", 1)[1]
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def key_names(target: str) -> tuple[str, ...]:
    entry = questions.CONNECTS.get(target)
    return entry.keys if entry else ()


def profile_template_files(name: str, intro: str, tone: str) -> dict[str, str]:
    return templates.files(name, intro, tone)


def write_template(directory: Path, name: str, intro: str, tone: str) -> list[str]:
    """Write persona.md, writer_rules.md and brand.md into an existing profile."""
    if not directory.is_dir():
        raise FactoryError("PROFILE_MISSING", directory.name)
    written: list[str] = []
    for filename, body in profile_template_files(name, intro, tone).items():
        (directory / filename).write_text(body, encoding="utf-8")
        written.append(filename)
    return sorted(written)


# --- the phone, when it is wired up --------------------------------------------

PHONE_EDIT_KEY = "edit_request"
PHONE_ARM_PREFIX = "telegram:"
PHONE_REJECT_CODE = "TELEGRAM_REJECTED"


def telegram_live(config: Config) -> bool:
    """`review = "telegram"` and a token and a chat actually exist.

    Both halves matter. A home with `review = "telegram"` and no bot would otherwise
    make every draft step fail on a send, and a home with a bot but `review =
    "terminal"` asked not to be buzzed.
    """
    if config.review != "telegram":
        return False
    from ..credentials import load_profile_env  # noqa: PLC0415 - late, see run_cli
    from ..telegram import setup as telegram_setup  # noqa: PLC0415

    try:
        env = load_profile_env(config.home_path, config.default_profile)
        state = telegram_setup.status(config.home_path,
                                      str(env.get(telegram_setup.TOKEN_KEY, "")))
    except Exception:  # noqa: BLE001 - an unreadable phone channel is just "no phone"
        return False
    return bool(state.get("token")) and bool(state.get("chat_id"))


def preview_draft(config: Config, *, brief_id: str, output: int = 0,
                  result: Result) -> str:
    """Show the draft on the phone. Returns "" or the code that stopped it.

    Never raises: the terminal already has the draft, and a phone that is out of
    signal must not end the flow.
    """
    ran = run_cli(["telegram", "preview", "--brief", brief_id,
                   "--output", str(output)], check=False)
    result.ran.append(ran)
    return "" if ran.code == 0 else (ran.error_code() or "TELEGRAM_UNREACHABLE")


def preview_item(config: Config, *, item_id: str, result: Result) -> str:
    """Show the queued item on the phone, with the three buttons. Never raises."""
    ran = run_cli(["telegram", "preview", "--item", item_id], check=False)
    result.ran.append(ran)
    return "" if ran.code == 0 else (ran.error_code() or "TELEGRAM_UNREACHABLE")


def phone_answer(config: Config, *, item_id: str) -> tuple[str, str]:
    """What the phone has already decided about this item: ("", "") when nothing.

    ("fix", text)  a correction was typed
    ("no", "")     the reject button was pressed
    ("ok", "")     the publish button was pressed, recognisable by the arm reason
    """
    from ..queue.store import QueueStore  # noqa: PLC0415 - late, see run_cli

    try:
        item = QueueStore(config.home_path).get(item_id)
    except FactoryError:
        return "", ""
    correction = str(item.extra.get(PHONE_EDIT_KEY) or "").strip()
    if correction:
        return "fix", correction
    if item.last_error == PHONE_REJECT_CODE and not item.approved:
        return "no", ""
    if item.armed and str(item.arm_reason or "").startswith(PHONE_ARM_PREFIX):
        return "ok", ""
    return "", ""


def clear_phone_edit(config: Config, *, item_id: str, result: Result) -> bool:
    """Consume the correction so the same one is not applied twice.

    A published item cannot be edited, so the field stays and the caller stops; that
    is why this answers with a bool instead of raising.
    """
    from ..queue.store import QueueStore  # noqa: PLC0415 - late, see run_cli

    store = QueueStore(config.home_path)
    try:
        store.set_extra(item_id, PHONE_EDIT_KEY, "")
    except FactoryError as error:
        result.notes["phone_clear_failed"] = error.code
        return False
    return True
