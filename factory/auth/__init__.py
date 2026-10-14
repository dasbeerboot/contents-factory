"""Interactive platform authorization. Tokens are written, never printed."""
from __future__ import annotations

from .base import AuthResult, code_from_redirect

__all__ = ["AuthResult", "code_from_redirect"]
