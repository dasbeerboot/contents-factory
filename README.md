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
factory brief show --brief b_...
factory gen text --brief b_... [--dry-run]
factory gate run (--brief b_... | --text "...") [--format threads_post] [--gate repeat_guard]
factory queue add --platform threads [--brief b_... --output 0] [--text "..."]
                  [--media path --public-url url] [--slot next|2026-10-15T12:00:00Z]
factory queue list [--pending] [--json] | approve | unapprove | arm --reason "..." | disarm | purge
factory queue reopen --item q_... --reason "..."   # unknown phase 해제 (사람 확인 후)
factory publish <platform> --item q_... [--dry-run]
factory schedule install|remove|status [--runner launchd|cron] | tick [--dry-run]
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

## credentials.env 키 / Credential keys

`~/.contents-factory/profiles/<name>/credentials.env`, 한 줄에 `KEY=value`, chmod 600.
값은 어떤 출력에도 안 나온다 (`--dry-run` 페이로드엔 `<REDACTED:KEY>`로 찍힌다).

**시크릿 아닌 계정 ID는 여기 두지 않는다** (§10 결정6). TikTok `connector_id`와 네이버
`blog_id`는 `platforms.json`에 있다 — 버전관리 대상이고, 없으면 `CONNECTOR_ID_MISSING` /
`BLOG_ID_MISSING`으로 멈춘다.

| 용도 | 필수 키 | 선택 키 |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `THREADS_TOKEN_ISSUED_AT`(refresh가 기록) |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | `IG_TOKEN_ISSUED_AT`, `IG_APP_ID`, `IG_APP_SECRET` |
| TikTok (Higgsfield MCP) | `HIGGSFIELD_MCP_TOKEN` | - |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `YOUTUBE_ACCESS_TOKEN`(단기 폴백) |
| 네이버 블로그 | (토큰 없음) | `FACTORY_ASIDE_BIN`(기본 `aside`) |
| 미디어 호스팅 | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_BUCKET`(기본 `contents-factory`) |
| 텍스트 생성 | `ANTHROPIC_API_KEY` | - |

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
  spec/{brief,profile,queue,format}.py
  formats/{text,image,video}/*.yaml
  generators/text.py          Anthropic claude-sonnet-5 + 캐싱
  gates/{repeat_guard,forbidden_words,limits,caption_safe_zone}.py
  queue/{store,cli}.py        큐 CRUD 단일 소유자
  scheduler/{tick,launchd,cron,token_refresh}.py
  publishers/{base,threads,instagram,tiktok,youtube,naver_blog,media_host}.py
  publishers/naver/*.mjs      pilot에서 벤더링한 aside 에디터 조작
  review/terminal.py
profiles/_example/            영문 예시 페르소나 (사주 아님)
.claude/skills/writing-factory/SKILL.md
tests/
```

## 이식 출처 / Ported from

| 팩토리 | 원본 |
|---|---|
| `gates/repeat_guard.py` | `~/.saju-threads/scripts/repeat_guard.py` |
| `publishers/threads.py` | `publish.sh`, `threads_delivery.py` |
| `publishers/instagram.py` | `ig_publish.sh`, `ig_preview.sh` |
| `publishers/tiktok.py` | `tiktok_publish.py` |
| `publishers/youtube.py` | `yt_upload.py`, `yt_oauth.py`, `platform_cta.py` |
| `publishers/naver/*` | `pilot/scripts/naver/{publish,md_to_document}.mjs`, `lib/{aside,document}.mjs` |
| `scheduler/token_refresh.py` | `token_refresh.sh` |
| `queue/`, `scheduler/tick.py` | `queue_autopub.py`, `com.saju.publish.plist` |
| `generators/text.py` | `persona_writer.py` |

원 레포는 읽기 전용 참조다. 운영 파이프라인은 건드리지 않는다.
