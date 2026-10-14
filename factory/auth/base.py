"""Shared pieces of the interactive OAuth flows.

A code and a token are both single use secrets. Nothing here returns either one to the
caller: the flow writes to `credentials.env` and hands back an `AuthResult` that carries
only the account identity and the key names that were written.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from ..errors import FactoryError

# Meta appends this to the code in the address bar. It is not part of the code.
CODE_SUFFIX = "#_"
CODE_MAX = 512


@dataclass(frozen=True)
class AuthResult:
    """Outcome of one authorization. Deliberately token free."""

    platform: str
    profile: str
    username: str = ""
    user_id: str = ""
    issued_at: str = ""
    route: str = ""
    scopes: tuple[str, ...] = ()
    written_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "profile": self.profile,
                "username": self.username, "user_id": self.user_id,
                "issued_at": self.issued_at, "route": self.route,
                "scopes": list(self.scopes), "written_keys": list(self.written_keys),
                "warnings": list(self.warnings)}


def code_from_redirect(pasted: str) -> str:
    """Pull the one time code out of a pasted redirect URL, or accept a bare code.

    The user is told to paste the whole address bar, which is the only part of this flow
    that cannot be automated. A pasted URL may carry `error=...` instead of a code, and
    that has to surface as a code, not as an empty string.
    """
    text = str(pasted or "").strip().strip('"').strip("'")
    if not text:
        raise FactoryError("CODE_MISSING")
    if len(text) > 4096:
        raise FactoryError("CODE_TOO_LONG")
    if "?" in text or text.lower().startswith("http"):
        query = urllib.parse.urlparse(text).query
        params = urllib.parse.parse_qs(query)
        if params.get("error") or params.get("error_reason"):
            raise FactoryError("CONSENT_DENIED")
        values = params.get("code") or []
        text = values[0] if values else ""
    text = "".join(text.split())
    if text.endswith(CODE_SUFFIX):
        text = text[: -len(CODE_SUFFIX)]
    text = text.rstrip("#")
    if not text:
        raise FactoryError("CODE_MISSING")
    if len(text) > CODE_MAX or any(character.isspace() for character in text):
        raise FactoryError("CODE_INVALID")
    return text


def scope_list(scopes: tuple[str, ...]) -> str:
    return ",".join(scopes)


def consent_url(base: str, *, app_id: str, redirect_uri: str,
                scopes: tuple[str, ...]) -> str:
    if not str(app_id or "").strip():
        raise FactoryError("APP_ID_REQUIRED")
    if not str(redirect_uri or "").strip().lower().startswith("https://"):
        raise FactoryError("REDIRECT_URI_INVALID")
    query = urllib.parse.urlencode({"client_id": str(app_id).strip(),
                                    "redirect_uri": str(redirect_uri).strip(),
                                    "scope": scope_list(scopes),
                                    "response_type": "code"})
    return f"{base}?{query}"
