"""Higgsfield authentication. The CLI owns the credential; this repo never stores one.

`higgsfield auth login` runs a browser OAuth PKCE flow and keeps the token in the
CLI's own credentials file, refreshing it as needed. So there is no static key to
paste anywhere: every place that needs a bearer asks `higgsfield auth token` for
the current one immediately before the call it is for.

The token is returned and nothing else. It is never logged, never put in an error
detail and never written to a file by this module — a stale copy in our own
process is exactly the failure mode (`HTTP_401` hours later) that the static key
produced.
"""
from __future__ import annotations

import re
import subprocess
from typing import Callable, Sequence

from .errors import FactoryError
from .osinfo import which

BINARY = "higgsfield"
TOKEN_ARGS: tuple[str, ...] = ("auth", "token")
LOGIN_COMMAND = "higgsfield auth login"
# One local subprocess that prints a stored token. It runs before every request, so
# the budget is short on purpose: a minute of hanging per call would stall a tick.
TOKEN_TIMEOUT_SECONDS = 20
# One opaque word. `auth token` prints the access token and nothing else, so
# anything with whitespace or prose in it is an error message, not a token.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,4096}$")

Runner = Callable[[Sequence[str], int], str]


def binary_path() -> str:
    """Where the CLI is, or `HIGGSFIELD_CLI_MISSING` with the install hint."""
    found = which(BINARY)
    if found is None:
        raise FactoryError("HIGGSFIELD_CLI_MISSING", BINARY)
    return found


def available() -> bool:
    return which(BINARY) is not None


def access_token(*, runner: Runner | None = None,
                 timeout: int = TOKEN_TIMEOUT_SECONDS) -> str:
    """The current access token, straight from the CLI.

    Not logged in is `HIGGSFIELD_LOGIN_REQUIRED`: the CLI exits non-zero, or
    prints something that is not a token. Either way the detail stays a code,
    because the thing the CLI printed could be the token itself.
    """
    invoke = runner or _subprocess_runner
    command = [BINARY if runner is not None else binary_path(), *TOKEN_ARGS]
    raw = invoke(command, timeout)
    token = str(raw or "").strip()
    if not token or not TOKEN_SHAPE.match(token):
        raise FactoryError("HIGGSFIELD_LOGIN_REQUIRED", "auth_token")
    return token


def token_provider(*, runner: Runner | None = None,
                   timeout: int = TOKEN_TIMEOUT_SECONDS) -> Callable[[], str]:
    """A callable that fetches a fresh token per call, for per-request use."""

    def provide() -> str:
        return access_token(runner=runner, timeout=timeout)

    return provide


def login_hint() -> str:
    return LOGIN_COMMAND


def _subprocess_runner(command: Sequence[str], timeout: int) -> str:
    try:
        done = subprocess.run(list(command), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        raise FactoryError("HIGGSFIELD_CLI_MISSING", BINARY) from None
    except subprocess.TimeoutExpired:
        raise FactoryError("HIGGSFIELD_LOGIN_REQUIRED", "auth_token_timeout") from None
    if done.returncode != 0:
        # stderr can carry the token (or a URL with it), so none of it travels.
        raise FactoryError("HIGGSFIELD_LOGIN_REQUIRED", f"exit_{done.returncode}")
    return done.stdout


def login_state(*, runner: Runner | None = None) -> tuple[bool, str]:
    """(logged_in, code) for `factory doctor`. Never returns the token."""
    if not available() and runner is None:
        return False, "HIGGSFIELD_CLI_MISSING"
    try:
        access_token(runner=runner)
    except FactoryError as error:
        return False, error.code
    return True, "OK"
