from ..osinfo import scheduler_for
from . import cron, launchd, tick, token_refresh, windows

RUNNERS: tuple[str, ...] = ("launchd", "cron", "schtasks")


def runner_for(os_name: str | None = None) -> str:
    """The scheduler this OS uses. `schedule --runner auto` resolves through here."""
    return scheduler_for(os_name)


__all__ = ["RUNNERS", "cron", "launchd", "runner_for", "tick", "token_refresh",
           "windows"]
