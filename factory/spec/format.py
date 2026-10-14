"""Format catalogue loader. The yaml files themselves live in factory/formats/<kind>/.

The yaml is canonical (P0-SPEC §2): the loader bends to it, not the other way round.
That means `name` and `kind` are inferred from the path when a file omits them (free.yaml
does), `limits` stays a free-form dict because every kind carries its own keys, and
`questions` entries may carry ready-made ko/en wording that beats the i18n catalogue.

Override the search root with FACTORY_FORMATS_DIR; tests point it at a fixture tree.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import FactoryError
from .brief import Kind
from .profile import PLATFORMS

FORMATS_ENV = "FACTORY_FORMATS_DIR"
FREE_FORMAT = "free"
KINDS: tuple[str, ...] = ("text", "image", "video")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    required: bool = True
    question_ko: str = ""
    question_en: str = ""

    def prompt(self, lang: str) -> str:
        """Wording for this question. The yaml text wins; i18n key is the fallback."""
        if lang == "ko" and self.question_ko:
            return self.question_ko
        if lang == "en" and self.question_en:
            return self.question_en
        return self.question_ko or self.question_en or self.key


class FormatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Kind
    platforms: list[str] = Field(default_factory=list)
    aspect: str | None = None
    duration_s: list[int] | None = None
    questions: list[Question] = Field(default_factory=list)
    prompt_template: str = ""
    gates: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    credit_cap_per_item: int = Field(default=0, ge=0)

    @field_validator("platforms")
    @classmethod
    def _known_platforms(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(PLATFORMS))
        if unknown:
            raise ValueError(f"unknown platforms: {','.join(unknown)}")
        return value

    @field_validator("duration_s")
    @classmethod
    def _duration_pair(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) != 2 or value[0] > value[1] or value[0] < 0:
            raise ValueError("duration_s must be [min, max] seconds")
        return value

    @property
    def is_free(self) -> bool:
        return self.name == FREE_FORMAT or not self.prompt_template.strip()

    def question_keys(self) -> list[str]:
        return [question.key for question in self.questions]

    def interview(self, lang: str) -> list[dict[str, str]]:
        return [{"key": question.key, "question": question.prompt(lang),
                 "required": "true" if question.required else "false"}
                for question in self.questions]

    def safe_zone(self) -> dict[str, int] | None:
        for key in ("safe_zone_px", "center_safe_zone_px"):
            zone = self.limits.get(key)
            if isinstance(zone, dict) and {"x_min", "x_max", "y_min", "y_max"} <= set(zone):
                return {name: int(zone[name]) for name in
                        ("x_min", "x_max", "y_min", "y_max")}
        return None

    def frame(self) -> tuple[int, int] | None:
        width, height = self.limits.get("width_px"), self.limits.get("height_px")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        return None


def formats_root() -> Path:
    override = os.environ.get(FORMATS_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "formats"


def format_path(kind: str, name: str) -> Path:
    return formats_root() / kind / f"{name}.yaml"


def load_format(kind: str, name: str) -> FormatSpec:
    path = format_path(kind, name)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        raise FactoryError("FORMAT_MISSING", f"{kind}/{name}") from None
    except yaml.YAMLError:
        raise FactoryError("FORMAT_INVALID", f"{kind}/{name}") from None
    return parse_format(raw, kind=kind, name=name)


def parse_format(raw: object, *, kind: str, name: str) -> FormatSpec:
    if not isinstance(raw, dict):
        raise FactoryError("FORMAT_INVALID", f"{kind}/{name}")
    data = dict(raw)
    data.setdefault("name", name)
    data.setdefault("kind", kind)
    if data.get("gates") is None:
        data["gates"] = []
    if data.get("platforms") is None:
        data["platforms"] = []
    questions = data.get("questions") or []
    if isinstance(questions, list):
        data["questions"] = [entry if isinstance(entry, dict) else {"key": str(entry)}
                             for entry in questions]
    if data["name"] != name or data["kind"] != kind:
        raise FactoryError("FORMAT_PATH_MISMATCH", f"{kind}/{name}")
    try:
        return FormatSpec(**data)
    except ValueError:
        raise FactoryError("FORMAT_INVALID", f"{kind}/{name}") from None


def catalogue(kind: str | None = None) -> dict[str, list[str]]:
    root = formats_root()
    out: dict[str, list[str]] = {}
    for entry in ([kind] if kind else list(KINDS)):
        directory = root / entry
        if not directory.is_dir():
            out[entry] = []
            continue
        out[entry] = sorted(path.stem for path in directory.glob("*.yaml"))
    return out


def load_all() -> dict[str, FormatSpec]:
    found: dict[str, FormatSpec] = {}
    for kind, names in catalogue().items():
        for name in names:
            found[f"{kind}/{name}"] = load_format(kind, name)
    return found
