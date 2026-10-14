# contents-factory 기획안 (v0, 2026-10-14)

> 이해한 요청 — saju-threads·saju-shortform·commerce-project·saju-website·pilot에 흩어진 "글·이미지·영상 뽑는 법 + 자동발행"을 **페르소나 무관한 범용 모듈**로 추출한다. 사용자는 레포를 클론한 뒤 Claude Code에서 `/writing-factory` `/image-factory` `/video-factory`를 치고, 에이전트의 질문에 답하며 결과물을 만든다. 만든 것은 큐에 쌓아 플랫폼별 지정 시각에 자동발행한다. 질문·문서는 한국어/영어 선택. 사주 스타일은 하나의 "프로필 예시"일 뿐 본체에 박지 않는다.

## 1. 한 장 도식

```
[/setup]  언어·프로필·키·플랫폼 연결
    │
    ▼
[/writing-factory] [/image-factory] [/video-factory]
    │  인터뷰(무엇·어디·누구·레퍼런스·언어·예산)
    ▼
 brief.json  ──▶ generator ──▶ gates ──▶ review(터미널/텔레) ──▶ queue/
                 (LLM / Higgsfield / ElevenLabs+ffmpeg)         │
                                                                ▼
[/publish]  슬롯 편성 ──▶ scheduler(launchd/cron) ──▶ publishers(Threads·IG·TikTok·YouTube)
                                                                │
                                                                ▼
                                                      state/ + ledger(비용·발행 로그)
```

## 2. 레포 구조 (제안)

```
contents-factory/
  .claude/skills/
    setup/            언어·프로필·크레덴셜·플랫폼 연결 인터뷰
    writing-factory/  글 인터뷰 → brief → 생성 → 게이트 → 큐
    image-factory/    이미지 인터뷰 → brief → Higgsfield → QA 크롭 체크 → 큐
    video-factory/    영상 인터뷰 → 대본 → 스틸 → 영상 → TTS → 자막 → 조립 → 게이트 → 큐
    publish/          큐 조회·슬롯 편성·arm/disarm·스케줄러 설치
    profile/          페르소나·브랜드·보이스·Soul id 등록/수정
  factory/            파이썬 패키지 (CLI `factory ...`)
    spec/             brief 스키마 (text / image / video / publish)
    generators/       text(Anthropic, 캐싱 규칙), image(Higgsfield CLI), video(Seedance+ElevenLabs+ffmpeg)
    gates/            repeat_guard, forbidden_words, caption_safe_zone, limb_motion, bg_motion, stt_wpm, identity
    publishers/       threads, instagram, tiktok, youtube, naver_blog  (5종 모두 필수, 주원 2026-10-14)
    queue/            큐 CRUD 소유 CLI (손글씨 금지 원칙 그대로)
    scheduler/        launchd(mac)·cron(linux) 생성기, tick 러너
    i18n/             ko / en 질문·메시지 사전
  profiles/
    _example/         persona.md, brand.md, voice.json, soul.json, locale, platforms.json
  docs/
    rules/            writing-rules, image-rules, video-rules, publishing-rules (원 레포에서 추출)
    PLAN.md
  factory.toml        ui_lang / content_lang / profile 기본값
```

## 3. 원 레포에서 가져올 것 (추출 매핑)

| 도메인 | 원본 | 팩토리로 옮기는 형태 |
|---|---|---|
| 글 생성 | `~/.saju-threads/scripts/persona_writer.py`, `raid_fire.py`, `prompts/`, profiles/*/prompts/persona.md·angles.json·writer_rules.md | generators/text + profiles 스키마. 훅 5종·CTA 5종·셀프크리틱 체크리스트는 docs/rules/writing-rules.md |
| 글 게이트 | `scripts/repeat_guard.py`, GLOBAL_BANNED, 이모지·해시태그·링크 한도 | gates/repeat_guard, forbidden_words (프로필별 금지어 파일) |
| 훅 이론 | saju-website `docs/hook-playbook.md`, `hook-devices.md`, `tone.md` | rules/writing-rules.md "훅 장치 12종·3초 구조" 섹션 (사주 예시는 _example로) |
| 이미지 | commerce-project `docs/design/2026-10-06-image-generation-postmortem.md`, `tote-prompts.md`, manifest.json, `data/credits_ledger.jsonl` | rules/image-rules.md (레퍼런스 우선순위·LOCAL EDIT·텍스트 0·크롭 5종 검수) + generators/image + ledger |
| 영상 | saju-shortform `gen3/reelv1/*` (mkprompts_stills·hf·tts·captions·assemble·stt·gates), saju-threads `docs/video-briefs/STILL-GATE.md`, `VIDEO-INFRA.md`, gen.py --check | generators/video 단계 모듈 + gates/* + rules/video-rules.md (1테이크 150cr 캡, 시선·구도, 세이프존, 소리 검수) |
| 발행 | `publish.sh`, `ig_publish.sh`, `tiktok_publish.py`, `yt_upload.py`, `queue_autopub.py`, `token_refresh.sh`, launchd plist 묶음 | publishers/* + scheduler + rules/publishing-rules.md (슬롯 시각, autopub 디폴트, 무응답 처리, 지각분 무상한 차단) |
| 리뷰 전달 | `mkreview.py` (sendVideo 인라인) | review 채널 플러그인 (terminal 기본, telegram 옵션) |
| 블로그·SEO 글 | saju-website·pilot `SEO-GEO-AGENT.md`, `geo_audit.py`, `fanout_coverage.py`, pilot `docs/31-seo-blog.md`, `scripts/naver/publish.mjs`·`md_to_document.mjs` | writing-factory "blog" 모드 + publishers/naver_blog (브라우저 자동화) |

## 4. 인터뷰 설계 (각 팩토리 공통 골격)

1. 무엇을 (형식: 글/카드/상품컷/토킹헤드/POV/텍스트카드 영상)
2. 어디에 (플랫폼 복수 선택 → 비율·길이·글자수 자동 결정)
3. 누구로 (프로필 선택 또는 신규 → persona/brand/voice/soul)
4. 레퍼런스 (파일·URL·캡쳐. 없으면 스킵)
5. 콘텐츠 언어 (UI 언어와 분리)
6. 수량·A/B (훅 후보 몇 개, 색상 변형 몇 개)
7. 예산 캡 (크레딧·토큰 상한. 영상은 편당 캡 디폴트)
8. 발행 (지금 큐만 / 슬롯 지정 / 승인 후 자동)

→ brief.json 저장 → 생성 → 게이트 결과표 → 사용자 택1 → 큐.

## 5. 자동발행 모델

- 큐 아이템 = `{id, profile, platform, kind, media, text, scheduled_at(UTC), approved, published, attempts}`
- 승인 ≠ 즉시 발행. 슬롯 시각 엄수. 지각분은 "다음 슬롯으로 이월" 또는 "폐기" 중 하나를 명시 (무상한 자동 발행 금지).
- 스케줄러는 사용자 머신의 launchd/cron. 설치·해제는 `factory schedule install|remove`.
- 상태파일은 CLI로만 변경. 손글씨 금지 원칙을 README에 명시.
- 플랫폼별 제약 (사실):
  - Threads/IG: Meta 앱 필요. 개발 모드 앱은 테스터 역할 계정만 OAuth 가능. 공개 사용은 앱 심사 통과 또는 사용자 본인 앱(BYO).
  - TikTok: Higgsfield MCP `tiktok_connect` 경유가 현실적. 직접 API는 미심사 앱이 비공개 게시만 가능.
  - YouTube: 미검증 GCP 앱 업로드는 private 고정. BYO GCP 프로젝트 전제.
  - 네이버 블로그: 공개 글쓰기 API 없음. pilot `scripts/naver/publish.mjs`는 aside 브라우저(로그인된 크롬 탭)로 에디터를 조작해 발행 (2026-09 실측). 팩토리에서는 Playwright 영속 프로필 또는 aside 중 택1로 이식. 사용자가 네이버 로그인을 한 번 해두는 전제.

## 6. 언어

- `ui_lang`: 인터뷰 질문·리포트·에러 메시지 (ko/en, i18n 사전)
- `content_lang`: 생성물 언어 (프로필 기본값, 인터뷰에서 덮어쓰기)
- 스킬 SKILL.md는 영어 본문 + 한국어 트리거 병기.

## 7. 범위 단계

| 단계 | 내용 |
|---|---|
| P0 | 레포 뼈대, /setup, /profile, /writing-factory(Threads·IG 글 + 블로그 long-form), gates(repeat·forbidden), queue, 스케줄러, ko/en, publishers 5종(Threads·IG·TikTok·YouTube·네이버) 골격 이식 |
| P1 | /image-factory (Higgsfield, 상품컷·카드뉴스·OG·썸네일), 텔레 리뷰 채널 |
| P2 | /video-factory (토킹헤드·POV, Soul·Seedance·ElevenLabs·게이트) |
| P3 | SEO/GEO 감사 모듈, 레포 공개 정리(예시 프로필·문서·라이선스) |

## 8. 확정 결정 (주원 2026-10-14)

| # | 항목 | 결정 |
|---|---|---|
| 1 | 공개 범위 | 프라이빗 레포, 지인 초대 |
| 2 | Meta 앱 | saju-threads 앱 공용 (주원: 심사 완료). ⚠ P0 검증 항목: 테스터 아닌 계정으로 OAuth→Threads 1건 발행 실측. 9/22 기록(서브계정 테스터 필수)과 충돌하므로 실측 전 "됨"으로 쓰지 않음 |
| 3 | 실행 환경 | Claude Code 스킬팩 (클론 후 슬래시 명령) |
| 4 | 스케줄러 | 사용자 로컬 launchd(mac)·cron(linux) |
| 5 | 네이버 발행 | aside 브라우저 방식 그대로 이식 (사용자도 aside 설치 전제) |
| 6 | 영상 포맷 | 카탈로그에서 선택 + 자유 모드. 프롬프트는 클라이언트와 **같이 짠다** (초안→수정→확정 루프) |
| 7 | 이미지 포맷 | 상품컷·카드뉴스·OG·썸네일 전부 + 자유 모드 |
| 8 | 기존 운영 | 추출만. saju-threads 운영 파이프 무변경 |
| 9 | 언어 | ui_lang / content_lang 분리 |
| 10 | 구현 | 설계·리뷰 opus 4.8, 양산 codex. fable은 기획만 |

상세 구현 스펙: `docs/P0-SPEC.md`
