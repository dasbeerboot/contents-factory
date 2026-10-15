"""The desktop launcher: the only entry point a non-technical user touches.

Nothing here writes to a real desktop. `FACTORY_DESKTOP` redirects the target
and `Path.home` is patched, which is also the honest limit of a macOS test run
for the Windows branch: these assert the generated script, not that Explorer ran
it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from factory import launcher
from factory.cli import main


@pytest.fixture()
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "Desktop"
    target.mkdir()
    monkeypatch.setenv(launcher.DESKTOP_ENV, str(target))
    return target


def test_launcher_filename_per_os(desktop: Path) -> None:
    assert launcher.launcher_path("macos").name == "콘텐츠공장.command"
    assert launcher.launcher_path("windows").name == "콘텐츠공장.bat"
    assert launcher.launcher_path("linux").name == "콘텐츠공장.sh"
    assert launcher.launcher_path("macos").parent == desktop


def test_desktop_override_beats_the_home_directory(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A Windows desktop is often redirected into OneDrive, so this is settable."""
    monkeypatch.delenv(launcher.DESKTOP_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert launcher.desktop_dir() == tmp_path / "Desktop"
    monkeypatch.setenv(launcher.DESKTOP_ENV, str(tmp_path / "OneDrive" / "Desktop"))
    assert launcher.desktop_dir() == tmp_path / "OneDrive" / "Desktop"


def test_command_script_runs_factory_start_in_the_repo(desktop: Path) -> None:
    body = launcher.script(repo=Path("/opt/contents-factory"), os_name="macos",
                           uv="/home/ada/.local/bin/uv")
    assert body.startswith("#!/bin/sh")
    assert 'cd "/opt/contents-factory"' in body
    assert '"$UV" run factory start' in body
    # PATH first, recorded absolute path second: neither alone survives a
    # double-click from Finder.
    assert 'UV="$(command -v uv 2>/dev/null || true)"' in body
    assert 'UV="/home/ada/.local/bin/uv"' in body
    # A window that closes on failure tells the user nothing.
    assert body.count("read -r _ignored") == 3


def test_command_script_is_valid_shell(desktop: Path) -> None:
    import subprocess

    body = launcher.script(repo=Path("/opt/contents-factory"), os_name="macos",
                           uv="/usr/bin/uv")
    for shell in ("sh", "bash"):
        done = subprocess.run([shell, "-n", "-"], input=body, text=True,
                              capture_output=True, check=False)
        assert done.returncode == 0, f"{shell}: {done.stderr}"


def test_batch_script_is_ascii_crlf_and_pauses(desktop: Path) -> None:
    body = launcher.script(repo=Path(r"C:\Users\ada\contents-factory"),
                           os_name="windows", uv=r"C:\Users\ada\.local\bin\uv.exe")
    # cmd.exe reads a .bat in the console codepage (949 on a Korean install), so
    # Korean inside the file would arrive as mojibake.
    body.encode("ascii")
    assert "\r\n" in body and "\n" not in body.replace("\r\n", "")
    assert r'cd /d "C:\Users\ada\contents-factory"' in body
    assert "chcp 65001" in body
    assert r'set "FACTORY_UV=C:\Users\ada\.local\bin\uv.exe"' in body
    assert body.count("pause") == 2


def test_create_writes_an_executable_command(desktop: Path) -> None:
    report = launcher.create(repo=Path("/opt/cf"), os_name="macos")
    path = Path(str(report["path"]))
    assert report["created"] is True
    assert path.exists() and os.access(path, os.X_OK)
    assert "factory start" in path.read_text(encoding="utf-8")


def test_create_is_idempotent(desktop: Path) -> None:
    first = launcher.create(repo=Path("/opt/cf"), os_name="macos")
    second = launcher.create(repo=Path("/opt/other"), os_name="macos")
    assert first["path"] == second["path"]
    # The second run rewrites rather than making a second file.
    assert len(list(desktop.iterdir())) == 1
    assert "/opt/other" in Path(str(second["path"])).read_text(encoding="utf-8")


def test_create_dry_run_writes_nothing(desktop: Path) -> None:
    report = launcher.create(repo=Path("/opt/cf"), os_name="macos", dry_run=True)
    assert report["dry_run"] is True
    assert "factory start" in str(report["script"])
    assert list(desktop.iterdir()) == []


def test_batch_create_keeps_the_crlf_it_generated(desktop: Path) -> None:
    """`write_text` with the default newline would translate these on Windows."""
    report = launcher.create(repo=Path(r"C:\cf"), os_name="windows")
    raw = Path(str(report["path"])).read_bytes()
    assert raw.count(b"\r\n") > 5
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_remove_is_quiet_when_there_is_nothing_to_remove(desktop: Path) -> None:
    assert launcher.remove(os_name="macos")["removed"] is False
    launcher.create(repo=Path("/opt/cf"), os_name="macos")
    assert launcher.remove(os_name="macos")["removed"] is True
    assert list(desktop.iterdir()) == []


def test_status_reports_the_uv_fallback(desktop: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "which", lambda name: None)
    state = launcher.status(os_name="windows")
    assert state["installed"] is False
    assert state["uv"] == ""
    assert state["uv_fallback"].endswith("uv.exe")


def test_cli_launcher_create_remove_status(desktop: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["launcher", "create", "--os", "macos"]) == 0
    assert "콘텐츠공장.command" in capsys.readouterr().out
    assert main(["launcher", "status", "--os", "macos"]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is True
    assert main(["launcher", "remove", "--os", "macos"]) == 0
    capsys.readouterr()
    assert main(["launcher", "status", "--os", "macos"]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is False


def test_cli_launcher_create_dry_run_prints_the_script(
        desktop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["launcher", "create", "--os", "windows", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "chcp 65001" in out and "run factory start" in out
    assert list(desktop.iterdir()) == []


def test_scripts_prepend_the_usual_bin_directories_to_path(desktop: Path) -> None:
    """A double-clicked launcher inherits a PATH that never sourced a profile, so uv,
    the agent, node and ffmpeg are all on disk and invisible. The script fixes PATH
    once instead of every downstream command guessing."""
    body = launcher.script(repo=Path("/opt/contents-factory"), os_name="macos")
    assert 'PATH="$HOME/.local/bin:' in body
    assert "export PATH" in body
    assert "/opt/homebrew/bin" in body
    # The PATH line carries $HOME, not this machine's expanded home, so the same
    # script works for another account. (The uv fallback is still an absolute path:
    # it is a recorded location, not a lookup.)
    path_line = next(line for line in body.splitlines() if line.startswith('PATH='))
    assert str(Path.home()) not in path_line


def test_the_batch_path_line_stays_ascii_for_a_korean_account(desktop: Path) -> None:
    body = launcher.script(repo=Path(r"C:\cf"), os_name="windows")
    assert r"%USERPROFILE%\.local\bin" in body
    assert r"%USERPROFILE%\AppData\Roaming\npm" in body
    assert body.isascii(), "cmd.exe reads this in the console codepage, not UTF-8"
