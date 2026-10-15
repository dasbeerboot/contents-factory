"""`factory start` is what the desktop launcher runs, and the two agent files are the
only instructions a fresh agent gets. Both are tested as contracts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory import start
from factory.cli import main
from factory.config import Config, load_config
from factory.errors import FactoryError

REPO = Path(__file__).resolve().parent.parent
CLAUDE = REPO / "CLAUDE.md"
AGENTS = REPO / "AGENTS.md"


def config_for(home: Path, **overrides: object) -> Config:
    data: dict[str, object] = {"home": str(home), "ui_lang": "en"}
    data.update(overrides)
    return Config(**data)


def which_only(*names: str):
    allowed = set(names)
    return lambda name: f"/usr/local/bin/{name}" if name in allowed else None


# --- agent detection -----------------------------------------------------------

def test_one_installed_agent_is_used_without_asking(isolated_home: Path) -> None:
    agent, fresh = start.choose_agent(config_for(isolated_home),
                                      found=start.installed(which_only("codex")))
    assert agent == "codex" and fresh is False


def test_neither_agent_installed_says_how_to_get_one(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        start.choose_agent(config_for(isolated_home), found=[])
    assert error.value.code == "AGENT_MISSING"
    from factory import explain

    assert "@anthropic-ai/claude-code" in explain.entry("AGENT_MISSING", "ko")["command"]


def test_both_installed_follows_the_recorded_answer(isolated_home: Path) -> None:
    config = config_for(isolated_home, agent="codex")
    agent, fresh = start.choose_agent(config, found=["claude", "codex"])
    assert agent == "codex" and fresh is False


def test_both_installed_and_nothing_recorded_asks_once_and_writes_it(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return "claude"

    target = start.plan(config, found=["claude", "codex"], ask=ask)
    assert target.agent == "claude" and target.recorded is True
    assert len(asked) == 1
    assert load_config().agent == "claude"
    # Second time the answer is on disk, so no question.
    again = start.plan(load_config(), found=["claude", "codex"], ask=ask)
    assert again.agent == "claude" and again.recorded is False
    assert len(asked) == 1


def test_both_installed_without_a_way_to_ask_refuses_to_guess(
        isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        start.choose_agent(config_for(isolated_home), found=["claude", "codex"])
    assert error.value.code == "AGENT_AMBIGUOUS"


def test_an_unknown_requested_agent_is_refused(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        start.choose_agent(config_for(isolated_home), requested="cursor",
                           found=["claude"])
    assert error.value.code == "AGENT_UNKNOWN"


def test_each_agent_opens_interactively_on_the_wizard() -> None:
    assert start.argv_for("claude") == ("claude", "/wizard")
    # codex has no slash commands, so it gets the sentence AGENTS.md teaches it to act
    # on. `codex exec` is not used: it answers once and exits.
    assert start.argv_for("codex")[0] == "codex"
    assert "factory wizard" in start.argv_for("codex")[1]
    assert "exec" not in start.argv_for("codex")


# --- the command ---------------------------------------------------------------

def test_start_stops_on_a_blocking_doctor_row(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(start, "doctor_blockers",
                        lambda config, report=None: (["ffmpeg missing"], "table"))
    assert main(["start"]) == 3
    out = capsys.readouterr().out
    assert "ffmpeg missing" in out
    assert "factory doctor" in out, "the dictionary line names the next command"


def test_start_no_launch_prints_the_command_and_runs_nothing(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(start, "doctor_blockers",
                        lambda config, report=None: ([], "table"))
    monkeypatch.setattr(start, "installed", lambda which=None: ["claude"])
    monkeypatch.setattr(start, "_subprocess_launcher",
                        lambda argv, directory: launched.append(tuple(argv)) or 0)
    assert main(["start", "--no-launch"]) == 0
    out = capsys.readouterr().out
    assert "claude /wizard" in out
    assert launched == []


def test_start_launches_the_chosen_agent_in_the_repo(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[tuple[tuple[str, ...], Path]] = []

    def launcher(argv, directory):
        seen.append((tuple(argv), Path(directory)))
        return 0

    monkeypatch.setattr(start, "doctor_blockers",
                        lambda config, report=None: ([], "table"))
    monkeypatch.setattr(start, "installed", lambda which=None: ["claude", "codex"])
    monkeypatch.setattr(start, "_subprocess_launcher", launcher)
    assert main(["start", "--agent", "codex"]) == 0
    capsys.readouterr()
    assert seen[0][0] == start.argv_for("codex")
    assert (seen[0][1] / "pyproject.toml").is_file()


def test_a_launcher_that_blows_up_tells_the_user_to_paste_the_command(
        isolated_home: Path) -> None:
    def launcher(argv, directory):
        raise OSError("no pty")

    target = start.Plan("claude", start.argv_for("claude"), REPO)
    with pytest.raises(FactoryError) as error:
        start.launch(target, launcher=launcher)
    assert error.value.code == "AGENT_LAUNCH_FAILED"


# --- the two agent files -------------------------------------------------------

def test_claude_md_and_agents_md_are_the_same_file() -> None:
    """Two agents, one contract. A drift between them is a drift in behaviour."""
    assert CLAUDE.is_file() and AGENTS.is_file()
    assert CLAUDE.read_text(encoding="utf-8") == AGENTS.read_text(encoding="utf-8")


def test_the_agent_contract_is_short_enough_to_be_read() -> None:
    assert len(CLAUDE.read_text(encoding="utf-8").splitlines()) <= 60


@pytest.mark.parametrize("phrase", [
    "factory wizard next",
    "factory wizard answer --id",
    "factory explain",
    "~/.contents-factory/",
    "simple = true",
])
def test_the_agent_contract_names_the_loop_and_the_limits(phrase: str) -> None:
    assert phrase in CLAUDE.read_text(encoding="utf-8"), phrase


def test_the_agent_contract_is_written_in_both_languages() -> None:
    body = CLAUDE.read_text(encoding="utf-8")
    assert "금지" in body and "Never" in body
    assert "루프" in body and "loop" in body.lower()


def test_the_agent_contract_bans_inventing_and_hand_writing() -> None:
    body = CLAUDE.read_text(encoding="utf-8")
    for phrase in ("invent a question", "질문을 지어내지 마라", "Never read a credential",
                   "순서를 바꾸거나"):
        assert phrase in body, phrase
