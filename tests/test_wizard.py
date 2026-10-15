"""The wizard is the only thing a beginner touches, so its state machine is tested like
the queue: every transition, every refusal, and the one place it is allowed to publish.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.cli import main
from factory.config import Config, load_config, write_keys
from factory.errors import FactoryError
from factory.queue.store import QueueStore
from factory.wizard import apply, engine, questions, state, templates


def config_for(home: Path, **overrides: object) -> Config:
    data: dict[str, object] = {"home": str(home), "ui_lang": "en",
                               "default_profile": "_example"}
    data.update(overrides)
    return Config(**data)


def answer(config: Config, question_id: str, value: str, *, dry_run: bool = True):
    return engine.answer_question(config, question_id, value, dry_run=dry_run)


def ask(config: Config, *, dry_run: bool = True) -> dict:
    return engine.next_question(config, dry_run=dry_run)


# --- the catalogue itself -------------------------------------------------------

def test_setup_is_seven_steps_and_make_is_four() -> None:
    setup = questions.setup_plan({"platforms": "threads", "slots": "evening"})
    assert {entry.step for entry in setup} == set(range(1, 8))
    make = questions.make_plan({"what": "text", "kind": "text"})
    assert {entry.step for entry in make} == set(range(1, 5))
    assert [entry.id for entry in make][:3] == ["what", "topic", "attach"]


def test_connect_questions_follow_the_platform_answer() -> None:
    plan = questions.setup_plan({"platforms": "threads,youtube"})
    connects = [entry.id for entry in plan if entry.id.startswith("connect.")]
    assert connects == ["connect.anthropic", "connect.higgsfield", "connect.elevenlabs",
                        "connect.threads", "connect.youtube"]
    # No platform answered yet means nothing to connect to.
    assert not [entry.id for entry in questions.setup_plan({})
                if entry.id.startswith("connect.")]


def test_every_connect_target_has_one_action_and_one_check() -> None:
    for target, entry in questions.CONNECTS.items():
        assert entry.action and entry.verify, target
        assert entry.action.count("factory") <= 1 or "key set" in entry.action


def test_custom_slots_add_a_follow_up_question() -> None:
    ids = [entry.id for entry in questions.setup_plan({"platforms": "threads",
                                                       "slots": "custom"})]
    assert "slots_custom" in ids
    assert "slots_custom" not in [entry.id for entry in
                                  questions.setup_plan({"platforms": "threads",
                                                        "slots": "evening"})]


def test_cost_question_appears_only_for_paid_kinds() -> None:
    assert "cost_ok" not in [entry.id for entry in questions.make_plan({"kind": "text"})]
    for kind in ("image", "video"):
        assert "cost_ok" in [entry.id for entry in questions.make_plan({"kind": kind})]


@pytest.mark.parametrize("raw,expected", [
    ("맥북이요", "macos"), ("I'm on a Mac", "macos"), ("윈도우 PC", "windows"),
    ("ubuntu", "linux"),
])
def test_os_aliases_cover_how_people_answer(raw: str, expected: str) -> None:
    assert questions.resolve("os", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("했어요", "yes"), ("ㅇㅋ", "yes"), ("did it", "yes"),
    ("아직 안했어요", "no"), ("못했어요", "no"), ("나중에 할게요", "no"),
    ("not yet", "no"), ("haven't", "no"),
])
def test_negation_is_read_before_the_yes_aliases(raw: str, expected: str) -> None:
    """"아직 안했어요" contains 했어. Reading it as yes skips an unconnected platform."""
    assert questions.resolve_confirm(raw) == expected


def test_multi_select_reads_several_platforms_from_one_sentence() -> None:
    assert questions.resolve_many("platforms", "쓰레드랑 인스타그램") == ["threads",
                                                                  "instagram"]
    assert questions.resolve_many("platforms", "tiktok, youtube") == ["tiktok",
                                                                      "youtube"]
    assert questions.resolve_many("platforms", "아무데나") == []


def test_long_draft_answer_is_a_correction_even_when_it_says_good() -> None:
    question = questions.Question("draft", 3, questions.DRAFT, ("ok", "again", "fix"))
    verdict = questions.validate(question, "좋아요 근데 마지막 줄만 빼줘")
    assert verdict.value.startswith("fix:")
    assert questions.validate(question, "이대로").value == "ok"
    assert questions.validate(question, "다시").value == "again"


def test_hangul_brand_name_is_accepted_and_gets_an_ascii_profile() -> None:
    question = questions.Question("brand_name", 3, questions.TEXT)
    assert questions.validate(question, "하루 한 줄 공방").ok
    assert not questions.validate(question, "!!!").ok
    assert questions.profile_slug("하루 한 줄 공방") == "brand"
    assert questions.profile_slug("하루 한 줄 공방", ["brand"]) == "brand-2"
    assert questions.profile_slug("Daily Notes") == "daily-notes"


def test_local_times_become_utc_slots() -> None:
    offset = timedelta(hours=9)
    assert questions.to_utc_slot(20, 0, offset=offset) == "11:00Z"
    assert questions.parse_local_times("8시, 20:30", offset=offset) == ["23:00Z",
                                                                        "11:30Z"]
    assert questions.parse_local_times("점심때", offset=offset) == []
    assert questions.parse_when("2026-10-20 21:00",
                                offset=offset) == "2026-10-20T12:00:00Z"
    assert questions.parse_when("tomorrow") == ""


def test_tone_templates_write_three_documents_and_differ() -> None:
    warm = templates.files("Studio", "one line", "warm")
    dry = templates.files("Studio", "one line", "dry")
    assert set(warm) == {"persona.md", "writer_rules.md", "brand.md"}
    assert warm["persona.md"] != dry["persona.md"]
    assert "Studio" in warm["brand.md"]
    with pytest.raises(FactoryError):
        templates.files("Studio", "one line", "sarcastic")


# --- the state machine ---------------------------------------------------------

def test_next_returns_one_question_with_everything_needed_to_ask_it(
        isolated_home: Path) -> None:
    payload = ask(config_for(isolated_home))
    assert payload["id"] == "os"
    assert payload["step"] == 1 and payload["step_total"] == 7
    assert payload["question"] and payload["help"] and payload["input"]
    assert [entry["value"] for entry in payload["choices"]] == ["macos", "windows",
                                                                 "linux"]
    assert payload["default"] in {"macos", "windows", "linux"}
    assert payload["answer_with"].startswith("factory wizard answer --id os")


def test_answer_out_of_order_is_refused(isolated_home: Path) -> None:
    config = config_for(isolated_home)
    with pytest.raises(FactoryError) as error:
        answer(config, "brand_name", "Studio")
    assert error.value.code == "WIZARD_OUT_OF_ORDER"


def test_unreadable_answer_re_asks_instead_of_guessing(isolated_home: Path) -> None:
    config = config_for(isolated_home)
    result = answer(config, "os", "whatever you think")
    assert result["ok"] is False
    assert result["reask"]
    # Nothing was stored, so the same question comes back.
    assert ask(config)["id"] == "os"


def test_answers_move_the_flow_forward_and_write_the_config(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    assert answer(config, "os", "맥북")["value"] == "macos"
    assert ask(config)["id"] == "ui_lang"
    answer(config, "ui_lang", "English")
    assert load_config().ui_lang == "en"
    assert ask(config)["id"] == "content_lang"


def test_back_undoes_the_last_answer_and_skip_only_works_when_allowed(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    answer(config, "os", "macos")
    assert ask(config)["id"] == "ui_lang"
    engine.back(config)
    assert ask(config)["id"] == "os"
    with pytest.raises(FactoryError) as error:
        engine.skip(config)
    assert error.value.code == "WIZARD_SKIP_REFUSED"


def test_back_on_a_fresh_flow_says_so(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        engine.back(config_for(isolated_home))
    assert error.value.code == "WIZARD_BACK_EMPTY"


def walk_setup(config: Config, *, platforms: str = "threads") -> None:
    answer(config, "os", "macos")
    answer(config, "ui_lang", "en")
    answer(config, "content_lang", "en")
    answer(config, "brand_name", "하루 한 줄 공방")
    answer(config, "brand_intro", "a solo maker logging what each day cost")
    answer(config, "brand_tone", "따뜻하게")
    answer(config, "platforms", platforms)


def test_the_tone_answer_creates_the_profile_through_the_profile_commands(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    walk_setup(config)
    directory = isolated_home / "profiles" / "brand"
    assert (directory / "persona.md").is_file()
    assert (directory / "writer_rules.md").is_file()
    assert "하루 한 줄 공방" in (directory / "brand.md").read_text(encoding="utf-8")
    assert load_config().default_profile == "brand"


def test_setup_connects_then_slots_then_hands_over_to_make(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    walk_setup(config, platforms="threads")
    for target in ("anthropic", "higgsfield", "threads"):
        assert ask(config)["id"] == f"connect.{target}"
        answer(config, f"connect.{target}", "했어요")
    slots = ask(config)
    assert slots["id"] == "slots" and slots["step"] == 6
    answer(config, "slots", "저녁")
    platforms = json.loads((isolated_home / "profiles" / "brand"
                            / "platforms.json").read_text(encoding="utf-8"))
    assert platforms["threads"]["enabled"] is True
    assert len(platforms["threads"]["slots"]) == 1
    assert ask(config)["id"] == "first_post"
    answer(config, "first_post", "yes")
    # Saying yes hands straight over to the make flow, question one.
    assert ask(config)["id"] == "what"
    assert engine.status(config)["flow"] == "make"


def test_connect_question_carries_the_action_and_the_check(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    walk_setup(config, platforms="naver_blog")
    while True:
        payload = ask(config)
        if payload["id"] == "connect.naver_blog":
            break
        answer(config, payload["id"], "yes")
    assert payload["action"] == "factory naver login"
    assert payload["verify"] == "factory naver status"
    assert payload["keys"] == []


def test_youtube_connect_names_its_keys_and_never_a_value(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    walk_setup(config, platforms="youtube")
    payload = ask(config)
    while payload["id"] != "connect.youtube":
        answer(config, payload["id"], "yes")
        payload = ask(config)
    assert "YOUTUBE_REFRESH_TOKEN" in payload["keys"]
    assert "factory key set" in payload["action"]
    assert "--value" not in payload["action"]


# --- the make flow -------------------------------------------------------------

def start_make(config: Config) -> None:
    engine.reset(config, "make")


def test_make_flow_runs_brief_gen_gate_and_queue_in_simple_mode(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    start_make(config)
    answer(config, "what", "글")
    answer(config, "topic", "three tools at once, and why I stopped")
    engine.skip(config)
    shown = ask(config)
    assert shown["id"] == "draft"
    assert shown["draft"] and shown["gates"]
    ran = " ".join(shown["ran"])
    assert "brief new" in ran and "gen text" in ran and "gate run" in ran
    answer(config, "draft", "이대로")
    assert ask(config)["id"] == "when"
    result = answer(config, "when", "next")
    item_id = result["notes"]["item"]
    item = QueueStore(isolated_home).get(item_id)
    assert item.platform == "threads"
    # simple mode: approved and armed in one step, with the user's own words.
    assert item.approved and item.armed
    assert item.arm_reason == "three tools at once, and why I stopped"
    assert item.published is False


def test_expert_mode_leaves_approval_and_arming_to_the_user(
        isolated_home: Path) -> None:
    config = config_for(isolated_home, simple=False)
    start_make(config)
    answer(config, "what", "text")
    answer(config, "topic", "a cache header cost three days")
    engine.skip(config)
    ask(config)
    answer(config, "draft", "ok")
    result = answer(config, "when", "next")
    item = QueueStore(isolated_home).get(result["notes"]["item"])
    assert not item.approved and not item.armed


def test_a_correction_revises_the_brief_and_asks_the_same_question_again(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    start_make(config)
    answer(config, "what", "text")
    answer(config, "topic", "one cached header")
    engine.skip(config)
    first = ask(config)
    brief_id = engine.status(config)["flows"]["make"]["notes"]["brief"]
    result = answer(config, "draft", "첫 줄을 더 짧게 해줘")
    assert any("brief revise" in command for command in result["ran"])
    assert ask(config)["id"] == "draft", "a correction re-asks the draft question"
    brief = json.loads((isolated_home / "briefs" / f"{brief_id}.json")
                       .read_text(encoding="utf-8"))
    assert brief["prompt_revisions"] == 1
    assert "첫 줄을 더 짧게 해줘" in brief["prompt_final"]
    assert first["id"] == "draft"


def test_declining_the_cost_makes_nothing(isolated_home: Path) -> None:
    config = config_for(isolated_home)
    start_make(config)
    answer(config, "what", "이미지")
    answer(config, "topic", "a product on a worn wooden surface")
    engine.skip(config)
    cost = ask(config)
    assert cost["id"] == "cost_ok"
    assert cost["cost"], "the credit and duration note has to be on the question"
    result = answer(config, "cost_ok", "아니요")
    assert result["message"]
    assert not QueueStore(isolated_home).list()


def test_gate_failures_are_regenerated_in_simple_mode_then_reported(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAIL is not shown as a code to a beginner: it is retried, then explained."""
    calls: list[str] = []

    def never_passes(config, *, brief_id="", text="", profile="", format_name="",
                     kind="text", result):
        calls.append(brief_id or text)
        return apply.Gates(ok=False, table="limits  FAIL",
                           failures=("limits: MAX_CHARS_OVER:900>500",))

    def fake_generate(config, *, kind, brief_id, dry_run, result):
        return "a draft that keeps failing"

    monkeypatch.setattr(apply, "gate", never_passes)
    monkeypatch.setattr(apply, "generate", fake_generate)
    config = config_for(isolated_home)
    start_make(config)
    answer(config, "what", "text", dry_run=False)
    answer(config, "topic", "something long", dry_run=False)
    engine.skip(config)
    shown = engine.next_question(config, dry_run=False)
    assert len(calls) == 1 + engine.MAX_AUTO_REGEN
    assert "MAX_CHARS_OVER" in shown["note"]


def test_status_and_reset_report_both_flows(isolated_home: Path) -> None:
    config = config_for(isolated_home)
    answer(config, "os", "macos")
    status = engine.status(config)
    assert status["flows"]["setup"]["answered"] == 1
    assert status["flows"]["setup"]["next"] == "ui_lang"
    assert status["flows"]["make"]["answered"] == 0
    engine.reset(config, "setup")
    assert engine.status(config)["flows"]["setup"]["answered"] == 0


def test_the_state_file_is_the_only_one_and_is_rebuilt_when_corrupt(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    answer(config, "os", "macos")
    path = state.state_path(isolated_home)
    assert path.is_file() and path.name == "wizard.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(FactoryError) as error:
        ask(config)
    assert error.value.code == "WIZARD_STATE_INVALID"


def test_format_and_platform_are_chosen_not_asked() -> None:
    assert apply.pick_platform("text", ["threads", "instagram"]) == "threads"
    assert apply.pick_platform("video", ["instagram", "tiktok"]) == "tiktok"
    with pytest.raises(FactoryError) as error:
        apply.pick_platform("text", [])
    assert error.value.code == "WIZARD_NO_PLATFORM"
    # The fixture tree only has threads_post and product_shot, so the fallback has to
    # find a format that names the platform rather than insisting on the table.
    assert apply.pick_format("text", "threads") == "threads_post"
    assert apply.pick_format("image", "instagram") == "product_shot"


def test_slot_answers_become_a_queue_slot() -> None:
    now = datetime(2026, 10, 15, 12, 34, 56, tzinfo=timezone.utc)
    assert apply.slot_value("next") == "next"
    assert apply.slot_value("now", now=now) == "2026-10-15T12:34:56Z"
    assert apply.slot_value("date:2026-10-20T12:00:00Z") == "2026-10-20T12:00:00Z"


# --- the command surface -------------------------------------------------------

def test_cli_next_and_answer_round_trip(isolated_home: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["wizard", "next", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "os"
    assert main(["wizard", "answer", "--id", "os", "--value", "macos"]) == 0
    capsys.readouterr()
    assert main(["wizard", "status"]) == 0
    assert "setup" in capsys.readouterr().out


def test_cli_answer_exits_non_zero_on_a_re_ask(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["wizard", "answer", "--id", "os", "--value", "???"]) == 3
    out = capsys.readouterr().out
    assert "macos" in out, "the choices come back with the re-ask"


def test_cli_reset_takes_a_flow(isolated_home: Path,
                                capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["wizard", "reset", "--flow", "make"]) == 0
    assert "make" in capsys.readouterr().out


def test_key_set_never_prints_the_value(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    # Deliberately not shaped like a real key: a scanner should not flag a test.
    secret = "placeholder-value-000111222333"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(secret + "\n"))
    assert main(["key", "set", "--profile", "_example", "--name",
                 "ANTHROPIC_API_KEY", "--stdin"]) == 0
    captured = capsys.readouterr()
    assert secret not in captured.out and secret not in captured.err
    assert "ANTHROPIC_API_KEY" in captured.out
    assert main(["key", "list", "--profile", "_example", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["keys"]["ANTHROPIC_API_KEY"] is True
    assert listed["keys"]["YOUTUBE_REFRESH_TOKEN"] is False
    assert secret not in json.dumps(listed)


def test_profile_platform_is_the_owner_of_platforms_json(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "add", "demo"]) == 0
    capsys.readouterr()
    assert main(["profile", "platform", "--profile", "demo", "--platform", "threads",
                 "--enable", "--slot", "09:00Z", "--daily-cap", "2"]) == 0
    capsys.readouterr()
    blocks = json.loads((isolated_home / "profiles" / "demo" / "platforms.json")
                        .read_text(encoding="utf-8"))
    assert blocks["threads"] == {**blocks["threads"], "enabled": True,
                                 "slots": ["09:00Z"], "daily_cap": 2}
    assert main(["profile", "platform", "--profile", "demo", "--platform", "threads",
                 "--slot", "nope"]) == 3


def test_simple_flag_lives_in_factory_toml(isolated_home: Path,
                                           tmp_path: Path) -> None:
    assert Config().simple is True
    write_keys(tmp_path / "factory.toml", {"simple": False})
    assert load_config().simple is False


@pytest.mark.parametrize("raw,expected", [
    ("글이요", "text"), ("그냥 글", "text"), ("사진 하나", "image"),
    ("영상 만들래", "video"), ("말로 설명할게", "talk"),
])
def test_one_syllable_korean_aliases_still_match(raw: str, expected: str) -> None:
    """글 is one character and a whole word. Requiring two dropped it, and "글이요" came
    back as "not one of the choices" (measured 2026-10-15)."""
    assert questions.resolve("what", raw) == expected


def test_gate_failures_come_back_as_codes_not_scraped_from_a_table(
        isolated_home: Path) -> None:
    """The table is localized, so "FAIL" is not in it in Korean. Reading the reason out
    of the rendered table produced an empty reason (measured 2026-10-15)."""
    config = config_for(isolated_home, ui_lang="ko")
    result = apply.Result()
    gates = apply.gate(config, text="a " * 400, profile="_example",
                       format_name="threads_post", kind="text", result=result)
    assert gates.ok is False
    assert gates.first_failure().startswith("limits")
    assert "CHARS_OVER" in gates.first_failure()
    assert gates.table and "FAIL" not in gates.table
    assert any("--json" in command for command in result.commands())


# --- the phone, wired into the make flow ---------------------------------------

def wire_telegram(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Make `telegram_live` true and record the previews instead of sending them.

    Generation is mocked too: a phone send is not a dry run, so these flows run with
    `dry_run=False` and must not reach a paid generator.
    """
    sent: list[list[str]] = []

    def fake_generate(config, *, kind, brief_id, dry_run, result):
        # The real generator writes the candidate onto the brief, and the queue step
        # reads it from there, so the stand-in has to do the same.
        from factory.spec.brief import Output, load_brief, save_brief

        text = "one cached header cost three days"
        brief = load_brief(config.home_path, brief_id)
        brief.outputs = [Output(text=text, gates={}, chosen=False)]
        save_brief(config.home_path, brief)
        return text

    monkeypatch.setattr(apply, "generate", fake_generate)
    monkeypatch.setattr(apply, "gate",
                        lambda config, **kwargs: apply.Gates(ok=True,
                                                             table="limits  PASS"))

    def fake_preview_draft(config, *, brief_id, output=0, result):
        sent.append(["draft", brief_id])
        result.ran.append(apply.Ran(("telegram", "preview", "--brief", brief_id), 0))
        return ""

    def fake_preview_item(config, *, item_id, result):
        sent.append(["item", item_id])
        result.ran.append(apply.Ran(("telegram", "preview", "--item", item_id), 0))
        return ""

    monkeypatch.setattr(apply, "telegram_live", lambda config: True)
    monkeypatch.setattr(apply, "preview_draft", fake_preview_draft)
    monkeypatch.setattr(apply, "preview_item", fake_preview_item)
    return sent


def test_telegram_review_mirrors_the_draft_and_then_the_queued_item(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = wire_telegram(isolated_home, monkeypatch)
    config = config_for(isolated_home, review="telegram")
    start_make(config)
    answer(config, "what", "text", dry_run=False)
    answer(config, "topic", "one cached header cost three days", dry_run=False)
    engine.skip(config)
    shown = ask(config, dry_run=False)
    assert sent[0][0] == "draft", "the draft goes to the phone at the draft step"
    assert shown["phone"]
    answer(config, "draft", "ok", dry_run=False)
    result = answer(config, "when", "next", dry_run=False)
    assert sent[1][0] == "item", "the buttons ride on the queued item"
    assert sent[1][1] == result["notes"]["item"]


def test_a_dry_run_never_sends_to_the_phone(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run has nothing real to show, and a send is not a dry run."""
    sent = wire_telegram(isolated_home, monkeypatch)
    config = config_for(isolated_home, review="telegram")
    start_make(config)
    answer(config, "what", "text")
    answer(config, "topic", "a dry run")
    engine.skip(config)
    shown = ask(config)
    assert sent == []
    assert "phone" not in shown
    answer(config, "draft", "ok")
    answer(config, "when", "next")
    assert sent == []


def test_a_terminal_only_home_never_touches_the_phone(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = wire_telegram(isolated_home, monkeypatch)
    monkeypatch.setattr(apply, "telegram_live", lambda config: False)
    config = config_for(isolated_home, review="terminal")
    start_make(config)
    answer(config, "what", "text", dry_run=False)
    answer(config, "topic", "a quiet terminal run", dry_run=False)
    engine.skip(config)
    shown = ask(config, dry_run=False)
    assert sent == []
    assert "phone" not in shown


def test_telegram_live_needs_both_the_channel_and_a_real_chat(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.telegram import setup as telegram_setup
    from factory.telegram import state as telegram_state

    assert apply.telegram_live(config_for(isolated_home, review="telegram")) is False
    telegram_setup.save_token(isolated_home, "_example", "123456:" + "a" * 35)
    assert apply.telegram_live(config_for(isolated_home, review="telegram")) is False
    telegram_state.save_chat(isolated_home, "99887766")
    assert apply.telegram_live(config_for(isolated_home, review="telegram")) is True
    # The channel still decides: a bot plus review=terminal asked not to be buzzed.
    assert apply.telegram_live(config_for(isolated_home, review="terminal")) is False


def queue_one(config: Config, isolated_home: Path) -> str:
    start_make(config)
    answer(config, "what", "text")
    answer(config, "topic", "three tools at once")
    engine.skip(config)
    ask(config)
    answer(config, "draft", "ok")
    return answer(config, "when", "next")["notes"]["item"]


def test_a_phone_correction_reopens_the_draft_and_is_consumed_once(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    item_id = queue_one(config, isolated_home)
    store = QueueStore(isolated_home)
    store.unapprove(item_id)
    store.disarm(item_id)
    store.set_extra(item_id, apply.PHONE_EDIT_KEY, "cut the last line")
    brief_id = engine.status(config)["flows"]["make"]["notes"]["brief"]

    reopened = ask(config)
    assert reopened["id"] == "draft", "a correction from the phone asks the draft again"
    assert "cut the last line" in reopened["phone"]
    brief = json.loads((isolated_home / "briefs" / f"{brief_id}.json")
                       .read_text(encoding="utf-8"))
    assert brief["prompt_revisions"] == 1
    assert "cut the last line" in brief["prompt_final"]
    # Consumed: the same correction must not be applied again on the next next.
    assert store.get(item_id).extra[apply.PHONE_EDIT_KEY] == ""
    answer(config, "draft", "ok")
    assert ask(config)["id"] == "when"


def test_a_phone_rejection_asks_for_another_draft(isolated_home: Path) -> None:
    config = config_for(isolated_home)
    item_id = queue_one(config, isolated_home)
    store = QueueStore(isolated_home)
    store.unapprove(item_id)
    store.disarm(item_id)
    store.record_error(item_id, apply.PHONE_REJECT_CODE)
    assert ask(config)["id"] == "draft"


def test_whichever_answer_lands_first_is_the_one_used(isolated_home: Path) -> None:
    """Both ends can answer the draft question. Running both would make two decisions
    out of one draft, so the second is dropped and said out loud."""
    config = config_for(isolated_home)
    item_id = queue_one(config, isolated_home)
    store = QueueStore(isolated_home)
    store.unapprove(item_id)
    store.disarm(item_id)
    store.set_extra(item_id, apply.PHONE_EDIT_KEY, "make the hook shorter")
    # The terminal answers the same round without fetching the question first.
    result = answer(config, "draft", "이대로 좋아요")
    assert result["ok"] is True
    assert "이미 폰에서" in result["saved"] or "phone already" in result["saved"].lower()
    # The phone's correction is what got applied, so the draft is asked again.
    assert ask(config)["id"] == "draft"


def test_a_phone_approval_is_not_mistaken_for_the_wizards_own_arming(
        isolated_home: Path) -> None:
    config = config_for(isolated_home)
    item_id = queue_one(config, isolated_home)
    item = QueueStore(isolated_home).get(item_id)
    # simple mode armed it with the user's words, not from a chat.
    assert item.armed and not str(item.arm_reason).startswith(apply.PHONE_ARM_PREFIX)
    assert apply.phone_answer(config, item_id=item_id) == ("", "")
    QueueStore(isolated_home).arm(item_id, f"{apply.PHONE_ARM_PREFIX}99887766")
    assert apply.phone_answer(config, item_id=item_id) == ("ok", "")
