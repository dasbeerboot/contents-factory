"""Threads OAuth, ported from saju-threads scripts/oauth_exchange.sh and profile_add.sh.

Four steps, same as the shell original:

1. print the consent URL (the scopes written here are the scopes the token ends up with,
   which is the whole reason this flow exists: the dashboard token generator reissues
   inside the account's *old* grant and silently drops newly added scopes)
2. the user allows access and pastes the redirected address back in
3. code to short lived token, short lived to long lived (60 days)
4. `debug_token` for the real scope list, `/me` for the account identity

Step 3 needs the app secret. Two routes:

- **direct**: `THREADS_APP_SECRET` is on this machine, so the exchange happens here
- **delegated**: no local secret, so `{code, redirect_uri, app_id}` goes to an endpoint
  that holds the secret (default `https://koreansaju.xyz/api/threads/exchange`) and
  returns the long lived token

Neither the code nor any token is printed, logged or returned to the caller. The only
thing that comes back out of here is the account identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..credentials import credentials_path, env_set, load_profile_env
from ..errors import FactoryError
from ..publishers.base import get_json, post_form, post_json
from .base import AuthResult, consent_url

AUTHORIZE_BASE = "https://threads.net/oauth/authorize"
TOKEN_URL = "https://graph.threads.net/oauth/access_token"
LONG_LIVED_URL = "https://graph.threads.net/access_token"
DEBUG_TOKEN_URL = "https://graph.threads.net/v1.0/debug_token"
ME_URL = "https://graph.threads.net/v1.0/me"
DEFAULT_EXCHANGE_URL = "https://koreansaju.xyz/api/threads/exchange"
# The URI already registered on the Meta app (saju-threads PROJECT-CONTEXT 2026-08-09).
# A new one has to be whitelisted in the dashboard before it will return a code.
DEFAULT_REDIRECT_URI = "https://koreansaju.xyz/admin"

# Same list the shell flow consents to. Publishing needs the first two; the rest are what
# the app is already approved for, and asking for less here would shrink an existing grant.
SCOPES: tuple[str, ...] = (
    "threads_basic",
    "threads_content_publish",
    "threads_manage_replies",
    "threads_read_replies",
    "threads_manage_insights",
    "threads_keyword_search",
    "threads_manage_mentions",
    "threads_profile_discovery",
    "threads_delete",
)
# Without these the factory cannot post at all, so a token missing them is not saved.
REQUIRED_SCOPES: tuple[str, ...] = ("threads_basic", "threads_content_publish")

TOKEN_KEY = "THREADS_ACCESS_TOKEN"
USER_ID_KEY = "THREADS_USER_ID"
ISSUED_AT_KEY = "THREADS_TOKEN_ISSUED_AT"
APP_ID_KEY = "THREADS_APP_ID"
APP_SECRET_KEY = "THREADS_APP_SECRET"
REDIRECT_KEY = "THREADS_REDIRECT_URI"

PostForm = Callable[[str, dict[str, str]], dict[str, Any]]
GetJson = Callable[[str, dict[str, str]], dict[str, Any]]
PostJson = Callable[[str, dict[str, Any]], dict[str, Any]]


def authorize_url(*, app_id: str, redirect_uri: str,
                  scopes: tuple[str, ...] = SCOPES) -> str:
    return consent_url(AUTHORIZE_BASE, app_id=app_id, redirect_uri=redirect_uri,
                       scopes=scopes)


def resolve_app_id(env: dict[str, str], override: str | None) -> str:
    app_id = str(override or env.get(APP_ID_KEY) or "").strip()
    if not app_id:
        raise FactoryError("APP_ID_REQUIRED", "pass--app-id")
    if not app_id.isdigit():
        raise FactoryError("APP_ID_INVALID")
    return app_id


def resolve_redirect(env: dict[str, str], override: str | None) -> str:
    return str(override or env.get(REDIRECT_KEY) or DEFAULT_REDIRECT_URI).strip()


def _token_from(payload: dict[str, Any], step: str) -> str:
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise FactoryError("OAUTH_NO_TOKEN", step)
    return token


def exchange_direct(code: str, *, app_id: str, app_secret: str, redirect_uri: str,
                    post: PostForm | None = None,
                    get: GetJson | None = None) -> str:
    """code to short lived to long lived, using the local app secret."""
    sender = post or post_form
    reader = get or get_json
    try:
        short = _token_from(sender(TOKEN_URL, {
            "client_id": app_id, "client_secret": app_secret,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
            "code": code}), "short_lived")
    except FactoryError as error:
        raise FactoryError("OAUTH_EXCHANGE_FAILED", f"short_lived/{error.code}") from None
    try:
        return _token_from(reader(LONG_LIVED_URL, {
            "grant_type": "th_exchange_token", "client_secret": app_secret,
            "access_token": short}), "long_lived")
    except FactoryError as error:
        raise FactoryError("OAUTH_EXCHANGE_FAILED", f"long_lived/{error.code}") from None


def exchange_delegated(code: str, *, app_id: str, redirect_uri: str, service_url: str,
                       post: PostJson | None = None) -> str:
    """Hand the code to the endpoint that holds the app secret and take the token back.

    Request `{code, redirect_uri, app_id}`, response `{access_token}`. The endpoint is
    expected to have already done the long lived exchange.
    """
    if not str(service_url or "").lower().startswith("https://"):
        raise FactoryError("EXCHANGE_URL_INVALID")
    sender = post or post_json
    try:
        payload = sender(service_url, {"code": code, "redirect_uri": redirect_uri,
                                       "app_id": app_id})
    except FactoryError as error:
        raise FactoryError("OAUTH_EXCHANGE_FAILED", f"delegated/{error.code}") from None
    return _token_from(payload, "delegated")


def token_scopes(token: str, *, get: GetJson | None = None) -> tuple[str, ...]:
    """The grant actually attached to the token. The only reliable scope diagnostic."""
    reader = get or get_json
    try:
        payload = reader(DEBUG_TOKEN_URL, {"input_token": token, "access_token": token})
    except FactoryError:
        return ()
    data = payload.get("data")
    if not isinstance(data, dict):
        return ()
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        return ()
    return tuple(str(entry) for entry in scopes if isinstance(entry, str))


def identity(token: str, *, get: GetJson | None = None) -> tuple[str, str]:
    """`(user_id, username)` for the account that granted the token."""
    reader = get or get_json
    try:
        payload = reader(ME_URL, {"fields": "id,username", "access_token": token})
    except FactoryError as error:
        raise FactoryError("ME_FAILED", error.code) from None
    user_id = str(payload.get("id") or "").strip()
    username = str(payload.get("username") or "").strip()
    if not user_id.isdigit():
        raise FactoryError("ACCOUNT_ID_INVALID")
    return user_id, username


def connect(home: Path, profile: str, *, pasted: str, app_id: str | None = None,
            redirect_uri: str | None = None, exchange_url: str | None = None,
            post_form_fn: PostForm | None = None, get_json_fn: GetJson | None = None,
            post_json_fn: PostJson | None = None,
            now: datetime | None = None) -> AuthResult:
    """Run the exchange for one pasted redirect and write the profile credentials."""
    from .base import code_from_redirect

    env = load_profile_env(home, profile)
    resolved_app_id = resolve_app_id(env, app_id)
    resolved_redirect = resolve_redirect(env, redirect_uri)
    code = code_from_redirect(pasted)
    secret = str(env.get(APP_SECRET_KEY) or "").strip()
    if secret:
        route = "direct"
        token = exchange_direct(code, app_id=resolved_app_id, app_secret=secret,
                                redirect_uri=resolved_redirect, post=post_form_fn,
                                get=get_json_fn)
    else:
        route = "delegated"
        token = exchange_delegated(code, app_id=resolved_app_id,
                                   redirect_uri=resolved_redirect,
                                   service_url=exchange_url or DEFAULT_EXCHANGE_URL,
                                   post=post_json_fn)

    scopes = token_scopes(token, get=get_json_fn)
    warnings: list[str] = []
    if scopes:
        missing = [name for name in REQUIRED_SCOPES if name not in scopes]
        if missing:
            # Not saved: a token without publish scope would fail at the first slot.
            raise FactoryError("SCOPE_MISSING", ",".join(missing))
        optional = [name for name in SCOPES if name not in scopes]
        if optional:
            warnings.append("SCOPES_PARTIAL " + ",".join(optional))
    else:
        warnings.append("SCOPE_CHECK_UNAVAILABLE")

    user_id, username = identity(token, get=get_json_fn)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = moment.strftime("%Y-%m-%d")
    path = credentials_path(home, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    env_set(path, TOKEN_KEY, token)
    env_set(path, USER_ID_KEY, user_id)
    permission = env_set(path, ISSUED_AT_KEY, issued_at)
    if permission:
        # The token is saved; the file just could not be locked down. Say so
        # rather than swallow it: on Windows this is an icacls failure.
        warnings.append(permission)
    return AuthResult(platform="threads", profile=profile, username=username,
                      user_id=user_id, issued_at=issued_at, route=route, scopes=scopes,
                      written_keys=(TOKEN_KEY, USER_ID_KEY, ISSUED_AT_KEY),
                      warnings=tuple(warnings))
