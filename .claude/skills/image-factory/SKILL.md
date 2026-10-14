---
name: image-factory
description: >
  Interview-driven image production: product shots, card news, OG images, thumbnails, and a
  free mode with no template. Runs the eight common questions, picks a format, co-authors
  the prompt, then builds the Higgsfield command, checks the caption safe zone and queues
  the chosen frame. Use when the user wants a product photo, a card-news deck, an OG or
  social preview image, a thumbnail, or a reference-faithful edit. 트리거 —
  "/image-factory", "이미지 뽑자", "상품컷", "카드뉴스", "썸네일", "OG 이미지", "사진 만들어".
---

# Image factory

You run the interview and co-author the prompt. The CLI builds the command and owns state.

P0 status: the interview, the brief, the prompt loop and `--dry-run` all work.
`factory gen image` without `--dry-run` shells out to the `higgsfield` CLI, which spends
credits, so you do not run it until the user has seen the dry-run plan and said go.

## Hard rules

- **Never hand-edit state.** `~/.contents-factory/` is written only by `factory` commands.
- **Never spend credits without an explicit go.** Show `--dry-run` (the exact argv, the run
  count, the per-item credit cap) first. Say the number out loud.
- **Pilot before volume.** Three frames, confirm, then the rest. Never generate ten or more
  unconfirmed (`docs/rules/image-rules.md`).
- **At most three references at once.** More and they blend; the CLI refuses with
  `TOO_MANY_REFERENCES`. Different purposes become different stages.
- **No text inside the image.** Captions, prices, sizes and legal lines go on an HTML,
  canvas or design layer above it. The one allowed exception is a short hook line inside the
  safe zone for a thumbnail.
- Approved brand assets beat anything generated. Check `brand.md` reference priority first.
- Question wording comes from the format yaml, not from this file.

## Step 0: context

```
factory profile show <name>          # brand.md, soul.json, forbidden list
factory formats --kind image
factory formats --kind image --format product_shot        # questions, limits, gates
```

## Step 1: the eight common questions

Same as the writing factory, skipping whatever `factory.toml` or the profile already
answers. Fetch each line rather than writing your own:

```
factory i18n get interview.common.intro
factory i18n get interview.common.what.q
```

Covered: what, where (platform fixes aspect), who (profile), references, content language
(matters for any in-image hook text), count and variants, budget in credits, publish mode.

## Step 2: format

Show the catalogue plus `free`:

| Format | Use | Aspect |
|---|---|---|
| `product_shot` | one product, structure faithful to the reference | 4:5 |
| `cardnews` | multi-slide deck copy made visual | 4:5 |
| `og` | link preview, headline plus subline | 1.91:1 |
| `thumbnail` | tap-bait frame for Shorts, TikTok, Reels | 9:16 |
| `free` | no template, prompt written from scratch together | ask |

Then ask that format's own questions, in the user's language:

```
factory formats --kind image --format thumbnail --lang ko
```

Write the answers into a brief:

```
factory brief new --kind image --format thumbnail --profile <name> --answers -
```

## Step 3: prompt co-authoring, the step you do not skip

1. Compose a draft from the format template, the answers and `brand.md`.
2. Show it. Ask what to change, add or cut. Name the parts you are unsure about rather than
   hiding them.
3. Loop until the user says it is right. Count the rounds into `prompt_revisions` and store
   the agreed text as `prompt_final`.
4. For `free` there is no template: build it line by line with them.

Two things to get right while drafting, both from `image-rules.md`:

- Describe product and logo structure from the reference, never from memory. If you have not
  seen the reference, say so and ask for it.
- Block a recurring failure with composition, not with a negation. If hands keep holding
  something they should not, choose a framing where hands are nowhere near it.

## Step 4: plan, then generate

```
factory gen image --brief b_... --dry-run
```

Read back: model, aspect, references, run count, credit cap. `model` is chosen for you:
a profile Soul id selects the Soul model, text-bearing formats select the design model,
reference work selects the reference model.

After the user approves the plan, generate the pilot (set `count` to 3 in the brief, not 30).

## Step 5: gates and review

The format declares its gates. These run today:

```
factory gate run --brief b_... --gate caption_safe_zone --box 220,300,600,200,hook
```

`caption_safe_zone` needs the text box coordinates; without `--box` it reports
`SKIP:BOXES_REQUIRED`. Everything else in an image format's gate list
(`product_identity`, `text_legibility`, `logo_geometry`, `crop_margin`, `artifact_scan`)
prints `SKIP:NOT_IMPLEMENTED` in P0, which means **you check it by eye** and say which
checks were human, not automated. Never report a skipped gate as a pass.

Review by hand before the user picks:

- Open each candidate at full size. A thumbnail sheet selects, it does not approve.
- Product count, structure and colour against the reference, side by side.
- Any unintended readable text.
- Crop at every target aspect: nothing important near an edge.

## Step 6: queue

```
factory queue add --brief b_... --output 0 --platform instagram --slot next
```

Platform media needs a public URL because the platform fetches it server side. If
`media[].public_url` is empty, say so and route to the media hosting section of the README.

Then hand back. Approval and arming are separate acts and belong to `/publish`.

## Step 7: report

Three lines: what was made and at what cost in credits, which gates passed automatically
versus by eye, and where it is queued.
