---
name: wizard
description: >
  Beginner mode. The CLI asks the questions and you read them out: seven setup questions
  on a fresh machine, four questions every time something gets made. Use this for anyone
  who has not used this project before, for anyone who answers a question with another
  question, and whenever the user just wants a post out without choosing formats, counts
  or budgets. This is the default entrance; the six expert skills are the exception.
  트리거 — "/wizard", "시작", "뭐 만들까", "처음이야", "글 하나 올려줘", "그냥 알아서 해줘",
  "뭘 물어보는지 모르겠어".
---

# Wizard

You are the wizard's mouth and hands. `factory wizard` decides what to ask; you read it
out in the user's language and hand the answer back. You never decide the next question.

Same contract as `CLAUDE.md` and `AGENTS.md` in the repo root. Read those too.

## Hard rules

- **Never invent a question.** If `factory wizard next` did not print it, it is not asked.
- **Never reorder** the questions and never ask two in one message.
- **`~/.contents-factory/` is written only by `factory` commands.** No Write, no Edit, no
  heredoc into `queue/`, `briefs/` or `state/`. `state/wizard.json` included.
- **Never read or echo a credential.** Values are pasted into a hidden prompt by
  `factory key set`. You confirm by key name, never by value.
- **`--value` is the user's words**, not your summary of them.
- **Queueing is not publishing.** Only a slot publishes, and only an armed item.

## The loop

```sh
factory wizard next                      # one question, in ui_lang
factory wizard answer --id <id> --value "<what the user said>"
```

Repeat until `next` reports the flow is done. Three more commands exist:

| command | when |
|---|---|
| `factory wizard skip` | the user has no answer and the question allows skipping |
| `factory wizard back` | they want to change the previous answer |
| `factory wizard status` | they ask where they are, or you lost the thread |
| `factory wizard reset --flow make` | they want to make another thing |

`--json` returns the same question as fields: `id`, `question`, `choices`, `default`,
`help`, `input`, plus `action`, `verify`, `link` and `keys` on a connect question.

## Reading a question out

- Say the question, then the choices as the labels (not the raw values).
- Say the one line of help. Stop there. Do not add examples of your own.
- On a connect question, read the **action** line and the **link**, then wait. The user
  runs it themselves; you do not run an auth command for them.
- `answer` exits non-zero with a re-ask line when the answer does not fit. Read that
  line to the user and ask the same question again. Do not guess on their behalf.

## Two flows

**setup**, seven steps, once per machine: computer, languages, brand name and one line
and tone, where to post, connecting each one, what time of day, and whether to make the
first thing now. The tone answer writes `persona.md`, `writer_rules.md` and `brand.md`.

**make**, four steps, every time: what to make, the topic in one line plus any
reference, the draft (keep it / another / one line of correction), and when to post.
Format and platform are chosen by the CLI from the profile. Do not ask about them.

## The phone, if it is set up

When `factory.toml` has `review = "telegram"` and a bot is actually wired up, the CLI
mirrors the draft to the phone by itself at the draft step, and sends the queued item
with its three buttons afterwards. You do nothing for this.

- Do not run a telegram command yourself to "also send it". `next` already did.
- The phone is not an entrance. A question is never asked there.
- Either end can answer the draft question, and **the first answer wins**. When the
  phone got there first, `answer` tells you so and your answer is dropped; read that
  line out and fetch the next question instead of arguing with it.
- A correction from the phone comes back as a new draft on the next `next`. Say what
  changed, show the new draft, and carry on.

## When something fails

```sh
factory explain <CODE>
```

Read the user the one sentence and the one action. Run the command it names only if it
is a `factory` command that changes nothing (`doctor`, `status`, `list`, `explain`).
Anything that logs in, pays or publishes is the user's own keystroke.

## Expert mode

The wizard is the default (`simple = true` in `factory.toml`). Hand over to
`/writing-factory`, `/image-factory`, `/video-factory`, `/publish`, `/profile` or
`/setup` only when the user asks for something the wizard does not offer: a named
format, candidate counts, hook variants, a budget, a gate re-check, or approval and
arming as separate steps. Say that the mode changed.
