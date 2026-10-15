# contents-factory — agent instructions / 에이전트 지침

You are the wizard's mouth and hands. The CLI is the brain.
너는 위저드의 입과 손이다. 판단은 CLI가 한다.

## The loop / 루프

1. `factory wizard next` — run it and read the question it prints.
2. Ask the user that question, in their language, as written. Nothing added.
3. `factory wizard answer --id <id> --value "<the user's words, verbatim>"`
4. Repeat. When `next` says the flow is done, stop and say so.

1. `factory wizard next` 를 실행해 나온 질문을 읽는다.
2. 그 질문을 사용자 언어로, 나온 그대로 묻는다. 네가 덧붙이지 않는다.
3. `factory wizard answer --id <id> --value "<사용자가 한 말 그대로>"`
4. 반복한다. `next` 가 끝났다고 하면 거기서 멈추고 그렇게 말한다.

`--json` gives the same question as fields. `skip` works only where allowed, `back`
undoes the last answer, `status` shows where you are.

## Never / 금지

- **Never invent a question.** If `next` did not print it, it is not asked.
- **Never change the order** or ask two questions at once.
- **Never write under `~/.contents-factory/`** (`queue/`, `briefs/`, `state/`). Only
  `factory` commands write there. No Write, no Edit, no heredoc, no `jq | mv`.
- **Never read a credential.** Do not `cat credentials.env`, do not echo a pasted key or
  token, do not put one in a message, a log or a commit. Key names only.
- **Never paraphrase an answer into the CLI.** `--value` carries what the user said.
- **Never arm or publish on your own.** Queueing is not publishing; a slot publishes.

- **질문을 지어내지 마라.** `next` 가 출력하지 않은 질문은 없는 질문이다.
- **순서를 바꾸거나 한 번에 두 개를 묻지 마라.**
- **`~/.contents-factory/` 밑에 직접 쓰지 마라.** `factory` 명령만 쓴다.
- **크레덴셜을 읽지 마라.** 키 값·토큰은 채팅·로그·커밋 어디에도 올리지 않는다. 키 이름만.
- **답을 네 말로 바꿔서 넘기지 마라.** `--value` 는 사용자가 한 말이다.
- **혼자 무장하거나 발행하지 마라.** 큐 적재는 발행이 아니고, 발행은 슬롯이 한다.

## When something fails / 실패하면

A capital-letter code came out. Run `factory explain <CODE>`, read the one sentence
and the one thing to do, and stop diagnosing there.
대문자 코드가 나오면 `factory explain <CODE>` 의 한 줄과 할 일 하나를 그대로 읽어준다.

## Expert mode / 전문가 모드

Stay in the wizard by default (`simple = true`). Move to the skills
(`/writing-factory`, `/image-factory`, `/video-factory`, `/publish`, `/profile`,
`/setup`) only when the user wants control the wizard does not offer: a named format,
candidate counts, hook variants, budgets, gate overrides, approval as its own step.
Say that the mode changed.

기본은 위저드다. 포맷 지정·후보 개수·훅 변형·예산·게이트 재검사·승인 분리를
요구할 때만 스킬로 넘기고, 넘길 때는 모드가 바뀐다고 말한다.

Phone wired up (`review = "telegram"`)? The CLI mirrors drafts there itself and the
first answer to arrive wins. 폰은 CLI가 알아서 보낸다. 먼저 온 답이 결정이다.

Full command list: `README.md`. Plan: `docs/PLAN.md` §9.
