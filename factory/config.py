"""factory.toml loader. Runtime home is ~/.contents-factory unless overridden."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import FactoryError

CONFIG_FILENAME = "factory.toml"
DEFAULT_HOME = "~/.contents-factory"
HOME_ENV = "FACTORY_HOME"
CONFIG_ENV = "FACTORY_CONFIG"

UiLang = Literal["ko", "en"]
ReviewChannel = Literal["terminal", "telegram"]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_lang: UiLang = "ko"
    content_lang: str = "en"
    default_profile: str = "_example"
    home: str = DEFAULT_HOME
    review: ReviewChannel = "terminal"

    @field_validator("content_lang")
    @classmethod
    def _lang_shape(cls, value: str) -> str:
        text = value.strip()
        if not text or len(text) > 16:
            raise ValueError("content_lang must be a short language tag")
        return text

    @field_validator("default_profile")
    @classmethod
    def _profile_shape(cls, value: str) -> str:
        text = value.strip()
        if not text or "/" in text or text in {".", ".."}:
            raise ValueError("default_profile must be a plain directory name")
        return text

    @property
    def home_path(self) -> Path:
        override = os.environ.get(HOME_ENV, "").strip()
        base = override or self.home
        return Path(base).expanduser()

    def state_dir(self, *parts: str) -> Path:
        return self.home_path.joinpath(*parts)

    def queue_dir(self) -> Path:
        return self.state_dir("queue")

    def brief_dir(self) -> Path:
        return self.state_dir("briefs")

    def profile_dir(self, name: str) -> Path:
        return self.state_dir("profiles", name)

    def ledger_path(self) -> Path:
        return self.state_dir("ledger.jsonl")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_config_file(start: Path | None = None) -> Path | None:
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    seen: list[Path] = []
    cursor = (start or Path.cwd()).resolve()
    for parent in [cursor, *cursor.parents]:
        seen.append(parent / CONFIG_FILENAME)
    home_override = os.environ.get(HOME_ENV, "").strip()
    seen.append(Path(home_override or DEFAULT_HOME).expanduser() / CONFIG_FILENAME)
    seen.append(repo_root() / CONFIG_FILENAME)
    for path in seen:
        if path.is_file():
            return path
    return None


def load_config(path: Path | None = None) -> Config:
    target = path or find_config_file()
    if target is None:
        return Config()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise FactoryError("CONFIG_INVALID", type(error).__name__) from None
    try:
        return Config(**raw)
    except ValueError:
        raise FactoryError("CONFIG_INVALID", target.name) from None


def init_home(config: Config, template: Path | None = None) -> Path:
    home = config.home_path
    for sub in ("queue", "briefs", "profiles", "state", "logs", "outputs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    target = home / CONFIG_FILENAME
    if not target.exists():
        source = template or (repo_root() / "factory.toml.example")
        body = source.read_text(encoding="utf-8") if source.is_file() else ""
        target.write_text(body, encoding="utf-8")
    return home


class Paths(BaseModel):
    """Resolved locations for one run. Repo-side assets are read-only."""

    model_config = ConfigDict(extra="forbid")

    home: Path
    repo: Path = Field(default_factory=repo_root)

    def repo_profiles(self) -> Path:
        return self.repo / "profiles"

    def home_profiles(self) -> Path:
        return self.home / "profiles"
