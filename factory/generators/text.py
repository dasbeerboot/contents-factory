"""Text generator, ported from saju-threads scripts/persona_writer.py.

Prompt caching per workspace/LLM-API-캐싱-규칙.md:
  - the system block is an immutable prefix (persona, format prompt, rules, output schema)
    and carries cache_control ephemeral on its last block
  - every variable value (date, slot, angle, recent posts, retry notes) lives in the user
    message, never in the system block
  - the payload is serialized with sort_keys so the prefix is byte stable across calls
  - sonnet-5 needs a 1024 token prefix before caching engages; a short prefix is silently
    ignored, so `cache_eligible` reports it and the second call logs cache_read
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import FactoryError, scrub

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 2400
API_TIMEOUT_SECONDS = 120
MIN_CACHE_PREFIX_TOKENS = 1024
TOKENS_PER_CHAR = 0.27

OUTPUT_SCHEMA = """
## Output format (strict)

Emit exactly one JSON object. No code fence, no preamble, no commentary.

{
  "text": "the post body",
  "replies": ["optional self reply 1", "optional self reply 2"],
  "hypothesis": "one line: what this post is testing"
}

- replies is 0 to 2 entries. Use an empty array when there are none.
- Respect the length, emoji, hashtag and link limits given in the user message.
- Do not restate the brief. Write the artifact itself.
"""


@dataclass
class Usage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_input(self) -> int:
        return (self.input_tokens + self.cache_creation_input_tokens
                + self.cache_read_input_tokens)

    def as_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens,
                "cache_creation_input_tokens": self.cache_creation_input_tokens,
                "cache_read_input_tokens": self.cache_read_input_tokens,
                "output_tokens": self.output_tokens,
                "total_input": self.total_input}


@dataclass
class Draft:
    text: str
    replies: list[str] = field(default_factory=list)
    hypothesis: str = ""
    usage: Usage = field(default_factory=Usage)
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "replies": list(self.replies),
                "hypothesis": self.hypothesis, "usage": self.usage.as_dict()}


def build_system(persona: str, format_prompt: str, rules: str = "",
                 schema: str = OUTPUT_SCHEMA) -> str:
    """Immutable prefix. Nothing time varying or per request may enter this string."""
    parts = ["# Persona\n\n" + persona.strip()]
    if format_prompt.strip():
        parts.append("# Format\n\n" + format_prompt.strip())
    if rules.strip():
        parts.append("# Rules\n\n" + rules.strip())
    parts.append(schema.strip())
    return "\n\n---\n\n".join(parts)


def build_user(values: dict[str, Any], *, instruction: str = "",
               retry_note: str = "") -> str:
    """Every variable value goes here, ordered deterministically."""
    lines: list[str] = []
    for key in sorted(values):
        value = values[key]
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            lines.append(f"## {key}")
            lines.extend(f"- {entry}" for entry in value)
        else:
            lines.append(f"{key}: {value}")
    if instruction:
        lines.extend(["", instruction])
    if retry_note:
        lines.extend(["", retry_note])
    return "\n".join(lines).strip()


def estimate_tokens(text: str) -> int:
    return int(len(text) * TOKENS_PER_CHAR)


def cache_eligible(system_text: str, *,
                   minimum: int = MIN_CACHE_PREFIX_TOKENS) -> bool:
    return estimate_tokens(system_text) >= minimum


def build_payload(system_text: str, user_text: str, *, model: str = DEFAULT_MODEL,
                  max_tokens: int = MAX_TOKENS) -> dict[str, Any]:
    system_block: dict[str, Any] = {"type": "text", "text": system_text}
    if cache_eligible(system_text):
        system_block["cache_control"] = {"type": "ephemeral"}
    return {"model": model,
            "max_tokens": max_tokens,
            "system": [system_block],
            "messages": [{"role": "user", "content": user_text}]}


def parse_draft(raw: str) -> tuple[str, list[str], str]:
    body = raw.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    try:
        parsed = json.loads(body)
    except ValueError:
        raise FactoryError("DRAFT_NOT_JSON") from None
    if not isinstance(parsed, dict):
        raise FactoryError("DRAFT_NOT_JSON")
    text = str(parsed.get("text") or "")
    if not text.strip():
        raise FactoryError("DRAFT_EMPTY")
    replies_raw = parsed.get("replies") or []
    if not isinstance(replies_raw, list):
        raise FactoryError("DRAFT_REPLIES_INVALID")
    replies = [str(entry) for entry in replies_raw if str(entry).strip()]
    return text, replies, str(parsed.get("hypothesis") or "")


class TextGenerator:
    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL,
                 max_tokens: int = MAX_TOKENS,
                 transport: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        if not str(api_key or "").strip():
            raise FactoryError("ENV_MISSING", "ANTHROPIC_API_KEY")
        self._key = str(api_key).strip()
        self.model = model
        self.max_tokens = max_tokens
        self._transport = transport or _http_transport
        self._log = log or (lambda _message: None)
        self.calls = 0
        self.last_usage = Usage()

    def payload(self, system_text: str, user_text: str) -> dict[str, Any]:
        return build_payload(system_text, user_text, model=self.model,
                             max_tokens=self.max_tokens)

    def generate(self, system_text: str, user_text: str) -> Draft:
        payload = self.payload(system_text, user_text)
        response = self._transport(payload, self._key)
        self.calls += 1
        usage_raw = response.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("input_tokens") or 0),
            cache_creation_input_tokens=int(usage_raw.get("cache_creation_input_tokens") or 0),
            cache_read_input_tokens=int(usage_raw.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage_raw.get("output_tokens") or 0))
        self.last_usage = usage
        self._log(f"usage call={self.calls} input={usage.input_tokens} "
                  f"cache_creation={usage.cache_creation_input_tokens} "
                  f"cache_read={usage.cache_read_input_tokens} "
                  f"output={usage.output_tokens}")
        if self.calls >= 2 and usage.cache_read_input_tokens == 0:
            self._log("WARN cache miss on a repeated prefix: the system block moved or the "
                      "prefix is under the model minimum")
        text = ""
        for block in response.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                text = str(block["text"])
                break
        if not text:
            raise FactoryError("EMPTY_CONTENT")
        body, replies, hypothesis = parse_draft(text)
        return Draft(text=body, replies=replies, hypothesis=hypothesis, usage=usage, raw=text)

    def dry_run(self, system_text: str, user_text: str) -> dict[str, Any]:
        payload = self.payload(system_text, user_text)
        return {"url": API_URL, "model": self.model,
                "cache_control": "ephemeral" if cache_eligible(system_text) else "omitted",
                "system_prefix_tokens_estimate": estimate_tokens(system_text),
                "payload": payload}


def _http_transport(payload: dict[str, Any], key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raise FactoryError(f"HTTP_{error.code}") from None
    except OSError as error:
        raise FactoryError("NETWORK_UNREACHABLE", type(error).__name__) from None
    try:
        parsed = json.loads(body)
    except ValueError:
        raise FactoryError("BODY_NOT_JSON", scrub(body[:40])) from None
    if not isinstance(parsed, dict):
        raise FactoryError("BODY_NOT_JSON")
    if parsed.get("type") == "error":
        raise FactoryError("REMOTE_REJECTED")
    return parsed
