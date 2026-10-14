---
name: profile
description: >
  Create or revise a profile: persona voice, brand references, forbidden words, fixed closer
  phrases, voice and Soul ids, and per-platform slots, daily caps and late policy. Use when
  the user wants a new voice, is still on _example, wants to change posting times or caps,
  or keeps tripping the forbidden-words or repeat guard. 트리거 — "/profile", "프로필 만들어",
  "페르소나 바꿔", "말투 정해", "슬롯 바꿔", "금지어 추가".
---

# Profile

A profile is the only place a persona exists. Nothing persona-shaped belongs in the code,
the formats or the skills.

## Hard rules

- Profile files are editable text (repo `profiles/<name>/` or
  `~/.contents-factory/profiles/<name>/`). Runtime state under `~/.contents-factory/`
  (`queue/`, `briefs/`, `state/`) stays CLI-only: no Write, no Edit, no jq.
- `credentials.env` lives only in the runtime home, `chmod 600`, and you never read values
  back. `/setup` owns that file.
- Non-secret account ids (`tiktok.connector_id`, `naver_blog.blog_id`) go in
  `platforms.json`, never in `credentials.env`.
- A persona is **not** a list of adjectives. If the answers come back as "professional yet
  friendly", push for a concrete rule the model can obey.

## Step 1: start from the example or an existing profile

```
factory profile list
factory profile add <name> --from _example
factory profile show <name>
```

## Step 2: persona interview

Ask these one or two at a time and write `persona.md` from the answers. Every section must
end up as something checkable, not a mood.

| Section | What to pin down |
|---|---|
| Identity | Who is speaking, what they actually do, what they are not |
| Voice | Sentence length, tense, person, words they would never use |
| Never do | Concrete bans: opener shapes, template closers, promise language |
| Signature moves | Two or three structural habits, with an example line each |

Good: "Lead with the cost, then the cause." Bad: "Be relatable and authentic."

`persona.md` goes into the LLM system prefix verbatim, so it is also the caching prefix.
A thin persona means the prefix may fall under the 1,024-token minimum and caching is
skipped. Do not pad it to game that; write the rules that actually exist and check with
`factory gen text --brief b_... --dry-run` (the `cache` field says `ephemeral` or
`skipped`).

## Step 3: brand

`brand.md` carries reference priority (approved asset beats generated), logo rules, colour,
type and imagery constraints. The image factory reads this before it writes a prompt.

## Step 4: forbidden words and closers

- `forbidden.txt`: one phrase per line, case insensitive. These are hard FAILs on top of
  the global claim-shaped bans (guarantees, cures, absolute numbers).
- `closers.txt`: fixed closing phrases to watch. The repeat guard blocks a closer that has
  already been used three times in seven days. Add anything that is becoming a template,
  including the user's own favourite sign-off.

## Step 5: voice and Soul

- `voice.json`: `{provider, voice_id, model, stability, style}` for TTS.
- `soul.json`: `{provider, soul_id, ref_image, identity_rules}`. Identity rules matter:
  one reference image is the source of truth, do not describe the face in the prompt, keep
  skin texture. A Soul id switches the image generator to the Soul model automatically.

## Step 6: platforms

`platforms.json`, per platform:

```json
{"enabled": true, "account_id": null, "slots": ["12:00Z", "23:00Z"],
 "late_policy": "carry_once", "cta_rotation": ["..."], "daily_cap": 2,
 "connector_id": null, "blog_id": null}
```

- `slots` are **UTC only**, in `HH:MMZ` form. Convert from the user's local time and say
  the conversion out loud so a daylight-saving mistake surfaces now.
- `daily_cap` is per profile and platform. 0 means unlimited; say so if you set it.
- `late_policy`: `carry_once` moves a missed item to the next slot once, then drops it.
  `drop` discards immediately. There is no uncapped catch-up.
- `cta_rotation` exists so the same closing line does not go out twice in a week.

## Step 7: verify

```
factory profile show <name>
factory gate run --text "<a sample line in their voice>" --format threads_post
```

Report back in three lines: profile name, which platforms are enabled with which slots, and
anything still unset (`connector_id`, `blog_id`, Soul id, voice id).
