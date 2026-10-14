# contents-factory

글·이미지·영상 제작 + 5개 플랫폼(Threads·Instagram·TikTok·YouTube·네이버 블로그) 자동발행
스킬팩. 페르소나는 프로필로 갈아끼운다. 기획 `docs/PLAN.md`, 구현 스펙 `docs/P0-SPEC.md`.

Interview-driven content generation plus scheduled publishing to five platforms. The persona
lives in a profile directory, not in the code. Plan: `docs/PLAN.md`, P0 spec: `docs/P0-SPEC.md`.

## 설치 / Install

```sh
uv sync
uv run factory init        # creates ~/.contents-factory and copies factory.toml
uv run pytest
```

Python 3.11+. `node` is required for the Naver publisher, `aside` for its browser session.

## 🚨 상태파일은 CLI로만 / State is CLI-only

`~/.contents-factory/` 밑(`queue/`, `briefs/`, `state/`)은 **`factory` 명령만** 쓴다.
세션이나 사람이 손으로 json을 고치면 스케줄러가 죽는다. 전용 명령이 없는 변경이 필요하면
명령을 먼저 추가한다.

Everything under `~/.contents-factory/` is written only by `factory` commands. Hand editing
the json is how the scheduler dies.

## 명령 / Commands

```
factory init
factory profile add <name> [--from _example] | list | show [name]
factory brief new --kind text --format threads_post [--profile X] [--answers file|-]
                  [--force]   # id 는 b_<UTC초>_<4자>, 기존 id 덮으려면 --force
factory brief show --brief b_...
factory gen text  --brief b_... [--dry-run]
factory gen image --brief b_... [--dry-run]   # 실제 higgsfield 호출, --dry-run은 argv만
factory gen video --brief b_... [--stage script|still|clip|tts|captions|assemble|all]
                  [--dry-run] [--force]       # 단계별 실행, 산출물 있으면 건너뜀
factory gate run (--brief b_... | --text "...") [--format threads_post] [--gate repeat_guard]
                 [--box x,y,width,height[,label]]      # caption_safe_zone 입력
                 [--media path]                        # 영상 게이트 대상 (기본: brief 산출물)
                 [--item q_...]                        # duplicate_copy 자기 제외
factory queue add --platform threads [--brief b_... --output 0] [--text "..."]
                  [--media path --public-url url] [--slot next|2026-10-15T12:00:00Z]
                  [--extra key=value ...]   # title·tags·privacy·privacy_level·mode·
                                            # topic_tag·replies·ig_shape·cover_url
factory queue list [--pending] [--json] | approve | unapprove | arm --reason "..." | disarm | purge
factory queue reschedule q_... --slot next|<ISO>   # 슬롯 이동 (발행본·미확정 배송은 거부)
factory queue reopen --item q_... --reason "..."   # unknown phase 해제 (사람 확인 후)
factory media host --item q_... [--index 0] [--dry-run]  # 로컬 미디어 → 공개 URL,
                                                       # media[].public_url 기록
factory publish <platform> --item q_... [--dry-run]
factory schedule install|remove|status [--runner launchd|cron] | tick [--dry-run]
factory auth threads   --profile X [--app-id ID] [--redirect URI] [--exchange-url URL]
                       [--url-only] [--no-open]     # OAuth, writes THREADS_* keys
factory auth instagram --profile X [--app-id ID] [--redirect URI]  # consent URL only (P1)
factory token refresh [--profile X] [--platform threads|instagram] [--dry-run]
factory ledger summary [--days 30]
factory i18n get <key> [--lang ko|en] | keys | missing --lang ko
factory formats [--kind text|image|video] [--format <name>] [--lang ko|en]
```

`python -m factory ...` 도 같은 엔트리다.

## 발행 규칙 / Publishing rules

- **승인 ≠ 발행.** 슬롯 시각(UTC)에 tick이 발행한다. `factory publish`의 실발행은 수동
  예외이고, 승인·무장이 둘 다 돼 있어야 받는다.
- **armed=false면 approved여도 안 나간다.** `arm`은 근거 문자열이 필수다.
- 지각분은 `late_policy`: `carry_once`(다음 슬롯 1회 이월, 기본) 또는 `drop`. 이월은 1회뿐이고
  두 번째는 폐기 + 승인 해제다. 무상한 지각 자동발행은 없다.
- 프로필·플랫폼별 `daily_cap`을 넘으면 그 런은 건너뛴다.
- 지각 유예는 **900초 고정**이다(§10 결정5). 슬롯 +15분 안이면 그대로 발행하고, 넘으면
  `late_policy`로 넘어간다.
- P0에는 자동승인이 없다(§10 결정1). 승인은 사람이 한다. P1에서 프로필 옵션
  `auto_approve`(기본 false)가 들어오더라도 `armed` 게이트는 그대로 남는다.
- `repeat_guard`는 큐 적재 때 통과했어도 **발행 직전에 다시** 돈다.
- 이 팩토리로 넘어온 프로필은 `factory.toml`의 `[extra_corpus]`에 **이전 파이프라인의 발행
  이력**을 걸어둔다. 안 걸면 가드가 자기 큐만 보므로 이미 태운 문구가 새 문구로 통과한다.
  프로필명 키(`"*"` = 전 프로필), 값은 경로 리스트. `.jsonl`·`.json`·평문을 읽고 행의
  `text`를 본문, `published_at`을 시각으로 쓴다. 캐시 시그니처에 이 파일들의 mtime·size가
  들어가서 외부 코퍼스가 자라면 인덱스가 바로 무효화된다.
- 토큰 만료 7일 전부터 tick이 경고를 띄운다.
- 네이버는 로그인된 aside 브라우저 세션이 필요하다. 없으면 `NAVER_SESSION_MISSING`으로
  큐에 그대로 남는다.

### 중복발행 방어 (delivery phase)

큐 아이템은 `ready → creating → container → publishing → published` 상태를 지난다.
`creating`·`publishing`에서 런이 죽으면(응답 유실) 그 아이템은 `unknown`으로 주차되고
**자동 재시도하지 않는다** — 재발행이 중복 게시를 만들 수 있기 때문이다.

- `container` 상태면 다음 tick이 컨테이너를 **재사용**해서 publish 단계만 다시 한다.
  24시간 지난 컨테이너는 1회 재생성한다(`container_regen`).
- 실패 코드가 "아무것도 제출되지 않았음"을 증명하는 것(4xx·사전검증·세션없음)이면
  `ready`로 되돌려 재시도한다. 5xx·네트워크 끊김·미등록 코드는 전부 `unknown`이다.
- `content_sha256`이 박힌 뒤 본문·미디어가 바뀌면 `DELIVERY_CONTENT_CHANGED`로 멈춘다.
- `unknown` 해제는 사람이 플랫폼을 직접 확인한 뒤:
  `factory queue reopen --item q_... --reason "<확인 내용>"`.
  `container_id`나 `published_id`가 남아 있으면 거부한다(`DELIVERY_MAY_BE_PUBLISHED`).
- `phase=published`인데 `published` 플래그가 없으면 tick이 플래그만 복구한다(재발행 안 함).

## 영상 파이프라인 / Video pipeline

```sh
uv sync --extra video      # numpy + pillow. 없으면 GATE_DEPS_MISSING
factory gen video --brief b_... --dry-run      # 단계별 비용 견적까지
factory gen video --brief b_... --stage still  # 한 단계만
factory gen video --brief b_...                # 전부
```

단계는 `script → still → clip → tts → captions → assemble`. 명령이 단계마다 끊기므로
중간 산출물은 `~/.contents-factory/outputs/<brief_id>/`에 쌓이고 `stages.json`이 뭐가
있는지 기록한다. 산출물이 이미 있으면 그 단계는 건너뛴다 — 재렌더는 `--force`로만, 원장에
별도 과금으로 남는다.

- **편당 캡.** 유료 호출마다 프로바이더에 견적을 묻고 `credit_cap_per_item`(포맷 yaml)에서
  원장이 이미 기록한 금액을 뺀 잔액과 대조한다. 넘으면 `BUDGET_EXCEEDED`로 **호출 전에**
  멈춘다. 취소된 작업도 과금되기 때문이다.
- **재롤 없음.** 실패한 테이크는 폐기하고 예비안을 넣는다 (`docs/rules/video-rules.md` §8–9).
- **골든 프롬프트.** `script` 단계가 포맷 템플릿의 슬롯만 바뀌었는지 검사한다. 슬롯 밖
  문장이 다르면 `SCRIPT_GATE_FAILED`로 렌더하지 않는다.
- **멀티테이크.** 클립 모델 상한(15s)보다 긴 영상은 연속 테이크로 나누고, 뒤 테이크의 시작
  이미지는 **앞 테이크의 마지막 프레임**이다. 같은 스틸로 두 번 뽑으면 접합부에서 배경이
  처음으로 되돌아간다.
- **해상도는 예산 레버.** 24초를 1080p로 뽑으면 288크레딧, 720p + 조립 시 업스케일이면
  108크레딧이다. `limits.clip_resolution`.
- **자막 시계.** 자막 타이밍은 실제로 재생되는 오디오를 따른다 (`limits.caption_source`).
  테이크가 자기 음성을 갖고 있으면 그 테이크를 전사해서 맞춘다.

페르소나 고유값(voice id, Soul id, 레퍼런스 이미지, 엔드카드 문구)은 전부 프로필에서 온다.
엔드카드 같은 고정 문구는 `brand.md`의 `- End card: ...` 줄이 슬롯 값이 된다.

### 영상 게이트 / Video gates

| 게이트 | 잡는 것 |
|---|---|
| `stt_wpm` | 실발화 wpm, 승인 대본 대비 충실도, 필러·중복어. 전사는 **현재 테이크**에서 새로 뜬다 (오디오 해시로 캐시 무효화) |
| `limb_motion` | 테이크 내내 안 움직이는 팔. 몸통 기준 상대 벡터의 표준편차로 판정, 팔이 프레임 밖이면 PASS |
| `bg_motion` | `FROZEN_BG`(배경이 안 흐름) · `MORPH_BG`(흐르지만 모양이 변형) · `FRAME_JUMP`(한 프레임 순간이동) |
| `identity` | 레퍼런스 대비 얼굴 길이/폭 비. 비율만으로 끝내지 않고 몽타주를 같이 내놓는다 |

`factory gate run --brief b_...`는 brief의 렌더 결과를 자동으로 찾고, `caption_safe_zone`에
렌더러가 실제로 그린 박스를 넘긴다 — 손으로 좌표를 치지 않는다.

## 프로필 / Profile

`profiles/<name>/` (레포 예시) 또는 `~/.contents-factory/profiles/<name>/` (운영본).

| 파일 | 내용 |
|---|---|
| `persona.md` | 정체·말투·금지 표현·시그니처. system 프리픽스에 통째로 들어간다 |
| `brand.md` | 로고·색·폰트·레퍼런스 우선순위 |
| `voice.json` | `{provider, voice_id, model, stability, style}` |
| `soul.json` | `{provider, soul_id, ref_image, identity_rules}` |
| `platforms.json` | 플랫폼별 `{enabled, account_id, slots, late_policy, cta_rotation, daily_cap, connector_id, blog_id}` |
| `forbidden.txt` | 금지어 한 줄 1개 |
| `closers.txt` | 반복 감시할 고정 클로저 문구 |
| `writer_rules.md` | 추가 작성 규칙 (system 프리픽스) |
| `locale` | content_lang 기본값 |
| `credentials.env` | **런타임 홈에만.** chmod 600. 키 목록은 아래 |

`slots`는 `"12:00Z"` 꼴 UTC만 받는다.

## 🔑 외부 서비스 계정은 전부 본인 것 / Bring your own accounts

**이 레포에는 공유 계정이 없다.** 클론한다고 어떤 크레딧도 따라오지 않는다. 아래 서비스는
전부 사용자 본인이 가입하고, 본인이 결제하고, 본인 키를 넣는다. 설치 중 안내가 필요하면
`/setup` 스킬의 §0이 서비스별 가입 위치·로그인 명령·확인 명령을 다 들고 있다.

**Nothing here is a shared account.** Cloning this repo grants no credits on anything. Every
service below is signed up for, paid for and keyed by you. The `/setup` skill's §0 walks
through each one.

| 서비스 | 필요한 이유 | 없으면 | 키 이름 |
|---|---|---|---|
| [Higgsfield](https://higgsfield.ai) | 이미지·영상 생성, TikTok 게시 | 이미지·영상 전부 불가 | CLI 로그인 + `HIGGSFIELD_MCP_TOKEN` |
| [ElevenLabs](https://elevenlabs.io) | 내레이션 TTS, 발화 전사 | `tts` 단계·`stt_wpm` 게이트 불가 | `ELEVENLABS_API_KEY` |
| [Anthropic](https://console.anthropic.com) | 글 생성 | `gen text` 불가 | `ANTHROPIC_API_KEY` |
| [Supabase](https://supabase.com) | 미디어 공개 URL | 이미지·영상 발행 불가 (텍스트는 가능) | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| [Meta](https://developers.facebook.com) | Threads·Instagram 발행 | 두 플랫폼 발행 불가 | `THREADS_*`, `IG_*` |
| [Google Cloud](https://console.cloud.google.com) | YouTube 업로드 | YouTube 발행 불가 | `YOUTUBE_*` |

값은 어떤 출력에도 나오지 않는다 (`--dry-run` 페이로드엔 `<REDACTED:KEY>`). 문서·커밋·로그에
값을 쓰지 않는다 — 키 **이름**만 쓴다.

### Higgsfield 로그인 / Higgsfield login

```sh
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
higgsfield auth login
higgsfield account status      # 이메일 · 플랜 · 잔여 크레딧
```

크레딧 실측 (2026-10-14 `higgsfield generate cost`). 포맷 yaml의 `credit_cap_per_item`이
이 숫자 기준이다:

| 쓰임 | 모델 | 비용 | 포맷 캡 |
|---|---|---|---|
| OG·썸네일·상품컷·카드뉴스 | `gpt_image_2` | 6.5 cr/장 | 8 / 8 / 10 / 30 |
| 인물 스틸 (레퍼런스) | `nano_banana_pro` | 2 cr/장 | 영상 캡에 포함 |
| 인물 스틸 (Soul) | `text2image_soul_v2` | 0.12 cr/장 | 영상 캡에 포함 |
| 영상 테이크 720p | `seedance_2_0` | 4.5 cr/초 | `talking_head`·`pov` 150 |
| 영상 테이크 1080p | `seedance_2_0` | 12 cr/초 | 24초면 288 — 캡 초과 |

24초 토킹헤드 1편 ≈ **108 크레딧** (스틸 0.12 + 12초 테이크 2개). 유료 호출 전에 항상
견적을 받아 캡과 대조하고, 넘으면 **호출 자체를 하지 않는다** (`BUDGET_EXCEEDED`).
`--dry-run`은 과금 없이 단계별 견적만 찍는다.

### TikTok 커넥터 / TikTok connector

직접 TikTok API는 미심사 앱이면 비공개 게시만 되므로 Higgsfield MCP를 경유한다.
`tiktok_connect`로 본인 계정을 연결하고 `connector_id`는 `platforms.json`에,
토큰은 `credentials.env`의 `HIGGSFIELD_MCP_TOKEN`에 넣는다.

> ⚠️ **MCP 토큰은 현재 정적이고 수 시간 만에 만료된다.** 만료되면 발행이 `HTTP_401`로
> 멈춘다. 자동 갱신은 **아직 없다 (TODO: `factory token refresh --platform tiktok`)**.
> 그때까지 TikTok에 장기 예약 발행을 걸어두면 토큰이 먼저 죽는다.

### ElevenLabs voice_id

Voice Design(글로 묘사해서 생성)이나 Instant Voice Cloning(본인 음성 샘플 복제, 유료
플랜 필요)으로 만든 뒤 Voice Lab에서 id를 복사해 프로필 `voice.json`에 넣는다. 모델은
`eleven_v3` — 구형 다국어 모델은 오프캠 내레이션이 로봇처럼 들린다. 플랜별 월 글자 쿼터는
공식 pricing 페이지를 보라. 24초 영상 1편 대본이 약 320자였고, 편당 상한은 포맷의
`limits.tts_chars_max`(기본 2000)다. 같은 문장 재합성은 캐시에 걸려 0자다.

### Meta: 본인 앱으로 가는 경우 / your own Meta app

남의 앱에 테스터로 들어가는 절차는 아래 "플랫폼 제약"과 `/setup` Step 4에 있다. 본인 앱을
만든다면: 앱 생성 → Threads API / Instagram Graph API 제품 추가 → 리다이렉트 URL 등록 →
본인 계정을 앱 역할에 추가. **개발 모드 앱은 본인 앱이어도 테스터 역할 계정만 OAuth가
된다** — 플랫폼 안에서 초대를 수락하는 단계는 똑같이 필요하다. 일반 공개 발행은 앱 심사가
필요하고 이 레포 범위 밖이다.

### YouTube: 본인 GCP / your own GCP project

`/setup` Step 6. 미검증 프로젝트는 업로드가 **private 고정**이다 — 검토 후 예약 공개에는
문제없지만 알고 시작해야 한다.

## credentials.env 키 / Credential keys

`~/.contents-factory/profiles/<name>/credentials.env`, 한 줄에 `KEY=value`, chmod 600.
값은 어떤 출력에도 안 나온다 (`--dry-run` 페이로드엔 `<REDACTED:KEY>`로 찍힌다).

**시크릿 아닌 계정 ID는 여기 두지 않는다** (§10 결정6). TikTok `connector_id`와 네이버
`blog_id`는 `platforms.json`에 있다 — 버전관리 대상이고, 없으면 `CONNECTOR_ID_MISSING` /
`BLOG_ID_MISSING`으로 멈춘다.

| 용도 | 필수 키 | 선택 키 |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `THREADS_TOKEN_ISSUED_AT`(auth·refresh가 기록), `THREADS_APP_ID`, `THREADS_APP_SECRET`(있으면 로컬 교환), `THREADS_REDIRECT_URI` |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | `IG_TOKEN_ISSUED_AT`, `IG_APP_ID`, `IG_APP_SECRET` |
| TikTok (Higgsfield MCP) | `HIGGSFIELD_MCP_TOKEN` | - |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `YOUTUBE_ACCESS_TOKEN`(단기 폴백) |
| 네이버 블로그 | (토큰 없음) | `FACTORY_ASIDE_BIN`(기본 `aside`) |
| 미디어 호스팅 | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_BUCKET`(기본 `contents-factory`) |
| 텍스트 생성 | `ANTHROPIC_API_KEY` | - |

Threads 토큰은 `factory auth threads --profile X`가 직접 넣는다 — 동의 URL 출력 → 리다이렉트된
전체 URL을 숨긴 프롬프트에 붙여넣기 → code 교환 → `/me` → `credentials.env`(chmod 600) 기록.
값은 어디에도 안 찍히고 핸들만 출력한다. `THREADS_APP_SECRET`이 로컬에 있으면 직접 교환하고,
없으면 `--exchange-url`(기본 `https://koreansaju.xyz/api/threads/exchange`)에
`{code, redirect_uri, app_id}`만 보내 long-lived를 받아온다. 토큰 grant에
`threads_content_publish`가 없으면 저장을 거부한다(`SCOPE_MISSING`). Instagram은 같은 구조의
스텁이라 동의 URL만 출력한다.

플랫폼 제약 (사실, 2026-10 기준):

- Threads/IG: Meta 앱이 필요하다. 개발 모드 앱은 테스터 역할 계정만 OAuth가 된다.
- Threads/IG의 IMAGE·VIDEO와 TikTok import 경로는 **공개 HTTPS URL**이 필요하다. 서버가
  직접 가져가므로 로컬 경로 업로드가 안 된다. `media_host`(Supabase) 또는 BYO로 채운다.
- TikTok 직접 API는 미심사 앱이면 비공개 게시만 된다. 그래서 Higgsfield MCP 경로를 쓴다.
- YouTube: 미검증 GCP 앱 업로드는 private 고정. `publishAt`은 private + 미래 시각일 때만 먹는다.
- 네이버: 공개 글쓰기 API가 없다. aside 브라우저로 에디터를 조작한다.

## 미디어 호스팅 버킷 / BYO media bucket

Threads·Instagram의 IMAGE·VIDEO와 TikTok import 경로는 **플랫폼 서버가 직접 URL을 가져간다**.
그래서 공개 HTTPS URL이 필요하고, 그 URL은 **사용자 본인의 Supabase 프로젝트**에서 나온다
(§10 결정7 — 남의 버킷을 공유하지 않는다. 남의 프로젝트에 내 미디어를 올리면 그 쪽 요금·
수명·삭제 정책에 내 발행이 묶인다).

셋업:

1. Supabase 프로젝트를 만들고 Storage에서 **public** 버킷 1개를 만든다 (기본 이름
   `contents-factory`, 바꾸려면 `SUPABASE_BUCKET`).
2. `credentials.env`에 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`를 넣는다.
   service role 키는 서버 전용이다 — 클라이언트·로그·커밋 어디에도 노출 금지.
3. 업로드 경로는 `<profile>/<queue_item_id>/<filename>`이고 공개 URL은
   `{SUPABASE_URL}/storage/v1/object/public/<bucket>/<path>`다.

**7일 수명 정책**: 발행이 끝난 미디어는 플랫폼이 자기 CDN에 복사해 두므로 버킷에 남겨둘
이유가 없다. 7일 뒤 지우는 걸 기본으로 한다 (발행 실패·재시도·검수 여유를 합친 값).

```sql
-- 버킷 정책 예시: public read, 쓰기는 service role만
create policy "public read"
  on storage.objects for select
  using (bucket_id = 'contents-factory');

-- 7일 지난 객체 정리 (pg_cron). Storage 는 자동 수명 정책이 없어서 잡으로 돈다.
select cron.schedule(
  'contents-factory-media-ttl', '17 4 * * *',
  $$delete from storage.objects
     where bucket_id = 'contents-factory'
       and created_at < now() - interval '7 days'$$
);
```

버킷을 직접 쓰지 않을 거면 `media[].public_url`을 큐 적재 시점에 채워 넣으면 된다.
그 경우 `media_host`는 아무것도 하지 않고 그 URL을 그대로 쓴다 (BYO 호스팅).

## 원장 / Ledger

`~/.contents-factory/ledger.jsonl`, 한 줄 1건, append only.

- **돈이 드는 호출은 전부 `ledger.charge()` 한 곳**으로 간다. `op`(작업 키)가 필수이고,
  값은 프로바이더 작업 id(`higgsfield:<job_id>`)다. 같은 `op` 가 이미 있으면 안 쓴다 —
  단계 재실행·크래시 후 재개·복구 스크립트 재생이 전부 1회로 수렴한다.
- 작업 id 를 못 받은 호출은 `PROVIDER_JOB_ID_MISSING` 으로 멈춘다. 키 없는 과금은
  같은 작업을 두 번 청구하는 경로라서 추정하지 않는다.
- 단계 롤업·발행·토큰 갱신은 과금이 아니라 `record()` 로 가고 `credits=0`·
  `detail.rollup=true` 다. 영상 단계 롤업이 크레딧까지 들고 있으면 유료 단계가 전부
  두 배가 된다.
- `factory ledger summary` 와 단계별 캡(`generators/budget.py`)은 같은 파일을 읽는다.

⚠️ 2026-10-14 이전 행에는 `op` 가 없다. 그 구간의 image 13.0cr 은 같은 작업
`de1dac52…` 가 두 번 기록된 것이고 실제는 6.5cr 이다.

## 프롬프트 캐싱 / Prompt caching

`workspace/LLM-API-캐싱-규칙.md` + P0-SPEC §10 결정4 준수.

system 프리픽스 = `persona.md` → `docs/rules/<kind>-rules.md`의 절차·게이트·금지 절 →
포맷 `prompt_template` → 출력 스키마 순서다. 안정적인 것이 앞이라 포맷을 바꿔도 뒤쪽만
무효화된다. 날짜·슬롯·앵글 같은 가변값은 전부 user 메시지로 간다.

**패딩은 하지 않는다.** sonnet-5 최소 프리픽스는 1,024토큰이고, 미달이면 마커를 아예 안
붙이고 원장에 `cache:skipped`를 남긴다(마커만 달면 조용히 무시되므로 기록이 남는 쪽이 낫다).
`factory gen text --dry-run`이 `cache`와 프리픽스 추정 토큰을 찍는다. 추정은 CJK를 글자당
0.7토큰, 라틴을 0.27토큰으로 센다. 2회째 호출에 `cache_read_input_tokens`가 0이면 생성기가
WARN을 남긴다.

## 레이아웃 / Layout

```
factory/
  cli.py __main__.py config.py credentials.py errors.py ledger.py
  i18n/{ko,en}.json           인터뷰·리포트 문구 (영문 점표기 키)
  auth/{base,threads,instagram}.py   OAuth 동의 URL·code 교환·credentials 기록
  spec/{brief,profile,queue,format}.py
  formats/{text,image,video}/*.yaml
  generators/text.py          Anthropic claude-sonnet-5 + 캐싱
  gates/{repeat_guard,forbidden_words,limits,caption_safe_zone,cta_single,duplicate_copy}.py
  generators/image.py         higgsfield CLI 래퍼 (argv 조립 + dry-run)
  rules.py                    docs/rules 절 단위 리더 (캐싱 프리픽스용)
  queue/{store,cli}.py        큐 CRUD 단일 소유자
  scheduler/{tick,launchd,cron,token_refresh}.py
  publishers/{base,threads,instagram,tiktok,youtube,naver_blog,media_host}.py
  publishers/naver/*.mjs      pilot에서 벤더링한 aside 에디터 조작
  review/terminal.py
profiles/_example/            영문 예시 페르소나 (사주 아님)
.claude/skills/{setup,profile,writing-factory,image-factory,video-factory,publish}/SKILL.md
tests/
```

## 이식 출처 / Ported from

| 팩토리 | 원본 |
|---|---|
| `gates/repeat_guard.py` | `~/.saju-threads/scripts/repeat_guard.py` |
| `gates/{vision,limb_motion,bg_motion}.py` | `~/.saju-threads/reelv1/gates/{limb_motion,bg_motion}.py` |
| `gates/identity.py` | `reelv1` eyemetric + saju-threads `docs/video-briefs/STILL-GATE.md` §B-7 |
| `gates/stt_wpm.py` | `reelv1/stt.py`, `viewer_voice.py` |
| `generators/video/script.py` | `docs/video-briefs/prompts-2026-09-15/gen.py --check` |
| `generators/video/{tts,captions,assemble}.py` | `reelv1/{tts,captions,textrender_safe,assemble}.py` |
| `publishers/threads.py` | `publish.sh`, `threads_delivery.py` |
| `publishers/instagram.py` | `ig_publish.sh`, `ig_preview.sh` |
| `publishers/tiktok.py` | `tiktok_publish.py` |
| `publishers/youtube.py` | `yt_upload.py`, `yt_oauth.py`, `platform_cta.py` |
| `publishers/naver/*` | `pilot/scripts/naver/{publish,md_to_document}.mjs`, `lib/{aside,document}.mjs` |
| `auth/threads.py` | `oauth_exchange.sh`, `profile_add.sh --exchange` |
| `scheduler/token_refresh.py` | `token_refresh.sh` |
| `queue/`, `scheduler/tick.py` | `queue_autopub.py`, `com.saju.publish.plist` |
| `generators/text.py` | `persona_writer.py` |

원 레포는 읽기 전용 참조다. 운영 파이프라인은 건드리지 않는다.
