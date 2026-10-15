"""`factory start`: the one thing the desktop launcher runs.

Three steps, in this order:
  1. `factory doctor`. A blocking row means the install line is the answer, not a flow.
  2. find the agent. `claude` or `codex` on PATH; both installed and no recorded answer
     is a question asked once and written to factory.toml.
  3. launch it in the repo directory with an opening prompt, so the first thing the user
     sees is the wizard's first question rather than an empty agent.

The agent is the entrance (PLAN §9.8). This file knows nothing about what the wizard
asks; it only gets a conversation open in the right directory.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import doctor as doctor_module
from . import i18n, osinfo
from .config import Config, config_targets, repo_root, write_keys
from .errors import FactoryError

AGENTS: tuple[str, ...] = ("claude", "codex")
# Opening prompts. Claude Code resolves a slash command; codex has no skill mechanism,
# so it gets the plain sentence that AGENTS.md teaches it to act on.
PROMPTS: dict[str, str] = {"claude": "/wizard",
                           "codex": "factory wizard를 시작해"}
INSTALL_HINT = "npm i -g @anthropic-ai/claude-code"

Launcher = Callable[[Sequence[str], Path], int]
Asker = Callable[[str], str]


@dataclass(frozen=True)
class Plan:
    agent: str
    argv: tuple[str, ...]
    directory: Path
    recorded: bool = False

    @property
    def command(self) -> str:
        # Quoted: the codex prompt is a sentence, and an unquoted sentence pasted into a
        # terminal becomes several arguments.
        return " ".join(shlex.quote(part) for part in self.argv)


def installed(which: Callable[[str], str | None] | None = None) -> list[str]:
    find = which or osinfo.which
    return [name for name in AGENTS if find(name)]


def prompt_for(agent: str) -> str:
    if agent not in PROMPTS:
        raise FactoryError("AGENT_UNKNOWN", str(agent)[:32])
    return PROMPTS[agent]


def argv_for(agent: str) -> tuple[str, ...]:
    """`claude "/wizard"` / `codex "factory wizard를 시작해"`.

    Both CLIs take a positional prompt and open an interactive session with it, which is
    what this needs: `codex exec` would answer once and exit, leaving nobody to read the
    second question.
    """
    return (agent, prompt_for(agent))


def choose_agent(config: Config, *, requested: str | None = None,
                 found: Sequence[str] | None = None,
                 ask: Asker | None = None) -> tuple[str, bool]:
    """(agent, whether the answer is new). Raises when there is nothing to launch."""
    available = list(found if found is not None else installed())
    if requested:
        if requested not in AGENTS:
            raise FactoryError("AGENT_UNKNOWN", requested[:32])
        return requested, requested != config.agent
    if not available:
        raise FactoryError("AGENT_MISSING", ",".join(AGENTS))
    if len(available) == 1:
        return available[0], False
    if config.agent in available:
        return config.agent, False
    if ask is None:
        raise FactoryError("AGENT_AMBIGUOUS", ",".join(available))
    answer = str(ask(i18n.get("start.agent.ask", config.ui_lang,
                              agents=", ".join(available)))).strip().lower()
    for name in available:
        if answer == name or (answer and name.startswith(answer)):
            return name, True
    raise FactoryError("AGENT_UNKNOWN", answer[:32] or "empty")


def record_agent(config: Config, agent: str) -> None:
    for path in config_targets(config):
        write_keys(path, {"agent": agent})


def plan(config: Config, *, requested: str | None = None,
         found: Sequence[str] | None = None, ask: Asker | None = None,
         record: bool = True) -> Plan:
    agent, fresh = choose_agent(config, requested=requested, found=found, ask=ask)
    if fresh and record:
        record_agent(config, agent)
    return Plan(agent=agent, argv=argv_for(agent), directory=repo_root(),
                recorded=fresh and record)


def doctor_blockers(config: Config,
                    report: Callable[[Config], dict[str, object]] | None = None,
                    ) -> tuple[list[str], str]:
    """Blocking rows plus the rendered table, so start can show what doctor showed."""
    data = (report or doctor_module.report)(config)
    return doctor_module.blockers(data), doctor_module.render(data)


def launch(target: Plan, *, launcher: Launcher | None = None) -> int:
    run = launcher or _subprocess_launcher
    try:
        return int(run(target.argv, target.directory))
    except FactoryError:
        raise
    except Exception:  # noqa: BLE001 - the printed command is still usable by hand
        raise FactoryError("AGENT_LAUNCH_FAILED", target.agent) from None


def _subprocess_launcher(argv: Sequence[str], directory: Path) -> int:
    import subprocess  # noqa: PLC0415 - only the real launch needs this

    done = subprocess.run(list(argv), cwd=str(directory), check=False)
    return done.returncode
