"""Instagram OAuth, same shape as the Threads flow. Consent URL only for now.

TODO (P1): finish the exchange. The pieces that still have to be verified against a live
app before this can write credentials, because guessing any of them produces a token that
looks fine and then fails at the first slot:

- the short lived exchange is `POST https://api.instagram.com/oauth/access_token` and the
  long lived one is `GET https://graph.instagram.com/access_token` with
  `grant_type=ig_exchange_token`, which is a *different* grant name than Threads uses
- `IG_USER_ID` is the content publishing node id (the 17841... one), not the Threads user
  id and not the handle. The Threads flow reads it off `/me`; Instagram Login returns a
  `user_id` in the token response and it has to be confirmed against
  `GET /me?fields=id,username` before it is written
- the account has to be a professional (business or creator) account. A personal account
  passes OAuth and then cannot publish
- the delegated route needs its own endpoint; the Threads one rejects a mismatched app id
  on purpose, so `src/app/api/instagram/exchange/route.ts` is a separate file

Until then `factory auth instagram` prints the consent URL and the manual steps, and the
keys go into `credentials.env` by hand.
"""
from __future__ import annotations

from pathlib import Path

from ..credentials import load_profile_env
from ..errors import FactoryError
from .base import AuthResult, consent_url

AUTHORIZE_BASE = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_URL = "https://graph.instagram.com/access_token"
DEFAULT_REDIRECT_URI = "https://koreansaju.xyz/admin"

SCOPES: tuple[str, ...] = (
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_comments",
    "instagram_business_manage_insights",
)
REQUIRED_SCOPES: tuple[str, ...] = ("instagram_business_basic",
                                    "instagram_business_content_publish")

TOKEN_KEY = "IG_ACCESS_TOKEN"
USER_ID_KEY = "IG_USER_ID"
ISSUED_AT_KEY = "IG_TOKEN_ISSUED_AT"
APP_ID_KEY = "IG_APP_ID"
APP_SECRET_KEY = "IG_APP_SECRET"
REDIRECT_KEY = "IG_REDIRECT_URI"

MANUAL_STEPS: tuple[str, ...] = (
    "1. Convert the account to a professional (business or creator) account.",
    "2. Meta app dashboard: add the account as an Instagram tester, then accept the"
    " invite from inside the Instagram app (Settings, Apps and websites).",
    "3. Open the consent URL above while logged in as that account and allow everything.",
    f"4. Exchange the code for a long lived token and put it in credentials.env as"
    f" {TOKEN_KEY}, with the content publishing node id as {USER_ID_KEY} and today's"
    f" date as {ISSUED_AT_KEY}.",
    "5. Verify with: factory publish instagram --item q_... --dry-run",
)


def resolve_app_id(env: dict[str, str], override: str | None) -> str:
    app_id = str(override or env.get(APP_ID_KEY) or "").strip()
    if not app_id:
        raise FactoryError("APP_ID_REQUIRED", "pass--app-id")
    if not app_id.isdigit():
        raise FactoryError("APP_ID_INVALID")
    return app_id


def resolve_redirect(env: dict[str, str], override: str | None) -> str:
    return str(override or env.get(REDIRECT_KEY) or DEFAULT_REDIRECT_URI).strip()


def authorize_url(*, app_id: str, redirect_uri: str,
                  scopes: tuple[str, ...] = SCOPES) -> str:
    return consent_url(AUTHORIZE_BASE, app_id=app_id, redirect_uri=redirect_uri,
                       scopes=scopes)


def plan(home: Path, profile: str, *, app_id: str | None = None,
         redirect_uri: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Consent URL plus the steps that are still manual."""
    env = load_profile_env(home, profile)
    url = authorize_url(app_id=resolve_app_id(env, app_id),
                        redirect_uri=resolve_redirect(env, redirect_uri))
    return url, MANUAL_STEPS


def connect(home: Path, profile: str, **_: object) -> AuthResult:
    raise FactoryError("AUTH_NOT_IMPLEMENTED", "instagram")
