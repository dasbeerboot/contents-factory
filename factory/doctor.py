"""`factory doctor`: one table answering "will anything work on this machine?".

Setup used to fail one command at a time — `FFMPEG_MISSING` on the video stage,
`NODE_MISSING` on the Naver publisher, `HIGGSFIELD_LOGIN_REQUIRED` at the first
paid call — each discovered hours apart. This collects every host dependency in
one place, says where each one is, and attaches the install line for this OS to
the ones that are absent.

No secret is read and no paid call is made. The Higgsfield row runs
`higgsfield auth token` and reports only whether it answered.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from . import higgsfield, osinfo
from .config import Config, repo_root
from .spec.profile import list_profiles

# ffprobe ships with ffmpeg but is a separate executable and goes missing on its
# own in minimal builds, which is why it is checked rather than assumed.
BINARIES: tuple[str, ...] = ("ffmpeg", "ffprobe", "node", "uv", "higgsfield", "aside")
# What breaks when each one is absent, so a missing row is a decision not a worry.
NEEDED_FOR: dict[str, str] = {
    "ffmpeg": "video assemble, captions, frame gates",
    "ffprobe": "media probing for every video gate",
    "node": "naver_blog publisher",
    "uv": "running this project (uv run factory ...)",
    "higgsfield": "image and video generation, TikTok auth",
    "aside": "naver_blog aside backend (macOS only)",
}
OPTIONAL: frozenset[str] = frozenset({"aside"})
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_SKIP = "n/a"


def binary_rows(os_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in BINARIES:
        found = osinfo.which(name)
        if name == "aside" and os_name != "macos":
            rows.append({"name": name, "status": STATUS_SKIP, "path": "",
                         "needed_for": NEEDED_FOR[name],
                         "hint": "naver_blog uses the playwright backend here"})
            continue
        rows.append({"name": name,
                     "status": STATUS_OK if found else STATUS_MISSING,
                     "path": found or "",
                     "needed_for": NEEDED_FOR[name],
                     "optional": name in OPTIONAL,
                     "hint": "" if found else osinfo.install_hint(name, os_name)})
    return rows


def report(config: Config, *,
           token_runner: Callable[[Sequence[str], int], str] | None = None,
           scheduler_status: Callable[[], dict[str, object]] | None = None,
           ) -> dict[str, Any]:
    import sys

    configured = config.os
    detected = osinfo.detect_os()
    home = config.home_path
    logged_in, login_code = higgsfield.login_state(runner=token_runner)
    runner = osinfo.scheduler_for(configured)
    try:
        profiles = list_profiles(home, repo_root())
    except OSError:
        profiles = []
    return {
        "os": {"configured": configured, "detected": detected,
               "agrees": configured == detected,
               "home_display": osinfo.home_display(configured)},
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "home": {"path": str(home), "exists": home.is_dir(),
                 "queue": (home / "queue").is_dir()},
        "binaries": binary_rows(configured),
        "higgsfield_login": {"logged_in": logged_in, "code": login_code,
                             "fix": "" if logged_in else higgsfield.login_hint()},
        "profiles": profiles,
        "scheduler": _scheduler(runner, scheduler_status),
    }


def _scheduler(runner: str,
               override: Callable[[], dict[str, object]] | None) -> dict[str, object]:
    if override is not None:
        return {"runner": runner, **override()}
    from .scheduler import cron, launchd, windows

    if runner == "launchd":
        return {"runner": runner, **launchd.status()}
    if runner == "schtasks":
        return {"runner": runner, **windows.status()}
    return {"runner": runner, **cron.status()}


def blockers(data: dict[str, Any]) -> list[str]:
    """Rows that stop something from working, in the order they should be fixed."""
    out = [f"{row['name']} {STATUS_MISSING}"
           for row in data.get("binaries", [])
           if row.get("status") == STATUS_MISSING and not row.get("optional")]
    if not data["higgsfield_login"]["logged_in"]:
        out.append(data["higgsfield_login"]["code"])
    if not data["home"]["exists"]:
        out.append("HOME_MISSING")
    return out


def render(data: dict[str, Any]) -> str:
    lines: list[str] = ["factory doctor", ""]
    os_row = data["os"]
    note = "" if os_row["agrees"] else f"  (detected {os_row['detected']})"
    rows: list[tuple[str, str, str]] = [
        ("os", os_row["configured"] + note, "factory init --os <name> to change"),
        ("python", data["python"]["version"], data["python"]["executable"]),
        ("home", data["home"]["path"],
         "ok" if data["home"]["exists"] else "run factory init"),
    ]
    for row in data["binaries"]:
        detail = row["path"] or row.get("hint") or ""
        mark = row["status"]
        if mark == STATUS_MISSING and row.get("optional"):
            mark = "missing (optional)"
        rows.append((row["name"], mark, detail))
    login = data["higgsfield_login"]
    rows.append(("higgsfield login", "ok" if login["logged_in"] else login["code"],
                 login["fix"]))
    scheduler = data["scheduler"]
    rows.append(("scheduler", str(scheduler.get("runner")),
                 "installed" if scheduler.get("installed") else "not installed"))
    rows.append(("profiles", str(len(data["profiles"])),
                 ", ".join(data["profiles"][:6])))
    width = max(len(name) for name, _value, _detail in rows)
    for name, value, detail in rows:
        lines.append(f"  {name.ljust(width)}  {value}" + (f"  {detail}" if detail else ""))
    found = blockers(data)
    lines.append("")
    lines.append("blocking: " + (", ".join(found) if found else "nothing"))
    return "\n".join(lines)


def paths_note(os_name: str) -> str:
    """Where this OS keeps the runtime home, for the setup skill to read back."""
    return f"{osinfo.home_display(os_name)} ({osinfo.scheduler_for(os_name)})"
