"""factory CLI. State under the runtime home is only ever written through these commands."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from . import i18n, ledger, rules
from .config import Config, init_home, load_config, repo_root
from .credentials import load_profile_env
from .errors import FactoryError
from .gates import forbidden_words, limits, repeat_guard
from .generators import text as text_generator
from .publishers import publisher_for, required_env
from .queue import cli as queue_cli
from .publishers.base import DeliveryTracker, failure_mode
from .queue.store import QueueStore, StoreTracker
from .review import terminal
from .scheduler import cron, launchd, tick, token_refresh
from .spec.brief import (Output, load_brief, new_brief_id, parse_brief, save_brief,
                         utc_now)
from .spec.format import FormatSpec, catalogue, load_format
from .spec.profile import Profile, list_profiles, load_profile
from .spec.queue import QueueItem

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory",
                                     description="content factory: generate, gate, queue, publish")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the runtime home and copy factory.toml")

    profile = commands.add_parser("profile", help="profile add/list/show")
    profile_actions = profile.add_subparsers(dest="profile_command", required=True)
    profile_add = profile_actions.add_parser("add")
    profile_add.add_argument("name")
    profile_add.add_argument("--from", dest="source", default="_example")
    profile_actions.add_parser("list")
    profile_show = profile_actions.add_parser("show")
    profile_show.add_argument("name", nargs="?")

    brief = commands.add_parser("brief", help="brief new/show")
    brief_actions = brief.add_subparsers(dest="brief_command", required=True)
    brief_new = brief_actions.add_parser("new")
    brief_new.add_argument("--kind", required=True, choices=["text", "image", "video"])
    brief_new.add_argument("--format", required=True)
    brief_new.add_argument("--profile")
    brief_new.add_argument("--answers", help="path to a json file, or - for stdin")
    brief_show = brief_actions.add_parser("show")
    brief_show.add_argument("--brief", required=True)

    gen = commands.add_parser("gen", help="gen text/image")
    gen_actions = gen.add_subparsers(dest="gen_command", required=True)
    gen_text = gen_actions.add_parser("text")
    gen_text.add_argument("--brief", required=True)
    gen_text.add_argument("--dry-run", action="store_true")
    gen_image = gen_actions.add_parser("image")
    gen_image.add_argument("--brief", required=True)
    gen_image.add_argument("--dry-run", action="store_true")

    gate = commands.add_parser("gate", help="gate run")
    gate_actions = gate.add_subparsers(dest="gate_command", required=True)
    gate_run = gate_actions.add_parser("run")
    gate_run.add_argument("--brief")
    gate_run.add_argument("--text")
    gate_run.add_argument("--profile")
    gate_run.add_argument("--format")
    gate_run.add_argument("--kind", default="text")
    gate_run.add_argument("--gate", action="append", default=[])

    queue_cli.build_parser(commands)

    publish = commands.add_parser("publish", help="publish one queue item")
    publish.add_argument("platform")
    publish.add_argument("--item", required=True)
    publish.add_argument("--dry-run", action="store_true")

    schedule = commands.add_parser("schedule", help="schedule install/remove/status/tick")
    schedule_actions = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_install = schedule_actions.add_parser("install")
    schedule_install.add_argument("--runner", choices=["launchd", "cron"], default="launchd")
    schedule_install.add_argument("--interval", type=int, default=900)
    schedule_remove = schedule_actions.add_parser("remove")
    schedule_remove.add_argument("--runner", choices=["launchd", "cron"], default="launchd")
    schedule_status = schedule_actions.add_parser("status")
    schedule_status.add_argument("--runner", choices=["launchd", "cron"], default="launchd")
    schedule_status.add_argument("--days", type=int, default=7)
    schedule_tick = schedule_actions.add_parser("tick")
    schedule_tick.add_argument("--dry-run", action="store_true")

    token = commands.add_parser("token", help="token refresh")
    token_actions = token.add_subparsers(dest="token_command", required=True)
    token_refresh_parser = token_actions.add_parser("refresh")
    token_refresh_parser.add_argument("--profile")
    token_refresh_parser.add_argument("--platform", default="threads",
                                      choices=sorted(token_refresh.PLATFORM_TOKENS))
    token_refresh_parser.add_argument("--dry-run", action="store_true")

    ledger_parser = commands.add_parser("ledger", help="ledger summary")
    ledger_actions = ledger_parser.add_subparsers(dest="ledger_command", required=True)
    ledger_summary = ledger_actions.add_parser("summary")
    ledger_summary.add_argument("--days", type=int, default=30)

    i18n_parser = commands.add_parser("i18n", help="i18n get/keys/missing")
    i18n_actions = i18n_parser.add_subparsers(dest="i18n_command", required=True)
    i18n_get = i18n_actions.add_parser("get")
    i18n_get.add_argument("key")
    i18n_get.add_argument("--lang")
    i18n_keys = i18n_actions.add_parser("keys")
    i18n_keys.add_argument("--lang")
    i18n_missing = i18n_actions.add_parser("missing")
    i18n_missing.add_argument("--lang", default="ko")

    formats = commands.add_parser("formats",
                                  help="list the catalogue, or a format's questions")
    formats.add_argument("--kind", choices=["text", "image", "video"])
    formats.add_argument("--format", dest="format_name",
                         help="with --kind, print that format's interview questions")
    formats.add_argument("--lang", choices=["ko", "en"],
                         help="question wording language, defaults to ui_lang")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        config = load_config()
        return dispatch(config, args)
    except FactoryError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


def dispatch(config: Config, args: argparse.Namespace) -> int:
    handlers = {
        "init": _init,
        "profile": _profile,
        "brief": _brief,
        "gen": _gen,
        "gate": _gate,
        "queue": queue_cli.run,
        "publish": _publish,
        "schedule": _schedule,
        "token": _token,
        "ledger": _ledger,
        "i18n": _i18n,
        "formats": _formats,
    }
    handler = handlers.get(args.command)
    if handler is None:
        raise FactoryError("COMMAND_UNKNOWN", str(args.command)[:32])
    return handler(config, args)


def _profile_of(config: Config, name: str | None) -> Profile:
    return load_profile(name or config.default_profile, home=config.home_path,
                        repo=repo_root())


def _init(config: Config, args: argparse.Namespace) -> int:
    home = init_home(config)
    translate = i18n.Translator(config.ui_lang)
    print(f"home: {home}")
    print(f"ui_lang={config.ui_lang} content_lang={config.content_lang} "
          f"default_profile={config.default_profile} review={config.review}")
    print(translate("error.state_handwrite"))
    return EXIT_OK


def _profile(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    if args.profile_command == "list":
        for name in list_profiles(home, repo_root()):
            print(name)
        return EXIT_OK
    if args.profile_command == "show":
        profile = _profile_of(config, args.name)
        print(json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return EXIT_OK
    if args.profile_command == "add":
        source = load_profile(args.source, home=home, repo=repo_root())
        target = home / "profiles" / args.name
        if target.exists():
            raise FactoryError("PROFILE_EXISTS", args.name)
        target.mkdir(parents=True)
        (target / "persona.md").write_text(source.persona + "\n", encoding="utf-8")
        (target / "brand.md").write_text(source.brand + "\n", encoding="utf-8")
        (target / "voice.json").write_text(
            json.dumps(source.voice.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8")
        (target / "soul.json").write_text(
            json.dumps(source.soul.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8")
        (target / "platforms.json").write_text(
            json.dumps({key: value.model_dump(mode="json")
                        for key, value in source.platforms.items()},
                       indent=2, sort_keys=True), encoding="utf-8")
        (target / "forbidden.txt").write_text("\n".join(source.forbidden) + "\n",
                                              encoding="utf-8")
        (target / "locale").write_text(source.locale + "\n", encoding="utf-8")
        credentials = target / "credentials.env"
        credentials.write_text("# add the keys listed in the README, one KEY=value per line\n",
                               encoding="utf-8")
        credentials.chmod(0o600)
        print(str(target))
        return EXIT_OK
    raise FactoryError("PROFILE_COMMAND_UNKNOWN")


def _read_answers(source: str | None) -> dict[str, object]:
    if not source:
        return {}
    raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text(
        encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise FactoryError("ANSWERS_NOT_JSON") from None
    if not isinstance(parsed, dict):
        raise FactoryError("ANSWERS_NOT_JSON")
    return parsed


def _brief(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    if args.brief_command == "show":
        brief = load_brief(home, args.brief)
        print(brief.model_dump_json(indent=2))
        return EXIT_OK
    if args.brief_command == "new":
        payload = _read_answers(args.answers)
        profile_name = args.profile or str(payload.get("profile") or config.default_profile)
        profile = _profile_of(config, profile_name)
        spec = load_format(args.kind, args.format)
        data: dict[str, object] = {
            "id": str(payload.get("id") or new_brief_id(args.format)),
            "kind": args.kind,
            "format": args.format,
            "profile": profile_name,
            "platforms": payload.get("platforms") or spec.platforms
            or profile.enabled_platforms(),
            "content_lang": payload.get("content_lang") or profile.locale
            or config.content_lang,
            "created_at": utc_now(),
        }
        for key in ("references", "count", "ab_hooks", "budget", "answers", "prompt_draft",
                    "prompt_final", "prompt_revisions", "publish", "outputs", "max_chars",
                    "aspect", "variants", "duration_s"):
            if key in payload:
                data[key] = payload[key]
        if spec.credit_cap_per_item and "budget" not in data:
            data["budget"] = {"credits": spec.credit_cap_per_item, "tokens": 0}
        brief = parse_brief(data)
        path = save_brief(home, brief)
        print(json.dumps({"brief": brief.id, "path": str(path),
                          "questions": spec.question_keys()}, ensure_ascii=False))
        return EXIT_OK
    raise FactoryError("BRIEF_COMMAND_UNKNOWN")


IMPLEMENTED_GATES: tuple[str, ...] = ("limits", "forbidden_words", "repeat_guard")
TEXT_DEFAULT_GATES: tuple[str, ...] = ("limits", "forbidden_words", "repeat_guard")


def _gate_plan(spec: FormatSpec | None, kind: str,
               selected: Sequence[str]) -> list[str]:
    """Ordered gate list: explicit selection wins, else the format's own list plus the
    mandatory text trio. repeat_guard is never dropped by a format omitting it."""
    if selected:
        return list(dict.fromkeys(selected))
    planned = list(spec.gates) if spec else []
    if kind == "text":
        planned.extend(TEXT_DEFAULT_GATES)
    elif not planned:
        planned.extend(TEXT_DEFAULT_GATES)
    return list(dict.fromkeys(planned))


def _gate_results(config: Config, *, body: str, profile: Profile,
                  kind: str, format_name: str | None,
                  selected: Sequence[str]) -> list[tuple[str, str, str]]:
    spec = load_format(kind, format_name) if format_name else None
    rules = spec.limits if spec else {}
    results: list[tuple[str, str, str]] = []
    for gate in _gate_plan(spec, kind, selected):
        if gate == "limits":
            report = limits.check(body, rules)
            detail = ",".join(report.violations + report.advisories)
            results.append((gate, terminal.PASS if report.ok else terminal.FAIL, detail))
        elif gate == "forbidden_words":
            forbidden = forbidden_words.check(body, profile.forbidden)
            results.append((gate, terminal.PASS if forbidden.ok else terminal.FAIL,
                            ",".join(forbidden.hits)))
        elif gate == "repeat_guard":
            allowed, reason = repeat_guard.check(body, home=config.home_path,
                                                 profile=profile.name,
                                                 closers=profile.closers)
            results.append((gate, terminal.PASS if allowed else terminal.FAIL, reason))
        else:
            results.append((gate, terminal.SKIP, "NOT_IMPLEMENTED"))
    return results


def _gate(config: Config, args: argparse.Namespace) -> int:
    profile = _profile_of(config, args.profile)
    if args.brief:
        brief = load_brief(config.home_path, args.brief)
        profile = _profile_of(config, brief.profile)
        bodies = [(f"output{index}", output.text or "")
                  for index, output in enumerate(brief.outputs)]
        format_name = brief.format
        kind = brief.kind
    else:
        if not args.text:
            raise FactoryError("TEXT_REQUIRED")
        bodies = [("text", args.text)]
        format_name = args.format
        kind = args.kind
    failed = False
    for label, body in bodies:
        results = _gate_results(config, body=body, profile=profile, kind=kind,
                                format_name=format_name, selected=args.gate)
        print(f"[{label}]")
        print(terminal.gate_table(results, lang=config.ui_lang))
        failed = failed or any(status == terminal.FAIL
                               for _name, status, _detail in results)
    return EXIT_ERROR if failed else EXIT_OK


def _gen(config: Config, args: argparse.Namespace) -> int:
    if args.gen_command == "image":
        raise FactoryError("NOT_IMPLEMENTED", "gen image arrives with the image factory")
    home = config.home_path
    brief = load_brief(home, args.brief)
    profile = _profile_of(config, brief.profile)
    spec = load_format(brief.kind, brief.format)
    rules_prefix = rules.prefix_for_kind(brief.kind)
    if profile.writer_rules.strip():
        rules_prefix = f"{rules_prefix}\n\n## Profile rules\n\n{profile.writer_rules}"
    system_text = text_generator.build_system(
        profile.persona, spec.prompt_template or brief.prompt_final, rules_prefix)
    user_text = text_generator.build_user(
        {"content_lang": brief.content_lang,
         "platforms": brief.platforms,
         "count": brief.count,
         "ab_hooks": brief.ab_hooks,
         "limits": json.dumps(spec.limits, sort_keys=True) if spec.limits else "",
         "references": brief.references,
         "answers": [f"{key}={value}" for key, value in sorted(brief.answers.items())]},
        instruction=brief.prompt_final or brief.prompt_draft)
    env = load_profile_env(home, brief.profile)
    cache_mode = text_generator.cache_state(system_text)
    if args.dry_run:
        generator = text_generator.TextGenerator(env.get("ANTHROPIC_API_KEY") or "dry-run")
        print(json.dumps(generator.dry_run(system_text, user_text), ensure_ascii=False,
                         indent=2, sort_keys=True))
        return EXIT_OK
    generator = text_generator.TextGenerator(env.get("ANTHROPIC_API_KEY", ""),
                                             log=lambda message: print(message,
                                                                       file=sys.stderr))
    outputs: list[dict[str, object]] = []
    for _index in range(brief.count):
        draft = generator.generate(system_text, user_text)
        results = _gate_results(config, body=draft.text, profile=profile, kind=brief.kind,
                                format_name=brief.format, selected=())
        outputs.append({"text": draft.text,
                        "gates": {name: (status if status == terminal.PASS
                                         else f"{status}:{detail}")
                                  for name, status, detail in results},
                        "chosen": False})
        ledger.record(home, track="text", kind=brief.format, model=generator.model,
                      tokens=draft.usage.total_input + draft.usage.output_tokens,
                      profile=brief.profile, ref=brief.id,
                      result="ok" if all(status != terminal.FAIL
                                         for _n, status, _d in results)
                      else "gate_fail",
                      detail={"cache": cache_mode,
                              "cache_read_tokens": draft.usage.cache_read_input_tokens,
                              "prefix_tokens_estimate":
                                  text_generator.estimate_tokens(system_text)})
    brief.outputs = [Output(text=str(entry["text"]), gates=dict(entry["gates"]),
                            chosen=False) for entry in outputs]
    save_brief(home, brief)
    print(terminal.candidates(outputs, lang=config.ui_lang))
    return EXIT_OK


def _publish(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    store = QueueStore(home)
    item = store.get(args.item)
    if item.platform != args.platform:
        raise FactoryError("PLATFORM_MISMATCH", args.platform)
    publisher = publisher_for(args.platform, home)
    if args.dry_run:
        result = publisher.publish(item, dry_run=True)
        print(i18n.get("publish.dry_run.header", config.ui_lang))
        print(result.render())
        return EXIT_OK if result.ok else EXIT_ERROR
    if not item.approved:
        raise FactoryError("NOT_APPROVED", item.id)
    if not item.armed:
        raise FactoryError("NOT_ARMED", item.id)
    store.begin_delivery(item.id)
    result = publisher.publish(item, tracker=StoreTracker(store, item.id, datetime.now(timezone.utc)))
    print(result.render())
    if result.ok:
        store.mark_published(item.id, result.published_id)
    else:
        mode = failure_mode(result.code)
        if mode == "retry":
            store.delivery_failed(item.id, result.code)
        elif mode == "hold":
            store.record_error(item.id, result.code)
        else:
            store.delivery_unknown(item.id, result.code)
    ledger.record(home, track="publish", kind=item.kind, platform=item.platform,
                  profile=item.profile, ref=item.id,
                  result="ok" if result.ok else result.code)
    return EXIT_OK if result.ok else EXIT_ERROR


def _schedule(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    if args.schedule_command == "install":
        if args.runner == "launchd":
            path = launchd.install(home=home, interval=args.interval)
            print(f"installed {path}")
        else:
            ok = cron.install(home=home)
            print("installed crontab block" if ok else "CRONTAB_WRITE_FAILED")
            if not ok:
                return EXIT_ERROR
        return EXIT_OK
    if args.schedule_command == "remove":
        removed = launchd.remove() if args.runner == "launchd" else cron.remove()
        print("removed" if removed else "nothing to remove")
        return EXIT_OK
    if args.schedule_command == "status":
        state = launchd.status() if args.runner == "launchd" else cron.status()
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        print(i18n.get("schedule.next_seven_days", config.ui_lang))
        now = datetime.now(timezone.utc)
        for item in QueueStore(home).upcoming(now=now, days=args.days):
            flags = f"{'A' if item.approved else '-'}{'R' if item.armed else '-'}"
            print(f"  {item.scheduled_at}  {item.platform:<10} {flags}  {item.id}")
        return EXIT_OK
    if args.schedule_command == "tick":
        return _tick(config, dry_run=args.dry_run)
    raise FactoryError("SCHEDULE_COMMAND_UNKNOWN")


def _tick(config: Config, *, dry_run: bool) -> int:
    home = config.home_path
    store = QueueStore(home)
    profiles: dict[str, Profile] = {}

    def profile_for(item: QueueItem) -> Profile:
        if item.profile not in profiles:
            profiles[item.profile] = _profile_of(config, item.profile)
        return profiles[item.profile]

    def slots_for(item: QueueItem) -> tuple[str, ...]:
        return tuple(profile_for(item).platform(item.platform).slots)

    def cap_for(item: QueueItem) -> int:
        return profile_for(item).platform(item.platform).daily_cap

    def guard(body: str, *, profile: str) -> tuple[bool, str]:
        return repeat_guard.check(body, home=home, profile=profile,
                                  closers=profiles[profile].closers
                                  if profile in profiles else ())

    def publish(item: QueueItem, tracker: DeliveryTracker) -> tuple[bool, str, str | None]:
        if dry_run:
            return False, "DRY_RUN", None
        publisher = publisher_for(item.platform, home)
        result = publisher.publish(item, tracker=tracker)
        ledger.record(home, track="publish", kind=item.kind, platform=item.platform,
                      profile=item.profile, ref=item.id,
                      result="ok" if result.ok else result.code)
        return result.ok, result.code, result.published_id

    report = tick.run(store, now=datetime.now(timezone.utc), slots_for=slots_for,
                      cap_for=cap_for, publish=publish, guard=guard)
    for name in sorted({item.profile for item in store.list()}):
        report.warnings.extend(token_refresh.warnings(home, name, lang=config.ui_lang))
    for line in report.as_lines():
        print(line)
    return EXIT_OK if not report.errors else EXIT_ERROR


def _token(config: Config, args: argparse.Namespace) -> int:
    profile = args.profile or config.default_profile
    result = token_refresh.refresh(config.home_path, profile, args.platform,
                                   dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.dry_run:
        ledger.record(config.home_path, track="token", kind=args.platform, profile=profile)
    return EXIT_OK


def _ledger(config: Config, args: argparse.Namespace) -> int:
    print(ledger.render_summary(ledger.summary(config.home_path, days=args.days)))
    return EXIT_OK


def _i18n(config: Config, args: argparse.Namespace) -> int:
    lang = getattr(args, "lang", None) or config.ui_lang
    if args.i18n_command == "get":
        print(i18n.get(args.key, lang))
        return EXIT_OK
    if args.i18n_command == "keys":
        for key in i18n.keys(lang):
            print(key)
        return EXIT_OK
    if args.i18n_command == "missing":
        for key in i18n.missing(args.lang):
            print(key)
        return EXIT_OK
    raise FactoryError("I18N_COMMAND_UNKNOWN")


def _formats(config: Config, args: argparse.Namespace) -> int:
    if args.format_name:
        if not args.kind:
            raise FactoryError("KIND_REQUIRED", "pass --kind with --format")
        spec = load_format(args.kind, args.format_name)
        lang = args.lang or config.ui_lang
        print(json.dumps({"format": f"{spec.kind}/{spec.name}", "lang": lang,
                          "free": spec.is_free, "platforms": spec.platforms,
                          "gates": spec.gates, "limits": spec.limits,
                          "credit_cap_per_item": spec.credit_cap_per_item,
                          "questions": spec.interview(lang)},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    print(json.dumps(catalogue(args.kind), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def required_keys(platform: str) -> tuple[str, ...]:
    return required_env(platform)
