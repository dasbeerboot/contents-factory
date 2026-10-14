"""factory CLI. State under the runtime home is only ever written through these commands."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import webbrowser
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from . import i18n, ledger, rules
from .auth import instagram as instagram_auth
from .auth import threads as threads_auth
from .config import Config, init_home, load_config, repo_root
from .credentials import load_profile_env
from .errors import FactoryError
from .gates import (bg_motion, caption_safe_zone, cta_single, duplicate_copy,
                    forbidden_words, identity, limb_motion, limits,
                    repeat_guard, stt_wpm)
from .generators import budget, fetch
from .generators import image as image_generator
from .generators import text as text_generator
from .generators import video as video_generator
from .publishers import media_host, publisher_for, required_env
from .queue import cli as queue_cli
from .publishers.base import DeliveryTracker, failure_mode
from .queue.store import QueueStore, StoreTracker
from .review import terminal
from .scheduler import cron, launchd, tick, token_refresh
from .spec.brief import (Brief, Output, load_brief, new_brief_id, parse_brief,
                         save_brief, utc_now)
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
    brief_new.add_argument("--force", action="store_true",
                           help="overwrite a brief that already has this id")
    brief_show = brief_actions.add_parser("show")
    brief_show.add_argument("--brief", required=True)

    gen = commands.add_parser("gen", help="gen text/image/video")
    gen_actions = gen.add_subparsers(dest="gen_command", required=True)
    gen_text = gen_actions.add_parser("text")
    gen_text.add_argument("--brief", required=True)
    gen_text.add_argument("--dry-run", action="store_true")
    gen_image = gen_actions.add_parser("image")
    gen_image.add_argument("--brief", required=True)
    gen_image.add_argument("--dry-run", action="store_true")
    gen_video = gen_actions.add_parser("video")
    gen_video.add_argument("--brief", required=True)
    gen_video.add_argument("--stage", default="all",
                           choices=["all", *video_generator.STAGES],
                           help="one stage, or all of them in order")
    gen_video.add_argument("--dry-run", action="store_true")
    gen_video.add_argument("--force", action="store_true",
                           help="re-render a stage that already has its artifact")

    gate = commands.add_parser("gate", help="gate run")
    gate_actions = gate.add_subparsers(dest="gate_command", required=True)
    gate_run = gate_actions.add_parser("run")
    gate_run.add_argument("--brief")
    gate_run.add_argument("--text")
    gate_run.add_argument("--profile")
    gate_run.add_argument("--format")
    gate_run.add_argument("--kind", default="text")
    gate_run.add_argument("--gate", action="append", default=[])
    gate_run.add_argument("--box", action="append", default=[],
                          help="caption box as x,y,width,height[,label]; repeatable")
    gate_run.add_argument("--item",
                          help="queue id being re-checked, excluded from duplicate_copy")
    gate_run.add_argument("--media",
                          help="file for stt_wpm/limb_motion/bg_motion/identity; "
                               "defaults to the brief's rendered output")

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

    media = commands.add_parser("media", help="media host: upload local media, get a URL")
    media_actions = media.add_subparsers(dest="media_command", required=True)
    media_host_parser = media_actions.add_parser(
        "host", help="upload a queue item's media and record the public URL on it")
    media_host_parser.add_argument("--item", required=True)
    media_host_parser.add_argument("--index", type=int, default=0,
                                   help="which media entry, default the first")
    media_host_parser.add_argument("--dry-run", action="store_true",
                                   help="print the request plan without uploading")

    auth = commands.add_parser("auth", help="connect a platform account by OAuth")
    auth_actions = auth.add_subparsers(dest="auth_command", required=True)
    for platform in ("threads", "instagram"):
        single = auth_actions.add_parser(platform)
        single.add_argument("--profile")
        single.add_argument("--app-id", dest="app_id",
                            help="Meta app id; defaults to THREADS_APP_ID / IG_APP_ID")
        single.add_argument("--redirect",
                            help="must match the registered redirect URL exactly")
        single.add_argument("--url-only", action="store_true",
                            help="print the consent URL and stop")
        single.add_argument("--no-open", action="store_true",
                            help="do not open a browser")
        if platform == "threads":
            single.add_argument("--exchange-url",
                                default=threads_auth.DEFAULT_EXCHANGE_URL,
                                help="endpoint holding the app secret, used when "
                                     "THREADS_APP_SECRET is not on this machine")

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
        "media": _media,
        "auth": _auth,
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
        (target / "closers.txt").write_text("\n".join(source.closers) + "\n",
                                            encoding="utf-8")
        (target / "writer_rules.md").write_text(source.writer_rules + "\n",
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
        path = save_brief(home, brief, overwrite=bool(args.force))
        print(json.dumps({"brief": brief.id, "path": str(path),
                          "questions": spec.question_keys()}, ensure_ascii=False))
        return EXIT_OK
    raise FactoryError("BRIEF_COMMAND_UNKNOWN")


IMPLEMENTED_GATES: tuple[str, ...] = ("limits", "forbidden_words", "repeat_guard",
                                      "caption_safe_zone", "cta_single",
                                      "duplicate_copy", "stt_wpm", "limb_motion",
                                      "bg_motion", "identity")
TEXT_DEFAULT_GATES: tuple[str, ...] = ("limits", "forbidden_words", "repeat_guard")
# Gates that measure a rendered file rather than a string. `gate run` reaches
# for the brief's chosen output; without one there is nothing to measure and the
# gate is reported as skipped instead of quietly passing.
MEDIA_GATES: tuple[str, ...] = ("stt_wpm", "limb_motion", "bg_motion", "identity")


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


def _parse_boxes(raw: Sequence[str]) -> list[dict[str, object]]:
    boxes: list[dict[str, object]] = []
    for index, entry in enumerate(raw, 1):
        parts = [piece.strip() for piece in str(entry).split(",")]
        if len(parts) not in (4, 5):
            raise FactoryError("BOX_INVALID", f"box{index}")
        try:
            numbers = [int(piece) for piece in parts[:4]]
        except ValueError:
            raise FactoryError("BOX_INVALID", f"box{index}") from None
        boxes.append({"x": numbers[0], "y": numbers[1], "width": numbers[2],
                      "height": numbers[3],
                      "label": parts[4] if len(parts) == 5 else f"box{index}"})
    return boxes


def _media_gate(gate: str, media_path: Path | None, *, profile: Profile,
                rules: dict[str, object], home: Path, script_text: str,
                env: dict[str, str]) -> tuple[str, str]:
    """Run one file-measuring gate. Returns (status, detail)."""
    if media_path is None or not media_path.is_file():
        return terminal.SKIP, "MEDIA_REQUIRED"
    reports = home / "outputs" / "gates"
    if gate == "stt_wpm":
        report = stt_wpm.check(media_path,
                               transcript=media_path.parent / "gates" / "stt.json",
                               script_text=script_text, limits=rules,
                               api_key=env.get("ELEVENLABS_API_KEY", ""))
    elif gate == "limb_motion":
        report = limb_motion.check(media_path, limits=rules,
                                   sheet=media_path.parent / "gates" / "limb.png")
    elif gate == "bg_motion":
        # The end card is a held still by design. Measuring it as background
        # motion reports the design as a defect.
        held = (float(rules.get("endcard_min_s") or 0.0)
                if media_path.name == "final.mp4" else 0.0)
        report = bg_motion.check(media_path, limits=rules, tail_s=held)
    else:
        report = identity.check_video(media_path,
                                      reference=profile.soul.ref_image,
                                      limits=rules,
                                      montage=media_path.parent / "gates"
                                      / "identity.png")
    reports.mkdir(parents=True, exist_ok=True)
    return ((terminal.PASS if report.ok else terminal.FAIL),
            ",".join(report.violations))


def _gate_results(config: Config, *, body: str, profile: Profile,
                  kind: str, format_name: str | None,
                  selected: Sequence[str],
                  boxes: Sequence[dict[str, object]] = (),
                  exclude_id: str | None = None,
                  media_path: Path | None = None,
                  script_text: str = "") -> list[tuple[str, str, str]]:
    spec = load_format(kind, format_name) if format_name else None
    rules = spec.limits if spec else {}
    env = load_profile_env(config.home_path, profile.name)
    results: list[tuple[str, str, str]] = []
    for gate in _gate_plan(spec, kind, selected):
        if gate in MEDIA_GATES:
            status, detail = _media_gate(gate, media_path, profile=profile,
                                         rules=rules, home=config.home_path,
                                         script_text=script_text, env=env)
            results.append((gate, status, detail))
            continue
        if gate == "limits":
            report = limits.check(body, rules)
            detail = ",".join(report.violations + report.advisories)
            results.append((gate, terminal.PASS if report.ok else terminal.FAIL, detail))
        elif gate == "forbidden_words":
            forbidden = forbidden_words.check(body, profile.forbidden)
            results.append((gate, terminal.PASS if forbidden.ok else terminal.FAIL,
                            ",".join(forbidden.hits)))
        elif gate == "repeat_guard":
            allowed, reason = repeat_guard.check(
                body, home=config.home_path, profile=profile.name,
                closers=profile.closers,
                extra_corpus=config.corpus_paths(profile.name))
            results.append((gate, terminal.PASS if allowed else terminal.FAIL, reason))
        elif gate == "cta_single":
            report = cta_single.check(body, rules)
            results.append((gate, terminal.PASS if report.ok else terminal.FAIL,
                            ",".join(report.violations)))
        elif gate == "duplicate_copy":
            allowed, reason = duplicate_copy.check(body, home=config.home_path,
                                                   profile=profile.name,
                                                   exclude_id=exclude_id, limits=rules)
            results.append((gate, terminal.PASS if allowed else terminal.FAIL, reason))
        elif gate == "caption_safe_zone":
            if not boxes:
                results.append((gate, terminal.SKIP, "BOXES_REQUIRED"))
                continue
            report = caption_safe_zone.check(list(boxes), limits=rules)
            results.append((gate, terminal.PASS if report.ok else terminal.FAIL,
                            ",".join(report.violations)))
        else:
            results.append((gate, terminal.SKIP, "NOT_IMPLEMENTED"))
    return results


def _gate_media_inputs(config: Config, brief: Brief | None,
                       explicit: str | None) -> tuple[Path | None, str,
                                                      list[dict[str, object]]]:
    """Media file, approved script and caption boxes for the media gates.

    When a brief is given these come from what the video pipeline already
    produced, so `factory gate run --brief b_...` needs no extra flags: the
    caption boxes are the ones the renderer drew, not ones typed by hand.
    """
    if explicit:
        path = Path(explicit).expanduser()
        return (path if path.is_file() else None), "", []
    if brief is None:
        return None, "", []
    workspace = video_generator.Workspace.for_brief(config.home_path, brief.id)
    found = next((Path(output.path) for output in brief.outputs
                  if output.path and Path(output.path).is_file()
                  and Path(output.path).suffix.lower() == ".mp4"), None)
    if found is None and workspace.final().is_file():
        found = workspace.final()
    script_text = ""
    record = workspace.record().get("script") or {}
    if isinstance(record, dict):
        script_text = " ".join((record.get("script") or {}).get("lines") or [])
    boxes: list[dict[str, object]] = []
    if workspace.cards_json().is_file():
        drawn = video_generator.captions.load_cards(
            workspace.cards_json()).get("boxes")
        if isinstance(drawn, list):
            boxes = [entry for entry in drawn if isinstance(entry, dict)]
    return found, script_text, boxes


def _gate(config: Config, args: argparse.Namespace) -> int:
    profile = _profile_of(config, args.profile)
    brief: Brief | None = None
    if args.brief:
        brief = load_brief(config.home_path, args.brief)
        profile = _profile_of(config, brief.profile)
        bodies = [(f"output{index}", output.text or "")
                  for index, output in enumerate(brief.outputs)] or [("brief", "")]
        format_name = brief.format
        kind = brief.kind
    else:
        if not args.text and not getattr(args, "media", None):
            raise FactoryError("TEXT_REQUIRED")
        bodies = [("text", args.text or "")]
        format_name = args.format
        kind = args.kind
    media_path, script_text, drawn = _gate_media_inputs(
        config, brief, getattr(args, "media", None))
    boxes = _parse_boxes(getattr(args, "box", []) or []) or drawn
    failed = False
    for label, body in bodies:
        results = _gate_results(config, body=body, profile=profile, kind=kind,
                                format_name=format_name, selected=args.gate,
                                boxes=boxes, exclude_id=getattr(args, "item", None),
                                media_path=media_path, script_text=script_text)
        print(f"[{label}]")
        print(terminal.gate_table(results, lang=config.ui_lang))
        failed = failed or any(status == terminal.FAIL
                               for _name, status, _detail in results)
    return EXIT_ERROR if failed else EXIT_OK


def _gen(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    brief = load_brief(home, args.brief)
    profile = _profile_of(config, brief.profile)
    spec = load_format(brief.kind, brief.format)
    if args.gen_command == "image":
        return _gen_image(config, args, brief, profile, spec)
    if args.gen_command == "video":
        return _gen_video(config, args, brief, profile, spec)
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


def _image_job(brief: Brief, profile: Profile,
               spec: FormatSpec) -> image_generator.ImageJob:
    prompt = (brief.prompt_final or brief.prompt_draft or spec.prompt_template).strip()
    soul_id = profile.soul.soul_id
    references = tuple(brief.references)
    model = image_generator.pick_model(
        has_soul=bool(soul_id), has_reference=bool(references),
        needs_text=spec.name in {"og", "thumbnail", "cardnews"})
    aspect = image_generator.snap_aspect(
        brief.aspect or spec.aspect or "4:5",
        image_generator.supported_aspects(model))
    return image_generator.ImageJob(
        prompt=prompt, model=model,
        aspect_ratio=aspect,
        references=references,
        soul_id=soul_id if model == image_generator.MODEL_SOUL else None,
        count=brief.count,
        credit_cap=spec.credit_cap_per_item)


def _gen_image(config: Config, args: argparse.Namespace, brief: Brief,
               profile: Profile, spec: FormatSpec) -> int:
    """Quote, check the cap, call Higgsfield for real, keep the files."""
    home = config.home_path
    job = _image_job(brief, profile, spec)
    if args.dry_run:
        print(json.dumps(image_generator.plan(job), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return EXIT_OK
    allowance = budget.for_brief(home, ref=brief.id,
                                 format_cap=spec.credit_cap_per_item,
                                 brief_cap=brief.budget.credits, track="image")
    per_run = image_generator.quote(job)
    outputs: list[Output] = []
    directory = home / "outputs" / brief.id
    for index in range(job.count):
        allowance.check(per_run, stage=f"image{index}")
        single = replace(job, count=1)
        payload = image_generator.generate(single)[0]
        urls = image_generator.result_paths(payload)
        saved = [str(fetch.download(url, directory / f"{spec.name}_{index}_{slot}"))
                 for slot, url in enumerate(urls)
                 if str(url).lower().startswith("http")]
        job_id = str(payload.get("id") or "")
        if not job_id:
            # No id means no idempotency key, and an unkeyed charge is how the
            # same job got billed twice. Refuse rather than guess.
            raise FactoryError("PROVIDER_JOB_ID_MISSING", job.model[:32])
        ledger.charge(home, op=f"higgsfield:{job_id}", track="image",
                      kind=brief.format, model=job.model, credits=per_run,
                      profile=brief.profile, ref=brief.id,
                      result="ok" if saved else "no_output",
                      detail={"job_id": job_id, "files": saved,
                              "quoted_credits": per_run})
        outputs.extend(Output(path=path, gates={}, chosen=False) for path in saved)
    if not outputs:
        raise FactoryError("NO_IMAGE_OUTPUT", brief.id)
    brief.outputs = outputs
    save_brief(home, brief)
    print(f"credits: {round(allowance.spent(), 3)} spent of cap "
          f"{allowance.cap:g} ({per_run:g} per image)")
    print(terminal.candidates([entry.model_dump(mode="json") for entry in outputs],
                              lang=config.ui_lang))
    return EXIT_OK


def _gen_video(config: Config, args: argparse.Namespace, brief: Brief,
               profile: Profile, spec: FormatSpec) -> int:
    """Run the video stages. The pipeline owns budget, caching and the ledger."""
    home = config.home_path
    report = video_generator.run(home=home, brief=brief, profile=profile,
                                 spec=spec, stages=args.stage,
                                 dry_run=args.dry_run, force=args.force,
                                 log=lambda line: print(line, file=sys.stderr))
    if not args.dry_run:
        outputs = video_generator.outputs_for(
            video_generator.Workspace.for_brief(home, brief.id))
        if outputs:
            brief.outputs = outputs
            save_brief(home, brief)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_ERROR if report.get("gate_failures") else EXIT_OK


def _publish(config: Config, args: argparse.Namespace) -> int:
    home = config.home_path
    store = QueueStore(home)
    item = store.get(args.item)
    if item.platform != args.platform:
        raise FactoryError("PLATFORM_MISMATCH", args.platform)
    publisher = publisher_for(args.platform, home,
                              settings=_profile_of(config, item.profile)
                              .platform(args.platform))
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


def _media(config: Config, args: argparse.Namespace) -> int:
    """Upload one queue item's media so the platform has a URL it can fetch.

    Threads, Instagram and the TikTok import route all pull media server side, so a local
    path cannot be posted. This is the only writer of `media[].public_url`.
    """
    if args.media_command != "host":
        raise FactoryError("MEDIA_COMMAND_UNKNOWN", str(args.media_command)[:32])
    home = config.home_path
    store = QueueStore(home)
    item = store.get(args.item)
    if not 0 <= args.index < len(item.media):
        raise FactoryError("MEDIA_INDEX_INVALID", str(args.index))
    entry = item.media[args.index]
    env = load_profile_env(home, item.profile)
    if args.dry_run:
        plan = media_host.plan(entry, profile=item.profile, item_id=item.id, env=env)
        print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    public_url = media_host.upload(entry, profile=item.profile, item_id=item.id, env=env)
    updated = store.set_media_url(item.id, args.index, public_url)
    print(i18n.get("media.hosted", config.ui_lang, item_id=updated.id,
                   index=args.index, url=public_url))
    return EXIT_OK


def _announce_consent(url: str, *, translate: i18n.Translator, open_browser: bool) -> None:
    print(translate("auth.consent.header"))
    print()
    print(url)
    print()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - a headless box still gets the printed URL
            pass


def _auth(config: Config, args: argparse.Namespace) -> int:
    """OAuth for one profile. The pasted code and the token stay off every output."""
    home = config.home_path
    translate = i18n.Translator(config.ui_lang)
    profile = args.profile or config.default_profile

    if args.auth_command == "instagram":
        url, steps = instagram_auth.plan(home, profile, app_id=args.app_id,
                                         redirect_uri=args.redirect)
        _announce_consent(url, translate=translate,
                          open_browser=not args.no_open and not args.url_only)
        print(translate("auth.instagram.manual"))
        for step in steps:
            print(f"  {step}")
        return EXIT_OK

    if args.auth_command != "threads":
        raise FactoryError("AUTH_COMMAND_UNKNOWN", str(args.auth_command)[:32])

    env = load_profile_env(home, profile)
    app_id = threads_auth.resolve_app_id(env, args.app_id)
    redirect_uri = threads_auth.resolve_redirect(env, args.redirect)
    url = threads_auth.authorize_url(app_id=app_id, redirect_uri=redirect_uri)
    _announce_consent(url, translate=translate,
                      open_browser=not args.no_open and not args.url_only)
    print(translate("auth.redirect.note", redirect=redirect_uri))
    if args.url_only:
        return EXIT_OK
    print(translate("auth.paste.warning"))
    try:
        pasted = getpass.getpass(translate("auth.paste.prompt"))
    except EOFError:
        raise FactoryError("CODE_MISSING") from None
    result = threads_auth.connect(home, profile, pasted=pasted, app_id=app_id,
                                  redirect_uri=redirect_uri,
                                  exchange_url=args.exchange_url)
    for warning in result.warnings:
        print(translate("auth.warning", detail=warning))
    print(translate("auth.saved", platform=result.platform, profile=result.profile,
                    username=result.username or "?", route=result.route,
                    keys=",".join(result.written_keys)))
    return EXIT_OK


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
                                  if profile in profiles else (),
                                  extra_corpus=config.corpus_paths(profile))

    def publish(item: QueueItem, tracker: DeliveryTracker) -> tuple[bool, str, str | None]:
        if dry_run:
            return False, "DRY_RUN", None
        publisher = publisher_for(item.platform, home,
                                  settings=profile_for(item).platform(item.platform))
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
