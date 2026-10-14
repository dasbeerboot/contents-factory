from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory import i18n, ledger
from factory.cli import main
from factory.credentials import credentials_path, env_set, load_env, require
from factory.errors import FactoryError, scrub
from factory.generators import text as text_generator
from factory.queue.store import QueueStore
from factory.scheduler import cron, launchd, token_refresh
from tests.conftest import make_item

PERSONA = "You write short honest build notes. " * 200


def test_system_prefix_is_stable_and_marked() -> None:
    first = text_generator.build_system(PERSONA, "format body", "rules body")
    second = text_generator.build_system(PERSONA, "format body", "rules body")
    assert first == second
    payload = text_generator.build_payload(first, "user body")
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["model"] == "claude-sonnet-5"
    assert payload["messages"] == [{"role": "user", "content": "user body"}]


def test_short_prefix_skips_the_cache_marker() -> None:
    payload = text_generator.build_payload("tiny system", "user body")
    assert "cache_control" not in payload["system"][0]


def test_system_prefix_order_is_persona_rules_format_schema() -> None:
    system_text = text_generator.build_system(PERSONA, "FORMAT_BODY", "RULES_BODY")
    assert system_text.index("# Persona") < system_text.index("RULES_BODY")
    assert system_text.index("RULES_BODY") < system_text.index("FORMAT_BODY")
    assert system_text.index("FORMAT_BODY") < system_text.index("Output format (strict)")


def test_cjk_prefix_is_not_undercounted() -> None:
    korean = "규칙" * 800
    latin = "rule" * 800
    assert text_generator.estimate_tokens(korean) > text_generator.estimate_tokens(latin)
    assert text_generator.cache_state(korean) == text_generator.CACHE_EPHEMERAL


def test_variable_values_stay_out_of_the_system_block() -> None:
    system_text = text_generator.build_system(PERSONA, "format body")
    user_text = text_generator.build_user(
        {"date": "2026-10-15", "slot": "12:00Z", "recent": ["a", "b"]},
        instruction="write it")
    assert "2026-10-15" not in system_text
    assert "12:00Z" not in system_text
    assert user_text.index("date: 2026-10-15") < user_text.index("write it")
    assert "- a" in user_text


def test_build_user_is_deterministic() -> None:
    values = {"b": 2, "a": 1, "empty": "", "none": None}
    assert text_generator.build_user(values) == "a: 1\nb: 2"


def test_generate_parses_and_tracks_usage() -> None:
    responses = [
        {"content": [{"type": "text",
                      "text": json.dumps({"text": "body one", "replies": ["r1"],
                                          "hypothesis": "h"})}],
         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1200,
                   "output_tokens": 30}},
        {"content": [{"type": "text", "text": json.dumps({"text": "body two"})}],
         "usage": {"input_tokens": 10, "cache_read_input_tokens": 1200,
                   "output_tokens": 20}},
    ]
    seen: list[dict[str, object]] = []
    logs: list[str] = []

    def transport(payload: dict[str, object], key: str) -> dict[str, object]:
        assert key == "test-key"
        seen.append(payload)
        return responses[len(seen) - 1]

    generator = text_generator.TextGenerator("test-key", transport=transport,
                                             log=logs.append)
    system_text = text_generator.build_system(PERSONA, "format body")
    first = generator.generate(system_text, "first user")
    second = generator.generate(system_text, "second user")
    assert first.text == "body one" and first.replies == ["r1"]
    assert second.usage.cache_read_input_tokens == 1200
    assert seen[0]["system"] == seen[1]["system"]
    assert not [line for line in logs if line.startswith("WARN")]


def test_second_call_without_cache_read_warns() -> None:
    logs: list[str] = []
    payloads = {"content": [{"type": "text", "text": json.dumps({"text": "x"})}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    generator = text_generator.TextGenerator("k", transport=lambda _p, _k: payloads,
                                             log=logs.append)
    system_text = text_generator.build_system(PERSONA, "f")
    generator.generate(system_text, "u")
    generator.generate(system_text, "u")
    assert any(line.startswith("WARN cache miss") for line in logs)


def test_generate_rejects_non_json() -> None:
    generator = text_generator.TextGenerator(
        "k", transport=lambda _p, _k: {"content": [{"type": "text", "text": "not json"}]})
    with pytest.raises(FactoryError) as error:
        generator.generate("system", "user")
    assert error.value.code == "DRAFT_NOT_JSON"


def test_generate_strips_code_fence() -> None:
    fenced = "```json\n{\"text\": \"body\"}\n```"
    generator = text_generator.TextGenerator(
        "k", transport=lambda _p, _k: {"content": [{"type": "text", "text": fenced}]})
    assert generator.generate("system", "user").text == "body"


def test_dry_run_reports_prefix_estimate() -> None:
    generator = text_generator.TextGenerator("k")
    system_text = text_generator.build_system(PERSONA, "format body")
    report = generator.dry_run(system_text, "user")
    assert report["cache"] == text_generator.CACHE_EPHEMERAL
    assert report["cache_minimum_tokens"] == 1024
    assert report["system_prefix_tokens_estimate"] > 1024
    assert report["payload"]["messages"][0]["content"] == "user"
    thin = generator.dry_run("tiny system", "user")
    assert thin["cache"] == text_generator.CACHE_SKIPPED
    assert "cache_control" not in thin["payload"]["system"][0]


def test_generator_requires_key() -> None:
    with pytest.raises(FactoryError) as error:
        text_generator.TextGenerator("")
    assert error.value.code == "ENV_MISSING"


def test_i18n_falls_back_to_key() -> None:
    assert i18n.get("interview.common.what.q", "ko").startswith("무엇")
    assert i18n.get("interview.common.what.q", "en").startswith("What")
    assert i18n.get("no.such.key", "ko") == "no.such.key"
    assert i18n.get("queue.added", "en", item_id="q", platform="threads",
                    scheduled_at="t") == "Queued q for threads at t."


def test_i18n_catalogues_match() -> None:
    assert i18n.missing("ko") == []
    assert i18n.missing("en") == []
    assert len([key for key in i18n.keys("en")
                if key.startswith("interview.common.")]) >= 33


def test_credentials_parse_and_require(tmp_path: Path) -> None:
    path = tmp_path / "credentials.env"
    path.write_text('# comment\nexport A_KEY="value one"\nB_KEY = plain \nbad line\n',
                    encoding="utf-8")
    values = load_env(path)
    assert values == {"A_KEY": "value one", "B_KEY": "plain"}
    require(values, ("A_KEY",))
    with pytest.raises(FactoryError) as error:
        require(values, ("C_KEY", "D_KEY"))
    assert error.value.code == "ENV_MISSING"
    assert error.value.detail == "C_KEY,D_KEY"


def test_env_set_replaces_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "credentials.env"
    env_set(path, "TOKEN", "one")
    env_set(path, "TOKEN", "two")
    env_set(path, "OTHER", "three")
    assert load_env(path) == {"TOKEN": "two", "OTHER": "three"}
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_error_detail_is_sanitized() -> None:
    assert str(FactoryError("HTTP_401", "Authorization: Bearer abcdef")) == "HTTP_401"
    assert str(FactoryError("ENV_MISSING", "A_KEY,B_KEY")) == "ENV_MISSING A_KEY,B_KEY"
    assert str(FactoryError("lowercase code")) == "UNEXPECTED"
    assert scrub("x-api-key: sk-abcdefghijklmnopqrstuvwxyz0123456789") == (
        "x-api-key=<masked>")
    assert scrub("see https://db.test/object/x") == "see <url>"


def test_token_refresh_dry_run_redacts(isolated_home: Path) -> None:
    path = credentials_path(isolated_home, "_example")
    env_set(path, "THREADS_ACCESS_TOKEN", "super-secret")
    report = token_refresh.refresh(isolated_home, "_example", "threads", dry_run=True)
    body = json.dumps(report)
    assert "super-secret" not in body
    assert report["request"]["params"]["access_token"] == "<REDACTED:THREADS_ACCESS_TOKEN>"


def test_token_refresh_writes_new_token(isolated_home: Path) -> None:
    path = credentials_path(isolated_home, "_example")
    env_set(path, "THREADS_ACCESS_TOKEN", "old-token")
    moment = datetime(2026, 10, 15, tzinfo=timezone.utc)
    report = token_refresh.refresh(isolated_home, "_example", "threads",
                                   fetch=lambda _url, _params: {"access_token": "new-token"},
                                   now=moment)
    assert report["issued_at"] == "2026-10-15"
    values = load_env(path)
    assert values["THREADS_ACCESS_TOKEN"] == "new-token"
    assert values["THREADS_TOKEN_ISSUED_AT"] == "2026-10-15"


def test_token_refresh_failure_is_code_form(isolated_home: Path) -> None:
    env_set(credentials_path(isolated_home, "_example"), "THREADS_ACCESS_TOKEN", "old")
    with pytest.raises(FactoryError) as error:
        token_refresh.refresh(isolated_home, "_example", "threads",
                              fetch=lambda _url, _params: {"error": {"message": "nope"}})
    assert error.value.code == "TOKEN_REFRESH_FAILED"


def test_expiry_warning_window(isolated_home: Path) -> None:
    path = credentials_path(isolated_home, "_example")
    env_set(path, "THREADS_ACCESS_TOKEN", "tok")
    env_set(path, "THREADS_TOKEN_ISSUED_AT", "2026-08-20")
    status = token_refresh.expiry_status(isolated_home, "_example", "threads",
                                        now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert status.days_left == 4 and status.warn is True
    fresh = token_refresh.expiry_status(isolated_home, "_example", "threads",
                                        now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert fresh.warn is False
    messages = token_refresh.warnings(isolated_home, "_example", lang="en",
                                      now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert any("expires in 4" in message for message in messages)


def test_launchd_definition_shape(isolated_home: Path) -> None:
    definition = launchd.definition(home=isolated_home, python="/usr/bin/python3")
    assert definition["Label"] == "com.contents-factory.tick"
    assert definition["ProgramArguments"][1:] == ["-m", "factory", "schedule", "tick"]
    assert definition["StartInterval"] == 900
    assert definition["EnvironmentVariables"]["FACTORY_HOME"] == str(isolated_home)
    assert launchd.render(definition).startswith(b"<?xml")


def test_cron_block_is_idempotent(isolated_home: Path) -> None:
    first = cron.merge("# existing\n0 * * * * echo hi", home=isolated_home,
                       python="/usr/bin/python3")
    second = cron.merge(first, home=isolated_home, python="/usr/bin/python3")
    assert first == second
    assert first.count(cron.BEGIN) == 1
    assert "factory schedule tick" in first
    assert cron.strip_block(first) == "# existing\n0 * * * * echo hi"


def test_ledger_records_and_summarizes(isolated_home: Path) -> None:
    ledger.record(isolated_home, track="text", kind="threads_post", model="claude-sonnet-5",
                  tokens=1200, now="2026-10-15T12:00:00Z")
    ledger.record(isolated_home, track="publish", kind="text", platform="threads",
                  result="HTTP_429", now="2026-10-15T12:05:00Z")
    data = ledger.summary(isolated_home, days=30,
                          now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    assert data["rows"] == 2
    assert data["tracks"]["text"]["tokens"] == 1200
    assert data["tracks"]["publish"]["failures"] == 1
    assert "ledger summary" in ledger.render_summary(data)


def test_cli_i18n_get(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["i18n", "get", "interview.common.what.q", "--lang", "en"]) == 0
    assert capsys.readouterr().out.startswith("What do you want to make?")


def test_cli_init_creates_home(isolated_home: Path,
                               capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init"]) == 0
    assert (isolated_home / "queue").is_dir()
    assert (isolated_home / "factory.toml").is_file()
    assert "home:" in capsys.readouterr().out


def test_cli_queue_add_approve_arm_and_dry_run_publish(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["queue", "add", "--platform", "threads", "--text", "a build note",
                 "--slot", "2036-10-15T12:00:00Z"]) == 0
    capsys.readouterr()
    item_id = QueueStore(isolated_home).list()[0].id
    assert main(["queue", "approve", "--item", item_id]) == 0
    assert main(["queue", "arm", "--item", item_id, "--reason", "owner said go"]) == 0
    capsys.readouterr()
    assert main(["publish", "threads", "--item", item_id, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "<REDACTED:THREADS_ACCESS_TOKEN>" in out
    assert "graph.threads.net" in out


def test_cli_publish_refuses_unarmed(isolated_home: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    store = QueueStore(isolated_home)
    item = store.add(make_item(scheduled_at="2036-10-15T12:00:00Z"))
    assert main(["publish", "threads", "--item", item.id]) == 3
    assert "NOT_APPROVED" in capsys.readouterr().err


def test_cli_gate_run_reports_failures(isolated_home: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["gate", "run", "--text", "this is a game changer", "--format",
                 "threads_post", "--kind", "text"])
    out = capsys.readouterr().out
    assert code == 3
    assert "forbidden_words" in out
    assert "game changer" in out


def test_cli_brief_new_and_gen_dry_run(isolated_home: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    answers = isolated_home / "answers.json"
    answers.write_text(json.dumps({"platforms": ["threads"], "count": 1,
                                   "answers": {"text.threads.angle": "cached header"},
                                   "prompt_final": "Write one build note."}),
                       encoding="utf-8")
    assert main(["brief", "new", "--kind", "text", "--format", "threads_post",
                 "--answers", str(answers)]) == 0
    brief_id = json.loads(capsys.readouterr().out)["brief"]
    assert main(["gen", "text", "--brief", brief_id, "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"] == "claude-sonnet-5"
    system_text = payload["payload"]["system"][0]["text"]
    assert "one-person software studio" in system_text
    assert "cached header" in payload["payload"]["messages"][0]["content"]


def test_cli_formats_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["formats", "--kind", "text"]) == 0
    assert json.loads(capsys.readouterr().out) == {"text": ["threads_post"]}


def test_cli_schedule_status_lists_upcoming(isolated_home: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    store = QueueStore(isolated_home)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    stamp = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    item = store.add(make_item(scheduled_at=stamp))
    assert main(["schedule", "status", "--runner", "cron"]) == 0
    out = capsys.readouterr().out
    assert "next seven days" in out.lower()
    assert item.id in out


# --------------------------------------------- thinking eats max_tokens (2026-10-14)

def _response(content: list[dict[str, object]], stop_reason: str) -> dict[str, object]:
    return {"content": content, "stop_reason": stop_reason,
            "usage": {"input_tokens": 10, "output_tokens": 8000}}


def test_a_thinking_only_response_reports_truncation_not_emptiness() -> None:
    """Sonnet 5 thinks by default and thinking tokens come out of max_tokens. A budget
    that runs out mid-deliberation returns an empty thinking block and no text."""
    from factory.generators import text as text_generator

    generator = text_generator.TextGenerator(
        "key", transport=lambda payload, key: _response(
            [{"type": "thinking", "thinking": ""}], "max_tokens"))
    with pytest.raises(FactoryError) as error:
        generator.generate("system", "user")
    assert error.value.code == "OUTPUT_TRUNCATED"
    assert "max_tokens=" in error.value.detail


def test_a_refusal_is_its_own_code() -> None:
    from factory.generators import text as text_generator

    generator = text_generator.TextGenerator(
        "key", transport=lambda payload, key: _response([], "refusal"))
    with pytest.raises(FactoryError) as error:
        generator.generate("system", "user")
    assert error.value.code == "OUTPUT_REFUSED"


def test_an_empty_answer_that_ended_normally_stays_empty_content() -> None:
    from factory.generators import text as text_generator

    generator = text_generator.TextGenerator(
        "key", transport=lambda payload, key: _response([], "end_turn"))
    with pytest.raises(FactoryError) as error:
        generator.generate("system", "user")
    assert error.value.code == "EMPTY_CONTENT"


def test_a_thinking_block_before_the_answer_is_skipped_not_fatal() -> None:
    from factory.generators import text as text_generator

    body = '{"text": "the post", "replies": [], "hypothesis": "h"}'
    generator = text_generator.TextGenerator(
        "key", transport=lambda payload, key: _response(
            [{"type": "thinking", "thinking": ""}, {"type": "text", "text": body}],
            "end_turn"))
    assert generator.generate("system", "user").text == "the post"


def test_the_payload_never_sends_budget_tokens() -> None:
    """budget_tokens is a 400 on sonnet-5; adaptive thinking replaced it."""
    from factory.generators import text as text_generator

    payload = text_generator.build_payload("system", "user")
    assert "thinking" not in payload
    assert payload["max_tokens"] >= 8000
