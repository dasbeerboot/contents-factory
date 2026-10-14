"""OAuth flow. Every HTTP call is mocked; the real one would burn a single use code."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from factory.auth import instagram as instagram_auth
from factory.auth import threads as threads_auth
from factory.auth.base import code_from_redirect, consent_url
from factory.cli import main
from factory.credentials import credentials_path, load_env
from factory.errors import FactoryError

APP_ID = "1430408979109687"
REDIRECT = "https://koreansaju.xyz/admin"
CODE = "AQD-oneTimeCode123"
SHORT = "short-lived-token"
LONG = "long-lived-token"
NOW = datetime(2026, 10, 14, 9, 30, 0, tzinfo=timezone.utc)
ALL_SCOPES = list(threads_auth.SCOPES)


@pytest.fixture(autouse=True)
def _no_ambient_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_profile_env also reads os.environ, so a developer's shell must not pick the
    exchange route for these tests."""
    for key in ("THREADS_APP_SECRET", "THREADS_APP_ID", "THREADS_REDIRECT_URI",
                "IG_APP_ID", "IG_APP_SECRET", "IG_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)


class Recorder:
    """Stands in for post_form / get_json / post_json and remembers every call."""

    def __init__(self, *, scopes: list[str] | None = None,
                 username: str = "saju.unni.kr", user_id: str = "17841400000000001",
                 delegated: dict[str, Any] | None = None) -> None:
        self.scopes = ALL_SCOPES if scopes is None else scopes
        self.username = username
        self.user_id = user_id
        self.delegated = delegated
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post_form(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        self.calls.append(("POST", url, dict(fields)))
        if url == threads_auth.TOKEN_URL:
            return {"access_token": SHORT}
        raise AssertionError(url)

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("POST_JSON", url, dict(payload)))
        return self.delegated if self.delegated is not None else {"access_token": LONG}

    def get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append(("GET", url, dict(params)))
        if url == threads_auth.LONG_LIVED_URL:
            return {"access_token": LONG, "expires_in": 5183944}
        if url == threads_auth.DEBUG_TOKEN_URL:
            return {"data": {"scopes": list(self.scopes)}}
        if url == threads_auth.ME_URL:
            return {"id": self.user_id, "username": self.username}
        raise AssertionError(url)

    def urls(self) -> list[str]:
        return [url for _method, url, _fields in self.calls]

    def fields_for(self, url: str) -> dict[str, Any]:
        for _method, target, fields in self.calls:
            if target == url:
                return fields
        raise AssertionError(url)


def connect(home: Path, recorder: Recorder, *, pasted: str,
            exchange_url: str | None = None) -> Any:
    return threads_auth.connect(home, "acct", pasted=pasted, app_id=APP_ID,
                                redirect_uri=REDIRECT, exchange_url=exchange_url,
                                post_form_fn=recorder.post_form,
                                get_json_fn=recorder.get_json,
                                post_json_fn=recorder.post_json, now=NOW)


def write_secret(home: Path, **values: str) -> Path:
    path = credentials_path(home, "acct")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()),
                    encoding="utf-8")
    path.chmod(0o600)
    return path


# ---------------------------------------------------------------- code extraction

@pytest.mark.parametrize("pasted", [
    f"https://koreansaju.xyz/admin?code={CODE}#_",
    f"https://koreansaju.xyz/admin?code={CODE}",
    f"  https://koreansaju.xyz/admin/?state=x&code={CODE}#_  ",
    f'"{CODE}#_"',
    CODE,
])
def test_code_is_pulled_out_of_anything_the_user_pastes(pasted: str) -> None:
    assert code_from_redirect(pasted) == CODE


def test_denied_consent_is_its_own_code_not_an_empty_one() -> None:
    with pytest.raises(FactoryError) as error:
        code_from_redirect("https://koreansaju.xyz/admin?error=access_denied"
                           "&error_reason=user_denied")
    assert error.value.code == "CONSENT_DENIED"


@pytest.mark.parametrize("pasted, code", [
    ("", "CODE_MISSING"),
    ("https://koreansaju.xyz/admin", "CODE_MISSING"),
    ("https://koreansaju.xyz/admin?code=", "CODE_MISSING"),
    ("x" * 5000, "CODE_TOO_LONG"),
    ("a" * 600, "CODE_INVALID"),
])
def test_unusable_pastes_raise_codes(pasted: str, code: str) -> None:
    with pytest.raises(FactoryError) as error:
        code_from_redirect(pasted)
    assert error.value.code == code


# ---------------------------------------------------------------- consent url

def test_consent_url_carries_the_scopes_that_end_up_in_the_token() -> None:
    url = threads_auth.authorize_url(app_id=APP_ID, redirect_uri=REDIRECT)
    assert url.startswith(threads_auth.AUTHORIZE_BASE + "?")
    assert f"client_id={APP_ID}" in url
    assert "response_type=code" in url
    assert "threads_content_publish" in url
    assert "koreansaju.xyz%2Fadmin" in url


def test_plain_http_redirect_is_refused() -> None:
    with pytest.raises(FactoryError) as error:
        consent_url("https://x/authorize", app_id=APP_ID,
                    redirect_uri="http://localhost:3000/cb", scopes=("a",))
    assert error.value.code == "REDIRECT_URI_INVALID"


def test_app_id_has_to_be_numeric() -> None:
    with pytest.raises(FactoryError) as error:
        threads_auth.resolve_app_id({}, "my-app")
    assert error.value.code == "APP_ID_INVALID"


def test_app_id_and_redirect_fall_back_to_the_profile_env() -> None:
    env = {"THREADS_APP_ID": APP_ID, "THREADS_REDIRECT_URI": "https://example.test/cb"}
    assert threads_auth.resolve_app_id(env, None) == APP_ID
    assert threads_auth.resolve_redirect(env, None) == "https://example.test/cb"
    assert threads_auth.resolve_redirect({}, None) == threads_auth.DEFAULT_REDIRECT_URI


# ---------------------------------------------------------------- direct exchange

def test_direct_route_does_short_then_long_then_writes_credentials(
        isolated_home: Path) -> None:
    write_secret(isolated_home, THREADS_APP_SECRET="app-secret")
    recorder = Recorder()
    result = connect(isolated_home, recorder,
                     pasted=f"https://koreansaju.xyz/admin?code={CODE}#_")

    assert recorder.urls() == [threads_auth.TOKEN_URL, threads_auth.LONG_LIVED_URL,
                               threads_auth.DEBUG_TOKEN_URL, threads_auth.ME_URL]
    short = recorder.fields_for(threads_auth.TOKEN_URL)
    assert short["grant_type"] == "authorization_code"
    assert short["code"] == CODE
    assert short["redirect_uri"] == REDIRECT
    assert recorder.fields_for(threads_auth.LONG_LIVED_URL)["grant_type"] \
        == "th_exchange_token"

    env = load_env(credentials_path(isolated_home, "acct"))
    assert env["THREADS_ACCESS_TOKEN"] == LONG
    assert env["THREADS_USER_ID"] == "17841400000000001"
    assert env["THREADS_TOKEN_ISSUED_AT"] == "2026-10-14"
    assert result.route == "direct"
    assert result.username == "saju.unni.kr"
    assert result.written_keys == ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID",
                                   "THREADS_TOKEN_ISSUED_AT")


def test_credentials_file_stays_at_mode_600(isolated_home: Path) -> None:
    path = write_secret(isolated_home, THREADS_APP_SECRET="app-secret")
    path.chmod(0o644)
    connect(isolated_home, Recorder(), pasted=CODE)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_no_token_and_no_code_reach_the_result_or_its_repr(
        isolated_home: Path) -> None:
    write_secret(isolated_home, THREADS_APP_SECRET="app-secret")
    result = connect(isolated_home, Recorder(), pasted=CODE)
    rendered = json.dumps(result.as_dict(), ensure_ascii=False) + repr(result)
    assert LONG not in rendered
    assert SHORT not in rendered
    assert CODE not in rendered
    assert "app-secret" not in rendered


# ---------------------------------------------------------------- delegated exchange

def test_delegated_route_posts_only_code_redirect_and_app_id(
        isolated_home: Path) -> None:
    recorder = Recorder()
    result = connect(isolated_home, recorder, pasted=CODE,
                     exchange_url="https://koreansaju.xyz/api/threads/exchange")

    assert result.route == "delegated"
    assert threads_auth.TOKEN_URL not in recorder.urls()
    payload = recorder.fields_for("https://koreansaju.xyz/api/threads/exchange")
    assert payload == {"code": CODE, "redirect_uri": REDIRECT, "app_id": APP_ID}
    env = load_env(credentials_path(isolated_home, "acct"))
    assert env["THREADS_ACCESS_TOKEN"] == LONG


def test_delegated_route_defaults_to_the_koreansaju_endpoint(
        isolated_home: Path) -> None:
    recorder = Recorder()
    connect(isolated_home, recorder, pasted=CODE)
    assert threads_auth.DEFAULT_EXCHANGE_URL in recorder.urls()


def test_delegated_endpoint_without_a_token_fails_with_a_code(
        isolated_home: Path) -> None:
    recorder = Recorder(delegated={"error": "APP_ID_MISMATCH"})
    with pytest.raises(FactoryError) as error:
        connect(isolated_home, recorder, pasted=CODE)
    assert error.value.code == "OAUTH_NO_TOKEN"
    assert not credentials_path(isolated_home, "acct").exists()


def test_plain_http_exchange_endpoint_is_refused(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        connect(isolated_home, Recorder(), pasted=CODE,
                exchange_url="http://localhost:3000/api/threads/exchange")
    assert error.value.code == "EXCHANGE_URL_INVALID"


# ---------------------------------------------------------------- scope gate

def test_a_token_without_publish_scope_is_not_saved(isolated_home: Path) -> None:
    recorder = Recorder(scopes=["threads_basic", "threads_manage_replies"])
    with pytest.raises(FactoryError) as error:
        connect(isolated_home, recorder, pasted=CODE)
    assert error.value.code == "SCOPE_MISSING"
    assert "threads_content_publish" in error.value.detail
    assert threads_auth.ME_URL not in recorder.urls()
    assert not credentials_path(isolated_home, "acct").exists()


def test_a_partial_but_publishable_grant_warns_and_saves(isolated_home: Path) -> None:
    result = connect(isolated_home, Recorder(scopes=list(threads_auth.REQUIRED_SCOPES)),
                     pasted=CODE)
    assert any(warning.startswith("SCOPES_PARTIAL") for warning in result.warnings)
    assert load_env(credentials_path(isolated_home, "acct"))["THREADS_ACCESS_TOKEN"] == LONG


def test_an_unreadable_scope_list_warns_instead_of_blocking(isolated_home: Path) -> None:
    result = connect(isolated_home, Recorder(scopes=[]), pasted=CODE)
    assert "SCOPE_CHECK_UNAVAILABLE" in result.warnings


def test_a_non_numeric_me_id_is_refused(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        connect(isolated_home, Recorder(user_id="not-an-id"), pasted=CODE)
    assert error.value.code == "ACCOUNT_ID_INVALID"


# ---------------------------------------------------------------- cli surface

def test_cli_url_only_prints_the_consent_url_and_stops(
        capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["auth", "threads", "--profile", "_example", "--app-id", APP_ID,
                 "--url-only", "--no-open"]) == 0
    out = capsys.readouterr().out
    assert threads_auth.AUTHORIZE_BASE in out
    assert "threads_content_publish" in out
    assert "Paste" not in out


def test_cli_instagram_prints_the_url_and_the_manual_steps(
        capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["auth", "instagram", "--profile", "_example", "--app-id", "1234567890",
                 "--no-open"]) == 0
    out = capsys.readouterr().out
    assert instagram_auth.AUTHORIZE_BASE in out
    assert "IG_ACCESS_TOKEN" in out
    assert "professional" in out


def test_cli_threads_auth_runs_the_exchange_from_a_hidden_prompt(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    write_secret(isolated_home, THREADS_APP_SECRET="app-secret")
    recorder = Recorder()
    monkeypatch.setattr("factory.cli.getpass.getpass",
                        lambda prompt="": f"https://koreansaju.xyz/admin?code={CODE}#_")
    monkeypatch.setattr(threads_auth, "post_form", recorder.post_form)
    monkeypatch.setattr(threads_auth, "get_json", recorder.get_json)
    monkeypatch.setattr(threads_auth, "post_json", recorder.post_json)
    opened: list[str] = []
    monkeypatch.setattr("factory.cli.webbrowser.open", opened.append)

    assert main(["auth", "threads", "--profile", "acct", "--app-id", APP_ID]) == 0
    out = capsys.readouterr().out
    assert "saju.unni.kr" in out
    assert LONG not in out and CODE not in out
    assert opened and opened[0].startswith(threads_auth.AUTHORIZE_BASE)
    assert load_env(credentials_path(isolated_home, "acct"))["THREADS_USER_ID"]


def test_instagram_connect_is_still_a_stub(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        instagram_auth.connect(isolated_home, "acct")
    assert error.value.code == "AUTH_NOT_IMPLEMENTED"
