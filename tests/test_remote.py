"""Always-on remote control: the daemon definitions and the trust gate.

Nothing here registers anything. `Path.home` is patched into `tmp_path`, every
`subprocess.run` is intercepted, and the Windows and Linux branches are reached
by naming the runner rather than by being on that OS — which is the honest limit
of a macOS test run: these assert that the right document and the right
registration command are built, not that launchd, Task Scheduler or systemd
accepted them.

The two behaviours worth guarding hardest are the ones that would be silently
wrong: KeepAlive must not be attached to a command that exits on success
(`codex remote-control start` daemonises and returns, so KeepAlive would be a
restart loop), and install must never write a trust record on the user's behalf.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path

import pytest

from factory import remote
from factory.cli import main
from factory.errors import FactoryError

REPO = Path("/opt/contents-factory")


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    (home / "Library" / "Logs").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(remote, "which",
                        lambda name: f"/usr/local/bin/{name}" if name in
                        {"claude", "codex", "systemctl"} else None)
    return home


def _trust_claude(home: Path, repo: Path, *, accepted: bool = True) -> None:
    (home / ".claude.json").write_text(json.dumps(
        {"projects": {str(repo): {"hasTrustDialogAccepted": accepted}}}),
        encoding="utf-8")


def _trust_codex(home: Path, repo: Path) -> None:
    directory = home / ".codex"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(
        f'model = "gpt-5"\n\n[projects."{repo}"]\ntrust_level = "trusted"\n',
        encoding="utf-8")


# ------------------------------------------------------------------ the agents

def test_agent_table_covers_claude_and_codex() -> None:
    assert set(remote.AGENT_CHOICES) == {"claude", "codex"}
    claude = remote.agent_spec("claude")
    assert claude.arguments == ("remote-control", "--name", "contents-factory",
                                "--spawn", "same-dir")
    codex = remote.agent_spec("codex")
    assert codex.arguments == ("remote-control", "start")


def test_unknown_agent_is_a_code() -> None:
    with pytest.raises(FactoryError) as caught:
        remote.agent_spec("gemini")
    assert caught.value.code == "REMOTE_AGENT_UNKNOWN"


def test_codex_is_not_keep_alive_because_its_start_returns() -> None:
    """`codex remote-control start` daemonises; KeepAlive would restart it forever."""
    assert remote.agent_spec("claude").keep_alive is True
    assert remote.agent_spec("codex").keep_alive is False


def test_codex_remote_hosts_are_macos_and_windows_only() -> None:
    assert remote.agent_spec("codex").supported_os == ("macos", "windows")
    assert "linux" in remote.agent_spec("claude").supported_os


def test_missing_cli_is_a_named_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "which", lambda name: None)
    for agent, code in (("claude", "CLAUDE_CLI_MISSING"),
                        ("codex", "CODEX_CLI_MISSING")):
        with pytest.raises(FactoryError) as caught:
            remote.command(remote.agent_spec(agent))
        assert caught.value.code == code


def test_detect_agents_lists_what_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "which",
                        lambda name: "/bin/codex" if name == "codex" else None)
    assert remote.detect_agents() == ["codex"]


def test_runner_per_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "which", lambda name: "/bin/systemctl")
    assert remote.runner_for("macos") == "launchd"
    assert remote.runner_for("windows") == "schtasks"
    assert remote.runner_for("linux") == "systemd"
    # No systemd on this Linux box: @reboot cron is the fallback, not a failure.
    monkeypatch.setattr(remote, "which", lambda name: None)
    assert remote.runner_for("linux") == "cron"


# ----------------------------------------------------------------- macOS: plist

def test_plist_matches_the_reference_shape(fake_home: Path) -> None:
    spec = remote.agent_spec("claude")
    data = remote.definition(spec, repo=REPO)
    assert data["Label"] == "com.contents-factory.claude-remote-control"
    assert data["ProgramArguments"] == ["/usr/local/bin/claude", "remote-control",
                                        "--name", "contents-factory",
                                        "--spawn", "same-dir"]
    assert data["KeepAlive"] is True
    assert data["RunAtLoad"] is True
    assert data["ThrottleInterval"] == remote.THROTTLE_SECONDS
    assert data["WorkingDirectory"] == str(REPO)
    assert data["StandardOutPath"].endswith("contents-factory-remote.log")
    # A launchd agent gets almost no PATH, and the session has to reach uv.
    assert str(fake_home / ".local" / "bin") in str(
        data["EnvironmentVariables"]["PATH"])  # type: ignore[index]


def test_plist_carries_no_secret(fake_home: Path) -> None:
    spec = remote.agent_spec("claude")
    rendered = remote.render_plist(remote.definition(spec, repo=REPO)).decode()
    assert "TOKEN" not in rendered.upper()
    env = plistlib.loads(rendered.encode())["EnvironmentVariables"]
    assert set(env) == {"PATH", "HOME"}


def test_codex_plist_omits_keep_alive(fake_home: Path) -> None:
    data = remote.definition(remote.agent_spec("codex"), repo=REPO)
    assert "KeepAlive" not in data
    assert "ThrottleInterval" not in data
    assert data["RunAtLoad"] is True


def test_install_on_launchd_writes_the_plist_and_bootstraps(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos")
    assert report["installed"] is True and report["trusted"] is True
    target = Path(str(report["target"]))
    assert target.exists()
    assert plistlib.loads(target.read_bytes())["KeepAlive"] is True
    # bootout before bootstrap, so a second install replaces rather than stacks.
    assert [entry[1] for entry in seen] == ["bootout", "bootstrap"]


def test_launchd_install_is_idempotent(fake_home: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    first = remote.install(agent="claude", repo=REPO, runner="launchd",
                           os_name="macos")
    second = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos")
    assert first["target"] == second["target"]
    assert second["installed"] is True
    directory = fake_home / "Library" / "LaunchAgents"
    assert len(list(directory.iterdir())) == 1


def test_remove_boots_out_then_deletes(fake_home: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: calls.append(list(command))
                        or subprocess.CompletedProcess(command, 0, "", ""))
    remote.install(agent="claude", repo=REPO, runner="launchd", os_name="macos")
    report = remote.remove(agent="claude", runner="launchd", os_name="macos")
    assert report["removed"] is True
    assert not Path(str(report["target"])).exists()
    assert calls[-1][1] == "bootout"
    # Removing twice is not an error.
    assert remote.remove(agent="claude", runner="launchd",
                         os_name="macos")["removed"] is False


# --------------------------------------------------------- Windows: schtasks XML

def test_task_xml_has_a_logon_trigger_and_restart_on_failure(fake_home: Path) -> None:
    spec = remote.agent_spec("claude")
    xml = remote.task_xml(spec, repo=REPO, user="ada")
    assert "<LogonTrigger>" in xml
    # schtasks' own /SC ONLOGON cannot express this, which is why it is XML.
    assert f"<Interval>{remote.RESTART_INTERVAL}</Interval>" in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
    assert "<UserId>ada</UserId>" in xml
    # An always-on task must not stop the moment the laptop unplugs.
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "remote-control --name contents-factory --spawn same-dir" in xml


def test_task_xml_escapes_the_redirection(fake_home: Path) -> None:
    xml = remote.task_xml(remote.agent_spec("claude"), repo=REPO)
    assert "&gt;&gt;" in xml and "&amp;&amp;" in xml
    assert "2&gt;&amp;1" in xml
    # Raw < or > inside Arguments would make the XML unparseable for schtasks.
    body = xml.split("<Arguments>")[1].split("</Arguments>")[0]
    assert "<" not in body and ">" not in body


def test_codex_task_xml_has_no_restart_block(fake_home: Path) -> None:
    xml = remote.task_xml(remote.agent_spec("codex"), repo=REPO)
    assert "<RestartOnFailure>" not in xml
    assert "<LogonTrigger>" in xml


def test_schtasks_install_registers_from_a_utf16_xml_file(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        path = Path(command[command.index("/XML") + 1])
        # Read it here: install deletes the temp file afterwards.
        captured["xml"] = path.read_bytes()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = remote.install(agent="claude", repo=REPO, runner="schtasks",
                            os_name="windows")
    assert report["installed"] is True
    command = captured["command"]
    assert command[:4] == ["schtasks", "/Create", "/TN",  # type: ignore[index]
                           "contents-factory-remote-claude"]
    assert command[-1] == "/F"  # type: ignore[index]
    # The XML declares UTF-16, so it has to actually be UTF-16.
    raw = captured["xml"]
    assert isinstance(raw, bytes) and raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    assert "<LogonTrigger>" in raw.decode("utf-16")


def test_schtasks_failure_is_reported_not_raised(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            FileNotFoundError()))
    report = remote.install(agent="claude", repo=REPO, runner="schtasks",
                            os_name="windows")
    assert report["installed"] is False


# ------------------------------------------------------ Linux: systemd and cron

def test_systemd_unit_restarts_claude_and_not_codex(fake_home: Path) -> None:
    claude = remote.unit_text(remote.agent_spec("claude"), repo=REPO)
    assert "Type=simple" in claude and "Restart=always" in claude
    assert "WantedBy=default.target" in claude
    assert f"WorkingDirectory={REPO}" in claude
    codex = remote.unit_text(remote.agent_spec("codex"), repo=REPO)
    assert "Type=oneshot" in codex and "Restart=no" in codex


def test_systemd_install_enables_the_unit(fake_home: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: calls.append(list(command))
                        or subprocess.CompletedProcess(command, 0, "", ""))
    report = remote.install(agent="claude", repo=REPO, runner="systemd",
                            os_name="linux")
    assert report["installed"] is True
    assert Path(str(report["target"])).exists()
    assert calls[0][:3] == ["systemctl", "--user", "daemon-reload"]
    assert "enable" in calls[1]


def test_cron_block_is_fenced_and_strippable(fake_home: Path) -> None:
    spec = remote.agent_spec("claude")
    block = remote.cron_block(spec, repo=REPO)
    assert block.startswith(remote.CRON_BEGIN)
    assert "@reboot" in block
    existing = "0 9 * * * /usr/bin/true\n"
    merged = remote.cron_merge(existing, spec, repo=REPO)
    assert "/usr/bin/true" in merged
    # Merging twice must not leave two blocks.
    twice = remote.cron_merge(merged, spec, repo=REPO)
    assert twice.count(remote.CRON_BEGIN) == 1
    assert remote.cron_strip(twice).strip() == existing.strip()


# -------------------------------------------------------------- workspace trust

def test_claude_trust_is_read_from_the_recorded_decision(fake_home: Path) -> None:
    spec = remote.agent_spec("claude")
    assert remote.trusted(spec, REPO) is False
    _trust_claude(fake_home, REPO, accepted=False)
    assert remote.trusted(spec, REPO) is False
    _trust_claude(fake_home, REPO, accepted=True)
    assert remote.trusted(spec, REPO) is True
    # A different directory's acceptance is not this one's.
    assert remote.trusted(spec, Path("/opt/elsewhere")) is False


def test_codex_trust_accepts_a_trusted_parent(fake_home: Path) -> None:
    spec = remote.agent_spec("codex")
    _trust_codex(fake_home, Path("/opt"))
    assert remote.codex_trusted(Path("/opt/contents-factory")) is True
    assert remote.trusted(spec, Path("/srv/other")) is False


def test_codex_trust_ignores_a_non_trusted_level(fake_home: Path) -> None:
    directory = fake_home / ".codex"
    directory.mkdir(parents=True)
    (directory / "config.toml").write_text(
        f'[projects."{REPO}"]\ntrust_level = "untrusted"\n', encoding="utf-8")
    assert remote.codex_trusted(REPO) is False


def test_install_refuses_without_trust_and_writes_nothing(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix is the human accepting the prompt; forging the record is not ours."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(
                            args[0] if args else [], 0, "", ""))
    report = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos", probe=False)
    assert report["blocked"] == "CLAUDE_WORKSPACE_UNTRUSTED"
    assert report["installed"] is False
    assert list((fake_home / "Library" / "LaunchAgents").iterdir()) == []
    # The trust file is untouched, not created.
    assert not (fake_home / ".claude.json").exists()
    # The plan it would have written is still shown, so the user can see it.
    assert "com.contents-factory.claude-remote-control" in str(report["document"])


def test_probe_runs_claude_once_in_the_repo_to_record_trust(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], str]] = []

    def probe(arguments, cwd):
        seen.append((list(arguments), str(cwd)))
        # This is what accepting the prompt looks like from outside.
        _trust_claude(fake_home, REPO)
        return 0

    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    report = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos", probe_runner=probe)
    assert seen == [(["/usr/local/bin/claude", "--print", "ok"], str(REPO))]
    assert report["trusted"] is True and report["installed"] is True


def test_probe_that_does_not_earn_trust_still_refuses(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    report = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos",
                            probe_runner=lambda arguments, cwd: 1)
    assert report["blocked"] == "CLAUDE_WORKSPACE_UNTRUSTED"


def test_codex_is_never_probed(fake_home: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A codex probe would start a real turn, so its trust is read, not earned."""
    calls: list[object] = []
    report = remote.install(agent="codex", repo=REPO, runner="launchd",
                            os_name="macos",
                            probe_runner=lambda arguments, cwd: calls.append(1) or 0)
    assert calls == []
    assert report["blocked"] == "CODEX_WORKSPACE_UNTRUSTED"


# ------------------------------------------------------------------- dry run

def test_dry_run_registers_nothing(fake_home: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: calls.append(args) or
                        subprocess.CompletedProcess([], 0, "", ""))
    report = remote.install(agent="claude", repo=REPO, runner="launchd",
                            os_name="macos", dry_run=True)
    assert report["dry_run"] is True and report["installed"] is False
    assert "<key>KeepAlive</key>" in str(report["document"])
    assert calls == []
    assert list((fake_home / "Library" / "LaunchAgents").iterdir()) == []


def test_dry_run_remove_touches_nothing(fake_home: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_claude(fake_home, REPO)
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    remote.install(agent="claude", repo=REPO, runner="launchd", os_name="macos")
    target = remote.plist_path(remote.agent_spec("claude"))
    report = remote.remove(agent="claude", runner="launchd", os_name="macos",
                           dry_run=True)
    assert report["dry_run"] is True
    assert target.exists()


def test_install_on_an_unsupported_host_warns_but_proceeds(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    report = remote.install(agent="codex", repo=REPO, runner="cron",
                            os_name="linux", dry_run=True)
    assert report["warning"] == "REMOTE_OS_UNSUPPORTED"


# --------------------------------------------------------------------- status

def test_status_reports_without_registering(fake_home: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 1, "", ""))
    state = remote.status(agent="claude", runner="launchd", repo=REPO,
                          os_name="macos")
    assert state["installed"] is False and state["running"] is False
    assert state["available"] is True
    assert state["trusted"] is False
    assert state["log"].endswith("contents-factory-remote.log")


def test_status_survives_a_missing_launchctl(fake_home: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """`remote status` on the wrong OS is a report, not a crash."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            FileNotFoundError()))
    state = remote.status(agent="claude", runner="launchd", repo=REPO,
                          os_name="macos")
    assert state["running"] is False


def test_unknown_runner_is_a_code(fake_home: Path) -> None:
    with pytest.raises(FactoryError) as caught:
        remote.status(agent="claude", runner="upstart", repo=REPO, os_name="linux")
    assert caught.value.code == "REMOTE_RUNNER_UNKNOWN"


def test_sleep_hint_per_os() -> None:
    assert "energysaver" in remote.sleep_hint("macos")["open"]
    assert "powercfg" in remote.sleep_hint("windows")["open"]
    assert "sleep.target" in remote.sleep_hint("linux")["open"]


# ------------------------------------------------------------------------ cli

def test_cli_remote_install_dry_run_prints_the_plist(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    _trust_claude(fake_home, Path.cwd().resolve())
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 0, "", ""))
    assert main(["remote", "install", "--agent", "claude", "--runner", "launchd",
                 "--os", "macos", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "com.contents-factory.claude-remote-control" in out
    assert "<key>KeepAlive</key>" in out
    assert list((fake_home / "Library" / "LaunchAgents").iterdir()) == []


def test_cli_remote_install_without_trust_exits_nonzero_with_one_action(
        fake_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["remote", "install", "--agent", "claude", "--runner", "launchd",
                 "--os", "macos", "--dry-run", "--no-probe"]) == 3
    out = capsys.readouterr().out
    assert "CLAUDE_WORKSPACE_UNTRUSTED" in out
    # The message has to name the one thing the user does about it.
    assert "claude" in out and "cd " in out


def test_cli_remote_status_is_json(fake_home: Path,
                                   monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 1, "", ""))
    assert main(["remote", "status", "--agent", "codex", "--runner", "launchd",
                 "--os", "macos"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == "codex"
    assert payload["installed"] is False


def test_cli_remote_defaults_to_an_installed_agent(
        fake_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(remote, "which",
                        lambda name: "/bin/codex" if name == "codex" else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(
                            command, 1, "", ""))
    assert main(["remote", "status", "--runner", "launchd", "--os", "macos"]) == 0
    assert json.loads(capsys.readouterr().out)["agent"] == "codex"
