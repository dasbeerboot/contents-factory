"""Profile model: persona, brand, voice, soul, platform slots, forbidden words."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import FactoryError

SLOT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)Z$")
LatePolicy = Literal["carry_once", "drop"]
NaverBackend = Literal["aside", "playwright"]
PLATFORMS = ("threads", "instagram", "tiktok", "youtube", "naver_blog")


class PlatformConfig(BaseModel):
    """Non-secret platform settings. Tokens never live here, only in credentials.env.

    `connector_id` (tiktok) and `blog_id` (naver_blog) are account identifiers, not
    secrets, so P0-SPEC §10 decision 6 keeps them in version controlled profile config.

    `backend` (naver_blog) picks which browser holds the Naver session: `aside` on
    macOS, `playwright` anywhere. Left unset it follows the configured OS.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    account_id: str | None = None
    slots: list[str] = Field(default_factory=list)
    late_policy: LatePolicy = "carry_once"
    cta_rotation: list[str] = Field(default_factory=list)
    daily_cap: int = Field(default=1, ge=0, le=50)
    connector_id: str | None = None
    blog_id: str | None = None
    backend: NaverBackend | None = None

    @field_validator("blog_id")
    @classmethod
    def _blog_shape(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"^[A-Za-z0-9_-]{3,40}$", value):
            raise ValueError("blog_id must be [A-Za-z0-9_-]{3,40}")
        return value

    @field_validator("slots")
    @classmethod
    def _slot_shape(cls, value: list[str]) -> list[str]:
        for slot in value:
            if not SLOT.match(slot):
                raise ValueError(f"slot must look like 12:00Z, got {slot!r}")
        return value


class VoiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "elevenlabs"
    voice_id: str | None = None
    model: str | None = None
    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)


class SoulSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "higgsfield"
    soul_id: str | None = None
    ref_image: str | None = None
    identity_rules: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    persona: str = ""
    brand: str = ""
    voice: VoiceSpec = Field(default_factory=VoiceSpec)
    soul: SoulSpec = Field(default_factory=SoulSpec)
    platforms: dict[str, PlatformConfig] = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list)
    locale: str = "en"
    writer_rules: str = ""
    closers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not re.match(r"^[A-Za-z0-9_\-]{1,40}$", value):
            raise ValueError("profile name must be [A-Za-z0-9_-]{1,40}")
        return value

    @field_validator("platforms")
    @classmethod
    def _known_platforms(cls, value: dict[str, PlatformConfig]) -> dict[str, PlatformConfig]:
        unknown = sorted(set(value) - set(PLATFORMS))
        if unknown:
            raise ValueError(f"unknown platforms: {','.join(unknown)}")
        return value

    def platform(self, name: str) -> PlatformConfig:
        return self.platforms.get(name) or PlatformConfig()

    def enabled_platforms(self) -> list[str]:
        return [name for name in PLATFORMS if self.platforms.get(name, PlatformConfig()).enabled]


def _read_text(directory: Path, filename: str) -> str:
    try:
        return (directory / filename).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_json(directory: Path, filename: str) -> dict[str, object]:
    try:
        data = json.loads((directory / filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_lines(directory: Path, filename: str) -> list[str]:
    raw = _read_text(directory, filename)
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.startswith("#")]


def load_profile_dir(directory: Path, name: str | None = None) -> Profile:
    if not directory.is_dir():
        raise FactoryError("PROFILE_MISSING", directory.name)
    platforms_raw = _read_json(directory, "platforms.json")
    try:
        return Profile(
            name=name or directory.name,
            persona=_read_text(directory, "persona.md"),
            brand=_read_text(directory, "brand.md"),
            voice=VoiceSpec(**_read_json(directory, "voice.json")),
            soul=SoulSpec(**_read_json(directory, "soul.json")),
            platforms={key: PlatformConfig(**value) for key, value in platforms_raw.items()
                       if isinstance(value, dict)},
            forbidden=_read_lines(directory, "forbidden.txt"),
            locale=_read_text(directory, "locale") or "en",
            writer_rules=_read_text(directory, "writer_rules.md"),
            closers=_read_lines(directory, "closers.txt"),
        )
    except ValueError as error:
        raise FactoryError("PROFILE_INVALID", type(error).__name__) from None


def profile_candidates(home: Path, repo: Path, name: str) -> list[Path]:
    return [home / "profiles" / name, repo / "profiles" / name]


def load_profile(name: str, *, home: Path, repo: Path) -> Profile:
    for candidate in profile_candidates(home, repo, name):
        if candidate.is_dir():
            return load_profile_dir(candidate, name)
    raise FactoryError("PROFILE_MISSING", name)


def list_profiles(home: Path, repo: Path) -> list[str]:
    found: set[str] = set()
    for base in (home / "profiles", repo / "profiles"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                found.add(child.name)
    return sorted(found)
