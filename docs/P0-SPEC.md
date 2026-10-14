# P0 구현 스펙 (opus 4.8 / codex 인계용)

> 이해한 요청: 4개 레포의 글·이미지·영상 제작 절차와 5개 플랫폼 자동발행을 페르소나 무관 Claude Code 스킬팩 + 파이썬 CLI로 추출한다. 프롬프트는 에이전트와 사용자가 같이 짠다. 기존 saju-threads 운영 코드는 건드리지 않는다.

## 0. 원칙

- 파이썬 3.11+, `uv` 관리, 외부 의존 최소 (requests, pyyaml, pydantic, ffmpeg 바이너리).
- `~/.contents-factory/` 가 런타임 홈. 레포엔 코드·규칙·예시만. 상태파일은 CLI로만 변경 (손글씨 금지, README 명시).
- secret은 `~/.contents-factory/profiles/<name>/credentials.env` (chmod 600). 응답·로그에 `e.message` 그대로 금지, 코드형 에러.
- LLM 호출은 `workspace/LLM-API-캐싱-규칙.md` 준수 (system 불변 프리픽스, 가변값은 user 메시지).
- TypeScript 없음. 변수·함수명 영문, 주석·UI 문구만 ko/en.

## 1. 레포 레이아웃

```
contents-factory/
  .claude/skills/{setup,profile,writing-factory,image-factory,video-factory,publish}/SKILL.md
  factory/
    __main__.py            `factory` CLI 엔트리
    config.py              factory.toml 로더 (ui_lang, content_lang, default_profile, home)
    i18n/{ko,en}.json      인터뷰 질문·리포트 문구 사전. 키는 영문 점표기
    spec/
      brief.py             Brief 모델 (공통 + kind별 서브모델)
      profile.py           Profile 모델
      queue.py             QueueItem 모델
    formats/
      text/{threads_post,ig_caption,blog_long,cardnews_copy,free}.yaml
      image/{product_shot,cardnews,og,thumbnail,free}.yaml
      video/{talking_head,pov,textcard,explainer,free}.yaml
    generators/
      text.py              Anthropic (claude-sonnet-5), 캐싱
      image.py             Higgsfield CLI 래퍼 (nano_banana_pro / gpt_image_2_5 / soul_2)
      video/               P2. 디렉토리만
    gates/
      repeat_guard.py      saju-threads scripts/repeat_guard.py 이식 (exact 8단어, 6-gram 40%, closer 7일 3회)
      forbidden_words.py   프로필 금지어 + 글로벌 금지어
      limits.py            글자수·이모지·해시태그·링크 (포맷 yaml의 limits 블록 적용)
      caption_safe_zone.py 이미지·영상 텍스트 영역 (기본 x200..880 y290..1430 / 1080x1920)
    review/
      terminal.py          기본
      telegram.py          옵션 (sendPhoto / sendVideo 인라인, width·height·supports_streaming)
    queue/
      store.py             ~/.contents-factory/queue/YYYY-MM-DD.json CRUD
      cli.py               add / list / approve / unapprove / arm / disarm / purge
    publishers/
      base.py              publish(item) -> PublishResult, dry_run
      threads.py           publish.sh 이식 (TEXT/IMAGE/VIDEO 컨테이너→publish, 미디어 공개 URL 필요)
      instagram.py         ig_publish.sh 이식 (카드·캐러셀·릴스)
      tiktok.py            tiktok_publish.py 이식 (Higgsfield MCP media_import_url 경로)
      youtube.py           yt_upload.py 이식 (private 업로드 + publishAt)
      naver_blog.py        pilot scripts/naver/publish.mjs 호출 래퍼 (aside, md→document)
      media_host.py        공개 URL 호스팅 (Supabase storage 기본, 사용자 BYO)
    scheduler/
      launchd.py / cron.py 설치·해제 생성기
      tick.py              큐 스캔 → 슬롯 도래·approved·armed 확인 → publisher 호출 → 결과 기록
      token_refresh.py     Threads/IG 60일 토큰 주 1회 갱신
    ledger.py              비용·발행 원장 jsonl (track, kind, model, credits/tokens, result)
  profiles/_example/       persona.md, brand.md, voice.json, soul.json, platforms.json, forbidden.txt, locale
  docs/rules/{writing,image,video,publishing}-rules.md
  docs/{PLAN,P0-SPEC}.md
  factory.toml.example
  README.md (ko/en 병기)
  tests/
```

## 2. 데이터 스키마

### factory.toml
```toml
ui_lang = "ko"            # 질문·리포트 언어
content_lang = "en"       # 생성물 기본 언어 (프로필·브리프가 덮어씀)
default_profile = "_example"
home = "~/.contents-factory"
review = "terminal"       # terminal | telegram
```

### Profile (`profiles/<name>/`)
| 파일 | 내용 |
|---|---|
| persona.md | 정체·말투·금지 표현·시그니처 (system 프리픽스에 통째로) |
| brand.md | 로고·색·폰트·레퍼런스 이미지 경로 우선순위 |
| voice.json | `{provider:"elevenlabs", voice_id, model, stability, style}` |
| soul.json | `{provider:"higgsfield", soul_id, ref_image, identity_rules}` |
| platforms.json | 플랫폼별 `{enabled, account_id, slots:["12:00Z"], late_policy:"carry_once"|"drop", cta_rotation:[]}` |
| forbidden.txt | 한 줄 1개 |
| credentials.env | 런타임 홈에만 존재. 키 목록은 README |
| locale | content_lang 기본값 |

### Brief (`~/.contents-factory/briefs/<id>.json`)
```json
{
  "id": "b_20261014_x1", "kind": "text|image|video", "format": "threads_post",
  "profile": "_example", "platforms": ["threads","instagram"],
  "content_lang": "en", "references": ["path|url"], "count": 3, "ab_hooks": 2,
  "budget": {"credits": 150, "tokens": 20000},
  "answers": {"...포맷별 추가 질문 답"},
  "prompt_draft": "...", "prompt_final": "...", "prompt_revisions": 2,
  "publish": {"mode": "queue_only|slot|auto_after_approve", "scheduled_at": null},
  "outputs": [{"path": "...", "gates": {...}, "chosen": true}],
  "created_at": "..."
}
```

### Format yaml (예: `formats/video/talking_head.yaml`)
```yaml
name: talking_head
kind: video
platforms: [tiktok, youtube, instagram]
aspect: "9:16"
duration_s: [23, 28]
questions:            # 공통 8문항 뒤에 붙는 추가 질문. i18n 키
  - key: video.th.location
  - key: video.th.outfit
  - key: video.th.hook_count
prompt_template: |    # 슬롯 치환. 슬롯 밖 문장 수정 금지 (gen.py --check 이식)
  ...
gates: [caption_safe_zone, limb_motion, bg_motion, stt_wpm, identity]
limits: {wpm: [150, 180], first3s_events_min: 3}
credit_cap_per_item: 150
```
`free.yaml`은 questions만 (무엇을 보여주고 싶은지, 톤, 금지 사항) 받고 prompt_template 없이 에이전트가 처음부터 같이 쓴다.

### QueueItem
```json
{"id":"q_...","brief_id":"b_...","profile":"_example","platform":"threads","kind":"text",
 "text":"...","media":[{"path":"...","public_url":null}],
 "scheduled_at":"2026-10-15T12:00:00Z","approved":false,"armed":false,
 "published":false,"published_id":null,"attempts":0,"last_error":null,"late_policy":"carry_once"}
```

## 3. 인터뷰 + 프롬프트 공동작성 루프 (모든 팩토리 공통)

1. 공통 8문항 (무엇·어디·누구·레퍼런스·언어·수량/AB·예산·발행). 이미 factory.toml·프로필로 답이 있는 건 건너뜀.
2. 포맷 선택: 카탈로그 목록 + `free`. 선택한 포맷의 추가 질문.
3. **프롬프트 초안 제시** (템플릿+답+프로필 규칙 합성). 사용자가 수정·추가·거절. 확정까지 반복, 회차 `prompt_revisions`에 기록.
4. 생성 → 게이트 결과표 (PASS/FAIL 사유) → 후보 나열 → 사용자 택.
5. 큐 적재 (`factory queue add --brief b_... --output 0 --platform threads --slot next`).
6. 재생성 요청은 brief가 아니라 `prompt_final` 수정으로 처리.

## 4. CLI 명령 (P0)

```
factory init                              홈 생성, factory.toml 복사, 언어 선택
factory profile add <name> | list | show
factory brief new --kind text --format threads_post --profile X   (스킬이 답을 json으로 넘김)
factory gen text  --brief b_...           outputs[] 채움, 게이트 자동 실행
factory gen image --brief b_...
factory gate run  --brief b_... [--gate repeat_guard]
factory queue add|list|approve|unapprove|arm|disarm|purge
factory publish <platform> --item q_... [--dry-run]
factory schedule install|remove|status|tick
factory token refresh --profile X
factory ledger summary [--days 30]
```

## 5. 스킬 SKILL.md 규격

- frontmatter description: 영어 본문 + 한국어 트리거 병기 (`/writing-factory`, "글 뽑자", "쓰레드 글").
- 본문: 인터뷰 절차(§3) + 어떤 CLI를 어느 순서로 부르는지 + 금지 (상태파일 직접 쓰기, 승인 없이 arm, 슬롯 외 발행).
- 질문 문구는 SKILL.md에 하드코딩하지 않고 `factory i18n get <key>`로 가져온다 (ui_lang 적용).
- `/publish`는 큐 조회·슬롯 편성·arm·스케줄러 설치·향후 7일 실발행표 출력까지.

## 6. 발행 규칙 (`docs/rules/publishing-rules.md` 핵심)

- 승인 ≠ 발행. 슬롯 시각(UTC)에만 tick이 발행. 승인 후 지각분은 `late_policy` 적용: `carry_once`(다음 슬롯 1회 이월) 기본, `drop`.
- armed=false면 approved여도 발행 안 함. arm 명령은 근거 문자열 필수.
- 같은 프로필·플랫폼 일일 상한 (platforms.json `daily_cap`).
- repeat_guard는 발행 직전에 다시 1회 (큐 적재 시 통과했어도).
- 토큰 만료 7일 전 경고 (terminal/telegram).
- 네이버: aside 로그인 세션 필요. 미로그인은 `NAVER_SESSION_MISSING`으로 큐 유지.

## 7. 출처 이식 목록 (코드 복사 시 원본 경로)

| 팩토리 파일 | 원본 |
|---|---|
| gates/repeat_guard.py | `~/.saju-threads/scripts/repeat_guard.py` |
| publishers/threads.py | `~/.saju-threads/scripts/publish.sh`, `threads_delivery.py` |
| publishers/instagram.py | `~/.saju-threads/scripts/ig_publish.sh`, `ig_preview.sh` |
| publishers/tiktok.py | `~/.saju-threads/scripts/tiktok_publish.py` |
| publishers/youtube.py | `~/.saju-threads/scripts/yt_upload.py`, `yt_oauth.py`, `platform_cta.py` |
| publishers/naver_blog.py | `pilot/scripts/naver/publish.mjs`, `md_to_document.mjs`, `lib/aside.mjs` |
| scheduler/token_refresh.py | `~/.saju-threads/scripts/token_refresh.sh` |
| queue/, scheduler/tick.py | `~/.saju-threads/scripts/queue_autopub.py`, `com.saju.publish.plist` |
| review/telegram.py | `saju-shortform/gen3/reelv1/mkreview.py` |
| generators/text.py | `~/.saju-threads/scripts/persona_writer.py` (프롬프트 3분할·캐싱) |
| generators/image.py | commerce-project 생성 JSON 스키마 + `data/credits_ledger.jsonl` 형식 |
| rules/writing-rules.md | saju-threads HANDOFF §4, saju-website `docs/hook-playbook.md`·`hook-devices.md`·`tone.md` |
| rules/image-rules.md | commerce-project `docs/design/2026-10-06-image-generation-postmortem.md` |
| rules/video-rules.md | saju-threads `docs/video-briefs/STILL-GATE.md`, `VIDEO-INFRA.md`, `2026-09-15-prompts-and-reroll-guard.md` |

## 8. 검증 (완료 조건)

1. `uv run pytest` 통과 (스키마·게이트·큐·스케줄 tick 단위 테스트).
2. `factory init` → `/setup` → `/writing-factory` 로 `_example` 프로필 Threads 글 1건 생성, 큐 적재, `factory publish threads --dry-run` 페이로드 출력.
3. 5개 publisher 각각 `--dry-run` 통과 + 실발행 1건씩 (테스트 계정).
4. **Meta 앱 테스터 경로 검증**: 대시보드 실측(2026-10-14) 결과 `threads_content_publish`는 테스터 전용. /setup 스킬의 테스터 등록 안내(앱 역할 추가 → Threads 앱에서 초대 수락 → OAuth, 리다이렉트 URL 일치)를 따라 새 테스터 계정 1개로 OAuth → Threads 1건 발행. 비테스터 발행은 범위 밖.
5. `factory schedule install` 후 launchd 등록 확인, tick 1회 수동 실행, 상태파일 손상 없음.
6. ui_lang=en 으로 전 인터뷰 1회 완주.

## 9. 작업 분배

| 담당 | 범위 |
|---|---|
| opus 4.8 | 레포 뼈대, spec 모델, CLI 골격, queue/scheduler/tick, publishers 5종 이식, 캐싱 적용 text generator, 스킬 1개(writing-factory) 원형 |
| codex | i18n ko/en 사전 전량, formats yaml 카탈로그 15개, 나머지 SKILL.md 5개(원형 승인 후), gates 이식·테스트, rules md 4종 추출 정리 |
| 주원 | 테스트용 계정 제공, 사용자 계정을 Threads 테스터로 추가(§8-4, 앱 관리자 권한), 네이버 aside 로그인 |

## 10. 구현 중 결정 (2026-10-14, 뼈대 1차 후)

| # | 질문 | 결정 |
|---|---|---|
| 1 | 큐 자동승인(무응답=진행) | P0는 승인 필수 유지. P1에 프로필 옵션 `auto_approve`(기본 false) 추가. 켜도 armed 게이트는 그대로 |
| 2 | 중복발행 방어 | **P0에 포함**. `threads_delivery.py`의 phase 머신(ready→creating→container→publishing→published, content sha256, 재개)을 QueueItem에 이식. 실발행(§8-3) 전 필수 |
| 3 | 네이버 긴 글·이미지 | `cors_server.mjs` 벤더링해 90KB 상한 해제는 P0. 이미지 업로드는 P1 |
| 4 | 캐싱 최소 프리픽스 | 패딩 금지. system 프리픽스 = persona.md + `docs/rules/writing-rules.md` 해당 절 + 포맷 prompt_template 순으로 합쳐 1024 토큰 이상이면 마커, 미만이면 캐싱 생략 + ledger에 `cache:skipped` 기록 |
| 5 | 지각 유예 | 900초 확정 |
| 6 | 비밀 아닌 계정 ID 위치 | `platforms.json`으로 이동 (`tiktok.connector_id`, `naver_blog.blog_id`). credentials.env는 토큰·시크릿만 |
| 7 | media_host 버킷 | 사용자 BYO Supabase 프로젝트. saju 버킷 공유 안 함. README에 버킷 공개 설정·7일 수명 정책 예시 |

codex 한도 소진(11/13 리셋)으로 §9 codex 범위의 잔여분(SKILL.md 5개·게이트 테스트·i18n 보강)은 opus가 이어서 한다. codex가 이미 만든 `docs/rules/*.md` 4종과 `factory/formats/**/*.yaml` 15개는 워크트리 `.worktrees/codex-261014-140940`, `.worktrees/codex-261014-141556`에 있다.
