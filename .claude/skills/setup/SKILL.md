---
name: setup
description: >
  First-run setup: pick interview and content languages, create the runtime home, register a
  profile, write credentials.env, and connect each platform. Covers the Meta tester-role
  dance for Threads and Instagram, the Higgsfield connector for TikTok, a BYO GCP project
  for YouTube, and the logged-in browser session for Naver. Use when the user has just
  cloned the repo, is adding a platform, or is hitting auth errors like ENV_MISSING,
  CONNECTOR_ID_MISSING or NAVER_SESSION_MISSING. 트리거 — "/setup", "셋업", "처음 설정",
  "키 넣어야 해", "플랫폼 연결", "로그인 안 됨".
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
| Higgsfield | 이미지·영상 생성, TikTok 게시 | (CLI 로그인) + `HIGGSFIELD_MCP_TOKEN` | §0-1 |
| ElevenLabs | 내레이션 TTS, 전사 STT | `ELEVENLABS_API_KEY` + `voice.json`의 `voice_id` | §0-2 |
| Anthropic | 글 생성 | `ANTHROPIC_API_KEY` | §0-3 |
| Supabase | 미디어 공개 URL 호스팅 | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | §0-4 |
| Meta | Threads·Instagram 발행 | `THREADS_*`, `IG_*` | §0-5 / Step 4 |
| Google Cloud | YouTube 업로드 | `YOUTUBE_*` | §0-6 / Step 6 |

### 0-1. Higgsfield (이미지·영상·TikTok)

가입 / Sign up: <https://higgsfield.ai> 에서 본인 계정을 만든다. 크레딧은 구독 플랜에
포함되거나 별도 구매다. 생성은 전부 본인 잔액에서 나간다.

```sh
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
higgsfield auth login          # 브라우저가 열린다
higgsfield account status      # 확인 명령: 이메일 · 플랜 · 잔여 크레딧
```

`higgsfield account status`가 이메일과 잔액을 찍으면 끝이다. 값은 읽어 주지 말고 "로그인
확인됨"만 전한다.

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
경유한다. `tiktok_connect`로 본인 TikTok 계정을 연결하고, `connector_id`는
`platforms.json`에, MCP 토큰은 `credentials.env`의 `HIGGSFIELD_MCP_TOKEN`에 넣는다.

> ⚠️ **현재 MCP 토큰은 정적이고 수 시간 만에 만료된다.** 만료되면 발행이 `HTTP_401`로
> 멈춘다. 자동 갱신은 아직 없다 — **TODO: `factory token refresh --platform tiktok`**.
> 그때까지는 발행 전에 토큰을 다시 받아 넣는 수밖에 없다. 장기 예약 발행을 TikTok에
> 걸어두지 말라고 사용자에게 미리 말할 것.

흔한 에러 / Common errors:

| 코드 | 뜻 | 대응 |
|---|---|---|
| `HIGGSFIELD_CLI_MISSING` | CLI가 `$PATH`에 없음 | 위 설치 명령 |
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

## Step 1: home and language

```
uv sync
factory init
factory i18n get interview.common.intro
```

Ask two things and write them into `factory.toml`:

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

Keys go in `~/.contents-factory/profiles/<name>/credentials.env`, one `KEY=value` per line,
`chmod 600`. The README table is canonical; this is the same list with the setup path.

Threads is the one platform that writes its own keys: `factory auth threads` runs the
OAuth flow and records the token for you (step 4). Everything else is hand entered.

| Platform | credentials.env | platforms.json |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `slots`, `daily_cap`, `late_policy` |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | same |
| TikTok | `HIGGSFIELD_MCP_TOKEN` | `connector_id` |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `slots` |
| Naver blog | (none) | `blog_id` |
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

1. Connect the TikTok account in Higgsfield (`tiktok_connect`).
2. Read the connector id and put it in `platforms.json` under `tiktok.connector_id`.
3. Put the MCP token in `credentials.env` as `HIGGSFIELD_MCP_TOKEN`.
4. `factory publish tiktok --item q_... --dry-run` should show `tiktok_prepare_publish`
   with your connector id masked to its first four characters.

## Step 6: YouTube

Bring your own GCP project. An unverified project keeps every upload private no matter what
you pass, which is fine for review-then-schedule but must be said out loud.

1. GCP → OAuth consent screen → add the Google account as a test user.
2. Create an OAuth client (Desktop app). Scopes: `youtube.upload`, `youtube.readonly`.
3. Run the loopback flow once, store the refresh token in `credentials.env`.
4. `publishAt` only works while `privacyStatus` is private and the timestamp is in the
   future. A past timestamp is dropped rather than turned into an instant public post.

## Step 7: Naver blog

There is no public write API. Publishing drives the logged-in editor through the `aside`
browser.

1. Install `aside` and sign in to Naver in that browser, once.
2. Put the blog id in `platforms.json` under `naver_blog.blog_id`.
3. Leave exactly one editor tab open, or none. Two editor tabs is `MULTIPLE_EDITOR_TABS`.
4. No session means `NAVER_SESSION_MISSING` and the item stays queued. That is not a
   failure to retry blindly; it is a prompt to log in.

## Step 8: media hosting

Threads, Instagram and the TikTok import route all fetch media from a public URL, so local
paths cannot be posted. Follow the README "BYO media bucket" section: the user's own
Supabase project, a public bucket, a 7-day cleanup job. Or they fill
`media[].public_url` themselves and the factory passes it through.

## Step 9: scheduler

```
factory schedule install --runner launchd     # macOS
factory schedule install --runner cron        # Linux
factory schedule status
factory schedule tick --dry-run
```

Finish by reading back, in three lines: which profile, which platforms are connected, and
what the next seven days look like. Never list key values.
