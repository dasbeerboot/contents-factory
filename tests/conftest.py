from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.config import Config
from factory.spec.queue import Media, QueueItem

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    for sub in ("queue", "briefs", "profiles", "state", "logs", "outputs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FACTORY_HOME", str(home))
    monkeypatch.setenv("FACTORY_CONFIG", str(tmp_path / "factory.toml"))
    monkeypatch.setenv("FACTORY_FORMATS_DIR", str(FIXTURES / "formats"))
    (tmp_path / "factory.toml").write_text(
        'ui_lang = "en"\ncontent_lang = "en"\ndefault_profile = "_example"\n'
        f'home = "{home}"\nreview = "terminal"\n', encoding="utf-8")
    return home


@pytest.fixture()
def config(isolated_home: Path) -> Config:
    return Config(home=str(isolated_home), ui_lang="en")


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def make_item(**overrides: object) -> QueueItem:
    data: dict[str, object] = {
        "id": "q_20261015120000_ab12",
        "profile": "_example",
        "platform": "threads",
        "kind": "text",
        "text": "Three days of debugging came down to one cached header.",
        "scheduled_at": "2026-10-15T12:00:00Z",
    }
    data.update(overrides)
    return QueueItem(**data)


def make_media(path: str, kind: str = "image", public: str | None = None) -> Media:
    return Media(path=path, kind=kind, public_url=public)


@pytest.fixture()
def item_factory():
    return make_item
