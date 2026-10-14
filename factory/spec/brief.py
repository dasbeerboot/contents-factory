"""Brief model. One brief = one interview round for one format."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import FactoryError
from .profile import PLATFORMS

Kind = Literal["text", "image", "video"]
PublishMode = Literal["queue_only", "slot", "auto_after_approve"]
BRIEF_ID = re.compile(r"^b_[0-9]{8}_[A-Za-z0-9]{1,12}$")


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credits: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)


class PublishPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PublishMode = "queue_only"
    scheduled_at: str | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _utc_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value):
            raise ValueError("scheduled_at must be YYYY-MM-DDTHH:MM:SSZ")
        return value


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    text: str | None = None
    gates: dict[str, object] = Field(default_factory=dict)
    chosen: bool = False


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    format: str
    profile: str
    platforms: list[str] = Field(default_factory=list)
    content_lang: str = "en"
    references: list[str] = Field(default_factory=list)
    count: int = Field(default=1, ge=1, le=20)
    ab_hooks: int = Field(default=0, ge=0, le=10)
    budget: Budget = Field(default_factory=Budget)
    answers: dict[str, str] = Field(default_factory=dict)
    prompt_draft: str = ""
    prompt_final: str = ""
    prompt_revisions: int = Field(default=0, ge=0)
    publish: PublishPlan = Field(default_factory=PublishPlan)
    outputs: list[Output] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: utc_now())

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not BRIEF_ID.match(value):
            raise ValueError("brief id must look like b_20261014_x1")
        return value

    @field_validator("format")
    @classmethod
    def _format_shape(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", value):
            raise ValueError("format must be snake_case")
        return value

    @field_validator("platforms")
    @classmethod
    def _known_platforms(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(PLATFORMS))
        if unknown:
            raise ValueError(f"unknown platforms: {','.join(unknown)}")
        return value

    def chosen_output(self) -> Output | None:
        for output in self.outputs:
            if output.chosen:
                return output
        return None


class TextBrief(Brief):
    kind: Literal["text"] = "text"
    max_chars: int | None = Field(default=None, ge=1, le=100000)


class ImageBrief(Brief):
    kind: Literal["image"] = "image"
    aspect: str | None = None
    variants: int = Field(default=1, ge=1, le=20)


class VideoBrief(Brief):
    kind: Literal["video"] = "video"
    aspect: str | None = None
    duration_s: tuple[int, int] | None = None


_SUBMODELS: dict[str, type[Brief]] = {
    "text": TextBrief,
    "image": ImageBrief,
    "video": VideoBrief,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_brief(data: dict[str, object]) -> Brief:
    kind = str(data.get("kind") or "")
    model = _SUBMODELS.get(kind)
    if model is None:
        raise FactoryError("BRIEF_KIND_INVALID", kind[:16])
    try:
        return model(**data)
    except ValueError:
        raise FactoryError("BRIEF_INVALID", str(data.get("id") or "")[:32]) from None


def new_brief_id(seed: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    suffix = re.sub(r"[^A-Za-z0-9]", "", seed)[:12] or "x1"
    return f"b_{stamp}_{suffix}"


def brief_path(home: Path, brief_id: str) -> Path:
    if not BRIEF_ID.match(brief_id):
        raise FactoryError("BRIEF_ID_INVALID", brief_id[:32])
    return home / "briefs" / f"{brief_id}.json"


def save_brief(home: Path, brief: Brief) -> Path:
    path = brief_path(home, brief.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(brief.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_brief(home: Path, brief_id: str) -> Brief:
    path = brief_path(home, brief_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise FactoryError("BRIEF_MISSING", brief_id) from None
    except ValueError:
        raise FactoryError("BRIEF_INVALID", brief_id) from None
    if not isinstance(data, dict):
        raise FactoryError("BRIEF_INVALID", brief_id)
    return parse_brief(data)
