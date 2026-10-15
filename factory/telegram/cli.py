"""`factory telegram ...` — setup, status, test, preview, serve, install, remove.

Nothing here generates content or asks an interview question. The commands exist so a
phone can see what was made and answer one question about it.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from .. import i18n
from ..config import Config
from ..credentials import credentials_path, load_profile_env
from ..errors import FactoryError
from ..queue.store import QueueStore
from ..spec.brief import load_brief
from ..review import telegram as review_channel
from ..scheduler.tick import TickReport
from . import daemon, notify
from . import setup as setup_module
from . import state
from .api import Client
from .serve import POLL_TIMEOUT_SECONDS, Serve

EXIT_OK = 0
EXIT_ERROR = 3
TOKEN_KEY = setup_module.TOKEN_KEY


def build_parser(parent: argparse._SubParsersAction) -> None:
    telegram = parent.add_parser(
        "telegram",
        help="phone preview and publish confirmation (not a chat entrance)")
    actions = telegram.add_subparsers(dest="telegram_command", required=True)

    setup_parser = actions.add_parser(
        "setup", help="guide BotFather, store the token, learn the chat id from /start")
    setup_parser.add_argument("--profile")
    setup_parser.add_argument("--wait", type=int, default=setup_module.WAIT_SECONDS,
                              help="seconds to wait for your /start, default 60")
    setup_parser.add_argument("--no-wait", action="store_true",
                              help="store the token and stop; run setup again for /start")

    status_parser = actions.add_parser("status", help="what is wired up, no token value")
    status_parser.add_argument("--profile")
    status_parser.add_argument("--json", dest="as_json", action="store_true")

    test_parser = actions.add_parser("test", help="send one line to the saved chat")
    test_parser.add_argument("--profile")
    test_parser.add_argument("--text")
    test_parser.add_argument("--dry-run", action="store_true",
                             help="print the payload, send nothing")

    preview_parser = actions.add_parser(
        "preview", help="send one queue item with the three confirm buttons, or a "
                        "not-yet-queued brief draft with none")
    preview_parser.add_argument("--item", help="queue id; carries the three buttons")
    preview_parser.add_argument("--brief", help="brief id; a draft, no buttons, "
                                               "because there is nothing to approve yet")
    preview_parser.add_argument("--output", type=int, default=0,
                                help="which brief output, default the first")
    preview_parser.add_argument("--profile")
    preview_parser.add_argument("--dry-run", action="store_true",
                                help="print the payloads, send nothing")

    serve_parser = actions.add_parser("serve", help="poll for button presses")
    serve_parser.add_argument("--profile")
    serve_parser.add_argument("--once", action="store_true",
                              help="one poll and exit, for cron and Task Scheduler")
    serve_parser.add_argument("--poll-timeout", dest="poll_timeout", type=int,
                              default=POLL_TIMEOUT_SECONDS)

    for name, helptext in (("install", "register the serve loop with this OS"),
                           ("remove", "unregister it")):
        single = actions.add_parser(name, help=helptext)
        single.add_argument("--runner", choices=["auto", *daemon.RUNNERS],
                            default="auto", help="auto follows the configured os")
        single.add_argument("--dry-run", action="store_true",
                            help="print the plist or command, change nothing")


# ---------- shared helpers ----------

def _profile(config: Config, args: argparse.Namespace) -> str:
    return getattr(args, "profile", None) or config.default_profile


def _token(config: Config, profile: str) -> str:
    return str(load_profile_env(config.home_path, profile).get(TOKEN_KEY, "")).strip()


def _client(config: Config, profile: str) -> Client:
    return Client(_token(config, profile))


def _chat(config: Config) -> str:
    chat = state.chat_id(config.home_path)
    if not chat:
        raise FactoryError("TELEGRAM_CHAT_MISSING", "run factory telegram setup")
    return chat


def _serve(config: Config, profile: str) -> Serve:
    return Serve(home=config.home_path, store=QueueStore(config.home_path),
                 client=_client(config, profile), chat_id=_chat(config),
                 lang=config.ui_lang, simple=config.simple,
                 summary_at=config.telegram.summary_at,
                 log=lambda line: print(line, file=sys.stderr))


# ---------- commands ----------

def run(config: Config, args: argparse.Namespace) -> int:
    handlers = {"setup": _setup, "status": _status, "test": _test,
                "preview": _preview, "serve": _serve_command,
                "install": _install, "remove": _remove}
    handler = handlers.get(args.telegram_command)
    if handler is None:
        raise FactoryError("TELEGRAM_COMMAND_UNKNOWN", str(args.telegram_command)[:32])
    return handler(config, args)


def _setup(config: Config, args: argparse.Namespace) -> int:
    """Three steps, a hidden paste, a getMe, then wait for the owner's /start."""
    translate = i18n.Translator(config.ui_lang)
    profile = _profile(config, args)
    print(translate("telegram.role"))
    print()
    for line in setup_module.guide(config.ui_lang):
        print(line)
    print()
    try:
        pasted = getpass.getpass(translate("telegram.setup.token_prompt"))
    except EOFError:
        raise FactoryError("TELEGRAM_TOKEN_MISSING") from None
    identity = setup_module.verify(pasted)
    warning = setup_module.save_token(config.home_path, profile, pasted)
    if warning:
        print(translate("auth.warning", detail=warning), file=sys.stderr)
    # The username, never the token: that is the whole point of getMe here.
    print(translate("telegram.setup.token_saved", username=identity.username,
                    profile=profile,
                    path=str(credentials_path(config.home_path, profile))))
    if args.no_wait:
        return EXIT_OK
    print(translate("telegram.setup.start_wait", username=identity.username,
                    seconds=args.wait))
    chat = setup_module.wait_for_start(config.home_path, Client(pasted),
                                       seconds=args.wait)
    if not chat:
        print(translate("telegram.setup.chat_timeout"), file=sys.stderr)
        return EXIT_ERROR
    print(translate("telegram.setup.chat_saved", chat_id=chat,
                    path=str(state.chat_path(config.home_path))))
    return EXIT_OK


def _status(config: Config, args: argparse.Namespace) -> int:
    profile = _profile(config, args)
    data = setup_module.status(config.home_path, _token(config, profile))
    data.update({"profile": profile, "review": config.review,
                 "simple": config.simple,
                 "summary_at": config.telegram.summary_at,
                 "daemon": daemon.status(
                     runner=daemon.runner_for(config.os, "auto"))})
    if getattr(args, "as_json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    translate = i18n.Translator(config.ui_lang)
    print(translate("telegram.status.line",
                    token="y" if data["token"] else "n",
                    chat=data["chat_id"] or "-",
                    offset=data["offset"] if data["offset"] is not None else "-",
                    edits=len(data["pending_edits"])))
    print(translate("telegram.role"))
    return EXIT_OK if data["token"] and data["chat_id"] else EXIT_ERROR


def _test(config: Config, args: argparse.Namespace) -> int:
    translate = i18n.Translator(config.ui_lang)
    text = args.text or translate("telegram.test.text")
    if args.dry_run:
        print(json.dumps({"method": "sendMessage",
                          "params": {"chat_id": state.chat_id(config.home_path),
                                     "text": text,
                                     "disable_web_page_preview": True}},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    _client(config, _profile(config, args)).call(
        "sendMessage", chat_id=_chat(config), text=text,
        disable_web_page_preview=True)
    print(translate("telegram.test.sent"))
    return EXIT_OK


def _draft_of(config: Config, args: argparse.Namespace) -> review_channel.Draft:
    """A brief output, shaped for the phone. The platform comes from the brief."""
    from pathlib import Path

    brief = load_brief(config.home_path, args.brief)
    index = max(0, int(getattr(args, "output", 0) or 0))
    if index >= len(brief.outputs):
        raise FactoryError("BRIEF_OUTPUT_MISSING", str(index))
    output = brief.outputs[index]
    media_path = Path(output.path).expanduser() if output.path else None
    return review_channel.Draft(
        ref=brief.id, kind=brief.kind, profile=brief.profile,
        platform=(brief.platforms[0] if brief.platforms else ""),
        text=output.text or "", media=media_path)


def _preview(config: Config, args: argparse.Namespace) -> int:
    if getattr(args, "brief", None):
        return _preview_draft(config, args)
    if not getattr(args, "item", None):
        raise FactoryError("ITEM_REQUIRED", "preview")
    store = QueueStore(config.home_path)
    item = store.get(args.item)
    if args.dry_run:
        built = review_channel.plan(item, chat_id=state.chat_id(config.home_path) or "-",
                                    lang=config.ui_lang)
        print(json.dumps(built.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    result = review_channel.send_preview(
        item, client=_client(config, _profile(config, args)),
        chat_id=_chat(config), lang=config.ui_lang)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def _preview_draft(config: Config, args: argparse.Namespace) -> int:
    draft = _draft_of(config, args)
    chat = state.chat_id(config.home_path) or "-"
    if args.dry_run:
        built = review_channel.build_draft(draft, chat_id=chat, lang=config.ui_lang)
        print(json.dumps(built.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    result = review_channel.send_draft(
        draft, client=_client(config, _profile(config, args)),
        chat_id=_chat(config), lang=config.ui_lang)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def _serve_command(config: Config, args: argparse.Namespace) -> int:
    loop = _serve(config, _profile(config, args))
    actions = loop.run(once=bool(args.once), timeout=args.poll_timeout)
    if args.once:
        for action in actions:
            print(action)
    return EXIT_OK


def _install(config: Config, args: argparse.Namespace) -> int:
    runner = daemon.runner_for(config.os, args.runner)
    if args.dry_run:
        print(json.dumps(daemon.plan(home=config.home_path, runner=runner),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    result = daemon.install(home=config.home_path, runner=runner)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return EXIT_OK if result.get("installed") else EXIT_ERROR


def _remove(config: Config, args: argparse.Namespace) -> int:
    runner = daemon.runner_for(config.os, args.runner)
    if args.dry_run:
        print(json.dumps({"runner": runner, "would_remove": True},
                         ensure_ascii=False, sort_keys=True))
        return EXIT_OK
    print(json.dumps(daemon.remove(runner=runner), ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def notify_tick(config: Config, report: TickReport) -> int:
    """Alerts a tick earned, sent only if Telegram is wired up. Never raises.

    A tick must finish reporting even when the phone is unreachable, so a send
    failure here is a stderr line and nothing more.
    """
    chat = state.chat_id(config.home_path)
    token = _token(config, config.default_profile)
    messages = notify.from_tick(report, lang=config.ui_lang)
    if not messages or not chat or not token:
        return 0
    try:
        client = Client(token)
    except FactoryError:
        return 0
    sent = 0
    for message in messages:
        try:
            client.call("sendMessage", chat_id=chat, text=message.render(),
                        disable_web_page_preview=True)
            sent += 1
        except FactoryError as error:
            print(f"TELEGRAM_ALERT_FAILED {error.code}", file=sys.stderr)
    return sent
