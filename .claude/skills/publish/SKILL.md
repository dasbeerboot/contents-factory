---
name: publish
description: >
  Queue review and release: list pending items, assign slots, approve, arm with a reason,
  install the scheduler, and print the actual publishing plan for the next seven days. Also
  handles stuck deliveries (unknown phase, held containers) and token expiry. Use when the
  user asks what is queued, when something goes out, to approve or arm an item, or why
  nothing published. 트리거 — "/publish", "큐 보여줘", "발행 편성", "언제 나가", "승인",
  "arm", "발행 안 됐어".
---

# Publish

You schedule and release. The tick publishes. Those are different jobs and you do not do
the tick's job by hand.

## Hard rules

- **Approval is not publication.** Queueing and approving never post anything. Only the
  tick posts, and only at a slot.
- **Arm needs the user's own words.** `--reason` is quoted from them, never invented.
- **Never publish outside a slot.** `factory publish <platform> --item ... --dry-run` is
  always fine. The live form is a deliberate manual exception, requires approved and armed,
  and you ask first.
- **Never hand-edit state.** Nothing under `~/.contents-factory/` is edited by hand:
  no Write, no Edit, no jq into `queue/` or `state/`. Only `factory` commands write.
- An item parked in `unknown` delivery phase is **not retried**. Resending could double
  post. It is cleared only by `factory queue reopen` after the user checks the platform.

## Step 1: read the queue

```
factory queue list --pending
factory queue list --json          # full fields including phase
```

Columns are id, slot (UTC), platform, flags `A`pproved `R`armed `P`ublished, phase, profile,
text head. Explain `AR` versus `A-`: the second one will not go out.

## Step 2: slots

An item's slot comes from `platforms.json` when it is queued with `--slot next`, or from an
explicit UTC stamp. To move one:

```
factory queue list --json | <find the id>
factory queue add ... --slot 2026-10-15T12:00:00Z    # new item
```

There is no reschedule command for an already queued item in P0. If the slot is wrong, say
so plainly: unapprove it, let it drop or purge it, and queue the corrected one.

## Step 3: approve, then arm

```
factory queue approve --item q_...
factory queue arm --item q_... --reason "<the user's words>"
```

Arming is the last gate before automation. State what arming means before you run it: at
the slot, with no further confirmation, this goes out.

To pull it back:

```
factory queue disarm --item q_...      # keeps approval
factory queue unapprove --item q_...   # clears both
```

## Step 4: dry run

```
factory publish threads --item q_... --dry-run
```

Prints the exact request payload with secrets replaced by `<REDACTED:KEY>`. Read back what
platform, what text length, what media URL. If media has no `public_url`, stop: Threads,
Instagram and TikTok fetch media server side and a local path cannot be posted.

## Step 5: scheduler

```
factory schedule install --runner launchd    # macOS, 15 minute interval
factory schedule install --runner cron       # Linux
factory schedule status
factory schedule tick --dry-run              # decisions only, no publishing
factory schedule tick                        # a real run
```

`schedule status` ends with the actual seven-day publishing plan: every unpublished item
with its slot and flags. That table, not a promise, is what you show the user.

## Step 6: read the tick's decisions

Each line is `<id> <ACTION> <REASON>`:

| Reason | Meaning |
|---|---|
| `NOT_APPROVED` / `NOT_ARMED` | waiting on a human |
| `NOT_DUE` | before the slot |
| `DUE` | publishing now |
| `RESUME_CONTAINER` | finishing a container created on an earlier run |
| `LATE_CARRIED` | missed the slot by more than 900s, moved once |
| `LATE_CARRY_EXHAUSTED` / `LATE_DROPPED` | dropped and unapproved |
| `DAILY_CAP` | profile and platform cap reached today |
| `REPEAT_BLOCK:...` | repeat guard failed at the last moment |
| `DELIVERY_UNCONFIRMED:...` | a send was in flight and the answer was lost |
| `DELIVERY_CONTENT_CHANGED` | the text or media changed after a send attempt |
| `PUBLISHED_FLAG_MISSING` | repaired bookkeeping, nothing resent |

## Step 7: stuck items

Explain these in the user's language rather than pasting the code at them:

```
factory i18n get delivery.unconfirmed
factory i18n get delivery.resume
factory i18n get delivery.reopen_refused
factory i18n get media.public_url_required
```

- `DELIVERY_UNCONFIRMED`: tell the user to open the platform and look. If the post is
  there, leave it and purge the queue row. If it is not:
  `factory queue reopen --item q_... --reason "checked the profile, nothing posted"`.
  The command refuses when a container id or published id exists.
- `CONTAINER_IN_PROGRESS`: nothing to do, the next tick resumes the publish step.
- `NAVER_SESSION_MISSING`: the user signs in to Naver in the aside browser, then the next
  tick picks it up.
- `ENV_MISSING` / `CONNECTOR_ID_MISSING` / `BLOG_ID_MISSING`: route to `/setup`.

## Step 8: tokens

The tick prints `token.expiry_warning` from 7 days out.

```
factory token refresh --platform threads --profile <name> --dry-run
factory token refresh --platform threads --profile <name>
```

Report back in three lines: how many items pending, what goes out next and when (UTC plus
their local time), and anything blocked on a human.
