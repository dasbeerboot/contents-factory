"""QueueItem model. Only queue.store writes these; never hand-edit the json."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .brief import BRIEF_ID, Kind
from .profile import PLATFORMS, LatePolicy

QUEUE_ID = re.compile(r"^q_[0-9]{14}_[a-z0-9]{4}$")
UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
Platform = Literal["threads", "instagram", "tiktok", "youtube", "naver_blog"]


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    public_url: str | None = None
    kind: Literal["image", "video"] = "image"


class QueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    brief_id: str | None = None
    profile: str
    platform: Platform
    kind: Kind
    text: str = ""
    media: list[Media] = Field(default_factory=list)
    scheduled_at: str
    approved: bool = False
    armed: bool = False
    published: bool = False
    published_id: str | None = None
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    late_policy: LatePolicy = "carry_once"
    arm_reason: str | None = None
    carry_count: int = Field(default=0, ge=0)
    published_at: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not QUEUE_ID.match(value):
            raise ValueError("queue id must look like q_20261014120000_ab12")
        return value

    @field_validator("brief_id")
    @classmethod
    def _brief_shape(cls, value: str | None) -> str | None:
        if value is not None and not BRIEF_ID.match(value):
            raise ValueError("brief_id must look like b_20261014_x1")
        return value

    @field_validator("scheduled_at", "published_at")
    @classmethod
    def _stamp_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not UTC_STAMP.match(value):
            raise ValueError("timestamps must be YYYY-MM-DDTHH:MM:SSZ")
        return value

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, value: str) -> str:
        if value not in PLATFORMS:
            raise ValueError(f"unknown platform {value!r}")
        return value

    def scheduled_dt(self) -> datetime:
        return datetime.strptime(self.scheduled_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)

    def day(self) -> str:
        return self.scheduled_at[:10]

    def public_media(self) -> list[str]:
        return [entry.public_url for entry in self.media if entry.public_url]


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    if not UTC_STAMP.match(value):
        raise ValueError("timestamps must be YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
