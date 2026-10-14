from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.config import Config, load_config
from factory.errors import FactoryError
from factory.spec.brief import (Brief, TextBrief, load_brief, new_brief_id, parse_brief,
                                save_brief)
from factory.spec.format import load_format
from factory.spec.profile import PlatformConfig, Profile, load_profile
from factory.spec.queue import QueueItem

REPO = Path(__file__).resolve().parent.parent


def test_config_defaults_and_home(isolated_home: Path) -> None:
    config = load_config()
    assert config.ui_lang == "en"
    assert config.default_profile == "_example"
    assert config.home_path == isolated_home
    assert config.queue_dir() == isolated_home / "queue"


def test_config_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        Config(ui_lang="en", nonsense=True)


def test_config_rejects_bad_ui_lang() -> None:
    with pytest.raises(ValidationError):
        Config(ui_lang="fr")


def test_platform_config_slot_shape() -> None:
    assert PlatformConfig(slots=["12:00Z", "23:30Z"]).slots == ["12:00Z", "23:30Z"]
    for bad in (["12:00"], ["24:00Z"], ["12:60Z"], ["noon"]):
        with pytest.raises(ValidationError):
            PlatformConfig(slots=bad)


def test_profile_rejects_unknown_platform() -> None:
    with pytest.raises(ValidationError):
        Profile(name="x", platforms={"mastodon": PlatformConfig()})


def test_example_profile_loads(isolated_home: Path) -> None:
    profile = load_profile("_example", home=isolated_home, repo=REPO)
    assert profile.name == "_example"
    assert profile.persona.strip()
    assert profile.locale == "en"
    assert profile.platform("threads").enabled is True
    assert profile.platform("threads").slots == ["12:00Z", "23:00Z"]
    assert profile.enabled_platforms() == ["threads"]
    assert "game changer" in profile.forbidden
    assert profile.closers


def test_profile_missing_raises(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        load_profile("nope", home=isolated_home, repo=REPO)
    assert error.value.code == "PROFILE_MISSING"


def test_brief_id_validation() -> None:
    assert new_brief_id("threads_post").startswith("b_")
    with pytest.raises(ValidationError):
        TextBrief(id="bad", kind="text", format="threads_post", profile="_example")


def test_brief_roundtrip(isolated_home: Path) -> None:
    brief = parse_brief({"id": "b_20261014_x1", "kind": "text", "format": "threads_post",
                         "profile": "_example", "platforms": ["threads"],
                         "outputs": [{"text": "hello", "chosen": True}]})
    assert isinstance(brief, TextBrief)
    save_brief(isolated_home, brief)
    again = load_brief(isolated_home, "b_20261014_x1")
    assert again.chosen_output() is not None
    assert again.chosen_output().text == "hello"


def test_brief_rejects_unknown_platform() -> None:
    with pytest.raises(FactoryError) as error:
        parse_brief({"id": "b_20261014_x1", "kind": "text", "format": "threads_post",
                     "profile": "_example", "platforms": ["mastodon"]})
    assert error.value.code == "BRIEF_INVALID"


def test_brief_kind_discriminates() -> None:
    with pytest.raises(FactoryError) as error:
        parse_brief({"id": "b_20261014_x1", "kind": "audio", "format": "x",
                     "profile": "_example"})
    assert error.value.code == "BRIEF_KIND_INVALID"


def test_queue_item_validation(item_factory) -> None:
    item = item_factory()
    assert item.day() == "2026-10-15"
    assert item.scheduled_dt().hour == 12
    with pytest.raises(ValidationError):
        item_factory(id="queue-1")
    with pytest.raises(ValidationError):
        item_factory(scheduled_at="2026-10-15 12:00")
    with pytest.raises(ValidationError):
        item_factory(platform="mastodon")
    with pytest.raises(ValidationError):
        QueueItem(id="q_20261015120000_ab12", profile="_example", platform="threads",
                  kind="text", scheduled_at="2026-10-15T12:00:00Z", surprise=1)


def test_format_fixture_loads() -> None:
    spec = load_format("text", "threads_post")
    assert spec.limits["max_chars"] == 500
    assert spec.question_keys() == ["text.threads.angle", "text.threads.hook_count"]
    assert spec.is_free is False


def test_format_missing_raises() -> None:
    with pytest.raises(FactoryError) as error:
        load_format("text", "nope")
    assert error.value.code == "FORMAT_MISSING"


# ------------------------------------------- brief ids used to collide within a day

def test_two_briefs_of_the_same_format_on_one_day_get_different_ids() -> None:
    """`b_<date>_<format>` gave both the same id and the second overwrote the first
    (measured 2026-10-14, a sujin brief landing on an unni one)."""
    ids = {new_brief_id("threads_post") for _ in range(200)}
    assert len(ids) > 190


def test_a_new_brief_id_has_the_queue_shape() -> None:
    import re

    assert re.match(r"^b_[0-9]{14}_[a-z0-9]{4}$", new_brief_id("threads_post"))


def test_ids_already_on_disk_still_load() -> None:
    from factory.spec.brief import BRIEF_ID

    for old in ("b_20261014_threadspost", "b_20261014_thtest1", "b_20261014_x1"):
        assert BRIEF_ID.match(old), old


def test_creating_a_brief_over_an_existing_one_is_refused(
        isolated_home: Path) -> None:
    from factory.spec.brief import save_brief

    brief = parse_brief({"id": new_brief_id(), "kind": "text",
                         "format": "threads_post", "profile": "_example",
                         "created_at": "2026-10-14T00:00:00Z"})
    save_brief(isolated_home, brief, overwrite=False)
    with pytest.raises(FactoryError) as error:
        save_brief(isolated_home, brief, overwrite=False)
    assert error.value.code == "BRIEF_EXISTS"
    # Stage writes still land: outputs go back onto the brief after generation.
    assert save_brief(isolated_home, brief).is_file()
