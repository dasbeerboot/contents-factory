"""The error dictionary is a user-facing contract: every code the code can raise has to
have a sentence and an action, in both languages, or the wizard hits a dead end.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from factory import explain
from factory.cli import main
from factory.errors import FactoryError

REPO = Path(__file__).resolve().parent.parent
FACTORY = REPO / "factory"
RAISED = re.compile(r'FactoryError\("([A-Z][A-Z0-9_]+)"')
# Codes that reach the user without being raised: tick decisions, publisher result codes
# and the permission warnings.
EXTRA_USER_FACING: tuple[str, ...] = (
    "NOT_DUE", "ALREADY_PUBLISHED", "DAILY_CAP", "REPEAT_BLOCK", "LATE_CARRIED",
    "LATE_DROPPED", "LATE_CARRY_EXHAUSTED", "LATE_NO_SLOT", "PUBLISHED_FLAG_MISSING",
    "RESUME_CONTAINER", "DRY_RUN", "PUBLISH_FAILED", "PUBLISH_NOT_SENT",
    "PLAYWRIGHT_MISSING", "PLAYWRIGHT_LOAD_FAILED", "PLAYWRIGHT_BROWSER_MISSING",
    "FILE_ACL_UNSET", "FILE_PERMISSION_UNSET", "DIR_PERMISSION_UNSET", "HOME_MISSING",
    "CRONTAB_WRITE_FAILED", "SCHTASKS_WRITE_FAILED", "NOT_IMPLEMENTED",
    "BOXES_REQUIRED", "MEDIA_REQUIRED", "FROZEN_LIMB", "NO_SPEECH",
    "HTTP_401", "HTTP_403", "HTTP_429", "DOCTOR_BLOCKED",
)
# The ones the plan names explicitly (PLAN §9.6 / §9.7), including the token family,
# the duplicate family and the length codes the gate table prints.
NAMED_IN_PLAN: tuple[str, ...] = (
    "ENV_MISSING", "HIGGSFIELD_LOGIN_REQUIRED", "HIGGSFIELD_CLI_MISSING",
    "NAVER_SESSION_MISSING", "PLAYWRIGHT_MISSING", "FFMPEG_MISSING", "NODE_MISSING",
    "BUDGET_EXCEEDED", "SCOPE_MISSING", "CONSENT_DENIED", "TOKEN_REFRESH_FAILED",
    "HTTP_401", "DELIVERY_MAY_BE_PUBLISHED", "DELIVERY_UNCONFIRMED",
    "DUPLICATE_EXACT", "DUPLICATE_NGRAM", "DUPLICATE_SENTENCE", "CHARS_OVER",
    "TEXT_TOO_LONG", "NOT_APPROVED", "NOT_ARMED", "REPEAT_BLOCK", "GATE_DEPS_MISSING",
    "CONNECTOR_ID_MISSING", "BLOG_ID_MISSING",
)


def raised_codes() -> set[str]:
    found: set[str] = set()
    for path in FACTORY.rglob("*.py"):
        found |= set(RAISED.findall(path.read_text(encoding="utf-8")))
    return found


def test_the_dictionary_is_well_over_twenty_entries() -> None:
    assert len(explain.codes("ko")) >= 20
    assert len(explain.codes("ko")) == len(explain.codes("en"))


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_every_raised_code_has_a_human_line_and_an_action(lang: str) -> None:
    missing = sorted(code for code in raised_codes()
                     if not explain.known(code, lang))
    assert missing == [], f"no dictionary entry for: {missing}"
    for code in sorted(raised_codes()):
        row = explain.entry(code, lang)
        assert row["human"].strip(), code
        assert row["action"].strip(), code


@pytest.mark.parametrize("lang", ["ko", "en"])
@pytest.mark.parametrize("code", EXTRA_USER_FACING + NAMED_IN_PLAN)
def test_codes_the_user_sees_without_an_exception_are_covered(code: str,
                                                              lang: str) -> None:
    assert explain.known(code, lang), code
    row = explain.entry(code, lang)
    assert row["human"].strip() and row["action"].strip()


def test_both_languages_hold_the_same_codes_and_the_same_commands() -> None:
    korean, english = explain.dictionary("ko"), explain.dictionary("en")
    assert set(korean) == set(english)
    for code, row in korean.items():
        assert row["command"] == english[code]["command"], code


def factory_subcommands() -> set[str]:
    from factory.cli import build_parser

    for action in build_parser()._actions:  # noqa: SLF001 - argparse has no public read
        if action.dest == "command" and action.choices:
            return set(action.choices)
    raise AssertionError("the parser has no subcommands")


def test_commands_in_the_dictionary_are_real_commands() -> None:
    """A dictionary action that names a command nobody implemented is worse than none."""
    subcommands = factory_subcommands()
    assert "wizard" in subcommands and "explain" in subcommands
    for code, row in explain.dictionary("ko").items():
        command = row["command"]
        if not command or not command.startswith("factory "):
            continue
        head = command.split()[1]
        if head.startswith("-"):
            continue
        assert head in subcommands, f"{code} names unknown command {head}"


def test_an_unknown_code_still_answers_with_something_to_do() -> None:
    row = explain.entry("WHO_KNOWS_WHAT", "ko")
    assert row["known"] is False
    assert "WHO_KNOWS_WHAT" in row["human"]
    assert row["action"] and row["command"] == "factory doctor"


def test_a_non_code_is_refused() -> None:
    with pytest.raises(FactoryError) as error:
        explain.entry("not a code", "ko")
    assert error.value.code == "EXPLAIN_CODE_INVALID"


def test_one_line_form_carries_sentence_action_and_command() -> None:
    line = explain.line("HIGGSFIELD_LOGIN_REQUIRED", "ko")
    assert "higgsfield auth login" in line
    assert "\n" not in line


def test_no_dictionary_entry_leaks_a_secret_shaped_string() -> None:
    for lang in ("ko", "en"):
        body = json.dumps(explain.dictionary(lang), ensure_ascii=False)
        for marker in ("sk-ant", "Bearer ", "eyJ", "hf_"):
            assert marker not in body, marker


def test_cli_explain_prints_the_row_and_can_list_everything(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "ENV_MISSING"]) == 0
    out = capsys.readouterr().out
    assert "ENV_MISSING" in out and "factory key set" in out
    assert main(["explain", "env_missing", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["code"] == "ENV_MISSING" and row["known"] is True
    assert main(["explain", "X", "--list"]) == 0
    assert "NOT_ARMED" in capsys.readouterr().out


def test_a_failing_command_prints_the_dictionary_line_after_the_code(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The failure path the publish and wizard flows share: code, then what to do."""
    assert main(["queue", "approve", "--item", "q_20261015120000_zzzz"]) == 3
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("QUEUE_ITEM_MISSING")
    assert "factory queue list" in err
