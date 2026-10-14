"""Format catalogue loader. The yaml files themselves live in factory/formats/<kind>/.

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


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    required: bool = True


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

    @property
    def is_free(self) -> bool:
        return self.name == FREE_FORMAT or not self.prompt_template.strip()

    def question_keys(self) -> list[str]:
        return [question.key for question in self.questions]


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
    if not isinstance(raw, dict):
        raise FactoryError("FORMAT_INVALID", f"{kind}/{name}")
    questions = raw.get("questions") or []
    if isinstance(questions, list):
        raw["questions"] = [entry if isinstance(entry, dict) else {"key": str(entry)}
                            for entry in questions]
    try:
        return FormatSpec(**raw)
    except ValueError:
        raise FactoryError("FORMAT_INVALID", f"{kind}/{name}") from None


def catalogue(kind: str | None = None) -> dict[str, list[str]]:
    root = formats_root()
    out: dict[str, list[str]] = {}
    kinds = [kind] if kind else ["text", "image", "video"]
    for entry in kinds:
        directory = root / entry
        if not directory.is_dir():
            out[entry] = []
            continue
        out[entry] = sorted(path.stem for path in directory.glob("*.yaml"))
    return out
