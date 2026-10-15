---
name: video-factory
description: >
  Interview-driven short-form video: talking head, POV, text card, explainer, and a free
  mode with no template. Runs the eight common questions, picks a format, co-authors the
  script and prompt, then holds the one-take budget, caption safe zone and pacing gates
  before anything is queued. Use when the user wants a Reel, a Short, a TikTok, a narrated
  explainer or a text-card clip. 트리거 — "/video-factory", "영상 뽑자", "릴스", "쇼츠",
  "틱톡 영상", "토킹헤드", "대본 써줘".
---

# Video factory

> **컴맹 모드면 여기서 멈추고 `/wizard` 로 보내라.** 이 스킬은 전문가 모드다. 사용자가 포맷·후보 개수·예산·승인 분리를 직접 고르려 할 때만 쓴다. 그 외에는 `factory wizard next` 가 묻는 질문만 읽어주면 된다.
> **Beginner mode belongs in `/wizard`.** This skill is expert mode: use it only when the user wants to pick the format, the candidate count, the budget or to approve and arm separately.


You run the interview, write the script with the user, and hold the budget. The CLI owns
state.

P0 status: interview, brief, script and prompt confirmation work. `factory gen video` does
not exist yet, so rendering is still manual or external. Say that plainly rather than
implying a render is coming. Everything after a render (gates, queue, slot) does work.

## Hard rules

- **Never hand-edit state.** `~/.contents-factory/` is written only by `factory` commands.
- **One take.** The per-item credit cap in the format yaml (120 for explainer, 150 for
  talking head and POV, 60 for text card) is a ceiling, not a target. A failed take is
  normally **discarded and replaced by the backup script**, not re-rolled. A re-roll needs
  the failure cause and the extra cost stated, and an explicit yes.
- **Do not edit outside the template slots.** A verified `prompt_template` is changed only
  in its slots: location, outfit, lighting, lines, timing, gaze. If a sentence outside a
  slot differs, do not render.
- **Do not add defensive sentences.** "No extra objects in frame" puts the noun in the
  model's scene prior. Block failures with composition and slot choice.
- **Never approve a video from frames alone.** Sound, pacing and captions are checked on
  the finished file, with audio. If you cannot verify audio, say which checks are unverified.
- Question wording comes from the format yaml, not from this file.

## Step 0: context

```
factory profile show <name>          # persona, voice.json, soul.json identity rules
factory formats --kind video
factory formats --kind video --format talking_head --lang ko
```

## Step 1: the eight common questions

Skip whatever `factory.toml` or the profile answers. Fetch each line:

```
factory i18n get interview.common.intro
factory i18n get interview.common.budget.q
```

Budget deserves a real answer here: video is the expensive track and the cap is per item.

## Step 2: format

| Format | Use | Length | Credit cap |
|---|---|---|---|
| `talking_head` | person to camera, selfie distance | 23 to 28s | 150 |
| `pov` | viewer-perspective scene with an off-camera voice | 20 to 24s | 150 |
| `textcard` | typographic cards, no performer | 8 to 15s | 60 |
| `explainer` | narrated non-photoreal explainer | 30 to 60s | 120 |
| `free` | no template, built from scratch together | ask | ask |

```
factory brief new --kind video --format talking_head --profile <name> --answers -
```

## Step 3: script, then prompt

Two co-authoring loops, in this order. Neither is skippable.

**Script.** Word count against the format's `wpm` range, plus pause and end-card time, must
fit the duration. Show the script, take edits, repeat. A script that needs 200 words in a
24-second slot is not a script, it is a re-roll waiting to happen.

**Prompt.** Fill the template slots only. Show the filled template with the slots marked so
the user can see what changed and what did not. Store the agreed text as `prompt_final` and
count the rounds in `prompt_revisions`.

For `free`, write the prompt from scratch with the user and say that no verified template is
protecting them.

## Step 4: stills before motion

For performer formats, the still is the contract: identity, framing, arms, background and
any on-frame text are settled there. Compare a candidate against the reference image **side
by side**; never judge identity by a score. Do not write long facial descriptions into the
prompt, they drift the face. If no candidate passes, change the location or framing slot
rather than re-rolling the same prompt.

## Step 5: gates

The format's gate list runs through:

```
factory gate run --brief b_... --gate caption_safe_zone --box 220,1200,600,180,caption
```

- `caption_safe_zone` works now: pass the caption box coordinates. The zone comes from the
  format's `safe_zone_px` (x 200..880, y 290..1430 on 1080x1920) and scales to other frames.
  A violation means **reassemble the frame**, not shrink the text.
- `cta_single` works now: more than one ask in the script is a FAIL, and the limit comes
  from the format's `cta_max`.
- `stt_wpm`, `limb_motion`, `bg_motion`, `identity`, `start_freeze`, `text_legibility`,
  `viewer_voice_sync`, `timing`, `export_size` all print `SKIP:NOT_IMPLEMENTED` in P0.
  Those are your eyes and ears. Report which were human checks.

By-hand checklist before anything is shown as final, from `docs/rules/video-rules.md`:
frame strip, hand and arm sheet, face montage, background motion, caption safe zone, and
the audio pass with real sound. No freeze in the first two seconds. No limb parked in one
place for three seconds. End card at least 2.5 seconds.

## Step 6: queue

```
factory queue add --brief b_... --output 0 --platform tiktok --slot next
```

Approval never means immediate release: the item waits for its slot. TikTok and Instagram
fetch media server side, so the file needs a public URL first.

## Step 7: report

Three lines: what was made, credits spent against the cap, which checks were automated
versus human. Then the dry-run payload only if asked.
