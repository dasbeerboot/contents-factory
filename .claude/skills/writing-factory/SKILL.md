---
name: writing-factory
description: >
  Interview-driven text production for Threads, Instagram captions, long-form blog posts and
  card-news copy. Asks the eight common questions, drafts the prompt with the user, runs the
  generator, shows the gate table, then queues the chosen candidate for a scheduled slot.
  Use when the user wants a post, a caption, a thread or a blog draft written, or asks to
  refine and requeue one. 트리거 — "/writing-factory", "글 뽑자", "쓰레드 글", "캡션 써줘",
  "블로그 글 써줘", "이거 큐에 넣어".
---

# Writing factory

You run the interview. The CLI owns every file. You never write under the runtime home
yourself.

## Hard rules

- **No hand editing state.** `~/.contents-factory/` is written only by `factory ...`
  commands. No Write, no Edit, no heredoc, no `jq | mv` into `queue/`, `briefs/` or
  `state/`. If a command for what you need does not exist, say so and stop.
- **Approval is not publication.** Queueing never publishes. Only the scheduler tick
  publishes, and only at a slot.
- **Never arm without the user's words.** `factory queue arm` requires a reason string and
  the reason must come from the user, quoted, not invented by you.
- **Never publish outside a slot.** Do not reach for `factory publish` to "just get it out".
  Live publishing of an item is the tick's job. `factory publish --dry-run` is fine any time.
- **Never print a credential.** Read key names from the README, never the values in
  `credentials.env`.
- **Question wording comes from i18n**, not from this file: `factory i18n get <key>` renders
  in the user's `ui_lang`.

## Step 0: context

```
factory init                  # idempotent, prints home and language settings
factory profile list
factory formats --kind text
```

If the user has no profile other than `_example`, offer `/profile` first. `_example` is a
demo voice, not theirs.

## Step 1: the eight common questions

Ask them in order. Skip any whose answer already exists in `factory.toml` or the profile and
say so with the `.skip` string. Fetch each line:

```
factory i18n get interview.common.intro
factory i18n get interview.common.what.q
factory i18n get interview.common.what.hint
```

| # | key prefix | what it settles |
|---|---|---|
| 1 | `interview.common.what` | the artifact in one line |
| 2 | `interview.common.where` | platforms, which fixes length and aspect |
| 3 | `interview.common.who` | profile |
| 4 | `interview.common.reference` | references, skippable |
| 5 | `interview.common.content_lang` | content language, separate from `ui_lang` |
| 6 | `interview.common.count_ab` | candidate count and hook variants |
| 7 | `interview.common.budget` | credit and token cap |
| 8 | `interview.common.publish` | `queue_only`, `slot` or `auto_after_approve` |

Ask at most two questions per message. Never ask something the profile already answers.

## Step 2: format

Show the catalogue plus `free` (`factory i18n get interview.format.free`). Then ask the
format's own extra questions, whose keys come from the yaml:

```
factory brief new --kind text --format threads_post --profile <name> --answers -
```

Pipe the collected answers in as one json object on stdin. The command prints the brief id
and the extra question keys for that format.

## Step 3: prompt co-authoring

This is the step that must not be skipped. Compose a draft prompt from the format template,
the answers and the profile rules, then show it and ask for changes:

```
factory i18n get interview.prompt.draft
```

Loop: show, take edits, show again. Count the rounds and write them into the brief as
`prompt_revisions`, with the agreed text as `prompt_final`. Do not generate until the user
says the prompt is right. When they later dislike an output, revise `prompt_final` and
regenerate; do not start a new brief.

For `free` there is no template. Build the prompt from scratch with them, line by line.

## Step 4: generate and gate

```
factory gen text --brief b_... --dry-run     # shows the exact API payload, no call
factory gen text --brief b_...               # fills outputs[], runs gates per candidate
```

Then present the gate table and the candidates, and ask the user to pick one by index.
A FAIL is not a veto you override silently:

- `limits` fail: shorten or drop the hashtag or link, then regenerate.
- `forbidden_words` fail: the phrase is out, no negotiation.
- `repeat_guard` fail: it already went out. Rewrite the structure, do not reword a sentence.
  `factory gate run --brief b_... --gate repeat_guard` re-checks.

## Step 5: queue

```
factory queue add --brief b_... --output 0 --platform threads --slot next
factory queue list --pending
```

`--slot next` takes the next configured slot for that platform from the profile. An explicit
`--slot 2026-10-15T12:00:00Z` is UTC, always.

Then stop and hand back. Approval and arming are separate, deliberate acts:

```
factory queue approve --item q_...
factory queue arm --item q_... --reason "<the user's own words>"
```

Tell them the item will not publish until the tick reaches its slot, and that
`factory publish threads --item q_... --dry-run` shows exactly what will be sent.

## Step 6: report back

Three lines, no more: what was made, which gates passed, when it is scheduled. Then the
dry-run payload only if they ask for it.

## Pointers

- Publishing rules, slot and late policy: `docs/rules/publishing-rules.md`
- Writing rules, hooks and tone: `docs/rules/writing-rules.md`
- Credential key names per platform: `README.md`
