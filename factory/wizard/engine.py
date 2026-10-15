"""The state machine behind `factory wizard`. One question out, one answer in.

The agent in front of this holds no logic: it prints what `next` returns, hands back
what the user said, and repeats. Everything that could go wrong if a model improvised
lives here instead — the order, the validation, the format choice, the gate retries and
the approval rules.

`next` is allowed to do work before it can ask (the draft question has to have a draft
to show), and that work is recorded in the flow's notes so asking twice never generates
twice.
"""
from __future__ import annotations

from typing import Any

from .. import i18n, osinfo
from ..config import Config
from ..credentials import load_profile_env
from ..errors import FactoryError
from . import apply, classify, questions, state
from .questions import Question

DEFAULTS: dict[str, str] = {
    "brand_tone": "warm", "platforms": "threads", "slots": "evening",
    "first_post": "yes", "what": "text", "cost_ok": "yes", "draft": "ok",
    "when": "next", "attach": "",
}
MAX_AUTO_REGEN = 2
DRY_RUN_ENV = "FACTORY_WIZARD_DRY_RUN"


def default_for(config: Config, question: Question) -> str:
    if question.id == "os":
        return osinfo.detect_os()
    if question.id == "ui_lang":
        return config.ui_lang
    if question.id == "content_lang":
        return config.content_lang
    return DEFAULTS.get(question.id, "")


def flow_of(wizard: dict[str, Any]) -> str:
    return state.check_flow(str(wizard.get("flow") or "setup"))


def current(flow: str, record: dict[str, Any]) -> Question | None:
    answers = record["answers"]
    for question in questions.plan_for(flow, answers):
        if question.id in answers or question.id in record["skipped"]:
            continue
        return question
    return None


def _label(question: Question, value: str, lang: str) -> str:
    base = f"wizard.choice.{question.id}.{value}"
    if question.kind == questions.CONFIRM and question.id.startswith("connect."):
        base = f"wizard.choice.connect.{value}"
    text = i18n.get(base, lang)
    return value if text == base else text


def profile_name(config: Config, record: dict[str, Any]) -> str:
    return str(record["notes"].get("profile")
               or record["answers"].get("profile")
               or config.default_profile)


def prompt_payload(config: Config, flow: str, record: dict[str, Any],
                   question: Question) -> dict[str, Any]:
    lang = config.ui_lang
    profile = profile_name(config, record)
    payload: dict[str, Any] = {
        "flow": flow,
        "id": question.id,
        "step": question.step,
        "step_total": questions.SETUP_STEPS if flow == "setup" else questions.MAKE_STEPS,
        "question": i18n.get(f"wizard.q.{question.id}", lang),
        "help": i18n.get(f"wizard.help.{question.id}", lang),
        "input": i18n.get(f"wizard.input.{question.kind}", lang),
        "kind": question.kind,
        "optional": question.optional,
        "default": default_for(config, question),
        "choices": [{"value": value, "label": _label(question, value, lang)}
                    for value in question.choices],
        "answer_with": f'factory wizard answer --id {question.id} --value "<답>"',
    }
    if question.target:
        extras = dict(question.extras)
        payload["action"] = extras.get("action", "").format(profile=profile)
        payload["verify"] = extras.get("verify", "").format(profile=profile)
        payload["link"] = extras.get("link", "")
        payload["keys"] = [key for key in extras.get("keys", "").split(",") if key]
    # The draft and its gate table belong to the draft question only. Carrying them onto
    # the next question shows the user the same draft twice and reads as a second ask.
    shown = {"draft": ("draft", "gates", "note"), "cost_ok": ("cost",)}
    for key in shown.get(question.id, ()):
        if record["notes"].get(key):
            payload[key] = record["notes"][key]
    return payload


# --- next ----------------------------------------------------------------------

def next_question(config: Config, *, dry_run: bool = False) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    flow = flow_of(wizard)
    record = state.record(wizard, flow)
    question = current(flow, record)
    if question is None:
        record["done"] = True
        state.save(config.home_path, wizard)
        return {"flow": flow, "done": True,
                "message": i18n.get(f"wizard.done.{flow}", config.ui_lang)}
    prepared = _prepare(config, flow, record, question, dry_run=dry_run)
    state.save(config.home_path, wizard)
    payload = prompt_payload(config, flow, record, question)
    payload["ran"] = prepared.commands()
    return payload


def _prepare(config: Config, flow: str, record: dict[str, Any], question: Question, *,
             dry_run: bool) -> apply.Result:
    """Work that has to happen before a question can be asked."""
    result = apply.Result()
    if question.id == "cost_ok":
        _cost_note(config, record)
    if question.id == "draft" and not record["notes"].get("draft"):
        _build_draft(config, record, result=result, dry_run=dry_run)
    return result


def _target(config: Config, record: dict[str, Any]) -> tuple[str, str, str, str]:
    """(profile, kind, platform, format) for the current make flow."""
    answers = record["answers"]
    profile = profile_name(config, record)
    kind = str(answers.get("kind") or answers.get("what") or "text")
    if kind not in {"text", "image", "video"}:
        kind = "text"
    enabled = apply.enabled_platforms(config, profile)
    platform = apply.pick_platform(kind, enabled)
    format_name = apply.pick_format(kind, platform)
    return profile, kind, platform, format_name


def _cost_note(config: Config, record: dict[str, Any]) -> None:
    _profile, kind, _platform, format_name = _target(config, record)
    credits = apply.credit_note(kind, format_name)
    record["notes"]["cost"] = i18n.get("wizard.cost.note", config.ui_lang,
                                       credits=credits,
                                       minutes=apply.RENDER_MINUTES)


def _build_draft(config: Config, record: dict[str, Any], *, result: apply.Result,
                 dry_run: bool) -> None:
    lang = config.ui_lang
    profile, kind, platform, format_name = _target(config, record)
    answers = record["answers"]
    topic = str(answers.get("topic") or "")
    references = [piece.strip() for piece in str(answers.get("attach") or "").split(",")
                  if piece.strip()]
    brief_id = str(record["notes"].get("brief") or "")
    if not brief_id:
        brief_id = apply.make_brief(config, kind=kind, format_name=format_name,
                                    profile=profile, platform=platform, topic=topic,
                                    references=references, result=result)
    record["notes"].update({"brief": brief_id, "platform": platform,
                            "format": format_name, "kind": kind, "profile": profile})
    if dry_run:
        apply.generate(config, kind=kind, brief_id=brief_id, dry_run=True, result=result)
        draft = i18n.get("wizard.draft.dry_run", lang, topic=topic)
        gates = apply.gate(config, text=draft, profile=profile,
                           format_name=format_name, kind="text", result=result)
        if not gates.ok:
            # The placeholder is not the real copy, but a failing gate still has to be
            # said out loud: silence here is how a FAIL becomes a surprise at publish.
            record["notes"]["note"] = i18n.get("wizard.draft.gates_failed", lang,
                                               detail=gates.first_failure())
    else:
        draft = apply.generate(config, kind=kind, brief_id=brief_id, dry_run=False,
                               result=result)
        gates = apply.gate(config, brief_id=brief_id, result=result)
        regens = 0
        while not gates.ok and config.simple and regens < MAX_AUTO_REGEN:
            regens += 1
            draft = apply.generate(config, kind=kind, brief_id=brief_id, dry_run=False,
                                   result=result)
            gates = apply.gate(config, brief_id=brief_id, result=result)
        record["notes"]["regens"] = str(regens)
        if regens and gates.ok:
            record["notes"]["note"] = i18n.get("wizard.draft.gates_fixed", lang,
                                               count=regens)
        elif not gates.ok:
            record["notes"]["note"] = i18n.get("wizard.draft.gates_failed", lang,
                                               detail=gates.first_failure())
    record["notes"]["draft"] = draft
    record["notes"]["gates"] = gates.table.strip()
    record["notes"]["gate_ok"] = "yes" if gates.ok else "no"


# --- answer --------------------------------------------------------------------

def answer_question(config: Config, question_id: str, raw: str, *,
                    dry_run: bool = False) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    flow = flow_of(wizard)
    record = state.record(wizard, flow)
    question = current(flow, record)
    if question is None:
        raise FactoryError("WIZARD_NO_QUESTION", flow)
    if question_id != question.id:
        raise FactoryError("WIZARD_OUT_OF_ORDER", question_id[:40])
    verdict = questions.validate(question, raw)
    if not verdict.ok:
        verdict = _classify(config, record, question, raw, verdict)
    if not verdict.ok:
        return {"flow": flow, "id": question.id, "ok": False,
                "reask": _reask(config, question, verdict.reason),
                "question": i18n.get(f"wizard.q.{question.id}", config.ui_lang),
                "choices": [{"value": value, "label": _label(question, value,
                                                             config.ui_lang)}
                            for value in question.choices]}
    record["answers"][question.id] = verdict.value
    record["asked"].append(question.id)
    result = _effect(config, flow, record, question, verdict.value, dry_run=dry_run)
    if flow == "setup" and question.id == "first_post":
        if verdict.value == "yes":
            wizard["flow"] = "make"
            state.reset(wizard, "make")
        record["done"] = True
    state.save(config.home_path, wizard)
    return {"flow": flow, "id": question.id, "ok": True, "value": verdict.value,
            "saved": i18n.get("wizard.saved", config.ui_lang, id=question.id),
            "ran": result.commands(), "notes": result.notes,
            "message": result.notes.get("message", "")}


def _classify(config: Config, record: dict[str, Any], question: Question, raw: str,
              verdict: questions.Verdict) -> questions.Verdict:
    """Free-form answers get one Haiku call, when there is a key for it."""
    if not question.choices or question.kind in {questions.TEXT, questions.DRAFT}:
        return verdict
    env = load_profile_env(config.home_path, profile_name(config, record))
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return verdict
    try:
        picked = classify.classify(
            raw, question.choices, api_key=api_key,
            question=i18n.get(f"wizard.q.{question.id}", config.ui_lang))
    except FactoryError:
        return verdict
    return questions.validate(question, picked) if picked else verdict


def _reask(config: Config, question: Question, reason: str) -> str:
    lang = config.ui_lang
    for key in (f"wizard.reask.{question.id}", f"wizard.reask.{reason}",
                "wizard.reask.default"):
        text = i18n.get(key, lang)
        if text != key:
            return text
    return i18n.get("wizard.reask.default", lang)


def _effect(config: Config, flow: str, record: dict[str, Any], question: Question,
            value: str, *, dry_run: bool) -> apply.Result:
    """Side effects of one stored answer, all through ordinary factory commands."""
    result = apply.Result()
    answers = record["answers"]
    notes = record["notes"]
    if question.id == "os":
        apply.record_os(config, value, result)
    elif question.id == "ui_lang":
        apply.record_config(config, {"ui_lang": value}, result)
    elif question.id == "content_lang":
        apply.record_config(config, {"content_lang": value}, result)
    elif question.id == "brand_tone":
        profile = apply.create_profile(config, name=answers.get("brand_name", ""),
                                       intro=answers.get("brand_intro", ""),
                                       tone=value, result=result)
        apply.record_config(config, {"default_profile": profile}, result)
        notes["profile"] = profile
    elif question.id in {"slots", "slots_custom"}:
        if question.id == "slots" and value == "custom":
            return result
        slots = apply.slots_for_answer(answers.get("slots", value),
                                       answers.get("slots_custom", ""))
        platforms = [name for name in str(answers.get("platforms") or "").split(",")
                     if name]
        if platforms and slots:
            apply.enable_platforms(config, profile=profile_name(config, record),
                                   platforms=platforms, slots=slots, result=result)
    elif question.id == "what":
        if value != "talk":
            answers["kind"] = value
    elif question.id == "topic":
        if answers.get("what") == "talk" and "kind" not in answers:
            answers["kind"] = _guess_kind(config, record, value)
    elif question.id == "cost_ok" and value == "no":
        record["done"] = True
        result.notes["message"] = i18n.get("wizard.cost.declined", config.ui_lang)
    elif question.id == "draft":
        _apply_draft_answer(config, record, value, result=result)
    elif question.id == "when":
        _queue(config, record, value, result=result, dry_run=dry_run)
    return result


def _guess_kind(config: Config, record: dict[str, Any], topic: str) -> str:
    env = load_profile_env(config.home_path, profile_name(config, record))
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "text"
    try:
        picked = classify.classify(topic, ("text", "image", "video"), api_key=api_key,
                                   question=i18n.get("wizard.q.what", config.ui_lang))
    except FactoryError:
        return "text"
    return picked or "text"


def _apply_draft_answer(config: Config, record: dict[str, Any], value: str, *,
                        result: apply.Result) -> None:
    notes = record["notes"]
    if value == "ok":
        return
    brief_id = str(notes.get("brief") or "")
    if value.startswith("fix:") and brief_id:
        apply.revise_brief(config, brief_id=brief_id,
                           correction=value.split(":", 1)[1], result=result)
        result.notes["message"] = i18n.get("wizard.draft.revised", config.ui_lang)
    else:
        result.notes["message"] = i18n.get("wizard.draft.again", config.ui_lang)
    # Both "again" and "고쳐줘" drop the shown draft and re-ask the same question, so
    # the next `next` regenerates from the brief that is now on disk.
    for key in ("draft", "gates", "gate_ok", "note"):
        notes.pop(key, None)
    record["answers"].pop("draft", None)
    if record["asked"] and record["asked"][-1] == "draft":
        record["asked"].pop()
    notes["attempt"] = str(int(notes.get("attempt") or 0) + 1)


def _queue(config: Config, record: dict[str, Any], value: str, *, result: apply.Result,
           dry_run: bool) -> None:
    notes = record["notes"]
    answers = record["answers"]
    profile = str(notes.get("profile") or profile_name(config, record))
    kind = str(notes.get("kind") or answers.get("kind") or "text")
    platform = str(notes.get("platform") or "")
    draft = str(notes.get("draft") or "")
    if not platform:
        profile, kind, platform, _format = _target(config, record)
    slot = apply.slot_value(value)
    item = apply.queue_add(config, profile=profile, platform=platform, kind=kind,
                           brief_id="" if dry_run else str(notes.get("brief") or ""),
                           text=draft if dry_run else "", slot=slot, result=result)
    notes["item"] = item
    reason = str(answers.get("topic") or "").strip()
    if config.simple and reason:
        apply.approve_and_arm(config, item_id=item, reason=reason, result=result)
        result.notes["message"] = i18n.get("wizard.queued.simple", config.ui_lang,
                                           item_id=item, platform=platform)
    else:
        result.notes["message"] = i18n.get("wizard.queued.expert", config.ui_lang,
                                           item_id=item, platform=platform)
    record["done"] = True


# --- skip / back / status / reset -----------------------------------------------

def skip(config: Config) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    flow = flow_of(wizard)
    record = state.record(wizard, flow)
    question = current(flow, record)
    if question is None:
        raise FactoryError("WIZARD_NO_QUESTION", flow)
    if not question.optional:
        raise FactoryError("WIZARD_SKIP_REFUSED", question.id[:40])
    record["skipped"].append(question.id)
    record["asked"].append(question.id)
    state.save(config.home_path, wizard)
    return {"flow": flow, "id": question.id, "skipped": True,
            "message": i18n.get("wizard.skipped", config.ui_lang, id=question.id)}


def back(config: Config) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    flow = flow_of(wizard)
    record = state.record(wizard, flow)
    if not record["asked"]:
        raise FactoryError("WIZARD_BACK_EMPTY", flow)
    question_id = record["asked"].pop()
    record["answers"].pop(question_id, None)
    record["done"] = False
    while question_id in record["skipped"]:
        record["skipped"].remove(question_id)
    state.save(config.home_path, wizard)
    return {"flow": flow, "id": question_id, "back": True,
            "message": i18n.get("wizard.back", config.ui_lang, id=question_id)}


def status(config: Config) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    flow = flow_of(wizard)
    out: dict[str, Any] = {"flow": flow, "simple": config.simple,
                           "state_file": str(state.state_path(config.home_path)),
                           "flows": {}}
    for name in state.FLOWS:
        record = state.record(wizard, name)
        plan = questions.plan_for(name, record["answers"])
        question = current(name, record)
        out["flows"][name] = {
            "done": bool(record["done"]) or question is None,
            "answered": len(record["answers"]),
            "remaining": sum(1 for entry in plan
                             if entry.id not in record["answers"]
                             and entry.id not in record["skipped"]),
            "next": question.id if question else "",
            "skipped": list(record["skipped"]),
            "notes": {key: value for key, value in record["notes"].items()
                      if key in {"profile", "brief", "item", "platform", "format",
                                 "kind", "slots", "regens"}},
        }
    return out


def reset(config: Config, flow: str | None = None) -> dict[str, Any]:
    wizard = state.load(config.home_path)
    if flow is None:
        wizard = state.blank()
    else:
        state.reset(wizard, flow)
        wizard["flow"] = flow
    state.save(config.home_path, wizard)
    return {"flow": flow or "setup", "reset": True,
            "message": i18n.get("wizard.reset", config.ui_lang, flow=flow or "all")}
