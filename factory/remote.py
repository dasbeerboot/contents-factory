"""Always-on agent remote control, so the phone can drive the PC.

PLAN §9.8: the entry point is the agent, not Telegram. Both supported agents can
expose a session running on this machine to their phone app, and both want the
same thing from us — a process that is up whenever the user is logged in:

* **Claude Code** — `claude remote-control --name contents-factory --spawn same-dir`
  is a long-running foreground process, so it is registered with KeepAlive and
  restarted whenever it dies.
* **Codex** — `codex remote-control start` (`codex-cli` 0.154, still labelled
  experimental) starts a *shared app-server daemon* and returns. KeepAlive on a
  command that exits on success is a restart loop, so the codex registration is
  run-at-logon only. Pairing is a separate one-time human step: a QR scan from
  the ChatGPT desktop app, or `codex remote-control pair` for the manual code.
  Codex remote hosts are macOS and Windows only.

**Workspace trust is a gate, not a file we edit.** An agent that has never been
trusted in this directory will sit at a prompt no one can see, so install checks
for a recorded trust decision and refuses with `*_WORKSPACE_UNTRUSTED` when it is
absent. The fix is always the human opening the agent in the folder once —
forging the trust record would be answering a security question on the user's
behalf.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import repo_root
from .errors import FactoryError
from .osinfo import detect_os, home_dir, normalize, which

SESSION_NAME = "contents-factory"
RUNNERS: tuple[str, ...] = ("launchd", "schtasks", "systemd", "cron")
# What a launchd agent gets for PATH is almost nothing, and the agent has to be
# able to reach uv, node and ffmpeg. The resolved binary's own directory is
# prepended by `environment()`.
BASE_PATH: tuple[str, ...] = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
                              "/bin", "/usr/sbin", "/sbin")
THROTTLE_SECONDS = 15
RESTART_INTERVAL = "PT1M"
RESTART_COUNT = 99
PROBE_TIMEOUT = 120


@dataclass(frozen=True)
class Agent:
    """One agent's remote-control surface. A new agent is a row, not a branch."""

    name: str
    binary: str
    arguments: tuple[str, ...]
    # False when the command daemonises and exits: KeepAlive would then restart
    # it forever. Codex is that case.
    keep_alive: bool
    label: str
    task: str
    unit: str
    log: str
    trust_code: str
    missing_code: str
    # Hosts the vendor supports for remote control. Registering elsewhere is
    # allowed but reported, because "it installed and nothing connects" is worse
    # than a warning.
    supported_os: tuple[str, ...]
    pairing: str = ""


AGENTS: dict[str, Agent] = {
    "claude": Agent(
        name="claude",
        binary="claude",
        arguments=("remote-control", "--name", SESSION_NAME, "--spawn", "same-dir"),
        keep_alive=True,
        label="com.contents-factory.claude-remote-control",
        task="contents-factory-remote-claude",
        unit="contents-factory-remote-claude",
        log="contents-factory-remote.log",
        trust_code="CLAUDE_WORKSPACE_UNTRUSTED",
        missing_code="CLAUDE_CLI_MISSING",
        supported_os=("macos", "windows", "linux"),
    ),
    "codex": Agent(
        name="codex",
        binary="codex",
        arguments=("remote-control", "start"),
        keep_alive=False,
        label="com.contents-factory.codex-remote-control",
        task="contents-factory-remote-codex",
        unit="contents-factory-remote-codex",
        log="contents-factory-remote-codex.log",
        trust_code="CODEX_WORKSPACE_UNTRUSTED",
        missing_code="CODEX_CLI_MISSING",
        supported_os=("macos", "windows"),
        pairing="codex remote-control pair",
    ),
}
AGENT_CHOICES: tuple[str, ...] = tuple(AGENTS)


def agent_spec(name: str | None) -> Agent:
    key = str(name or "claude").strip().lower()
    spec = AGENTS.get(key)
    if spec is None:
        raise FactoryError("REMOTE_AGENT_UNKNOWN", key[:32])
    return spec


def detect_agents() -> list[str]:
    """Which agents are installed, in preference order."""
    return [name for name, spec in AGENTS.items() if which(spec.binary)]


def runner_for(os_name: str | None = None) -> str:
    """The always-on mechanism this host has. Linux prefers systemd user units."""
    target = normalize(os_name) if os_name else detect_os()
    if target == "macos":
        return "launchd"
    if target == "windows":
        return "schtasks"
    return "systemd" if which("systemctl") else "cron"


def resolve_binary(spec: Agent, *, binary: str | None = None) -> str:
    found = binary or which(spec.binary)
    if not found:
        raise FactoryError(spec.missing_code, spec.binary)
    return found


def command(spec: Agent, *, binary: str | None = None) -> list[str]:
    return [resolve_binary(spec, binary=binary), *spec.arguments]


def log_path(spec: Agent, *, os_name: str | None = None,
             home: Path | None = None) -> Path:
    """macOS keeps agent logs in ~/Library/Logs; elsewhere the runtime home does."""
    target = normalize(os_name) if os_name else detect_os()
    if target == "macos":
        return home_dir() / "Library" / "Logs" / spec.log
    base = Path(home) if home else home_dir() / ".contents-factory"
    return base / "logs" / spec.log


def environment(binary: str) -> dict[str, str]:
    """PATH wide enough for the agent to call uv, node and ffmpeg. No secrets."""
    parts = [str(Path(binary).parent), str(home_dir() / ".local" / "bin"),
             *BASE_PATH]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return {"PATH": os.pathsep.join(seen), "HOME": str(home_dir())}


# --------------------------------------------------------------- macOS: launchd

def plist_path(spec: Agent) -> Path:
    return home_dir() / "Library" / "LaunchAgents" / f"{spec.label}.plist"


def definition(spec: Agent, *, repo: Path, binary: str | None = None,
               home: Path | None = None) -> dict[str, object]:
    resolved = resolve_binary(spec, binary=binary)
    log = log_path(spec, os_name="macos", home=home)
    data: dict[str, object] = {
        "Label": spec.label,
        "ProgramArguments": [resolved, *spec.arguments],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": environment(resolved),
    }
    if spec.keep_alive:
        data["KeepAlive"] = True
        data["ThrottleInterval"] = THROTTLE_SECONDS
    return data


def render_plist(data: dict[str, object]) -> bytes:
    return plistlib.dumps(data, sort_keys=True)


# ------------------------------------------------------- Windows: Task Scheduler
# `schtasks /Create /SC ONLOGON` cannot express "restart on failure", so the task
# is registered from XML, which can.

def task_action(spec: Agent, *, repo: Path, binary: str | None = None,
                home: Path | None = None) -> str:
    resolved = resolve_binary(spec, binary=binary)
    log = log_path(spec, os_name="windows", home=home)
    arguments = " ".join(_quote(part) for part in spec.arguments)
    return (f'/c cd /d "{repo}" && "{resolved}" {arguments} '
            f'>> "{log}" 2>&1')


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def task_xml(spec: Agent, *, repo: Path, binary: str | None = None,
             home: Path | None = None, user: str | None = None) -> str:
    """Logon trigger plus restart-on-failure. Highest privileges are not asked for."""
    account = user or os.environ.get("USERNAME") or ""
    arguments = _xml_escape(task_action(spec, repo=repo, binary=binary, home=home))
    restart = ("" if not spec.keep_alive else
               f"    <RestartOnFailure>\n"
               f"      <Interval>{RESTART_INTERVAL}</Interval>\n"
               f"      <Count>{RESTART_COUNT}</Count>\n"
               f"    </RestartOnFailure>\n")
    # StopIfGoingOnBatteries/DisallowStartIfOnBatteries default to true, which
    # would silently stop an always-on task the moment the laptop unplugs.
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.3" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>contents-factory remote control "
        f"({spec.name})</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"{_user_id(account)}"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"{_principal_user(account)}"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        f"{restart}"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>cmd.exe</Command>\n"
        f"      <Arguments>{arguments}</Arguments>\n"
        f"      <WorkingDirectory>{_xml_escape(str(repo))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _user_id(account: str) -> str:
    return f"      <UserId>{_xml_escape(account)}</UserId>\n" if account else ""


def _principal_user(account: str) -> str:
    return f"      <UserId>{_xml_escape(account)}</UserId>\n" if account else ""


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def task_create_command(spec: Agent, xml_file: Path) -> list[str]:
    return ["schtasks", "/Create", "/TN", spec.task, "/XML", str(xml_file), "/F"]


def task_delete_command(spec: Agent) -> list[str]:
    return ["schtasks", "/Delete", "/TN", spec.task, "/F"]


def task_query_command(spec: Agent) -> list[str]:
    return ["schtasks", "/Query", "/TN", spec.task, "/FO", "LIST"]


# -------------------------------------------------------- Linux: systemd or cron

def unit_path(spec: Agent) -> Path:
    return home_dir() / ".config" / "systemd" / "user" / f"{spec.unit}.service"


def unit_text(spec: Agent, *, repo: Path, binary: str | None = None,
              home: Path | None = None) -> str:
    resolved = resolve_binary(spec, binary=binary)
    log = log_path(spec, os_name="linux", home=home)
    env = environment(resolved)
    restart = "always" if spec.keep_alive else "no"
    kind = "simple" if spec.keep_alive else "oneshot"
    return (
        "[Unit]\n"
        f"Description=contents-factory remote control ({spec.name})\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        f"Type={kind}\n"
        f"WorkingDirectory={repo}\n"
        f"ExecStart={resolved} {' '.join(spec.arguments)}\n"
        f"Restart={restart}\n"
        f"RestartSec={THROTTLE_SECONDS}\n"
        f"Environment=PATH={env['PATH']}\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


CRON_BEGIN = "# >>> contents-factory remote >>>"
CRON_END = "# <<< contents-factory remote <<<"


def cron_line(spec: Agent, *, repo: Path, binary: str | None = None,
              home: Path | None = None) -> str:
    resolved = resolve_binary(spec, binary=binary)
    log = log_path(spec, os_name="linux", home=home)
    arguments = " ".join(spec.arguments)
    return (f"@reboot cd {repo} && {resolved} {arguments} >> {log} 2>&1")


def cron_block(spec: Agent, *, repo: Path, binary: str | None = None,
               home: Path | None = None) -> str:
    return "\n".join([CRON_BEGIN,
                      cron_line(spec, repo=repo, binary=binary, home=home),
                      CRON_END])


def cron_strip(current: str) -> str:
    out: list[str] = []
    inside = False
    for entry in current.splitlines():
        if entry.strip() == CRON_BEGIN:
            inside = True
            continue
        if entry.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            out.append(entry)
    return "\n".join(out).strip()


def cron_merge(current: str, spec: Agent, *, repo: Path,
               binary: str | None = None, home: Path | None = None) -> str:
    base = cron_strip(current)
    body = cron_block(spec, repo=repo, binary=binary, home=home)
    return f"{base}\n{body}\n" if base else f"{body}\n"


# ------------------------------------------------------------- workspace trust
# Read-only. These files record a human's security answer and are never written
# from here.

def claude_config_path() -> Path:
    return home_dir() / ".claude.json"


def codex_config_path() -> Path:
    return home_dir() / ".codex" / "config.toml"


def claude_trusted(repo: Path) -> bool:
    try:
        data = json.loads(claude_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return False
    entry = projects.get(str(repo))
    return bool(isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"))


def codex_trusted(repo: Path) -> bool:
    """Codex records `[projects."<path>"] trust_level`, and a parent counts."""
    try:
        text = codex_config_path().read_text(encoding="utf-8")
    except OSError:
        return False
    target = Path(repo).resolve()
    candidates = {str(target), *(str(parent) for parent in target.parents)}
    for match in re.finditer(r'^\[projects\."([^"]+)"\]\s*$(.*?)(?=^\[|\Z)', text,
                             re.M | re.S):
        path, body = match.group(1), match.group(2)
        if path in candidates and re.search(r'trust_level\s*=\s*"trusted"', body):
            return True
    return False


def trusted(spec: Agent, repo: Path) -> bool:
    return codex_trusted(repo) if spec.name == "codex" else claude_trusted(repo)


def probe_trust(spec: Agent, repo: Path, *, binary: str | None = None,
                runner: Callable[[Sequence[str], Path], int] | None = None) -> bool:
    """Run the agent once in the repo so it can record its own trust decision.

    Only Claude Code has a cheap non-mutating probe (`claude --print "ok"`).
    Codex's equivalent would start a real turn, so its trust is read, not probed.
    """
    if trusted(spec, repo):
        return True
    if spec.name != "claude":
        return False
    resolved = resolve_binary(spec, binary=binary)
    call = runner or _run_probe
    try:
        call([resolved, "--print", "ok"], repo)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return trusted(spec, repo)


def _run_probe(arguments: Sequence[str], cwd: Path) -> int:
    done = subprocess.run(list(arguments), cwd=str(cwd), capture_output=True,
                          text=True, timeout=PROBE_TIMEOUT, check=False)
    return done.returncode


# -------------------------------------------------------------------- lifecycle

def plan(spec: Agent, *, repo: Path, runner: str, binary: str | None = None,
         home: Path | None = None, user: str | None = None) -> dict[str, object]:
    """Everything install would write, without writing it. This is `--dry-run`."""
    resolved = resolve_binary(spec, binary=binary)
    base: dict[str, object] = {
        "agent": spec.name,
        "runner": runner,
        "repo": str(repo),
        "command": [resolved, *spec.arguments],
        "keep_alive": spec.keep_alive,
        "log": str(log_path(spec, os_name=_os_for(runner), home=home)),
        "pairing": spec.pairing,
    }
    if runner == "launchd":
        base["target"] = str(plist_path(spec))
        base["document"] = render_plist(
            definition(spec, repo=repo, binary=resolved, home=home)).decode("utf-8")
    elif runner == "schtasks":
        base["target"] = spec.task
        base["document"] = task_xml(spec, repo=repo, binary=resolved, home=home,
                                    user=user)
        base["register"] = task_create_command(spec, Path("<temp>.xml"))
    elif runner == "systemd":
        base["target"] = str(unit_path(spec))
        base["document"] = unit_text(spec, repo=repo, binary=resolved, home=home)
        base["register"] = ["systemctl", "--user", "enable", "--now",
                            f"{spec.unit}.service"]
    elif runner == "cron":
        base["target"] = "crontab"
        base["document"] = cron_block(spec, repo=repo, binary=resolved, home=home)
    else:
        raise FactoryError("REMOTE_RUNNER_UNKNOWN", str(runner)[:32])
    return base


def _os_for(runner: str) -> str:
    return {"launchd": "macos", "schtasks": "windows"}.get(runner, "linux")


def install(*, agent: str | None = None, repo: Path | None = None,
            runner: str | None = None, os_name: str | None = None,
            home: Path | None = None, binary: str | None = None,
            dry_run: bool = False, probe: bool = True,
            probe_runner: Callable[[Sequence[str], Path], int] | None = None,
            ) -> dict[str, object]:
    """Register the always-on remote-control process. Idempotent."""
    spec = agent_spec(agent)
    directory = Path(repo or repo_root()).resolve()
    target_os = normalize(os_name) if os_name else detect_os()
    chosen = runner if runner and runner != "auto" else runner_for(target_os)
    report = plan(spec, repo=directory, runner=chosen, binary=binary, home=home)
    report["dry_run"] = dry_run
    if target_os not in spec.supported_os:
        report["warning"] = "REMOTE_OS_UNSUPPORTED"
    ok = trusted(spec, directory)
    if not ok and probe and not dry_run:
        ok = probe_trust(spec, directory, binary=binary, runner=probe_runner)
    report["trusted"] = ok
    if not ok:
        # Not an exception: the caller prints the one action that fixes it, and
        # the plan it would have written stays visible.
        report["blocked"] = spec.trust_code
        report["installed"] = False
        return report
    if dry_run:
        report["installed"] = False
        return report
    report["installed"] = _register(spec, chosen, report, repo=directory,
                                    binary=binary, home=home)
    return report


def _register(spec: Agent, runner: str, report: dict[str, object], *, repo: Path,
              binary: str | None, home: Path | None) -> bool:
    document = str(report["document"])
    log = Path(str(report["log"]))
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if runner == "launchd":
        target = Path(str(report["target"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(document.encode("utf-8"))
        _launchctl(["bootout", f"gui/{_uid()}/{spec.label}"])
        result = _launchctl(["bootstrap", f"gui/{_uid()}", str(target)])
        return result is not None and result.returncode == 0
    if runner == "schtasks":
        return _register_task(spec, document)
    if runner == "systemd":
        target = unit_path(spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        _systemctl(["daemon-reload"])
        result = _systemctl(["enable", "--now", f"{spec.unit}.service"])
        return result is not None and result.returncode == 0
    if runner == "cron":
        return _write_crontab(cron_merge(_read_crontab(), spec, repo=repo,
                                         binary=binary, home=home))
    raise FactoryError("REMOTE_RUNNER_UNKNOWN", str(runner)[:32])


def _register_task(spec: Agent, document: str) -> bool:
    """schtasks reads the XML from a file, and wants UTF-16 for a UTF-16 header."""
    handle = tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False)
    try:
        handle.write(document.encode("utf-16"))
        handle.close()
        result = _schtasks(task_create_command(spec, Path(handle.name)))
        return result is not None and result.returncode == 0
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def remove(*, agent: str | None = None, runner: str | None = None,
           os_name: str | None = None, dry_run: bool = False) -> dict[str, object]:
    spec = agent_spec(agent)
    target_os = normalize(os_name) if os_name else detect_os()
    chosen = runner if runner and runner != "auto" else runner_for(target_os)
    report: dict[str, object] = {"agent": spec.name, "runner": chosen,
                                 "dry_run": dry_run}
    if chosen == "launchd":
        target = plist_path(spec)
        report["target"] = str(target)
        if dry_run:
            return report
        _launchctl(["bootout", f"gui/{_uid()}/{spec.label}"])
        removed = target.exists()
        if removed:
            target.unlink()
        report["removed"] = removed
        return report
    if chosen == "schtasks":
        report["target"] = spec.task
        if dry_run:
            report["command"] = task_delete_command(spec)
            return report
        result = _schtasks(task_delete_command(spec))
        report["removed"] = result is not None and result.returncode == 0
        return report
    if chosen == "systemd":
        target = unit_path(spec)
        report["target"] = str(target)
        if dry_run:
            return report
        _systemctl(["disable", "--now", f"{spec.unit}.service"])
        removed = target.exists()
        if removed:
            target.unlink()
            _systemctl(["daemon-reload"])
        report["removed"] = removed
        return report
    if chosen == "cron":
        report["target"] = "crontab"
        if dry_run:
            return report
        current = _read_crontab()
        if CRON_BEGIN not in current:
            report["removed"] = False
            return report
        stripped = cron_strip(current)
        report["removed"] = _write_crontab(f"{stripped}\n" if stripped else "")
        return report
    raise FactoryError("REMOTE_RUNNER_UNKNOWN", str(chosen)[:32])


def status(*, agent: str | None = None, runner: str | None = None,
           repo: Path | None = None, os_name: str | None = None,
           home: Path | None = None) -> dict[str, object]:
    spec = agent_spec(agent)
    directory = Path(repo or repo_root()).resolve()
    target_os = normalize(os_name) if os_name else detect_os()
    chosen = runner if runner and runner != "auto" else runner_for(target_os)
    found = which(spec.binary)
    report: dict[str, object] = {
        "agent": spec.name,
        "runner": chosen,
        "repo": str(directory),
        "binary": found or "",
        "available": bool(found),
        "supported_os": target_os in spec.supported_os,
        "trusted": trusted(spec, directory),
        "log": str(log_path(spec, os_name=_os_for(chosen), home=home)),
        "installed": False,
        "running": False,
    }
    if chosen == "launchd":
        target = plist_path(spec)
        report["target"] = str(target)
        report["installed"] = target.exists()
        result = _launchctl(["print", f"gui/{_uid()}/{spec.label}"])
        report["running"] = result is not None and result.returncode == 0
    elif chosen == "schtasks":
        report["target"] = spec.task
        result = _schtasks(task_query_command(spec))
        report["installed"] = result is not None and result.returncode == 0
        report["running"] = bool(report["installed"]
                                 and "Running" in (result.stdout if result else ""))
    elif chosen == "systemd":
        target = unit_path(spec)
        report["target"] = str(target)
        report["installed"] = target.exists()
        result = _systemctl(["is-active", f"{spec.unit}.service"])
        report["running"] = bool(result and result.stdout.strip() == "active")
    elif chosen == "cron":
        report["target"] = "crontab"
        report["installed"] = CRON_BEGIN in _read_crontab()
    else:
        raise FactoryError("REMOTE_RUNNER_UNKNOWN", str(chosen)[:32])
    return report


# ------------------------------------------------------------------- subprocess
# Every helper returns None rather than raising when the tool is not on this host:
# `factory remote status` on the wrong OS is a report, not a crash.

def _uid() -> int:
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else -1


def _call(arguments: list[str], *, timeout: int = 30,
          stdin: str | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(arguments, input=stdin, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _launchctl(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _call(["launchctl", *arguments])


def _schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _call(arguments, timeout=60)


def _systemctl(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _call(["systemctl", "--user", *arguments])


def _read_crontab() -> str:
    result = _call(["crontab", "-l"])
    return result.stdout if result and result.returncode == 0 else ""


def _write_crontab(content: str) -> bool:
    result = _call(["crontab", "-"], stdin=content)
    return result is not None and result.returncode == 0


def sleep_hint(os_name: str | None = None) -> dict[str, str]:
    """How to stop this machine sleeping. Opening a pane is as far as we go."""
    target = normalize(os_name) if os_name else detect_os()
    if target == "macos":
        return {
            # Ventura and later renamed the pane; the legacy id still resolves on
            # Sequoia for desktops, so both are offered rather than guessed.
            "open": 'open "x-apple.systempreferences:com.apple.preference.energysaver"',
            "fallback": 'open "x-apple.systempreferences:com.apple.Battery-Settings.extension"',
        }
    if target == "windows":
        return {"open": "powercfg /change standby-timeout-ac 0",
                "fallback": "powercfg /change monitor-timeout-ac 0"}
    return {"open": "systemctl mask sleep.target suspend.target",
            "fallback": "check your desktop power settings"}
