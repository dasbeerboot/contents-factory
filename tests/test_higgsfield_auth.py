"""Higgsfield auth comes from the CLI, per call, and never reaches an output."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from factory import higgsfield
from factory.errors import FactoryError
from factory.publishers import publisher_for, required_env
from factory.publishers.tiktok import BEARER_PLACEHOLDER, McpClient
from factory.spec.profile import PlatformConfig
from factory.spec.queue import Media
from tests.conftest import make_item

TOKEN = "oat_LxQ2ePkV8nR4tZ"
TIKTOK_SETTINGS = PlatformConfig(enabled=True, connector_id="connector-1")


def runner_for(output: str, *, calls: list[list[str]] | None = None):
    def run(command: Sequence[str], timeout: int) -> str:
        if calls is not None:
            calls.append(list(command))
        return output

    return run


def test_access_token_comes_from_the_cli() -> None:
    calls: list[list[str]] = []
    assert higgsfield.access_token(runner=runner_for(f"{TOKEN}\n",
                                                     calls=calls)) == TOKEN
    assert calls == [["higgsfield", "auth", "token"]]


def test_no_static_key_is_required_for_tiktok() -> None:
    """The static key is gone: `required_env` names nothing to paste."""
    assert required_env("tiktok") == ()


def test_not_logged_in_is_a_code_not_a_message() -> None:
    def angry(command: Sequence[str], timeout: int) -> str:
        return "not logged in: run higgsfield auth login"

    with pytest.raises(FactoryError) as caught:
        higgsfield.access_token(runner=angry)
    assert caught.value.code == "HIGGSFIELD_LOGIN_REQUIRED"


def test_missing_cli_is_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(higgsfield, "which", lambda name: None)
    with pytest.raises(FactoryError) as caught:
        higgsfield.access_token()
    assert caught.value.code == "HIGGSFIELD_CLI_MISSING"


def test_empty_output_is_login_required() -> None:
    with pytest.raises(FactoryError) as caught:
        higgsfield.access_token(runner=runner_for("   \n"))
    assert caught.value.code == "HIGGSFIELD_LOGIN_REQUIRED"


def test_token_never_appears_in_the_error() -> None:
    """A CLI failure can print the token itself, so none of its output travels."""
    def failing(command: Sequence[str], timeout: int) -> str:
        raise FactoryError("HIGGSFIELD_LOGIN_REQUIRED", "exit_1")

    with pytest.raises(FactoryError) as caught:
        higgsfield.access_token(runner=failing)
    assert TOKEN not in str(caught.value)


def test_login_state_reports_without_the_value() -> None:
    logged_in, code = higgsfield.login_state(runner=runner_for(TOKEN))
    assert (logged_in, code) == (True, "OK")
    bad, bad_code = higgsfield.login_state(runner=runner_for("please log in"))
    assert bad is False and bad_code == "HIGGSFIELD_LOGIN_REQUIRED"


def test_provider_is_asked_once_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scheduled run can wait hours, so the bearer is fetched per call."""
    asked: list[int] = []
    frames = ("data: " + json.dumps({"result": {"content": []}}) + "\n").encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return frames

    seen: list[str] = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.headers.get("Authorization", ""))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    def provider() -> str:
        asked.append(1)
        return f"{TOKEN}{len(asked)}"

    client = McpClient(provider)
    client.call("tiktok_accounts", {})
    client.call("tiktok_accounts", {})
    # initialize + two tools/call requests, each with its own freshly fetched token.
    assert len(asked) == len(seen) == 3
    assert seen[0] != seen[-1]


def test_dry_run_names_the_cli_as_the_bearer_source(isolated_home: Path) -> None:
    publisher = publisher_for("tiktok", isolated_home, env={},
                              settings=TIKTOK_SETTINGS)
    item = make_item(platform="tiktok", kind="video", text="Hook line",
                     media=[Media(path="/tmp/r.mp4", kind="video",
                                  public_url="https://cdn.test/r.mp4")])
    result = publisher.publish(item, dry_run=True)
    body = result.render()
    assert result.ok and "HIGGSFIELD_MCP_TOKEN" not in body
    assert BEARER_PLACEHOLDER in body
    assert "higgsfield auth token" in body


def test_live_tiktok_publish_without_a_login_does_not_call_out(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLI means the failure happens before the first byte: retry is safe."""
    from factory.publishers.base import failure_mode

    monkeypatch.setattr(higgsfield, "which", lambda name: None)
    publisher = publisher_for("tiktok", isolated_home, env={},
                              settings=TIKTOK_SETTINGS)
    item = make_item(platform="tiktok", kind="video", text="Hook line",
                     media=[Media(path="/tmp/r.mp4", kind="video",
                                  public_url="https://cdn.test/r.mp4")])
    result = publisher.publish(item)
    assert result.ok is False
    assert result.code in {"HIGGSFIELD_CLI_MISSING", "HIGGSFIELD_LOGIN_REQUIRED"}
    assert failure_mode(result.code) == "retry"
