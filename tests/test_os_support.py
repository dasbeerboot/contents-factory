"""macOS, Windows and Linux: the places the code branches on the host.

Windows is covered by mocking `platform.system`, `shutil.which` and `subprocess.run`,
which is the honest limit of a macOS test run: these assert that the right command is
built and the right branch is taken, not that Task Scheduler accepted it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from factory import doctor, osinfo
from factory.cli import main
from factory.config import Config, load_config, write_os
from factory.credentials import env_set
from factory.errors import FactoryError
from factory.publishers import naver_blog
from factory.scheduler import cron, launchd, runner_for, windows


def test_detect_maps_platform_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for system, expected in (("Darwin", "macos"), ("Windows", "windows"),
                             ("Linux", "linux"), ("FreeBSD", "linux")):
        monkeypatch.setattr("platform.system", lambda value=system: value)
        assert osinfo.detect_os() == expected


def test_normalize_accepts_aliases_and_refuses_junk() -> None:
    assert osinfo.normalize("Darwin") == "macos"
    assert osinfo.normalize("WIN32") == "windows"
    with pytest.raises(FactoryError) as caught:
        osinfo.normalize("plan9")
    assert caught.value.code == "OS_UNKNOWN"


def test_which_finds_the_exe_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows every external binary is `name.exe`; the lookup has to try it."""
    def only_exe(name: str) -> str | None:
        return r"C:\tools\ffmpeg.exe" if name == "ffmpeg.exe" else None

    monkeypatch.setattr("shutil.which", only_exe)
    assert osinfo.which("ffmpeg") == r"C:\tools\ffmpeg.exe"
    assert osinfo.which("node") is None


def test_home_display_and_scheduler_per_os() -> None:
    assert osinfo.home_display("windows") == r"%USERPROFILE%\.contents-factory"
    assert osinfo.home_display("macos") == "~/.contents-factory"
    assert osinfo.scheduler_for("windows") == "schtasks"
    assert osinfo.scheduler_for("macos") == "launchd"
    assert osinfo.scheduler_for("linux") == "cron"
    assert runner_for("windows") == "schtasks"


def test_expand_uses_the_real_home(monkeypatch: pytest.MonkeyPatch,
                                   tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert osinfo.expand("~/x") == tmp_path / "x"
    assert osinfo.expand("~") == tmp_path


def test_secure_file_chmods_on_posix(tmp_path: Path) -> None:
    target = tmp_path / "credentials.env"
    target.write_text("A=1\n", encoding="utf-8")
    target.chmod(0o644)
    assert osinfo.secure_file(target, os_name="linux") == ""
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_secure_file_uses_icacls_on_windows(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "credentials.env"
    target.write_text("A=1\n", encoding="utf-8")
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("USERNAME", "ada")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert osinfo.secure_file(target, os_name="windows") == ""
    assert seen == [["icacls", str(target), "/inheritance:r", "/grant:r", "ada:F"]]


def test_windows_acl_failure_warns_rather_than_losing_the_token(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "credentials.env"

    def missing(command, **kwargs):
        raise FileNotFoundError("icacls")

    monkeypatch.setenv("USERNAME", "ada")
    monkeypatch.setattr(subprocess, "run", missing)
    monkeypatch.setattr(osinfo, "detect_os", lambda: "windows")
    warning = env_set(target, "THREADS_ACCESS_TOKEN", "value-stays-private")
    assert warning == "FILE_ACL_UNSET"
    # The point of warning instead of raising: the token is written.
    assert "THREADS_ACCESS_TOKEN=" in target.read_text(encoding="utf-8")


def test_config_carries_the_os_and_init_records_it(isolated_home: Path,
                                                   tmp_path: Path) -> None:
    config_path = tmp_path / "factory.toml"
    write_os(config_path, "windows")
    assert 'os = "windows"' in config_path.read_text(encoding="utf-8")
    assert load_config(config_path).os == "windows"
    # Replacing, not appending a second line.
    write_os(config_path, "linux")
    body = config_path.read_text(encoding="utf-8")
    assert body.count("os = ") == 1 and 'os = "linux"' in body


def test_write_os_refuses_an_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(FactoryError):
        write_os(tmp_path / "factory.toml", "plan9")


def test_cli_init_with_os_flag(isolated_home: Path, tmp_path: Path,
                               capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--os", "windows"]) == 0
    out = capsys.readouterr().out
    assert "os=windows" in out
    assert r"%USERPROFILE%\.contents-factory" in out
    assert "schtasks" in out
    assert 'os = "windows"' in (isolated_home / "factory.toml").read_text(
        encoding="utf-8")


def test_cli_init_without_the_flag_asks_for_confirmation(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "detected" in out and "factory init --os" in out


def test_schtasks_create_command_shape(isolated_home: Path) -> None:
    command = windows.create_command(home=isolated_home, python=r"C:\py\pythonw.exe",
                                     interval=900)
    assert command[:4] == ["schtasks", "/Create", "/TN", windows.TASK_NAME]
    assert command[-5:] == ["/SC", "MINUTE", "/MO", "15", "/F"]
    action = command[command.index("/TR") + 1]
    # A Task Scheduler action carries no environment, so FACTORY_HOME travels in it.
    assert f'set "FACTORY_HOME={isolated_home}"' in action
    assert r"C:\py\pythonw.exe" in action
    assert "schedule tick" in action


def test_schtasks_interval_is_clamped_to_minutes() -> None:
    assert windows.interval_minutes(900) == 15
    assert windows.interval_minutes(30) == 1
    assert windows.interval_minutes(10 ** 7) == 1439


def test_schtasks_install_and_status(isolated_home: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        if command[1] == "/Query":
            return subprocess.CompletedProcess(command, 0, "TaskName: \\contents-factory-tick\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert windows.install(home=isolated_home, python="py") is True
    state = windows.status()
    assert state["installed"] is True and state["runner"] == "schtasks"
    assert seen[0][1] == "/Create" and seen[1][1] == "/Query"


def test_schtasks_absent_is_a_false_not_a_crash(isolated_home: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    assert windows.install(home=isolated_home, python="py") is False
    assert windows.remove() is False
    assert windows.status()["installed"] is False


def test_schedule_runner_auto_follows_the_configured_os(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    (isolated_home.parent / "factory.toml").write_text(
        f'ui_lang = "en"\nos = "windows"\nhome = "{isolated_home}"\n', encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(windows, "status", lambda: calls.append("schtasks")
                        or {"installed": False})
    monkeypatch.setattr(launchd, "status", lambda: calls.append("launchd") or {})
    monkeypatch.setattr(cron, "status", lambda: calls.append("cron") or {})
    assert main(["schedule", "status"]) == 0
    assert calls == ["schtasks"]
    assert '"runner": "schtasks"' in capsys.readouterr().out


def test_naver_backend_default_follows_the_os() -> None:
    assert naver_blog.default_backend("macos") == "aside"
    assert naver_blog.default_backend("windows") == "playwright"
    assert naver_blog.default_backend("linux") == "playwright"


def test_naver_job_carries_the_backend_and_profile_dir(isolated_home: Path) -> None:
    from factory.spec.profile import PlatformConfig
    from tests.conftest import make_item

    publisher = naver_blog.NaverBlogPublisher(
        isolated_home, env={},
        settings=PlatformConfig(enabled=True, blog_id="examplelog",
                                backend="playwright"))
    item = make_item(platform="naver_blog", text="## Title\nx\n\n## Body\ny",
                     extra={"title": "t", "category": "Notes", "tags": "a"})
    result = publisher.publish(item, dry_run=True)
    fields = result.requests[0].fields
    assert fields["backend"] == "playwright"
    assert str(naver_blog.user_data_dir(isolated_home)) in fields["session"]
    # The aside binary is irrelevant to the playwright backend, so it is not named.
    assert fields["env"] == {}


def test_platforms_json_rejects_an_unknown_backend() -> None:
    """`backend` is a closed set, so a typo fails at load rather than at publish."""
    from pydantic import ValidationError

    from factory.spec.profile import PlatformConfig

    with pytest.raises(ValidationError):
        PlatformConfig(enabled=True, blog_id="x1y2", backend="firefox")


def test_naver_backend_unset_falls_back_to_the_os(isolated_home: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.spec.profile import PlatformConfig
    from tests.conftest import make_item

    monkeypatch.setattr(naver_blog, "_configured_os", lambda: "windows")
    publisher = naver_blog.NaverBlogPublisher(
        isolated_home, env={},
        settings=PlatformConfig(enabled=True, blog_id="x1y2", backend=None))
    item = make_item(platform="naver_blog", extra={"title": "t", "category": "c",
                                                   "tags": "a"})
    assert publisher.backend(item) == "playwright"
    assert publisher.publish(item, dry_run=True).requests[0].fields["backend"] == (
        "playwright")


def test_node_missing_is_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(naver_blog, "which", lambda name: None)
    with pytest.raises(FactoryError) as caught:
        naver_blog.node_binary()
    assert caught.value.code == "NODE_MISSING"


def test_doctor_lists_every_binary_with_an_install_hint(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osinfo, "which", lambda name: None)
    monkeypatch.setattr(doctor.osinfo, "which", lambda name: None)
    data = doctor.report(Config(home=str(isolated_home), os="windows"),
                         token_runner=lambda command, timeout: "",
                         scheduler_status=lambda: {"installed": False})
    names = [row["name"] for row in data["binaries"]]
    assert names == list(doctor.BINARIES)
    assert data["os"]["configured"] == "windows"
    assert data["scheduler"]["runner"] == "schtasks"
    # aside cannot exist off macOS, so it is "n/a" rather than a missing dependency.
    aside = next(row for row in data["binaries"] if row["name"] == "aside")
    assert aside["status"] == doctor.STATUS_SKIP
    ffmpeg = next(row for row in data["binaries"] if row["name"] == "ffmpeg")
    assert "winget" in ffmpeg["hint"]
    assert "ffmpeg missing" in doctor.blockers(data)
    assert "HIGGSFIELD_LOGIN_REQUIRED" in doctor.blockers(data)


def test_doctor_is_clean_when_everything_resolves(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.osinfo, "which", lambda name: f"/usr/bin/{name}")
    data = doctor.report(Config(home=str(isolated_home), os="macos"),
                         token_runner=lambda command, timeout: "oat_abcdefghijklmno",
                         scheduler_status=lambda: {"installed": True})
    assert doctor.blockers(data) == []
    rendered = doctor.render(data)
    assert "blocking: nothing" in rendered
    assert "oat_" not in rendered


def test_cli_doctor_json(isolated_home: Path,
                         capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["os"]["detected"] in osinfo.OS_CHOICES
    assert {row["name"] for row in payload["binaries"]} == set(doctor.BINARIES)
    assert code in (0, 3)


# --------------------------------------------------- naver, the playwright backend
# The live publish path cannot be exercised here: it needs a logged-in browser and
# would post for real. What is asserted is the plan, the backend routing and the
# missing-dependency codes.

NAVER_VENDOR = (Path(__file__).resolve().parent.parent / "factory" / "publishers"
                / "naver")


def _node_or_skip() -> None:
    if osinfo.which("node") is None:
        pytest.skip("node is not installed")


def test_naver_playwright_dry_run_plan_names_the_profile(isolated_home: Path) -> None:
    from factory.spec.profile import PlatformConfig

    _node_or_skip()
    publisher = naver_blog.NaverBlogPublisher(
        isolated_home, env={},
        settings=PlatformConfig(enabled=True, blog_id="examplelog",
                                backend="playwright"))
    job = {"blogId": "examplelog", "title": "One cached header", "category": "Notes",
           "tags": ["build"], "publishAt": "2026-10-20T21:00",
           "backend": "playwright",
           "userDataDir": str(naver_blog.user_data_dir(isolated_home)),
           "markdown": "## Title\nOne cached header\n\n## Body\nb\n\n## Tags\n#build\n"}
    result = publisher._invoke(job, dry_run=True)
    assert result["ok"] is True and result["code"] == "DRY_RUN"
    plan = result["plan"]
    assert plan["backend"] == "playwright"
    assert str(naver_blog.user_data_dir(isolated_home)) in plan["session"]
    assert plan["steps"] == ["start_cors_server", "ensure_editor", "inject_document",
                             "validate_editor", "publish_panel"]


def test_naver_aside_plan_still_says_aside(isolated_home: Path) -> None:
    from factory.spec.profile import PlatformConfig

    _node_or_skip()
    publisher = naver_blog.NaverBlogPublisher(
        isolated_home, env={},
        settings=PlatformConfig(enabled=True, blog_id="examplelog", backend="aside"))
    job = {"blogId": "examplelog", "title": "t", "category": "Notes", "tags": ["a"],
           "publishAt": "2026-10-20T21:00", "backend": "aside",
           "markdown": "## Title\nt\n\n## Body\nb\n\n## Tags\n#a\n"}
    result = publisher._invoke(job, dry_run=True)
    assert result["plan"]["backend"] == "aside"
    assert "aside" in result["plan"]["session"]


def test_naver_unknown_backend_is_refused_by_the_node_script(
        isolated_home: Path) -> None:
    from factory.spec.profile import PlatformConfig

    _node_or_skip()
    publisher = naver_blog.NaverBlogPublisher(
        isolated_home, env={}, settings=PlatformConfig(enabled=True, blog_id="x1y2"))
    job = {"blogId": "x1y2", "title": "t", "category": "Notes", "tags": ["a"],
           "publishAt": "2026-10-20T21:00", "backend": "firefox",
           "markdown": "## Title\nt\n\n## Body\nb\n\n## Tags\n#a\n"}
    assert publisher._invoke(job, dry_run=True) == {"ok": False,
                                                    "code": "NAVER_BACKEND_UNKNOWN"}


def test_naver_login_without_playwright_says_so(isolated_home: Path) -> None:
    """Playwright is not a dependency of this repo, so its absence is a named code."""
    _node_or_skip()
    result = naver_blog.login(isolated_home, status=True)
    assert result["ok"] is False
    assert result["code"] in {"PLAYWRIGHT_MISSING", "PLAYWRIGHT_BROWSER_MISSING",
                              "NAVER_SESSION_MISSING"}


def test_cli_naver_status_exits_nonzero_without_a_session(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Either "log in" or "install playwright" — whichever is actually the blocker."""
    _node_or_skip()
    assert main(["naver", "status"]) == 3
    out = capsys.readouterr().out
    assert "factory naver login" in out or "playwright" in out.lower()


def test_naver_failure_codes_keep_their_retry_meaning() -> None:
    """A phase suffix must not collapse the code: `UNEXPECTED` would mean "unknown"."""
    from factory.publishers.base import failure_mode

    cases = {
        "PUBLISH_NOT_SENT:CATEGORY": ("PUBLISH_NOT_SENT", "retry"),
        "PUBLISH_OUTCOME_UNCERTAIN:PUBLISH": ("PUBLISH_OUTCOME_UNCERTAIN", "unknown"),
        "ASIDE_TIMEOUT": ("NAVER_SESSION_MISSING", "retry"),
        "PLAYWRIGHT_MISSING": ("PLAYWRIGHT_MISSING", "retry"),
        "PLAYWRIGHT_BROWSER_MISSING": ("PLAYWRIGHT_BROWSER_MISSING", "retry"),
    }
    for raw, (code, mode) in cases.items():
        error = naver_blog.NaverBlogPublisher._failure(raw)
        assert error.code == code, raw
        assert failure_mode(error.code) == mode, raw


def test_nothing_reached_naver_codes_are_all_retry_safe() -> None:
    """Every pre-editor failure is retryable: no human reopen for a missing browser."""
    from factory.publishers.base import failure_mode

    for code in ("PLAYWRIGHT_MISSING", "PLAYWRIGHT_LOAD_FAILED",
                 "PLAYWRIGHT_BROWSER_MISSING", "PLAYWRIGHT_LAUNCH_FAILED",
                 "NAVER_PROFILE_MISSING", "MULTIPLE_EDITOR_TABS",
                 "RESUME_CANCEL_MISSING", "NAVER_BACKEND_UNKNOWN"):
        assert failure_mode(code) == "retry", code


def test_session_directory_is_owner_only(isolated_home: Path) -> None:
    """The Chromium profile holds the Naver cookies, so it is not left at 0755."""
    directory = isolated_home / "state" / "naver-profile"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    assert osinfo.secure_directory(directory, os_name="linux") == ""
    assert oct(directory.stat().st_mode & 0o777) == "0o700"


def test_cron_install_honours_the_interval() -> None:
    assert cron.schedule_for(900) == "*/15 * * * *"
    assert cron.schedule_for(60) == "*/1 * * * *"
    # A crontab minute field cannot say "every 90 minutes", so it clamps.
    assert cron.schedule_for(10 ** 6) == "*/59 * * * *"


def test_schtasks_action_sets_a_working_directory(isolated_home: Path) -> None:
    """A scheduled task starts in System32; uv and the vendored scripts need the repo."""
    action = windows.action(home=isolated_home, python="py",
                            working_directory=Path("C:/repo/contents-factory"))
    assert action.startswith('cmd /c cd /d "C:/repo/contents-factory"')
    assert f'set "FACTORY_HOME={isolated_home}"' in action


def test_token_refresh_reports_a_permission_failure(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tick calls this unattended, so a failed lockdown cannot be dropped."""
    from factory.scheduler import token_refresh

    monkeypatch.setattr(token_refresh, "env_set", lambda *args: "FILE_ACL_UNSET")
    monkeypatch.setattr(token_refresh, "load_env",
                        lambda target: {"THREADS_ACCESS_TOKEN": "old-token"})
    result = token_refresh.refresh(isolated_home, "_example", "threads",
                                   fetch=lambda url, params: {"access_token": "new"})
    assert result["warning"] == "FILE_ACL_UNSET"


# --------------------------------------------------- the playwright module resolver
# A stub module stands in for playwright: it proves the loader imports an absolute
# path (ESM cannot import a bare global name) and that the session check reads the
# Naver cookie rather than guessing.

STUB_MODULE = """
export const chromium = {
  async launchPersistentContext() {
    const cookies = process.env.STUB_COOKIE
      ? [{ name: process.env.STUB_COOKIE, value: "x" }] : [];
    return {
      pages: () => [],
      async newPage() { return { goto: async () => {}, url: () => "" }; },
      async cookies() { return cookies; },
      async close() {},
    };
  },
};
"""


def _run_node(script: str, *, env: dict[str, str]) -> dict[str, object]:
    done = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60, check=False,
                          env={**os.environ, **env})
    assert done.returncode == 0, done.stderr[-400:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _stub_package(tmp_path: Path) -> Path:
    package = tmp_path / "stub-playwright"
    package.mkdir(exist_ok=True)
    (package / "index.js").write_text(STUB_MODULE, encoding="utf-8")
    (package / "package.json").write_text(
        '{"name": "stub-playwright", "main": "index.js", "type": "module"}',
        encoding="utf-8")
    return package


def test_playwright_loader_accepts_a_directory_or_an_entry_file(tmp_path: Path) -> None:
    _node_or_skip()
    package = _stub_package(tmp_path)
    driver = NAVER_VENDOR / "driver_playwright.mjs"
    profile = tmp_path / "profile"
    script = (
        f"const {{ sessionState }} = await import({json.dumps(str(driver))});\n"
        f"const result = await sessionState({{ userDataDir: {json.dumps(str(profile))} }});\n"
        "console.log(JSON.stringify(result));\n")
    # A directory is the shape a user types; ESM cannot import one, so it is resolved.
    logged_out = _run_node(script, env={"FACTORY_PLAYWRIGHT_MODULE": str(package)})
    assert logged_out["logged_in"] is False
    # The entry file, with a live Naver session cookie present.
    logged_in = _run_node(script, env={
        "FACTORY_PLAYWRIGHT_MODULE": str(package / "index.js"),
        "STUB_COOKIE": "NID_AUT"})
    assert logged_in["logged_in"] is True
    # An unrelated Naver cookie is not a session.
    other = _run_node(script, env={"FACTORY_PLAYWRIGHT_MODULE": str(package),
                                   "STUB_COOKIE": "NNB"})
    assert other["logged_in"] is False


def test_playwright_loader_missing_module_is_a_code(tmp_path: Path) -> None:
    _node_or_skip()
    driver = NAVER_VENDOR / "driver_playwright.mjs"
    script = (
        f"const {{ loadPlaywright }} = await import({json.dumps(str(driver))});\n"
        "try {\n"
        "  await loadPlaywright();\n"
        '  console.log(JSON.stringify({ code: "LOADED" }));\n'
        "} catch (error) {\n"
        "  console.log(JSON.stringify({ code: error.code }));\n"
        "}\n")
    result = _run_node(script, env={
        "FACTORY_PLAYWRIGHT_MODULE": str(tmp_path / "nope" / "index.js")})
    assert result["code"] == "PLAYWRIGHT_MISSING"
