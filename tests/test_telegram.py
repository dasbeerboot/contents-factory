"""Telegram channel: setup, preview payloads, button handling, idempotency, policy.

Every test replaces the transport, so nothing reaches api.telegram.org and the only
token in play is a shape-valid fake.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.config import Config, TelegramConfig
from factory.credentials import load_env
from factory.errors import FactoryError
from factory.queue.store import QueueStore
from factory.review import telegram as channel
from factory.scheduler import tick
from factory.telegram import api, daemon, notify, state
from factory.telegram import setup as botsetup
from factory.telegram.serve import Serve
from tests.conftest import make_item

TOKEN = "123456789:AAEhBOweik6ad9r-FakeTokenForTestsOnly123"
CHAT = "4242424242"
OTHER_CHAT = "999999"
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


class FakeTransport:
    """Records every call and answers from a canned table."""

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str], api.Attachment | None]] = []
        self.responses = responses or {}

    def __call__(self, method: str, params: dict[str, str],
                 attachment: api.Attachment | None) -> dict:
        self.calls.append((method, params, attachment))
        answer = self.responses.get(method)
        if isinstance(answer, list):
            answer = answer.pop(0) if answer else None
        if answer is not None:
            return dict(answer)  # type: ignore[arg-type]
        return {"ok": True, "result": {"message_id": len(self.calls)}}

    def methods(self) -> list[str]:
        return [method for method, _params, _file in self.calls]

    def last(self, method: str) -> dict[str, str]:
        for name, params, _file in reversed(self.calls):
            if name == method:
                return params
        raise AssertionError(f"{method} was never called")


def client(responses: dict[str, object] | None = None) -> tuple[api.Client,
                                                                FakeTransport]:
    transport = FakeTransport(responses)
    return api.Client(TOKEN, transport=transport), transport


def written(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


# ---------- api ----------

@pytest.mark.parametrize("token", ["", "   ", "nope", "123:short",
                                   "abc:AAEhBOweik6ad9r-FakeTokenForTestsOnly123"])
def test_token_shape_is_refused_before_it_travels(token: str) -> None:
    with pytest.raises(FactoryError) as error:
        api.check_token(token)
    assert error.value.code in {"TELEGRAM_TOKEN_MISSING", "TELEGRAM_TOKEN_INVALID"}


def test_callback_data_stays_inside_the_sixty_four_byte_button_limit() -> None:
    item = make_item()
    data = api.callback_data("ok", item.id)
    assert data == f"cf:ok:{item.id}"
    assert len(data.encode("utf-8")) <= api.CALLBACK_MAX


def test_callback_data_refuses_an_oversized_payload() -> None:
    with pytest.raises(FactoryError) as error:
        api.callback_data("ok", "q_" + "x" * 80)
    assert error.value.code == "TELEGRAM_CALLBACK_TOO_LONG"


def test_booleans_and_structures_are_encoded_the_way_telegram_wants() -> None:
    bot, transport = client()
    bot.call("sendVideo", supports_streaming=True, width=1080,
             reply_markup={"inline_keyboard": []}, nothing=None)
    params = transport.last("sendVideo")
    assert params["supports_streaming"] == "true"
    assert params["width"] == "1080"
    assert json.loads(params["reply_markup"]) == {"inline_keyboard": []}
    assert "nothing" not in params


def test_multipart_carries_the_fields_and_the_file() -> None:
    attachment = api.Attachment(field="video", path=Path("clip.mp4"))
    content_type, body = api.multipart({"chat_id": CHAT, "supports_streaming": "true"},
                                       attachment, b"BINARY")
    assert content_type.startswith("multipart/form-data; boundary=")
    text = body.decode("utf-8", "replace")
    assert 'name="chat_id"' in text and CHAT in text
    assert 'name="video"; filename="clip.mp4"' in text
    assert "video/mp4" in text
    assert "BINARY" in text


def test_a_not_ok_response_becomes_a_code_without_the_description() -> None:
    with pytest.raises(FactoryError) as error:
        api.check_response({"ok": False, "error_code": 403,
                            "description": "token 123456789:secret"}, "sendMessage")
    assert error.value.code == "TELEGRAM_REJECTED"
    assert "secret" not in str(error.value)
    assert str(error.value).endswith("sendMessage:403")


# ---------- setup ----------

@pytest.mark.parametrize("lang", ["ko", "en"])
def test_setup_guide_is_three_numbered_botfather_steps(lang: str) -> None:
    steps = botsetup.guide(lang)
    assert len(steps) == 3
    assert steps[0].startswith("1. ")
    assert "BotFather" in steps[0]
    assert "/newbot" in steps[0]


def test_verify_returns_the_username_only() -> None:
    transport = FakeTransport({"getMe": {"ok": True,
                                         "result": {"id": 77, "username": "demo_bot"}}})
    identity = botsetup.verify(TOKEN, transport=transport)
    assert identity.username == "demo_bot"
    assert TOKEN not in json.dumps(identity.__dict__)


def test_verify_refuses_a_bot_without_a_username() -> None:
    transport = FakeTransport({"getMe": {"ok": True, "result": {}}})
    with pytest.raises(FactoryError) as error:
        botsetup.verify(TOKEN, transport=transport)
    assert error.value.code == "TELEGRAM_BOT_UNVERIFIED"


def test_save_token_writes_the_key_and_locks_the_file(isolated_home: Path) -> None:
    botsetup.save_token(isolated_home, "_example", TOKEN)
    path = isolated_home / "profiles" / "_example" / "credentials.env"
    assert load_env(path)["TELEGRAM_BOT_TOKEN"] == TOKEN
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_wait_for_start_saves_the_chat_and_advances_the_offset(
        isolated_home: Path) -> None:
    bot, _transport = client({"getUpdates": {
        "ok": True,
        "result": [{"update_id": 51,
                    "message": {"date": 1, "chat": {"id": int(CHAT),
                                                    "username": "owner"},
                                "text": "/start"}}]}})
    found = botsetup.wait_for_start(isolated_home, bot, seconds=1)
    assert found == CHAT
    assert state.chat_id(isolated_home) == CHAT
    # The serve loop must not replay this /start as a fresh instruction.
    assert state.read_offset(isolated_home) == 52


def test_wait_for_start_times_out_without_a_message(isolated_home: Path) -> None:
    bot, _transport = client({"getUpdates": {"ok": True, "result": []}})
    clock = iter([0.0, 0.0, 99.0, 99.0, 99.0])
    assert botsetup.wait_for_start(isolated_home, bot, seconds=1,
                                       clock=lambda: next(clock)) == ""
    assert state.chat_id(isolated_home) == ""


def test_status_never_contains_the_token_value(isolated_home: Path) -> None:
    state.save_chat(isolated_home, CHAT)
    data = botsetup.status(isolated_home, TOKEN)
    assert data["token"] is True
    assert TOKEN not in json.dumps(data)
    assert data["chat_id"] == CHAT


# ---------- preview payloads ----------

def test_text_preview_is_one_message_with_a_pre_block_and_three_buttons() -> None:
    item = make_item(text="line one\nline two <b>")
    built = channel.build(item, chat_id=CHAT, lang="ko")
    assert [payload.method for payload in built.payloads] == ["sendMessage"]
    params = built.payloads[0].params
    assert params["parse_mode"] == "HTML"
    assert "<pre>line one\nline two &lt;b&gt;</pre>" in params["text"]
    assert item.platform in params["text"] and item.scheduled_at in params["text"]
    buttons = params["reply_markup"]["inline_keyboard"][0]
    assert [button["callback_data"] for button in buttons] == [
        f"cf:ok:{item.id}", f"cf:no:{item.id}", f"cf:ed:{item.id}"]
    assert [button["text"] for button in buttons] == ["올리기 ✅", "거절 ❌", "고쳐줘 ✏️"]


def test_image_preview_uses_sendphoto(tmp_path: Path) -> None:
    picture = written(tmp_path / "card.png", 2048)
    item = make_item(kind="image", media=[{"path": str(picture), "kind": "image"}])
    built = channel.build(item, chat_id=CHAT)
    assert [payload.method for payload in built.payloads] == ["sendPhoto"]
    assert built.payloads[0].field == "photo"
    assert built.payloads[0].file == picture


def test_an_image_over_the_photo_ceiling_travels_as_a_document(tmp_path: Path) -> None:
    picture = written(tmp_path / "huge.png", api.PHOTO_MAX + 1)
    item = make_item(kind="image", media=[{"path": str(picture), "kind": "image"}])
    built = channel.build(item, chat_id=CHAT)
    assert built.payloads[0].method == "sendDocument"


def test_video_preview_declares_width_height_and_streaming(tmp_path: Path) -> None:
    clip = written(tmp_path / "reel.mp4", 4096)
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    built = channel.build(item, chat_id=CHAT)
    payload = built.payloads[0]
    assert payload.method == "sendVideo"
    assert payload.field == "video"
    assert payload.params["width"] == channel.VIDEO_WIDTH == 1080
    assert payload.params["height"] == channel.VIDEO_HEIGHT == 1920
    assert payload.params["supports_streaming"] is True


def test_video_is_never_sent_as_a_document(tmp_path: Path) -> None:
    """A document attachment is a download, not a preview."""
    clip = written(tmp_path / "reel.mp4", api.PHOTO_MAX + 1)
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    assert [payload.method for payload
            in channel.build(item, chat_id=CHAT).payloads] == ["sendVideo"]


def test_a_body_too_long_for_a_caption_becomes_its_own_message(tmp_path: Path) -> None:
    clip = written(tmp_path / "reel.mp4", 4096)
    item = make_item(kind="video", text="x" * (api.CAPTION_MAX + 100),
                     media=[{"path": str(clip), "kind": "video"}])
    built = channel.build(item, chat_id=CHAT)
    assert [payload.method for payload in built.payloads] == ["sendMessage", "sendVideo"]
    # The buttons must ride on the media message, not the text one.
    assert "reply_markup" not in built.payloads[0].params
    assert "reply_markup" in built.payloads[1].params


def test_plan_flags_an_oversized_clip_for_re_encoding(tmp_path: Path) -> None:
    clip = written(tmp_path / "reel.mp4", api.UPLOAD_MAX + 1)
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    built = channel.plan(item, chat_id=CHAT, lang="ko")
    assert built.note == "PREVIEW_REENCODE_REQUIRED"
    assert built.reduced is True
    assert "프리뷰본" in built.payloads[0].params["caption"]
    assert built.payloads[0].file == channel.preview_path(clip)


def test_send_preview_uploads_and_reports_message_ids(tmp_path: Path) -> None:
    clip = written(tmp_path / "reel.mp4", 4096)
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    bot, transport = client()
    result = channel.send_preview(item, client=bot, chat_id=CHAT, lang="en")
    assert result == {"item": item.id, "reduced": False, "message_ids": [1],
                      "methods": ["sendVideo"]}
    params = transport.last("sendVideo")
    assert params["supports_streaming"] == "true"
    assert params["width"] == "1080" and params["height"] == "1920"
    assert transport.calls[0][2] == api.Attachment(field="video", path=clip)


def test_send_preview_without_a_chat_refuses() -> None:
    bot, _transport = client()
    with pytest.raises(FactoryError) as error:
        channel.send_preview(make_item(), client=bot, chat_id="", lang="en")
    assert error.value.code == "TELEGRAM_CHAT_MISSING"


# ---------- serve: buttons ----------

def loop(home: Path, *, simple: bool = False,
         responses: dict[str, object] | None = None) -> tuple[Serve, FakeTransport]:
    bot, transport = client(responses)
    return (Serve(home=home, store=QueueStore(home), client=bot, chat_id=CHAT,
                  lang="en", simple=simple), transport)


def queued(home: Path, **overrides: object):
    item = make_item(**overrides)
    QueueStore(home).add(item)
    return item


def callback(item_id: str, action: str, *, chat: str = CHAT,
             update_id: int = 1) -> dict:
    return {"update_id": update_id,
            "callback_query": {"id": "cb1", "data": f"cf:{action}:{item_id}",
                               "from": {"id": 1},
                               "message": {"message_id": 9,
                                           "chat": {"id": int(chat)}}}}


def typed(text: str, *, chat: str = CHAT, update_id: int = 2,
          age: int = 0) -> dict:
    import time
    return {"update_id": update_id,
            "message": {"date": int(time.time()) - age, "text": text,
                        "chat": {"id": int(chat)}}}


def test_publish_button_in_simple_mode_approves_and_arms(isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, transport = loop(isolated_home, simple=True)
    assert serve.handle_update(callback(item.id, "ok")) == "APPROVED_ARMED"
    stored = QueueStore(isolated_home).get(item.id)
    assert (stored.approved, stored.armed) == (True, True)
    assert stored.arm_reason == f"telegram:{CHAT}"
    assert "answerCallbackQuery" in transport.methods()
    # The keyboard is cleared so a second tap has nothing to hit.
    assert "editMessageReplyMarkup" in transport.methods()


def test_publish_button_in_expert_mode_approves_without_arming(
        isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, _transport = loop(isolated_home, simple=False)
    assert serve.handle_update(callback(item.id, "ok")) == "APPROVED"
    stored = QueueStore(isolated_home).get(item.id)
    assert (stored.approved, stored.armed) == (True, False)


def test_reject_button_clears_approval_arming_and_records_a_reason(
        isolated_home: Path) -> None:
    item = queued(isolated_home, approved=True, armed=True,
                  arm_reason="owner approved in chat")
    serve, _transport = loop(isolated_home, simple=True)
    assert serve.handle_update(callback(item.id, "no")) == "REJECTED"
    stored = QueueStore(isolated_home).get(item.id)
    assert (stored.approved, stored.armed, stored.arm_reason) == (False, False, None)
    assert stored.last_error == "TELEGRAM_REJECTED"


def test_fix_it_records_the_next_typed_line_for_the_wizard(
        isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, _transport = loop(isolated_home)
    assert serve.handle_update(callback(item.id, "ed")) == "EDIT_WAIT"
    assert state.pending_edit(isolated_home, CHAT) == item.id
    assert serve.handle_update(typed("make the hook shorter")) == "EDIT_SAVED"
    assert state.read_edit(isolated_home, item.id) == "make the hook shorter"
    stored = QueueStore(isolated_home).get(item.id)
    assert stored.extra["edit_request"] == "make the hook shorter"
    # The regeneration is the wizard's job: nothing was approved or armed here.
    assert (stored.approved, stored.armed) == (False, False)
    assert state.pending_edit(isolated_home, CHAT) == ""


def test_a_typed_line_without_a_pending_edit_does_nothing(
        isolated_home: Path) -> None:
    queued(isolated_home)
    serve, transport = loop(isolated_home)
    assert serve.handle_update(typed("hello, make me a post")) == "NOOP"
    assert transport.methods() == []


def test_start_answers_with_the_role_and_nothing_else(isolated_home: Path) -> None:
    serve, transport = loop(isolated_home)
    assert serve.handle_update(typed("/start")) == "START"
    assert transport.methods() == ["sendMessage"]
    assert "preview" in transport.last("sendMessage")["text"]


def test_another_chat_cannot_drive_the_queue(isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, transport = loop(isolated_home, simple=True)
    assert serve.handle_update(callback(item.id, "ok", chat=OTHER_CHAT)) == "IGNORED_CHAT"
    assert QueueStore(isolated_home).get(item.id).approved is False
    assert transport.methods() == []


def test_an_old_typed_line_is_history_not_an_instruction(isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, _transport = loop(isolated_home)
    serve.handle_update(callback(item.id, "ed"))
    assert serve.handle_update(typed("too late", update_id=3, age=5000)) == "STALE"
    assert state.read_edit(isolated_home, item.id) == ""


def test_a_redelivered_update_is_a_no_op(isolated_home: Path) -> None:
    item = queued(isolated_home)
    serve, _transport = loop(isolated_home, simple=True)
    assert serve.handle_update(callback(item.id, "ok", update_id=7)) == "APPROVED_ARMED"
    QueueStore(isolated_home).unapprove(item.id)
    assert serve.handle_update(callback(item.id, "ok", update_id=7)) == "DUPLICATE"
    assert QueueStore(isolated_home).get(item.id).approved is False


def test_a_button_on_a_published_item_is_refused(isolated_home: Path) -> None:
    item = queued(isolated_home, published=True, published_id="x1", phase="published")
    serve, transport = loop(isolated_home, simple=True)
    assert serve.handle_update(callback(item.id, "ok")) == "ALREADY_PUBLISHED"
    assert "editMessageReplyMarkup" in transport.methods()


def test_an_unknown_callback_shape_is_answered_not_acted_on(
        isolated_home: Path) -> None:
    serve, transport = loop(isolated_home)
    update = {"update_id": 4,
              "callback_query": {"id": "cb2", "data": "cf:ok:not-an-id",
                                 "message": {"message_id": 1,
                                             "chat": {"id": int(CHAT)}}}}
    assert serve.handle_update(update) == "UNKNOWN_CALLBACK"
    assert transport.methods() == ["answerCallbackQuery"]


def test_a_missing_item_surfaces_as_a_failure_toast(isolated_home: Path) -> None:
    serve, transport = loop(isolated_home)
    action = serve.handle_update(callback("q_20261015120000_zzzz", "ok"))
    assert action == "FAILED:QUEUE_ITEM_MISSING"
    assert transport.methods() == ["answerCallbackQuery"]


# ---------- serve: polling ----------

def test_the_first_poll_skips_the_backlog(isolated_home: Path) -> None:
    serve, transport = loop(isolated_home, responses={
        "getUpdates": {"ok": True, "result": [{"update_id": 900}]}})
    assert serve.poll_once(timeout=0) == ["BACKLOG_SKIPPED"]
    assert state.read_offset(isolated_home) == 901
    assert transport.last("getUpdates")["offset"] == "-1"


def test_polling_advances_the_offset_past_each_update(isolated_home: Path) -> None:
    item = queued(isolated_home)
    state.write_offset(isolated_home, 10)
    serve, transport = loop(isolated_home, simple=True, responses={
        "getUpdates": {"ok": True, "result": [callback(item.id, "ok", update_id=10)]}})
    assert serve.poll_once(timeout=0) == ["APPROVED_ARMED"]
    assert state.read_offset(isolated_home) == 11
    assert transport.last("getUpdates")["allowed_updates"] == json.dumps(
        ["message", "callback_query"])


def test_run_once_returns_even_when_the_poll_fails(isolated_home: Path) -> None:
    state.write_offset(isolated_home, 0)
    serve, _transport = loop(isolated_home, responses={
        "getUpdates": {"ok": False, "error_code": 409}})
    assert serve.run(once=True, timeout=0) == ["FAILED:TELEGRAM_REJECTED"]


# ---------- notification policy ----------

@pytest.mark.parametrize("kind,expected", [
    ("publish_failed", notify.ALERT),
    ("delivery_unknown", notify.ALERT),
    ("token_expiry", notify.ALERT),
    ("daily_summary", notify.SUMMARY),
    ("publish_ok", notify.QUIET),
    ("queue_added", notify.QUIET),
    ("gate_passed", notify.QUIET),
    ("", notify.QUIET),
])
def test_only_the_listed_events_are_allowed_to_buzz(kind: str, expected: str) -> None:
    assert notify.classify(kind) == expected
    assert notify.messages_for(kind, "body") == (
        [] if expected == notify.QUIET else [notify.Message(kind, "body")])


def test_a_published_item_sends_nothing() -> None:
    report = tick.TickReport(published=["q_20261015120000_ab12"])
    assert notify.from_tick(report, lang="en") == []


def test_a_publish_failure_is_an_immediate_alert() -> None:
    report = tick.TickReport(errors=[("q_20261015120000_ab12", "HTTP_400")])
    messages = notify.from_tick(report, lang="en")
    assert [message.kind for message in messages] == ["publish_failed"]
    assert messages[0].render().startswith(notify.ALERT_SIGIL)
    assert "HTTP_400" in messages[0].text


def test_an_unconfirmed_delivery_is_reported_as_such() -> None:
    report = tick.TickReport(errors=[("q_20261015120000_ab12", "HTTP_500")])
    messages = notify.from_tick(report, lang="en")
    assert [message.kind for message in messages] == ["delivery_unknown"]
    assert "queue reopen" in messages[0].text


def test_a_token_warning_travels() -> None:
    report = tick.TickReport(warnings=["threads token expires in 3 day(s)"])
    messages = notify.from_tick(report, lang="en")
    assert [message.kind for message in messages] == ["token_expiry"]


def test_the_daily_digest_counts_yesterday_today_and_the_waiting(
        isolated_home: Path) -> None:
    store = QueueStore(isolated_home)
    store.add(make_item(id="q_20261014120000_aa11",
                        scheduled_at="2026-10-14T12:00:00Z", published=True,
                        published_id="p1", phase="published"))
    store.add(make_item(id="q_20261015120000_bb22",
                        scheduled_at="2026-10-15T12:00:00Z", approved=True))
    store.add(make_item(id="q_20261015230000_cc33",
                        scheduled_at="2026-10-15T23:00:00Z"))
    data = notify.digest(store, now=NOW)
    assert data.as_dict() == {"published_yesterday": 1, "scheduled_today": 2,
                              "awaiting_confirmation": 1}
    message = notify.digest_message(data, lang="ko")[0]
    assert message.render().startswith(notify.SUMMARY_SIGIL)
    assert "대기 컨펌 1건" in message.text


def test_the_digest_goes_out_once_a_day_after_its_hour() -> None:
    assert notify.due("21:00Z", now=NOW, last_sent_day="") is False
    evening = NOW.replace(hour=21, minute=30)
    assert notify.due("21:00Z", now=evening, last_sent_day="") is True
    assert notify.due("21:00Z", now=evening, last_sent_day="2026-10-15") is False
    assert notify.due("nonsense", now=evening, last_sent_day="") is False


def test_serve_sends_the_digest_once(isolated_home: Path) -> None:
    serve, transport = loop(isolated_home)
    serve.summary_at = "21:00Z"
    evening = NOW.replace(hour=21, minute=5)
    assert serve.maybe_digest(now=evening) == ["daily_summary"]
    assert state.summary_sent_on(isolated_home) == "2026-10-15"
    assert serve.maybe_digest(now=evening) == []
    assert transport.methods() == ["sendMessage"]


# ---------- state files ----------

def test_state_writes_are_atomic_and_bounded(isolated_home: Path) -> None:
    for index in range(state.SEEN_LIMIT + 20):
        assert state.mark_seen(isolated_home, f"u{index}") is True
    assert len(state.seen(isolated_home)) == state.SEEN_LIMIT
    assert state.mark_seen(isolated_home, f"u{state.SEEN_LIMIT + 19}") is False


@pytest.mark.parametrize("chat", ["", "abc", "12 34", "x" * 30])
def test_a_bad_chat_id_is_refused(isolated_home: Path, chat: str) -> None:
    with pytest.raises(FactoryError) as error:
        state.save_chat(isolated_home, chat)
    assert error.value.code == "TELEGRAM_CHAT_INVALID"


def test_an_edit_cannot_be_filed_under_a_bogus_item(isolated_home: Path) -> None:
    with pytest.raises(FactoryError) as error:
        state.save_edit(isolated_home, "../../etc/passwd", "text")
    assert error.value.code == "QUEUE_ID_INVALID"


def test_set_extra_refuses_a_mid_delivery_item(isolated_home: Path) -> None:
    item = queued(isolated_home, phase="container", container_id="c1")
    with pytest.raises(FactoryError) as error:
        QueueStore(isolated_home).set_extra(item.id, "edit_request", "later")
    assert error.value.code == "DELIVERY_IN_PROGRESS"


# ---------- daemon registration ----------

def test_launchd_plan_keeps_the_poller_alive_and_runs_serve(
        isolated_home: Path) -> None:
    plan = daemon.plan(home=isolated_home, runner="launchd", python="/usr/bin/python3")
    definition = plan["definition"]
    assert definition["ProgramArguments"][-2:] == ["telegram", "serve"]
    assert definition["KeepAlive"] is True
    assert definition["RunAtLoad"] is True
    assert "StartInterval" not in definition
    assert plan["label"] == "com.contents-factory.telegram"
    assert "<key>KeepAlive</key>" in plan["plist"]


def test_cron_and_schtasks_re_enter_a_single_poll_every_minute(
        isolated_home: Path) -> None:
    cron_plan = daemon.plan(home=isolated_home, runner="cron", python="/usr/bin/python3")
    assert "telegram serve --once" in cron_plan["crontab"]
    assert cron_plan["crontab"].startswith("# >>> contents-factory telegram >>>")
    task_plan = daemon.plan(home=isolated_home, runner="schtasks",
                            python="C:\\python.exe")
    command = " ".join(task_plan["command"])
    assert "telegram serve --once" in command
    assert "/MO 1" in command
    assert task_plan["task"] == "contents-factory-telegram"


def test_the_telegram_block_does_not_disturb_the_tick_block(
        isolated_home: Path) -> None:
    from factory.scheduler import cron

    tick_block = cron.block(home=isolated_home, python="/usr/bin/python3")
    merged = cron.merge(tick_block, home=isolated_home, python="/usr/bin/python3",
                        name=daemon.CRON_NAME, arguments=daemon.ONCE_ARGUMENTS)
    assert "schedule tick" in merged
    assert "telegram serve --once" in merged
    assert merged.count("# >>> contents-factory") == 2


def test_runner_resolution_follows_the_configured_os() -> None:
    assert daemon.runner_for("macos", "auto") == "launchd"
    assert daemon.runner_for("windows", "auto") == "schtasks"
    assert daemon.runner_for("linux", "auto") == "cron"
    assert daemon.runner_for("macos", "cron") == "cron"
    with pytest.raises(FactoryError) as error:
        daemon.runner_for("macos", "systemd")
    assert error.value.code == "RUNNER_UNKNOWN"


# ---------- config ----------

def test_simple_mode_and_the_digest_hour_come_from_factory_toml() -> None:
    # Beginner mode is the default (PLAN §9.7), so the phone asks one question unless
    # factory.toml turns it off.
    assert Config().simple is True
    assert Config(simple=False).simple is False
    assert Config().telegram == TelegramConfig(summary_at="21:00Z")
    assert Config(simple=True, telegram={"summary_at": "08:30Z"}).simple is True
    with pytest.raises(ValueError):
        TelegramConfig(summary_at="25:00Z")


def test_the_role_sentence_exists_in_both_languages() -> None:
    from factory import i18n

    for lang in ("ko", "en"):
        text = i18n.get("telegram.role", lang)
        assert text != "telegram.role"
        assert len(text) > 40
    assert i18n.missing("ko") == []


def test_the_review_channel_is_selectable() -> None:
    assert Config(review="telegram").review == "telegram"
    with pytest.raises(ValueError):
        Config(review="sms")


# ---------- oversized clips ----------

def test_an_oversized_clip_is_re_encoded_and_labelled(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = written(tmp_path / "reel.mp4", api.UPLOAD_MAX + 1)
    arguments: list[list[str]] = []

    def fake_ffmpeg(args, **_kwargs):
        arguments.append(list(args))
        written(channel.preview_path(clip), 1024)
        return ""

    monkeypatch.setattr(channel.media, "ffmpeg", fake_ffmpeg)
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    bot, transport = client()
    result = channel.send_preview(item, client=bot, chat_id=CHAT, lang="ko")
    assert result["reduced"] is True
    assert arguments and "-crf" in arguments[0]
    params = transport.last("sendVideo")
    assert "프리뷰본" in params["caption"]
    assert params["supports_streaming"] == "true"
    assert transport.calls[0][2].path == channel.preview_path(clip)


def test_a_clip_still_too_big_after_re_encoding_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = written(tmp_path / "reel.mp4", api.UPLOAD_MAX + 1)
    monkeypatch.setattr(channel.media, "ffmpeg",
                        lambda args, **kwargs: written(channel.preview_path(clip),
                                                       api.UPLOAD_MAX + 1) and "")
    item = make_item(kind="video", media=[{"path": str(clip), "kind": "video"}])
    bot, _transport = client()
    with pytest.raises(FactoryError) as error:
        channel.send_preview(item, client=bot, chat_id=CHAT, lang="en")
    assert error.value.code == "TELEGRAM_PREVIEW_TOO_LARGE"


# ---------- cli surface ----------

def wired(isolated_home: Path) -> None:
    botsetup.save_token(isolated_home, "_example", TOKEN)
    state.save_chat(isolated_home, CHAT)


def test_status_exits_nonzero_until_both_halves_are_wired(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main

    assert main(["telegram", "status"]) == 3
    wired(isolated_home)
    assert main(["telegram", "status"]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "token=y" in out and CHAT in out


def test_test_dry_run_prints_the_payload_and_sends_nothing(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main

    wired(isolated_home)
    assert main(["telegram", "test", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["method"] == "sendMessage"
    assert payload["params"]["chat_id"] == CHAT


def test_preview_dry_run_shows_the_exact_payloads(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main

    wired(isolated_home)
    item = queued(isolated_home)
    assert main(["telegram", "preview", "--item", item.id, "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["method"] for entry in payload["payloads"]] == ["sendMessage"]
    assert payload["payloads"][0]["params"]["chat_id"] == CHAT


def test_install_dry_run_changes_nothing_on_this_machine(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    from factory.scheduler import launchd

    assert main(["telegram", "install", "--runner", "launchd", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["definition"]["KeepAlive"] is True
    assert not launchd.agent_path(daemon.LABEL).exists()


# ---------- tick wiring ----------

def test_a_tick_that_published_cleanly_says_nothing(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.telegram import cli as telegram_cli

    wired(isolated_home)
    sent: list[str] = []
    monkeypatch.setattr(api.Client, "_http",
                        lambda self, method, params, attachment: sent.append(method)
                        or {"ok": True, "result": {}})
    report = tick.TickReport(published=["q_20261015120000_ab12"])
    assert telegram_cli.notify_tick(Config(home=str(isolated_home)), report) == 0
    assert sent == []


def test_a_tick_failure_reaches_the_phone(isolated_home: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.telegram import cli as telegram_cli

    wired(isolated_home)
    sent: list[dict[str, str]] = []

    def fake(self, method: str, params: dict[str, str], attachment: object) -> dict:
        sent.append({"method": method, **params})
        return {"ok": True, "result": {}}

    monkeypatch.setattr(api.Client, "_http", fake)
    report = tick.TickReport(errors=[("q_20261015120000_ab12", "HTTP_500")])
    assert telegram_cli.notify_tick(Config(home=str(isolated_home)), report) == 1
    assert sent[0]["method"] == "sendMessage"
    assert sent[0]["text"].startswith(notify.ALERT_SIGIL)


def test_an_unreachable_phone_does_not_fail_the_tick(
        isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.telegram import cli as telegram_cli

    wired(isolated_home)

    def dead(self, method: str, params: dict[str, str], attachment: object) -> dict:
        raise FactoryError("TELEGRAM_UNREACHABLE", method)

    monkeypatch.setattr(api.Client, "_http", dead)
    report = tick.TickReport(errors=[("q_20261015120000_ab12", "HTTP_500")])
    assert telegram_cli.notify_tick(Config(home=str(isolated_home)), report) == 0


def test_no_alert_is_attempted_without_a_chat(isolated_home: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.telegram import cli as telegram_cli

    botsetup.save_token(isolated_home, "_example", TOKEN)
    monkeypatch.setattr(api.Client, "_http",
                        lambda *args, **kwargs: pytest.fail("no chat, no send"))
    report = tick.TickReport(errors=[("q_20261015120000_ab12", "HTTP_500")])
    assert telegram_cli.notify_tick(Config(home=str(isolated_home)), report) == 0
