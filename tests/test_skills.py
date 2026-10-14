"""Skill files are the user-facing contract, so their shape is tested like code."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills"
EXPECTED = {"setup", "profile", "writing-factory", "image-factory", "video-factory",
            "publish"}
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def frontmatter(path: Path) -> tuple[str, str]:
    body = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(body)
    assert match, f"{path.parent.name} has no frontmatter block"
    block = match.group(1)
    name = re.search(r"^name:\s*(\S+)\s*$", block, re.M)
    assert name, f"{path.parent.name} has no name"
    description = re.search(r"^description:\s*>\n(.*)$", block, re.S | re.M)
    assert description, f"{path.parent.name} has no folded description"
    return name.group(1), description.group(1)


def test_all_six_skills_exist() -> None:
    assert {path.parent.name for path in skill_files()} == EXPECTED


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_frontmatter_name_matches_directory(path: Path) -> None:
    name, _description = frontmatter(path)
    assert name == path.parent.name


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_description_is_english_with_korean_triggers(path: Path) -> None:
    _name, description = frontmatter(path)
    assert "트리거" in description, "Korean trigger list is required"
    assert f"/{path.parent.name}" in description
    head = description.split("트리거")[0]
    assert not re.search(r"[가-힣]", head), "description body must be English"
    assert len(head.split()) >= 25, "description must say when to use the skill"


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_every_skill_states_the_state_file_ban(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "Hard rules" in body
    assert "~/.contents-factory/" in body
    assert "factory" in body


@pytest.mark.parametrize("path", [p for p in skill_files()
                                  if p.parent.name.endswith("-factory")],
                         ids=lambda p: p.parent.name)
def test_factory_skills_cover_the_prompt_loop_and_free_mode(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "free" in body
    assert "prompt_final" in body
    assert "prompt_revisions" in body
    assert "factory brief new" in body
    assert "factory queue add" in body


@pytest.mark.parametrize("path", [p for p in skill_files()
                                  if p.parent.name in {"image-factory",
                                                       "video-factory"}],
                         ids=lambda p: p.parent.name)
def test_media_skills_name_the_catalogue_and_the_unimplemented_gates(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "factory formats --kind" in body
    assert "NOT_IMPLEMENTED" in body
    assert "caption_safe_zone" in body


def test_setup_covers_the_meta_tester_dance_and_key_names() -> None:
    body = (SKILLS_DIR / "setup" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in ("tester", "Website permissions", "redirect URL",
                   "character for character"):
        assert phrase in body, phrase
    for key in ("THREADS_ACCESS_TOKEN", "IG_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN",
                "SUPABASE_SERVICE_ROLE_KEY", "ANTHROPIC_API_KEY"):
        assert key in body, key
    assert "connector_id" in body and "blog_id" in body


def test_setup_has_no_static_higgsfield_key() -> None:
    """Higgsfield auth is the CLI login; a pasteable key would be wrong to ask for."""
    body = (SKILLS_DIR / "setup" / "SKILL.md").read_text(encoding="utf-8")
    assert "HIGGSFIELD_MCP_TOKEN" not in body
    assert "higgsfield auth login" in body
    assert "higgsfield auth token" in body


def test_setup_asks_the_os_question_first_and_runs_doctor() -> None:
    body = (SKILLS_DIR / "setup" / "SKILL.md").read_text(encoding="utf-8")
    first = body.split("## Step 0")[0]
    assert "운영체제" in first, "the OS question comes before anything else"
    assert "factory init --os" in first
    assert "factory doctor" in first
    for platform_word in ("macOS", "Windows", "Linux"):
        assert platform_word in first, platform_word
    # The language question must come after the OS question, not before.
    assert body.index("운영체제") < body.index("ui_lang")


def test_setup_covers_both_naver_backends() -> None:
    body = (SKILLS_DIR / "setup" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in ("aside", "playwright", "factory naver login",
                   "PLAYWRIGHT_MISSING", "NAVER_SESSION_MISSING", "schtasks"):
        assert phrase in body, phrase


def test_publish_skill_lists_the_tick_reasons() -> None:
    body = (SKILLS_DIR / "publish" / "SKILL.md").read_text(encoding="utf-8")
    for reason in ("NOT_ARMED", "LATE_CARRIED", "DAILY_CAP", "REPEAT_BLOCK",
                   "DELIVERY_UNCONFIRMED", "PUBLISHED_FLAG_MISSING",
                   "RESUME_CONTAINER"):
        assert reason in body, reason
    assert "factory queue reopen" in body


def test_no_skill_leaks_a_persona_or_a_client_name() -> None:
    banned = ("saju", "사주", "언니", "checkride", "rallin", "주원")
    for path in skill_files():
        body = path.read_text(encoding="utf-8").lower()
        for word in banned:
            assert word not in body, f"{path.parent.name} mentions {word}"


def test_tick_reasons_in_the_publish_skill_exist_in_the_code() -> None:
    """Every code quoted at the user must be one the code can actually emit."""
    from factory.queue import store as queue_store
    from factory.scheduler import tick
    source = "\n".join(Path(module.__file__).read_text(encoding="utf-8")
                       for module in (tick, queue_store))
    body = (SKILLS_DIR / "publish" / "SKILL.md").read_text(encoding="utf-8")
    for reason in re.findall(r"`([A-Z][A-Z_]{4,})(?::\.\.\.)?`", body):
        if reason in {"UTC"}:
            continue
        assert reason in source or reason in {"ENV_MISSING", "CONNECTOR_ID_MISSING",
                                              "BLOG_ID_MISSING", "NAVER_SESSION_MISSING",
                                              "CONTAINER_IN_PROGRESS"}, reason
