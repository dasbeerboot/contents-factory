"""Code-form errors. Never carry request headers, tokens or raw exception text."""
from __future__ import annotations

import re

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# No spaces on purpose: a detail is a code fragment, not prose. Anything
# resembling a header line or a sentence is dropped instead of echoed.
_SAFE_DETAIL = re.compile(r"^[A-Za-z0-9._:/=+@#,<>()\-]{0,160}$")


class FactoryError(Exception):
    """Carries a machine code plus an optional already-sanitized detail."""

    def __init__(self, code: str, detail: str = "") -> None:
        if not _CODE.match(code):
            code = "UNEXPECTED"
        self.code = code
        self.detail = detail if _SAFE_DETAIL.match(detail) else ""
        super().__init__(self.code if not self.detail else f"{self.code} {self.detail}")

    def __str__(self) -> str:
        return self.code if not self.detail else f"{self.code} {self.detail}"


def http_code(status: int) -> str:
    return f"HTTP_{int(status)}"


def mask_secret(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:3]}***{text[-2:]}"


def scrub(text: str) -> str:
    """Strip URLs, bearer tokens and long opaque strings out of a diagnostic line."""
    out = re.sub(r"(?i)(authorization|bearer|token|x-api-key|access_token)"
                 r"\s*[:=]?\s*\S*", r"\1=<masked>", str(text or ""))
    out = re.sub(r"https?://[^\s\"'<>]+", "<url>", out)
    out = re.sub(r"[A-Za-z0-9_\-]{24,}", "<opaque>", out)
    return out.replace("\n", " ")[:160]
