"""Video pipeline: script -> still -> clip -> tts -> captions -> assemble.

Each stage is a separate command so an expensive run can be inspected between
steps, and so a failure costs one stage rather than the whole reel. Artifacts
pass through `workspace.Workspace`, and `stages.json` says what exists.

Two rules are enforced here rather than left to the operator:

* **The per-item cap.** Before every paid call the provider is asked what it will
  cost and the answer is checked against `credit_cap_per_item` minus what the
  ledger already charged this brief. Over cap is `BUDGET_EXCEEDED` and no call
  is made — a started-then-cancelled job still bills.
* **No rerolls.** A stage that already produced its artifact is skipped, not
  repeated. Re-rendering is a decision with a price, so it needs `--force` and
  shows up in the ledger as a separate charge.

Persona-specific values (voice id, Soul id, reference image, end card wording)
are read from the profile directory. There are no proper nouns in this package.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ... import ledger, media
from ...credentials import load_profile_env
from ...errors import FactoryError
from ...spec.brief import Brief, Output
from ...spec.format import FormatSpec
from ...spec.profile import Profile
from .. import budget as budget_module
from . import assemble, captions, clip, script, still, tts, values
from .workspace import STAGES, Workspace, resolve_stages

__all__ = ["STAGES", "Workspace", "StageResult", "run", "resolve_stages",
           "assemble", "captions", "clip", "script", "still", "tts", "values"]

TRACK = "video"
DEFAULT_DURATION_S = 24.0
SPOKEN_KEYS: tuple[str, ...] = ("hook_line", "body_lines", "host_lines",
                                "host_line_1", "host_line_2", "host_line_3",
                                "narration", "lines")
TTS_CHAR_CAP = 2000
Logger = Callable[[str], None]


@dataclass
class StageResult:
    stage: str
    status: str = "ok"
    credits: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "status": self.status,
                "credits": round(self.credits, 4), **self.detail}


@dataclass
class Context:
    home: Path
    brief: Brief
    profile: Profile
    spec: FormatSpec
    workspace: Workspace
    allowance: budget_module.Budget
    env: dict[str, str]
    dry_run: bool = False
    force: bool = False
    log: Logger = print

    @property
    def limits(self) -> dict[str, Any]:
        return self.spec.limits or {}

    @property
    def duration_s(self) -> float:
        band = self.spec.duration_s or []
        if isinstance(self.brief, object) and getattr(self.brief, "duration_s", None):
            low, high = self.brief.duration_s  # type: ignore[misc]
            return round((float(low) + float(high)) / 2.0, 2)
        if len(band) == 2:
            return round((float(band[0]) + float(band[1])) / 2.0, 2)
        return DEFAULT_DURATION_S

    @property
    def duration_band(self) -> tuple[float, float] | None:
        band = self.spec.duration_s or []
        return (float(band[0]), float(band[1])) if len(band) == 2 else None

    def values(self) -> dict[str, Any]:
        return values.resolve(
            template=self.spec.prompt_template, answers=dict(self.brief.answers),
            profile_name=self.profile.name, content_lang=self.brief.content_lang,
            duration_s=self.duration_s, aspect=self.spec.aspect,
            limits=self.limits,
            profile_fallbacks=values.from_brand(self.profile.brand))

    def record(self, result: StageResult) -> None:
        ledger.record(self.home, track=TRACK, kind=self.brief.format,
                      model=str(result.detail.get("model") or ""),
                      credits=result.credits, profile=self.brief.profile,
                      ref=self.brief.id, result=result.status,
                      detail={"stage": result.stage, **result.detail})
        self.workspace.note(result.stage, result.as_dict())


def _frame(limits: dict[str, Any]) -> tuple[int, int]:
    return (int(limits.get("width_px") or assemble.WIDTH),
            int(limits.get("height_px") or assemble.HEIGHT))


def _hold(limits: dict[str, Any]) -> float:
    return float(limits.get("endcard_min_s") or assemble.HOLD_S)


def _stage_script(context: Context) -> StageResult:
    resolved = context.values()
    template = context.spec.prompt_template
    if not template.strip():
        raise FactoryError("PROMPT_TEMPLATE_MISSING", context.spec.name)
    drafted = script.render(template, resolved)
    prompt = (context.brief.prompt_final or "").strip() or drafted
    report = script.check(template=template, prompt=prompt, values=resolved,
                          duration_s=context.duration_s, limits=context.limits,
                          spoken_keys=SPOKEN_KEYS)
    if context.dry_run:
        return StageResult(stage="script", status="dry_run",
                           detail={"prompt_chars": len(prompt),
                                   "script": report.as_dict()})
    if not report.ok:
        context.workspace.note("script", {"status": "gate_fail",
                                          "script": report.as_dict()})
        raise FactoryError("SCRIPT_GATE_FAILED", ",".join(report.violations)[:120])
    context.workspace.clip_prompt().parent.mkdir(parents=True, exist_ok=True)
    context.workspace.clip_prompt().write_text(prompt + "\n", encoding="utf-8")
    still_template = context.spec.still_prompt_template
    if still_template.strip():
        context.workspace.still_prompt().write_text(
            script.render(still_template, resolved) + "\n", encoding="utf-8")
    return StageResult(stage="script", detail={"prompt": str(context.workspace
                                                             .clip_prompt()),
                                               "still_prompt": str(
                                                   context.workspace.still_prompt())
                                               if still_template.strip() else "",
                                               "script": report.as_dict()})


def _prompt_for(context: Context, path: Path, template: str) -> str:
    """The prompt the script stage wrote, or a fresh render of the template.

    A dry run has to work before anything has been written, so it falls back to
    rendering. A real run reads the file, because that is the text the script
    gate actually passed.
    """
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    if not template.strip():
        raise FactoryError("PROMPT_TEMPLATE_MISSING", context.spec.name)
    if not context.dry_run:
        raise FactoryError("STAGE_INPUT_MISSING", f"run_script_first:{path.name}")
    return script.render(template, context.values()).strip()


def _stage_still(context: Context) -> StageResult:
    destination = context.workspace.still_image()
    prompt = _prompt_for(context, context.workspace.still_prompt(),
                         context.spec.still_prompt_template)
    job = still.build_job(prompt=prompt, soul_id=context.profile.soul.soul_id,
                          reference=context.profile.soul.ref_image,
                          aspect=context.spec.aspect or still.STILL_ASPECT,
                          credit_cap=context.spec.credit_cap_per_item)
    if context.dry_run:
        from .. import image as image_generator
        return StageResult(stage="still", status="dry_run",
                           detail={"model": job.model,
                                   "argv": image_generator.build_command(job)})
    if destination.is_file() and not context.force:
        return StageResult(stage="still", status="cached",
                           detail={"model": job.model, "path": str(destination)})
    quoted = still.quote(job)
    context.allowance.check(quoted, stage="still")
    detail = still.generate(job, destination)
    return StageResult(stage="still", credits=quoted, detail=detail)


def _take_prompts(context: Context, lengths: list[int]) -> list[str]:
    """One on-template prompt per take, rebased to that take's window."""
    resolved = context.values()
    lines = script.spoken_lines(resolved, SPOKEN_KEYS)
    per_take = values.for_takes(resolved, template=context.spec.prompt_template,
                                lengths=lengths, lines=lines,
                                limits=context.limits)
    return [script.render(context.spec.prompt_template, entry)
            for entry in per_take]


def _stage_clip(context: Context) -> StageResult:
    workspace = context.workspace
    prompt = _prompt_for(context, workspace.clip_prompt(),
                         context.spec.prompt_template)
    start = (workspace.still_image() if context.dry_run
             else workspace.require(workspace.still_image(), stage="clip"))
    lengths = clip.split_duration(context.duration_s)
    plan = clip.plan(prompt=prompt, duration_s=context.duration_s, still=start,
                     resolution=str(context.limits.get("clip_resolution")
                                    or clip.RESOLUTION),
                     aspect=context.spec.aspect or clip.ASPECT,
                     prompts=_take_prompts(context, lengths)
                     if len(lengths) > 1 else None)
    if context.dry_run:
        quotes = [clip.quote(take, plan_=plan) for take in plan.takes]
        return StageResult(stage="clip", status="dry_run",
                           detail={"model": plan.model, "plan": plan.as_dict(),
                                   "quoted_credits": sum(quotes),
                                   "per_take": quotes})
    if workspace.clip_video().is_file() and not context.force:
        return StageResult(stage="clip", status="cached",
                           detail={"model": plan.model,
                                   "path": str(workspace.clip_video())})
    directory = workspace.clip_dir()
    directory.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    results: list[dict[str, Any]] = []
    spent = 0.0
    for take in plan.takes:
        quoted = clip.quote(take, plan_=plan)
        context.allowance.check(quoted, stage=take.name)
        part = directory / f"{take.name}.mp4"
        detail = clip.render_take(take, part, plan_=plan)
        ledger.record(context.home, track=TRACK, kind=context.brief.format,
                      model=plan.model, credits=quoted,
                      profile=context.brief.profile, ref=context.brief.id,
                      detail={"stage": "clip", **detail})
        spent += quoted
        parts.append(Path(detail["path"]))
        results.append(detail)
        context.log(f"  {take.name}: {take.duration_s}s, {quoted:g} cr")
        if take.index < len(plan.takes):
            # Multi-take continuity: the next take starts on this take's last
            # frame, not on the still, or the background rewinds at the seam.
            seam = directory / f"seam_{take.index}.png"
            plan.takes[take.index] = clip.Take(
                index=plan.takes[take.index].index,
                duration_s=plan.takes[take.index].duration_s,
                start_image=clip.seam_frame(Path(detail["path"]), seam),
                prompt=plan.takes[take.index].prompt)
    clip.concat(parts, workspace.clip_video())
    clip.write_plan(directory / "clip.json", plan, results)
    return StageResult(stage="clip", credits=0.0,
                       detail={"model": plan.model, "takes": len(parts),
                               "path": str(workspace.clip_video()),
                               "credits_charged": round(spent, 3),
                               "duration_s": round(
                                   media.duration(workspace.clip_video()), 3)})


def _stage_tts(context: Context) -> StageResult:
    workspace = context.workspace
    resolved = context.values()
    lines = script.spoken_lines(resolved, SPOKEN_KEYS)
    if not lines:
        raise FactoryError("TTS_NO_LINES", context.spec.name)
    voice = tts.Voice.from_profile(context.profile.voice)
    chars = tts.character_count(lines)
    cap = int(context.limits.get("tts_chars_max") or TTS_CHAR_CAP)
    if context.dry_run:
        return StageResult(stage="tts", status="dry_run",
                           detail={"model": voice.model, "lines": len(lines),
                                   "chars": chars, "chars_cap": cap})
    tts.check_budget(lines, cap=cap)
    directory = workspace.tts_dir()
    results = [tts.synthesize(line, directory, voice=voice,
                              api_key=context.env.get("ELEVENLABS_API_KEY", ""))
               for line in lines]
    narration = tts.build_narration(results, workspace.narration())
    timed = tts.collect_word_times(results, narration["offsets"])
    (directory / "lines.json").write_text(
        json.dumps({"voice": voice.describe(), "lines": timed},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    fresh = sum(0 if result["cached"] else result["chars"] for result in results)
    return StageResult(stage="tts",
                       detail={"model": voice.model, "lines": len(results),
                               "chars": chars, "chars_billed": fresh,
                               "path": narration["path"],
                               "duration_s": narration["duration_s"]})


def _caption_source(limits: dict[str, Any]) -> str:
    """Which clock the captions follow.

    Captions have to be timed to the audio that actually plays. When the take
    carries its own speech the timings must come from transcribing that take;
    reusing the synthesiser's alignment would drift the highlight away from the
    words being said, because the two performances are not the same take.
    """
    declared = str(limits.get("caption_source") or "").strip()
    if declared in ("tts", "stt"):
        return declared
    return "tts" if str(limits.get("audio_source") or "clip") == "tts" else "stt"


def _caption_lines(context: Context) -> list[dict[str, Any]]:
    """Word timings from whichever source times the audio in the deliverable."""
    if _caption_source(context.limits) == "tts":
        timed = context.workspace.tts_dir() / "lines.json"
        if timed.is_file():
            try:
                parsed = json.loads(timed.read_text(encoding="utf-8"))
            except ValueError:
                parsed = {}
            lines = parsed.get("lines") if isinstance(parsed, dict) else None
            if isinstance(lines, list) and lines:
                return [entry for entry in lines if isinstance(entry, dict)]
    for transcript in (context.workspace.clip_dir() / "gates" / "stt.json",
                       context.workspace.root / "gates" / "stt.json"):
        if transcript.is_file():
            from ...gates import stt_wpm

            return stt_wpm.caption_lines(transcript)
    if context.dry_run:
        # Nothing has been spoken yet, so lay the cards out on the script's own
        # pacing. Enough to see the wrapping and the safe-zone boxes before a
        # take is paid for.
        return _planned_lines(context)
    raise FactoryError("STAGE_INPUT_MISSING", "captions:run_tts_or_stt_first")


def _planned_lines(context: Context) -> list[dict[str, Any]]:
    """Script lines with timings estimated from the target words per minute."""
    resolved = context.values()
    lines = script.spoken_lines(resolved, SPOKEN_KEYS)
    per_word = 60.0 / float(resolved.get("wpm") or 165)
    cursor = 0.0
    out: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        words = line.split()
        spans = []
        for _word in words:
            spans.append([round(cursor, 3), round(cursor + per_word, 3)])
            cursor += per_word
        cursor += 0.3
        out.append({"line": index, "text": line, "offset_s": spans[0][0],
                    "word_times": spans})
    return out


def _stage_captions(context: Context) -> StageResult:
    workspace = context.workspace
    lines = _caption_lines(context)
    frame = _frame(context.limits)
    width = int(context.limits.get("max_text_width_px") or captions.MAX_TEXT_WIDTH)
    metrics = captions.Metrics(captions.Typeface.discover())
    zone = context.spec.safe_zone() or dict(captions.SAFE)
    cards = captions.build_cards(lines, metrics, max_width=width)
    if not cards:
        raise FactoryError("NO_CAPTION_CARDS")
    boxes = captions.card_boxes(cards, metrics, frame=frame, zone=zone)
    if context.dry_run:
        return StageResult(stage="captions", status="dry_run",
                           detail={"cards": len(cards), "boxes": boxes})
    source = workspace.require(workspace.clip_video(), stage="captions")
    # The take only. The end card hold is appended after the captions are burned
    # in, so a track built for the full reel length runs past the footage.
    total = media.duration(source)
    track = captions.render_track(cards, workspace.root / "captions",
                                  total_s=total, fps=assemble.FPS,
                                  metrics=metrics, frame=frame, zone=zone)
    captions.save_cards(workspace.cards_json(), cards, boxes, track)
    return StageResult(stage="captions",
                       detail={"cards": len(cards), "path": str(
                           workspace.cards_json()), **track})


def _stage_assemble(context: Context) -> StageResult:
    workspace = context.workspace
    limits = context.limits
    frame = _frame(limits)
    if context.dry_run:
        return StageResult(stage="assemble", status="dry_run",
                           detail={"size": list(frame), "fps": assemble.FPS,
                                   "duration_band": list(context.duration_band or ())})
    source = workspace.require(workspace.clip_video(), stage="assemble")
    build = workspace.directory("build")
    body = assemble.normalise(source, build / "body.mp4", width=frame[0],
                              height=frame[1])
    if str(limits.get("audio_source") or "clip") == "tts" and \
            workspace.narration().is_file():
        body = assemble.mux_audio(body, workspace.narration(),
                                  build / "body_tts.mp4")
    if workspace.caption_frames().is_dir() and any(
            workspace.caption_frames().glob("*.png")):
        body = assemble.burn_captions(body, workspace.caption_frames(),
                                      build / "body_cap.mp4")
    hold = assemble.hold_last_frame(body, build / "hold.mp4",
                                    seconds=_hold(limits), width=frame[0],
                                    height=frame[1])
    assemble.concat([body, hold], workspace.final(), width=frame[0],
                    height=frame[1])
    report = assemble.verify(workspace.final(), duration_band=context.duration_band,
                             width=frame[0], height=frame[1])
    status = "ok" if report.ok else "gate_fail"
    return StageResult(stage="assemble", status=status,
                       detail={"path": str(workspace.final()),
                               "verify": report.as_dict()})


HANDLERS: dict[str, Callable[[Context], StageResult]] = {
    "script": _stage_script,
    "still": _stage_still,
    "clip": _stage_clip,
    "tts": _stage_tts,
    "captions": _stage_captions,
    "assemble": _stage_assemble,
}


def run(*, home: Path, brief: Brief, profile: Profile, spec: FormatSpec,
        stages: str = "all", dry_run: bool = False, force: bool = False,
        log: Logger = print) -> dict[str, Any]:
    """Run the requested stages in order. Returns a report, raises on failure."""
    if spec.kind != "video":
        raise FactoryError("FORMAT_KIND_MISMATCH", spec.kind)
    if not dry_run:
        media.require_binaries()
    workspace = Workspace.for_brief(home, brief.id)
    allowance = budget_module.for_brief(home, ref=brief.id,
                                        format_cap=spec.credit_cap_per_item,
                                        brief_cap=brief.budget.credits,
                                        track=TRACK)
    context = Context(home=Path(home), brief=brief, profile=profile, spec=spec,
                      workspace=workspace, allowance=allowance,
                      env=load_profile_env(Path(home), brief.profile),
                      dry_run=dry_run, force=force, log=log)
    results: list[StageResult] = []
    for name in resolve_stages(stages):
        log(f"[{name}]")
        result = HANDLERS[name](context)
        if not dry_run:
            context.record(result)
        results.append(result)
        log(f"  {result.status}" + (f", {result.credits:g} cr"
                                    if result.credits else ""))
    report = {"brief": brief.id, "workspace": str(workspace.root),
              "stages": [result.as_dict() for result in results],
              "credits_spent": allowance.spent() if not dry_run else 0.0,
              "credit_cap": allowance.cap}
    failed = [result.stage for result in results if result.status == "gate_fail"]
    if failed:
        report["gate_failures"] = failed
    return report


def outputs_for(workspace: Workspace) -> list[Output]:
    """What the brief should carry once the pipeline has run."""
    final = workspace.final()
    return [Output(path=str(final), gates={}, chosen=False)] if final.is_file() else []
