"""Put a free-form answer into one of the wizard's choices, with Haiku.

The alias tables in `questions.py` catch what people usually type. This is for the rest:
"그냥 짧게 쓰는 거 있잖아" is an answer to "글 / 이미지 / 영상", and re-asking a beginner
the same question because their words were not in a list is how the flow loses them.

Deliberately small:
  - Haiku, 16 output tokens, one word back
  - the system prompt is static, so nothing variable ever enters a cached prefix
  - no key means no call: the caller asks the question again instead of guessing
  - an answer outside the given choices is treated as a miss, not forced onto one
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Sequence

from ..errors import FactoryError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 16
TIMEOUT_SECONDS = 20
SYSTEM = ("You map a person's answer onto one of the allowed values for a setup "
          "question. Reply with exactly one allowed value and nothing else. If the "
          "answer matches none of them, reply with the single word NONE.")

Transport = Callable[[dict[str, object], str], dict[str, object]]


def build_payload(question: str, choices: Sequence[str], answer: str) -> dict[str, object]:
    user = (f"Question: {question}\n"
            f"Allowed values: {', '.join(choices)}\n"
            f"Answer: {answer}\n"
            "One allowed value, or NONE.")
    return {"model": MODEL, "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": user}]}


def classify(answer: str, choices: Sequence[str], *, question: str = "",
             api_key: str = "", transport: Transport | None = None) -> str:
    """One of `choices`, or "" when it cannot be decided. Never raises on a miss."""
    options = [str(choice) for choice in choices if str(choice).strip()]
    if not options or not str(answer).strip():
        return ""
    if not api_key and transport is None:
        raise FactoryError("CLASSIFY_UNAVAILABLE", "ANTHROPIC_API_KEY")
    payload = build_payload(question, options, answer)
    send = transport or _http_transport
    try:
        data = send(payload, api_key)
    except FactoryError:
        raise
    except Exception:  # noqa: BLE001 - a classifier miss must not end the flow
        raise FactoryError("CLASSIFY_FAILED", MODEL) from None
    text = _first_text(data).strip().strip(".,'\"").lower()
    for option in options:
        if text == option.lower():
            return option
    return ""


def _first_text(data: dict[str, object]) -> str:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def _http_transport(payload: dict[str, object], key: str) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, method="POST")
    # Header values are never echoed: an invalid-header exception carries the key.
    request.add_header("content-type", "application/json")
    request.add_header("anthropic-version", API_VERSION)
    request.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise FactoryError("CLASSIFY_FAILED", f"HTTP_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FactoryError("CLASSIFY_FAILED", "NETWORK") from None
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise FactoryError("CLASSIFY_FAILED", "BODY") from None
    return parsed if isinstance(parsed, dict) else {}
