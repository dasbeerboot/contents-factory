---
name: setup
description: >
  First-run setup on macOS, Windows or Linux: ask the host OS, run the host check, pick
  interview and content languages, create the runtime home, register a profile, write
  credentials.env, and connect each platform. Covers the Meta tester-role dance for
  Threads and Instagram, the Higgsfield CLI login that TikTok publishing rides on, a BYO
  GCP project for YouTube, and the two Naver browser backends. Use when the user has just
  cloned the repo, is adding a platform, or is hitting auth errors like ENV_MISSING,
  CONNECTOR_ID_MISSING, HIGGSFIELD_LOGIN_REQUIRED or NAVER_SESSION_MISSING.
  트리거 — "/setup", "셋업", "처음 설정", "키 넣어야 해", "플랫폼 연결", "로그인 안 됨",
  "윈도우에서 설치".
---

# Setup

You walk the user through setup. The CLI writes files; you never hand-edit state.

## Hard rules

- **Never read back a secret.** Do not `cat credentials.env`, do not echo a pasted token,
  do not put a value in a commit, a log or a chat message. You confirm by key name only.
- Tell the user to paste tokens **into the file themselves** (or use their editor). If they
  paste one into chat, say it is now burned and must be rotated.
- `~/.contents-factory/` is written only by `factory` commands. No Write, no Edit.
- Non-secret account ids go in `platforms.json`, not in `credentials.env`.

## First question, before anything else: which OS?

Ask it literally, and wait for the answer:

> 어떤 운영체제인가요? (macOS / Windows / Linux)

Then record it and check the host, in this order. Both commands before any key talk:

```sh
uv run factory init --os macos        # or --os windows / --os linux
uv run factory doctor
```

- `factory init --os ...` writes `os = "..."` into `factory.toml`. Everything that
  differs by platform reads it from there: the scheduler (launchd / Task Scheduler /
  cron), the Naver backend, how `credentials.env` is locked down.
- Omitting the flag detects the OS and **asks for confirmation** instead of adopting the
  guess. If the user answered the question, pass the flag.
- `factory doctor` is the first real step, not a troubleshooting command. It prints the
  OS (configured next to detected), the python, the runtime home, six external binaries
  with the install line for this OS, whether the Higgsfield CLI is logged in, the
  scheduler state and the profiles. Read the `blocking:` line out loud and fix those
  before going further — every one of them is a command that would otherwise fail hours
  later.

Windows specifics worth saying up front:

- the runtime home is `%USERPROFILE%\.contents-factory`, not `~/.contents-factory`
- the scheduler is Task Scheduler (`schtasks`), installed by
  `factory schedule install` (the default `--runner auto` picks it from `os`)
- `credentials.env` is locked with `icacls` rather than `chmod 600`; if that call is
  unavailable the command warns (`FILE_ACL_UNSET`) and still writes the key, so repeat
  the warning to the user rather than swallowing it
- Naver needs the Playwright backend, because aside is macOS only (Step 7)

Only the language question comes next, then the BYO accounts.

## Step 0: every external account is the user's own (BYO)

이 레포에는 공유 계정이 없다. 외부 서비스는 **전부 사용자 본인 계정**으로 가입하고 본인
키를 넣는다. 레포를 쓴다고 해서 어떤 크레딧도 따라오지 않는다.

Nothing in this repo is a shared account. Every external service is signed up for and paid
for by the user. Cloning the repo grants no credits on anything.

세 가지만 기억하면 된다 / Three rules:

- 값은 묻되 **절대 읽어 돌려주지 않는다**. 확인은 키 이름과 확인 명령으로만 한다.
- 키는 사용자가 직접 `credentials.env`에 넣는다. 채팅에 붙여넣었으면 그 키는 태운 것이니
  즉시 재발급하라고 말한다.
- 아래 표의 "확인 명령"이 통과하기 전에는 그 서비스가 연결됐다고 말하지 않는다.

| 서비스 / Service | 쓰는 곳 / Used for | 필수 키 / Keys | 위치 / Where |
|---|---|---|---|
| Higgsfield | 이미지·영상 생성, TikTok 게시 | **키 없음** — `higgsfield auth login` | §0-1 |
| ElevenLabs | 내레이션 TTS, 전사 STT | `ELEVENLABS_API_KEY` + `voice.json`의 `voice_id` | §0-2 |
| Anthropic | 글 생성 | `ANTHROPIC_API_KEY` | §0-3 |
| Supabase | 미디어 공개 URL 호스팅 | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | §0-4 |
| Meta | Threads·Instagram 발행 | `THREADS_*`, `IG_*` | §0-5 / Step 4 |
| Google Cloud | YouTube 업로드 | `YOUTUBE_*` | §0-6 / Step 6 |

### 0-1. Higgsfield (이미지·영상·TikTok)

가입 / Sign up: <https://higgsfield.ai> 에서 본인 계정을 만든다. 크레딧은 구독 플랜에
포함되거나 별도 구매다. 생성은 전부 본인 잔액에서 나간다.

**붙여넣을 키가 없다.** Higgsfield는 이 셋업에서 유일하게 크레덴셜 파일을 안 쓰는 서비스다 —
CLI가 OAuth(PKCE) 로그인을 들고 있고 토큰 갱신도 CLI가 한다. 팩토리는 토큰이 필요한
순간마다 `higgsfield auth token`을 물어서 그 자리에서 쓴다.

```sh
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
# Windows: 설치는 공식 안내를 따른다 → https://github.com/higgsfield-ai/cli
higgsfield auth login          # 브라우저가 열린다
higgsfield account status      # 확인 명령: 이메일 · 플랜 · 잔여 크레딧
```

`higgsfield account status`가 이메일과 잔액을 찍으면 끝이다. 값은 읽어 주지 말고 "로그인
확인됨"만 전한다. `factory doctor`의 `higgsfield login` 행도 같은 것을 본다 (토큰 값은
절대 찍지 않는다). 사용자에게 `higgsfield auth token` 출력을 채팅에 붙여넣게 하지 말 것 —
그건 유효한 액세스 토큰이다.

**크레딧 감각 (2026-10-14 `higgsfield generate cost` 실측)** — 포맷 yaml의
`credit_cap_per_item`이 이 숫자를 기준으로 잡혀 있다:

| 쓰임 | 모델 | 실측 비용 | 해당 포맷 캡 |
|---|---|---|---|
| OG·썸네일 카드 | `gpt_image_2` | 6.5 cr/장 | `og` 8, `thumbnail` 8 |
| 상품컷·카드뉴스 | `gpt_image_2` | 6.5 cr/장 | `product_shot` 10, `cardnews` 30 |
| 레퍼런스 인물 스틸 | `nano_banana_pro` | 2 cr/장 | 영상 캡에 포함 |
| Soul 인물 스틸 | `text2image_soul_v2` | 0.12 cr/장 (2k) | 영상 캡에 포함 |
| 영상 테이크 | `seedance_2_0` 720p | 4.5 cr/초 (12초 = 54) | `talking_head`·`pov` 150 |
| 영상 테이크 | `seedance_2_0` 1080p | 12 cr/초 (12초 = 144) | 캡 초과하므로 기본 아님 |

24초 토킹헤드 1편 = 스틸 0.12 + 테이크 2개 108 ≈ **108 크레딧**. 같은 편을 1080p로 뽑으면
288이라 150 캡에 안 들어간다 — 그래서 720p로 뽑고 조립에서 1080x1920으로 올린다.

호출 전에 항상 견적을 받고 캡과 대조한다. 넘으면 호출 자체를 막는다:

```sh
factory gen video --brief b_... --dry-run      # 단계별 견적만, 과금 없음
factory gen image --brief b_... --dry-run      # argv만, 호출 없음
```

**TikTok 커넥터**: 직접 TikTok API는 미심사 앱이면 비공개 게시만 되므로 Higgsfield MCP를
경유한다. `tiktok_connect`로 본인 TikTok 계정을 연결하고 `connector_id`는
`platforms.json`에 넣는다. **크레덴셜은 넣을 게 없다.**

**인증은 CLI, 게시 호출만 MCP 엔드포인트다.** CLI에 `tiktok` 서브커맨드가 없어서 게시는
MCP 엔드포인트를 그대로 쓰지만, bearer는 저장된 키가 아니라 매 요청 직전의
`higgsfield auth token`이다. 그래서 예약 발행이 몇 시간 대기해도 토큰 만료로 죽지 않고,
`factory token refresh --platform tiktok` 같은 명령은 없다 (있을 필요가 없다).
로그아웃 상태면 `HIGGSFIELD_LOGIN_REQUIRED`로 멈추고 큐에 남는다.

흔한 에러 / Common errors:

| 코드 | 뜻 | 대응 |
|---|---|---|
| `HIGGSFIELD_CLI_MISSING` | CLI가 `$PATH`에 없음 | 위 설치 명령. Windows는 `.exe`까지 탐색하니 PATH만 맞으면 된다 |
| `HIGGSFIELD_LOGIN_REQUIRED` | `auth token`이 토큰을 안 줌 (로그아웃·만료) | `higgsfield auth login` |
| `HIGGSFIELD_FAILED` | CLI가 0이 아닌 코드로 종료 | 상세는 `~/.contents-factory/logs/`의 해당 파일 |
| `HIGGSFIELD_COST_UNREADABLE` | 견적을 못 읽음 | 모델명·파라미터 확인, `higgsfield model get <model>` |
| `BUDGET_EXCEEDED` | 포맷 캡 초과 | 캡을 올리든가 길이·해상도를 줄인다. 호출은 안 나갔다 |
| `ASPECT_UNSUPPORTED` | 비율 형식이 `w:h`가 아님 | 포맷 yaml의 `aspect` 확인 |
| `CONNECTOR_ID_MISSING` | `platforms.json`에 `tiktok.connector_id` 없음 | `tiktok_connect` 후 기입 |

### 0-2. ElevenLabs (TTS·STT)

가입 / Sign up: <https://elevenlabs.io>. API 키는 프로필 → API Keys에서 발급한다.
유료 플랜은 월 글자 수 쿼터제이고, 음성 복제(Instant Voice Cloning)는 유료 플랜부터다.
현재 요금·쿼터는 공식 pricing 페이지를 보라고 안내한다 — 여기 숫자를 적지 않는다.

```sh
# 키는 사용자가 직접 넣는다
#   ~/.contents-factory/profiles/<name>/credentials.env
#   ELEVENLABS_API_KEY=...
chmod 600 ~/.contents-factory/profiles/<name>/credentials.env
factory gen video --brief b_... --stage tts --dry-run   # 확인: 글자 수와 상한만 찍는다
```

**voice_id 만드는 법** — 둘 중 하나:

1. **Voice Design**: 원하는 목소리를 글로 묘사해서 생성한다. 샘플 녹음이 필요 없다.
2. **Instant Voice Cloning**: 본인(또는 권리 있는) 음성 샘플을 올려 복제한다. 유료 플랜
   필요, 타인 음성 무단 복제 금지.

만든 뒤 Voice Lab에서 voice id를 복사해 프로필의 `voice.json`에 넣는다:

```json
{"provider": "elevenlabs", "voice_id": "<여기>", "model": "eleven_v3",
 "stability": 0.45, "style": 0.35}
```

`model`은 `eleven_v3`를 쓴다. 구형 다국어 모델은 오프캠 내레이션에서 로봇처럼 들렸다.

**글자 소비 감각**: 24초 토킹헤드 1편의 대본이 약 320자였다. 포맷 yaml의
`limits.tts_chars_max`(기본 2000)가 편당 상한이고, 넘으면 `TTS_CHARS_EXCEEDED`로 호출
전에 멈춘다. 같은 문장을 다시 합성하면 캐시에 걸려 **0자**다.

흔한 에러: `ENV_MISSING ELEVENLABS_API_KEY` / `VOICE_ID_MISSING`(voice.json 비어 있음) /
`HTTP_401`(키 틀림) / `TTS_CHARS_EXCEEDED`.

> STT도 같은 키를 쓴다 (`stt_wpm` 게이트). 키가 없으면 `$PATH`의 `whisper`로 폴백하고,
> 둘 다 없으면 `STT_ENGINE_MISSING`이다.

### 0-3. Anthropic (글 생성)

가입 / Sign up: <https://console.anthropic.com> 에서 본인 계정과 API 키. 사용량 과금이다.

```sh
#   credentials.env 에 ANTHROPIC_API_KEY=...
factory gen text --brief b_... --dry-run   # 확인: 캐싱 상태와 프리픽스 토큰 추정치
```

흔한 에러: `ENV_MISSING ANTHROPIC_API_KEY` / `HTTP_401` / `HTTP_429`(레이트리밋).

### 0-4. Supabase (미디어 호스팅)

Threads·Instagram·TikTok import 경로는 플랫폼 서버가 **공개 HTTPS URL**을 직접 가져가므로
로컬 경로로는 발행이 안 된다. 본인 Supabase 프로젝트의 공개 버킷을 쓴다. 버킷 정책과
7일 수명 잡은 README의 "미디어 호스팅 버킷 / BYO media bucket" 절에 SQL까지 있다.

> 남의 버킷을 공유하지 않는다 (P0-SPEC §10 결정7). 남의 프로젝트에 내 미디어를 올리면
> 내 발행이 그쪽 요금·수명·삭제 정책에 묶인다.

버킷을 쓰지 않을 거면 큐 적재 때 `media[].public_url`을 직접 채우면 된다.

흔한 에러: `ENV_MISSING SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY` / `MEDIA_URL_REQUIRED`.

### 0-5. Meta (Threads·Instagram)

경로가 둘이다:

- **남의 앱에 테스터로 들어가는 경우** — Step 4의 테스터 등록 절차를 그대로 따른다.
- **본인 앱을 만드는 경우** — <https://developers.facebook.com> 에서 앱을 만들고,
  Threads API / Instagram Graph API 제품을 추가하고, 리다이렉트 URL을 등록하고, 본인
  계정을 앱 역할에 넣는다. 개발 모드 앱은 **테스터 역할 계정만** OAuth가 되므로 본인
  앱이어도 Step 4의 2단계(플랫폼 안에서 초대 수락)는 똑같이 필요하다. 일반 공개 발행은
  앱 심사를 받아야 하고, 그건 이 레포 범위 밖이다.

둘 중 어느 쪽이든 토큰은 60일이고 `factory token refresh`가 갱신한다.

### 0-6. Google Cloud (YouTube)

본인 GCP 프로젝트를 쓴다. 절차는 Step 6에 있다. 미검증 프로젝트는 **업로드가 무조건
private**로 고정된다 — 검토 후 예약 공개 흐름에는 문제없지만, 사용자에게 먼저 말해야 한다.

---

## Step 1: language (the OS is already answered)

```
uv sync
factory i18n get interview.common.intro
```

The home was created by `factory init --os ...` in the first step. Now ask two things and
write them into `factory.toml`:

- `ui_lang` (ko/en): the language you ask questions in.
- `content_lang`: the language the output is written in. Separate on purpose.

## Step 2: profile

```
factory profile list
factory profile add <name> --from _example
factory profile show <name>
```

`_example` is a demo voice. Tell the user to replace `persona.md`, `brand.md`,
`forbidden.txt` and `closers.txt` with their own, then run `/profile` for the full
interview. Edit profile files directly (they live in the repo or the home profiles dir and
are not runtime state); everything under `queue/`, `briefs/` and `state/` is CLI-only.

## Step 3: platform keys

Keys go in `<home>/profiles/<name>/credentials.env`, one `KEY=value` per line, owner-only
(`chmod 600` on macOS and Linux, `icacls` on Windows — the CLI does it). The home is
`~/.contents-factory`, or `%USERPROFILE%\.contents-factory` on Windows. The README table
is canonical; this is the same list with the setup path.

Threads is the one platform that writes its own keys: `factory auth threads` runs the
OAuth flow and records the token for you (step 4). Everything else is hand entered.

| Platform | credentials.env | platforms.json |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `slots`, `daily_cap`, `late_policy` |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | same |
| TikTok | (none — `higgsfield auth login`) | `connector_id` |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `slots` |
| Naver blog | (none) | `blog_id`, `backend` |
| Media hosting | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | - |
| Text generation | `ANTHROPIC_API_KEY` | - |

Verify without revealing anything:

```
factory publish threads --item q_... --dry-run    # ENV_MISSING names the missing keys
```

## Step 4: Threads and Instagram, the tester-role dance

A Meta app in development mode only lets **tester-role accounts** through OAuth. Getting a
new account connected, in order, and every step is required:

1. **App roles**: Meta app dashboard → App roles → Roles → **Add people** → pick
   *Threads tester* (and *Instagram tester* for IG) → enter the target account.
2. **Accept the invite from inside the platform, not from email**: log in as the target
   account → Threads app → **Settings → Website permissions → Invites** (IG: Settings →
   Apps and websites → Tester invites) → accept. An unaccepted invite looks identical to no
   invite at all: OAuth fails with a generic error.
3. **OAuth** — this is the command, not a manual curl:

   ```
   factory auth threads --profile <name> --app-id <APP_ID> --redirect <REDIRECT_URI>
   factory auth threads --profile <name> --url-only        # print the URL, exchange later
   ```

   It prints the consent URL, opens a browser, and waits for you to paste the **whole
   redirected address** back in. The paste is hidden; the code never appears on screen.
   Then it exchanges the code, calls `/me`, and writes `THREADS_ACCESS_TOKEN`,
   `THREADS_USER_ID` and `THREADS_TOKEN_ISSUED_AT` into the profile's `credentials.env`
   at mode 600. It prints the connected handle and nothing else.

   Two things decide whether this works:
   - the **redirect URL must match the registered one character for character** —
     trailing slash, http vs https, port, everything. A mismatch is the single most
     common failure, and the command echoes the URI it used so you can compare.
   - the browser must be signed in as the **target** account. Another session hands you
     that other account's token, which is the worst outcome in the whole setup: the
     factory will publish to the wrong account and nothing will look broken.
4. Where the app secret lives decides the exchange route, and the command picks on its
   own:
   - `THREADS_APP_SECRET` present in `credentials.env` or the environment → the exchange
     runs locally, nothing leaves the machine except the Meta calls.
   - no local secret → the code goes to `--exchange-url` (a small endpoint that holds the
     secret, e.g. `https://<your-domain>/api/threads/exchange`) which returns the
     long-lived token. The request body is only `{code, redirect_uri, app_id}`.

   Tell the user which route ran; the command prints it. If you want the local route, the
   secret goes in `credentials.env` as `THREADS_APP_SECRET`, never in chat.
5. The command refuses to save a token whose grant is missing `threads_basic` or
   `threads_content_publish` (`SCOPE_MISSING`). `debug_token` is the only reliable scope
   check: the dashboard token generator reissues inside the account's **old** grant, so a
   newly added permission can be "ready" in the dashboard and still absent from the token.
6. Confirm with a dry run, then one real post from a test account.

Instagram has the same shape but the exchange is not automated yet:
`factory auth instagram --profile <name> --app-id <APP_ID>` prints the consent URL plus
the steps that are still manual, and `connect()` raises `AUTH_NOT_IMPLEMENTED`. The
account must be a professional (business or creator) account, and `IG_USER_ID` is the
content publishing node id, not the handle and not the Threads user id.

Tokens live 60 days. `factory token refresh --platform threads --profile <name>` renews
them; the tick warns from 7 days out. Refreshing is the single most common outage point in
the system it was ported from, so do not skip the warning.

If the app is already review-approved for public use, steps 1 and 2 are unnecessary.
Confirm by actually connecting a non-tester account before telling the user it works.

## Step 5: TikTok

Direct TikTok API posting from an unreviewed app can only produce private posts, so the
route is the Higgsfield MCP connector.

1. `higgsfield auth login`, if it has not been run already (Step 0-1). This is the only
   credential the TikTok route needs.
2. Connect the TikTok account in Higgsfield (`tiktok_connect`).
3. Read the connector id and put it in `platforms.json` under `tiktok.connector_id`.
   Nothing goes into `credentials.env` for TikTok.
4. `factory publish tiktok --item q_... --dry-run` should show `tiktok_prepare_publish`
   with your connector id masked to its first four characters, and the bearer printed as
   `<REDACTED:higgsfield auth token>` — that placeholder is the point: the token is
   fetched per request, not stored.

If a live publish returns `HIGGSFIELD_LOGIN_REQUIRED`, the CLI login is gone, not the
item: nothing was submitted, so the item goes back to `ready` on its own after
`higgsfield auth login`.

## Step 6: YouTube

Bring your own GCP project. An unverified project keeps every upload private no matter what
you pass, which is fine for review-then-schedule but must be said out loud.

1. GCP → OAuth consent screen → add the Google account as a test user.
2. Create an OAuth client (Desktop app). Scopes: `youtube.upload`, `youtube.readonly`.
3. Run the loopback flow once, store the refresh token in `credentials.env`.
4. `publishAt` only works while `privacyStatus` is private and the timestamp is in the
   future. A past timestamp is dropped rather than turned into an instant public post.

## Step 7: Naver blog, and which browser holds the session

There is no public write API, so publishing drives a logged-in editor. Two backends can
hold that login, and `platforms.json` picks one (`naver_blog.backend`). Left unset it
follows the `os` from Step 0: macOS gets `aside`, everything else gets `playwright`.

| backend | session lives in | OS |
|---|---|---|
| `aside` | the aside browser the user is signed into | **macOS 15.0 or later only** |
| `playwright` | a persistent Chromium profile under `<home>/state/naver-profile/` | macOS, Windows, Linux |

aside is macOS only — its docs say so outright ("Aside supports macOS 15.0 or later"),
and while the aside *CLI* has a Windows installer, the browser it drives does not exist
there. So Naver on Windows is not unsupported, it is the other backend.

**aside backend (macOS):**

1. Install `aside` and sign in to Naver in that browser, once.
2. Put the blog id in `platforms.json` under `naver_blog.blog_id`.
3. Leave exactly one editor tab open, or none. Two editor tabs is `MULTIPLE_EDITOR_TABS`.

**playwright backend (Windows, Linux, or macOS by choice):**

```powershell
npm install -g playwright
npx playwright install chromium       # the browser binary is a separate download
factory naver login                   # a window opens; the user signs in themselves
factory naver status                  # boolean, never a cookie value
```

1. `"backend": "playwright"` in `platforms.json` (or leave it unset on a Windows home).
2. `factory naver login` opens the Naver login page and waits. **Do not type the
   credentials for them** and do not ask for the password: they sign in in that window,
   2FA included, and the session stays in the profile directory.
3. `PLAYWRIGHT_MISSING` means node found no package anywhere — the loader tries a local
   resolution and then `npm root -g`, because ESM ignores `NODE_PATH`. For an install in
   an unusual place, point `FACTORY_PLAYWRIGHT_MODULE` at the package directory or its
   entry file. `PLAYWRIGHT_LOAD_FAILED` means it is installed but broken.
   `PLAYWRIGHT_BROWSER_MISSING` means the package is there and Chromium is not:
   `npx playwright install chromium`.
4. Verify with `factory publish naver_blog --item q_... --dry-run`; the plan names the
   backend and the profile directory.

Either way, no session means `NAVER_SESSION_MISSING` and the item stays queued. That is
not a failure to retry blindly; it is a prompt to log in.

> The playwright publish path has been exercised to dry run and login only. Say that
> plainly: the first real post should go to a test blog with the user watching. A failure
> after the publish button is `PUBLISH_OUTCOME_UNCERTAIN:<phase>` and is never retried
> automatically.

## Step 8: media hosting

Threads, Instagram and the TikTok import route all fetch media from a public URL, so local
paths cannot be posted. Follow the README "BYO media bucket" section: the user's own
Supabase project, a public bucket, a 7-day cleanup job. Or they fill
`media[].public_url` themselves and the factory passes it through.

## Step 9: scheduler

`--runner auto` is the default and reads the `os` from Step 0, so this is usually one
command on every platform:

```
factory schedule install           # auto: launchd / schtasks / cron
factory schedule status
factory schedule tick --dry-run
```

Forcing one is still possible, and the three are not interchangeable:

```
factory schedule install --runner launchd     # macOS, a LaunchAgent plist
factory schedule install --runner schtasks    # Windows Task Scheduler, /SC MINUTE
factory schedule install --runner cron        # Linux, a fenced crontab block
```

On Windows the task carries `FACTORY_HOME` inside its action line, because a scheduled
task has no environment of its own, and it runs `pythonw.exe` so no console window flashes
every interval. `SCHTASKS_WRITE_FAILED` usually means the shell is not allowed to create
tasks — rerun it from an elevated PowerShell.

Finish by reading back, in four lines: the OS and runtime home, which profile, which
platforms are connected, and what the next seven days look like. Never list key values.
If `factory doctor` still has a `blocking:` line, that is the fifth line and it is the
next thing to do.
