"""factory.toml loader. Runtime home is ~/.contents-factory unless overridden.

`os` is stored rather than detected on every run: the scheduler, the credentials
file permissions and the Naver backend all branch on it, and a machine that was
set up once should keep behaving the same way. `factory init --os` writes it;
`factory doctor` shows the stored value next to the detected one.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import FactoryError
from .osinfo import OS_CHOICES, OsName, detect_os, expand

CONFIG_FILENAME = "factory.toml"
DEFAULT_HOME = "~/.contents-factory"
HOME_ENV = "FACTORY_HOME"
CONFIG_ENV = "FACTORY_CONFIG"

UiLang = Literal["ko", "en"]
ReviewChannel = Literal["terminal", "telegram"]
AgentName = Literal["claude", "codex"]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_lang: UiLang = "ko"
    # Beginner mode, on by default. The wizard approves and arms in one step instead of
    # leaving two separate commands for someone who does not know what arming is, and a
    # failed gate is regenerated for them rather than reported as a code. Everything else
    # is identical: queueing still never publishes, only a slot does.
    simple: bool = True
    # Which agent CLI `factory start` launches. Unset means "detect"; the answer is
    # recorded the first time both are installed so the question is asked once.
    agent: AgentName | None = None
    # The host OS this home was set up on. Absent means "whatever this machine
    # looks like today", which is right for a fresh clone and wrong for a home
    # that was configured once, so `factory init` writes the answer down.
    os: OsName = Field(default_factory=detect_os)
    content_lang: str = "en"
    default_profile: str = "_example"
    home: str = DEFAULT_HOME
    review: ReviewChannel = "terminal"
    # Extra repeat_guard corpus files, keyed by profile name ("*" applies to every
    # profile). Point these at the post history of the pipeline this profile is taking
    # over from: without it the guard only knows what this factory itself published, so
    # every phrase the old pipeline already burned reads as fresh.
    #   [extra_corpus]
    #   someone = ["~/.other-pipeline/posts.jsonl"]
    extra_corpus: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("extra_corpus")
    @classmethod
    def _corpus_shape(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for profile, paths in value.items():
            if not re.match(r"^(\*|[A-Za-z0-9_\-]{1,40})$", profile):
                raise ValueError(f"extra_corpus key must be a profile name or *, "
                                 f"got {profile!r}")
            if not isinstance(paths, list) or any(not str(entry).strip()
                                                  for entry in paths):
                raise ValueError(f"extra_corpus[{profile}] must be a list of paths")
        return value

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
        return expand(base)

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

    def corpus_paths(self, profile: str) -> tuple[Path, ...]:
        """Existing extra corpus files for this profile, '*' entries first."""
        found: list[Path] = []
        for key in ("*", profile):
            for entry in self.extra_corpus.get(key, ()):
                path = expand(str(entry))
                if path not in found:
                    found.append(path)
        return tuple(found)


def os_line(os_name: str) -> str:
    if os_name not in OS_CHOICES:
        raise FactoryError("OS_UNKNOWN", str(os_name)[:32])
    return f'os = "{os_name}"'


# Keys the wizard is allowed to answer into factory.toml. Anything else is a hand edit.
WRITABLE_KEYS: tuple[str, ...] = ("os", "ui_lang", "content_lang", "default_profile",
                                  "review", "simple", "agent")


def toml_line(key: str, value: object) -> str:
    if key not in WRITABLE_KEYS:
        raise FactoryError("CONFIG_INVALID", key[:32])
    if key == "os":
        return os_line(str(value))
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    text = str(value)
    if not re.match(r"^[A-Za-z0-9_\-./~ ]{0,120}$", text):
        raise FactoryError("CONFIG_INVALID", key[:32])
    return f'{key} = "{text}"'


def write_keys(path: Path, values: dict[str, object]) -> Path:
    """Replace or append flat `key = value` lines in a factory.toml.

    The wizard records answers here instead of asking the user to open an editor, so
    this has to be idempotent and has to leave the comments in the file alone. Only
    `WRITABLE_KEYS` are accepted; a table or a list is a hand edit, not an answer.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for key, value in values.items():
        line = toml_line(key, value)
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.M)
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text = f"{line}\n{text}" if text else f"{line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_os(path: Path, os_name: str) -> str:
    """Record the chosen OS in a factory.toml, replacing any existing line."""
    write_keys(path, {"os": os_name})
    return os_name


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_config_file(start: Path | None = None) -> Path | None:
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        candidate = expand(explicit)
        return candidate if candidate.is_file() else None
    seen: list[Path] = []
    cursor = (start or Path.cwd()).resolve()
    for parent in [cursor, *cursor.parents]:
        seen.append(parent / CONFIG_FILENAME)
    home_override = os.environ.get(HOME_ENV, "").strip()
    seen.append(expand(home_override or DEFAULT_HOME) / CONFIG_FILENAME)
    seen.append(repo_root() / CONFIG_FILENAME)
    for path in seen:
        if path.is_file():
            return path
    return None


def config_targets(config: Config) -> list[Path]:
    """Every factory.toml this run would read, home first.

    The config that loaded may live in the repo, in the home, or wherever
    FACTORY_CONFIG points. An answer written to one of them and not the others is an
    answer that disappears on the next command, so the wizard writes all of them.
    """
    found: list[Path] = [config.home_path / CONFIG_FILENAME]
    current = find_config_file()
    if current is not None and current not in found:
        found.append(current)
    return found


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


def init_home(config: Config, template: Path | None = None, *,
              os_name: str | None = None) -> Path:
    home = config.home_path
    for sub in ("queue", "briefs", "profiles", "state", "logs", "outputs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    target = home / CONFIG_FILENAME
    if not target.exists():
        source = template or (repo_root() / "factory.toml.example")
        body = source.read_text(encoding="utf-8") if source.is_file() else ""
        target.write_text(body, encoding="utf-8")
    if os_name:
        # The config that this run loaded may live elsewhere (repo root, or
        # FACTORY_CONFIG); write the answer to both so the next run agrees.
        for path in {target, find_config_file()} - {None}:
            write_os(path, os_name)  # type: ignore[arg-type]
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
