"""The one-line installers: syntax, and the skip logic that makes them idempotent.

`install.sh` is written so that `FACTORY_INSTALL_LIB=1 . ./install.sh` defines its
functions and runs nothing, which is what lets each step be exercised on its own
here. Presence checks go through `command -v`, so a directory of stub
executables on `PATH` is enough to mock "this tool is already installed" — and
`FACTORY_INSTALL_DRY_RUN=1` turns every mutating command into a printed line, so
no test installs Homebrew, clones a repo or touches a real desktop.

`install.ps1` cannot be executed here. If `pwsh` is on the host its parser is
used for a real syntax check; otherwise that one test skips and says so, and the
structural checks below run regardless.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO / "install.sh"
INSTALL_PS1 = REPO / "install.ps1"
# `sh` needs these for uname, id and friends; the stub directory goes in front.
SYSTEM_PATH = "/usr/bin:/bin"


def _stub_bin(tmp_path: Path, *names: str, body: str = "exit 0") -> Path:
    directory = tmp_path / "stubbin"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = directory / name
        target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        target.chmod(0o755)
    return directory


def _run(body: str, *, tmp_path: Path, stubs: Path | None = None,
         env: dict[str, str] | None = None,
         arguments: str = "") -> subprocess.CompletedProcess[str]:
    """Source install.sh as a library, then run `body`."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = f"{stubs}:{SYSTEM_PATH}" if stubs else SYSTEM_PATH
    script = (f"set -eu\nexport FACTORY_INSTALL_LIB=1\n"
              f". '{INSTALL_SH}' {arguments}\n{body}\n")
    environment = {"PATH": path, "HOME": str(home), "FACTORY_INSTALL_DRY_RUN": "1",
                   "FACTORY_REPO_DIR": str(tmp_path / "clone"), "LANG": "en_US.UTF-8"}
    if env:
        environment.update(env)
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                          timeout=120, check=False, env=environment)


def _main(*, tmp_path: Path, stubs: Path | None = None,
          arguments: str = "--lang en --no-naver --dry-run",
          env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = f"{stubs}:{SYSTEM_PATH}" if stubs else SYSTEM_PATH
    environment = {"PATH": path, "HOME": str(home),
                   "FACTORY_REPO_DIR": str(tmp_path / "clone"), "LANG": "en_US.UTF-8"}
    if env:
        environment.update(env)
    return subprocess.run(["sh", str(INSTALL_SH), *arguments.split()],
                          capture_output=True, text=True, timeout=180, check=False,
                          env=environment)


# ------------------------------------------------------------------- syntax

@pytest.mark.parametrize("shell", ["sh", "bash", "dash"])
def test_install_sh_parses(shell: str) -> None:
    """Piped to `sh`, which is dash on most Linux hosts and bash on macOS."""
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    done = subprocess.run([shell, "-n", str(INSTALL_SH)], capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_install_sh_uses_no_bashisms() -> None:
    body = INSTALL_SH.read_text(encoding="utf-8")
    # `[[`, arrays and `function foo()` are the three that dash silently cannot
    # parse, and the script is piped straight into `sh`.
    assert "[[" not in body
    assert not re.search(r"^\s*function\s+\w+", body, re.M)
    assert not re.search(r"=\(", body)


def test_install_sh_library_mode_runs_nothing(tmp_path: Path) -> None:
    done = _run("echo loaded", tmp_path=tmp_path)
    assert done.returncode == 0
    assert done.stdout.strip() == "loaded"


# ------------------------------------------------------ step 3: tool skip logic

def test_uv_already_on_path_is_skipped(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "uv")
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_uv", tmp_path=tmp_path,
                stubs=stubs)
    assert "already present, skipping: uv" in done.stdout
    assert "astral.sh" not in done.stdout


def test_uv_installed_when_absent(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_uv", tmp_path=tmp_path)
    assert "installing: uv" in done.stdout
    assert "DRY  curl -LsSf https://astral.sh/uv/install.sh | sh" in done.stdout


def test_uv_off_path_but_installed_is_not_reinstalled(tmp_path: Path) -> None:
    """The uv installer puts it in ~/.local/bin, which a fresh shell may not have."""
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (local / "uv").chmod(0o755)
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_uv", tmp_path=tmp_path)
    assert "already present, skipping: uv" in done.stdout
    assert "astral.sh" not in done.stdout


def test_ffmpeg_needs_ffprobe_too(tmp_path: Path) -> None:
    """ffprobe is a separate executable and goes missing on its own."""
    stubs = _stub_bin(tmp_path, "ffmpeg", "brew")
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_ffmpeg", tmp_path=tmp_path,
                stubs=stubs)
    assert "installing: ffmpeg" in done.stdout
    assert "DRY  brew install ffmpeg" in done.stdout
    both = _stub_bin(tmp_path, "ffmpeg", "ffprobe", "brew")
    again = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_ffmpeg", tmp_path=tmp_path,
                 stubs=both)
    assert "already present, skipping: ffmpeg" in again.stdout


def test_node_install_line_matches_the_readme(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "brew")
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_node", tmp_path=tmp_path,
                stubs=stubs)
    assert "DRY  brew install node" in done.stdout


def test_linux_uses_the_detected_package_manager(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "apt-get", "sudo")
    done = _run("LANG_CHOICE=en; HOST_OS=linux; ensure_package_manager; "
                'echo "pkg=$LINUX_PKG"; ensure_ffmpeg', tmp_path=tmp_path,
                stubs=stubs)
    assert "pkg=apt-get" in done.stdout
    assert "DRY  sudo apt-get install -y ffmpeg" in done.stdout


def test_linux_with_no_package_manager_says_so(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; HOST_OS=linux; ensure_package_manager",
                tmp_path=tmp_path)
    assert "none of apt/dnf/pacman found" in done.stdout


def test_higgsfield_install_line(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_higgsfield", tmp_path=tmp_path)
    assert "higgsfield-ai/cli/main/install.sh" in done.stdout
    stubs = _stub_bin(tmp_path, "higgsfield")
    again = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_higgsfield",
                 tmp_path=tmp_path, stubs=stubs)
    assert "already present, skipping: higgsfield" in again.stdout


def test_homebrew_already_present_is_not_reinstalled(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "brew")
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_package_manager",
                tmp_path=tmp_path, stubs=stubs,
                env={"FACTORY_BREW_PATHS": str(tmp_path / "no-brew")})
    assert "already present, skipping: homebrew" in done.stdout
    assert "Homebrew/install" not in done.stdout


def test_homebrew_off_path_is_found_at_its_absolute_location(tmp_path: Path) -> None:
    """Right after the installer runs, brew is on disk but not yet on PATH.

    `brew shellenv` is what puts it there, so the stub emits the export line a
    real brew would: finding the binary is not enough, the eval has to work.
    """
    brew = tmp_path / "opt" / "bin" / "brew"
    brew.parent.mkdir(parents=True)
    brew.write_text('#!/bin/sh\nprintf \'export PATH="%s:$PATH"\\n\' '
                    '"$(dirname "$0")"\n', encoding="utf-8")
    brew.chmod(0o755)
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_package_manager",
                tmp_path=tmp_path, env={"FACTORY_BREW_PATHS": str(brew)})
    assert "already present, skipping: homebrew" in done.stdout
    assert "Homebrew/install" not in done.stdout


def test_homebrew_missing_is_installed_non_interactively(tmp_path: Path) -> None:
    # brew is probed at its absolute path, so a host that has it needs the probe
    # pointed somewhere empty to reach this branch at all.
    done = _run("LANG_CHOICE=en; HOST_OS=macos; ensure_package_manager",
                tmp_path=tmp_path,
                env={"FACTORY_BREW_PATHS": str(tmp_path / "no-brew")})
    # NONINTERACTIVE so a piped installer does not hang on a prompt nobody sees.
    assert "NONINTERACTIVE=1" in done.stdout
    assert "Homebrew/install" in done.stdout


# ---------------------------------------------------------- step 4: playwright

def test_playwright_skipped_without_the_naver_answer(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; WANT_NAVER=0; ensure_playwright", tmp_path=tmp_path)
    assert "Skipped" in done.stdout
    assert "playwright" in done.stdout.lower()
    assert "DRY  npx" not in done.stdout


def test_playwright_installed_when_naver_is_wanted(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "npm", "npx")
    done = _run("LANG_CHOICE=en; WANT_NAVER=1; ensure_playwright", tmp_path=tmp_path,
                stubs=stubs)
    assert "DRY  npm install -g playwright" in done.stdout
    assert "DRY  npx playwright install chromium" in done.stdout


def test_playwright_needs_npm(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; WANT_NAVER=1; ensure_playwright", tmp_path=tmp_path)
    assert "Skipped" in done.stdout


def test_the_naver_question_is_the_only_one(tmp_path: Path) -> None:
    """One question in the whole script, per PLAN 9.1."""
    body = INSTALL_SH.read_text(encoding="utf-8")
    assert body.count("ask_yes ") == 1
    # `curl | sh` leaves stdin on the script, so a prompt must use the terminal.
    assert "read -r reply < /dev/tty" in body


# -------------------------------------------------------------- step 5: the repo

def test_existing_clone_is_fast_forwarded_not_recloned(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    done = _run("LANG_CHOICE=en; ensure_repo", tmp_path=tmp_path)
    assert "fast-forwarding" in done.stdout
    assert "DRY  (cd" in done.stdout and "git pull --ff-only" in done.stdout
    assert "git clone" not in done.stdout


def test_missing_clone_is_cloned(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; ensure_repo", tmp_path=tmp_path)
    assert "cloning into" in done.stdout
    assert "DRY  git clone" in done.stdout


def test_a_non_git_directory_is_left_alone(tmp_path: Path) -> None:
    """Cloning over a user's unrelated folder of the same name is not on."""
    (tmp_path / "clone").mkdir()
    done = _run("LANG_CHOICE=en; ensure_repo", tmp_path=tmp_path)
    assert "git clone" not in done.stdout
    assert "skipping" in done.stdout


# -------------------------------------------------------------- step 8: an agent

def test_installed_agent_is_used_as_is(tmp_path: Path) -> None:
    for agent in ("claude", "codex"):
        stubs = _stub_bin(tmp_path / agent, agent)
        done = _run("LANG_CHOICE=en; ensure_agent", tmp_path=tmp_path, stubs=stubs)
        assert f"agent found: {agent}" in done.stdout
        assert "npm install" not in done.stdout


def test_claude_code_is_installed_when_neither_agent_exists(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "npm")
    done = _run("LANG_CHOICE=en; ensure_agent", tmp_path=tmp_path, stubs=stubs)
    assert "neither claude nor codex found" in done.stdout
    assert "DRY  npm install -g @anthropic-ai/claude-code" in done.stdout


def test_no_npm_means_a_manual_instruction_not_a_crash(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; ensure_agent", tmp_path=tmp_path)
    assert done.returncode == 0
    assert "npm i -g @anthropic-ai/claude-code" in done.stdout


# ------------------------------------------------------------------- preflight

def test_preflight_refuses_an_unsupported_os(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "uname", body='echo Plan9')
    done = _run("LANG_CHOICE=en; preflight || echo refused", tmp_path=tmp_path,
                stubs=stubs)
    assert "install.ps1" in done.stdout
    assert "refused" in done.stdout


def test_preflight_refuses_root(tmp_path: Path) -> None:
    """A root-owned clone and a root-owned Homebrew then fail for the user."""
    stubs = _stub_bin(tmp_path, "id", body="echo 0")
    (stubs / "uname").write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
    (stubs / "uname").chmod(0o755)
    done = _run("LANG_CHOICE=en; preflight || echo refused", tmp_path=tmp_path,
                stubs=stubs)
    assert "without sudo" in done.stdout
    assert "refused" in done.stdout


def test_preflight_names_the_host(tmp_path: Path) -> None:
    done = _run("LANG_CHOICE=en; preflight", tmp_path=tmp_path)
    assert "detected" in done.stdout


# --------------------------------------------------------------------- language

@pytest.mark.parametrize("value,expected", [
    ("ko_KR.UTF-8", "ko"),
    ("en_US.UTF-8", "en"),
    ("", "ko"),
])
def test_language_follows_the_locale(tmp_path: Path, value: str,
                                    expected: str) -> None:
    done = _run('pick_lang ""; echo "lang=$LANG_CHOICE"', tmp_path=tmp_path,
                env={"LANG": value, "LC_ALL": ""})
    assert f"lang={expected}" in done.stdout


def test_explicit_lang_flag_wins(tmp_path: Path) -> None:
    done = _run('pick_lang en; echo "lang=$LANG_CHOICE"', tmp_path=tmp_path,
                env={"LANG": "ko_KR.UTF-8"})
    assert "lang=en" in done.stdout


# ------------------------------------------------------------- the whole script

def test_dry_run_walks_all_eleven_steps_in_order(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "brew", "uv", "git", "ffmpeg", "ffprobe", "node",
                      "npm", "npx", "higgsfield", "claude", "open")
    done = _main(tmp_path=tmp_path, stubs=stubs)
    assert done.returncode == 0, done.stderr
    numbers = re.findall(r"^\[(\d+)/11\]", done.stdout, re.M)
    assert numbers == [str(index) for index in range(1, 12)]
    # Nothing was installed: every tool was already there.
    assert "installing:" not in done.stdout
    # The last thing it does is hand over to the launcher and then start.
    assert "factory launcher create --os macos" in done.stdout
    assert done.stdout.rindex("factory start") > done.stdout.rindex(
        "factory launcher create")


def test_dry_run_is_idempotent(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "brew", "uv", "git", "ffmpeg", "ffprobe", "node",
                      "npm", "npx", "higgsfield", "claude", "open")
    first = _main(tmp_path=tmp_path, stubs=stubs)
    second = _main(tmp_path=tmp_path, stubs=stubs)
    assert first.stdout == second.stdout


def test_no_start_stops_before_launching(tmp_path: Path) -> None:
    stubs = _stub_bin(tmp_path, "brew", "uv", "git", "ffmpeg", "ffprobe", "node",
                      "npm", "npx", "higgsfield", "claude", "open")
    done = _main(tmp_path=tmp_path, stubs=stubs,
                 arguments="--lang en --no-naver --dry-run --no-start")
    assert "Not starting" in done.stdout
    # The hint names the command; what must not appear is it actually running.
    assert "DRY  (cd" not in done.stdout.split("[11/11]")[-1]


def test_a_failure_names_the_step_and_says_to_rerun(tmp_path: Path) -> None:
    """"It broke somewhere in the script" is not a thing a user can report."""
    stubs = _stub_bin(tmp_path, "uname", body="echo Plan9")
    done = _main(tmp_path=tmp_path, stubs=stubs)
    assert done.returncode != 0
    assert "Failed at step [1/11] Preflight" in done.stdout
    assert "re-run the same command" in done.stdout


def test_the_failure_message_reports_whichever_step_was_current(
        tmp_path: Path) -> None:
    """Called directly: reaching step 6 for real would install things."""
    done = _run('LANG_CHOICE=en; STEP=6; CURRENT_LABEL="Python dependencies"; '
                "on_fail 3", tmp_path=tmp_path)
    assert "Failed at step [6/11] Python dependencies (exit 3)" in done.stdout


def test_dry_run_never_runs_a_mutating_command(tmp_path: Path) -> None:
    """A stub that would fail loudly proves nothing actually executed."""
    stubs = _stub_bin(tmp_path, "brew", "uv", "git", "ffmpeg", "ffprobe", "node",
                      "npm", "npx", "higgsfield", "claude", "open",
                      body='echo "EXECUTED $0 $*" >&2; exit 9')
    done = _main(tmp_path=tmp_path, stubs=stubs)
    assert "EXECUTED" not in done.stderr
    assert done.returncode == 0


# ------------------------------------------------------------------ install.ps1

def test_install_ps1_parses_if_pwsh_is_available() -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("pwsh is not installed: install.ps1 syntax is unverified here, "
                    "the structural checks below still run")
    check = ("$errors = $null; "
             "[System.Management.Automation.Language.Parser]::ParseFile("
             f"'{INSTALL_PS1}', [ref]$null, [ref]$errors) | Out-Null; "
             "if ($errors.Count -gt 0) { $errors | ForEach-Object "
             "{ Write-Output $_.Message }; exit 1 }; exit 0")
    done = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", check],
                          capture_output=True, text=True, timeout=180, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def _ps1_message_keys(language: str) -> set[str]:
    body = INSTALL_PS1.read_text(encoding="utf-8")
    block = re.search(rf"^\s{{4}}{language} = @\{{(.*?)^\s{{4}}\}}", body,
                      re.S | re.M)
    assert block, f"no {language} message table in install.ps1"
    return set(re.findall(r"^\s+(\w+)\s*=", block.group(1), re.M))


def test_install_ps1_message_tables_agree() -> None:
    """A key present in one language and not the other prints its own name."""
    assert _ps1_message_keys("ko") == _ps1_message_keys("en")


def test_install_ps1_has_a_library_guard_and_eleven_steps() -> None:
    body = INSTALL_PS1.read_text(encoding="utf-8")
    assert "if (-not $Lib) {" in body
    numbers = sorted(int(value) for value in
                     re.findall(r"Write-FactoryStep -Number (\d+)", body))
    assert numbers == list(range(1, 12))
    assert "$script:StepTotal = 11" in body


def test_install_ps1_offers_the_same_switches_as_install_sh() -> None:
    body = INSTALL_PS1.read_text(encoding="utf-8")
    for flag in ("$Lang", "$Dir", "$Naver", "$NoNaver", "$NoStart", "$DryRun"):
        assert flag in body
    # winget cannot be bootstrapped unattended, so it is a named instruction.
    assert "App Installer" in body


def test_both_installers_name_the_same_tools() -> None:
    shell = INSTALL_SH.read_text(encoding="utf-8")
    powershell = INSTALL_PS1.read_text(encoding="utf-8")
    for tool in ("uv", "ffmpeg", "node", "higgsfield", "playwright",
                 "@anthropic-ai/claude-code"):
        assert tool in shell, tool
        assert tool in powershell, tool
    for line, powershell_form in (
            ("factory init", "'factory', 'init'"),
            ("factory launcher create", "'launcher', 'create'"),
            ("factory start", "'factory', 'start'")):
        assert line in shell, line
        assert powershell_form in powershell, powershell_form


def test_installers_carry_no_credentials() -> None:
    for path in (INSTALL_SH, INSTALL_PS1):
        body = path.read_text(encoding="utf-8")
        assert not re.search(r"(?i)(api[_-]?key|secret|bearer|token)\s*=\s*['\"]?[A-Za-z0-9]",
                             body), path.name
