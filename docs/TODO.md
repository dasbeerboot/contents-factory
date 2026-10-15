# contents-factory TODO

> 갱신 2026-10-15. 근거 표기: ✅(파일) / 🟡(부분) / ❌(미구현)

## 액션플랜 (주원 손)
- [ ] ❌ 한 줄 설치·런처·폰 원격제어·텔레그램 **실기 1회** (10/15 코드만, 실행 0회):
      `bash install.sh --dry-run` → 실설치 → 바탕화면 더블클릭 → `factory telegram setup`
      (BotFather 토큰·/start) → 드롭 1건 미리보기·버튼 왕복 → `factory remote install`
      후 폰 Claude 앱에서 환경 보이는지
- [ ] ❌ Windows PC에서 `uv run factory init --os windows` → `factory doctor` → `schedule install` 실기 검증 (10/15 코드만, 실기 0회)
- [ ] ❌ 네이버 playwright 백엔드 첫 실발행 (테스트 블로그, 입회 필수)
- [ ] ❌ saju-website: `src/app/api/threads/exchange/route.ts`(미커밋, yearmap-v1 작업트리) + /radar 글쓰기 박스(`/tmp/meta-review/radar-publish-spec.md`) → 별도 opus 4.8 세션에서 tsc·배포·master 머지
- [ ] ❌ Meta 검수: 스크린캐스트 녹화 → 폼 업로드 → 제출 (초안 submission 1332487598864551)
- [ ] 🟡 §8-3 실발행 5종 — **큐 4종이 approved+armed 로 대기 중**(2026-10-14, 아래 표).
      실발행 클릭은 주원 몫. 네이버만 미준비(aside 세션 없음)
- [ ] ❌ saju-website `src/app/api/threads/exchange/route.ts` 배포 + main 머지
      (tsc 통과, 미커밋·미배포. `factory auth threads` 의 위임 교환 경로가 이걸 쓴다)
- [ ] ❌ §8-4 Threads 테스터 경로 검증 — 새 계정을 앱 역할 테스터로 추가 → 초대 수락 →
      `factory auth threads --profile <name> --app-id <ID> --redirect <URI>` → 1건 발행
- [ ] ❌ 네이버 aside 로그인 세션 준비
- [ ] ❌ 지인 1명 온보딩 실측 (클론 → /setup → /writing-factory → 큐 → 드라이런)
- [ ] ❌ 텔레그램 실기 검증 1회 — 실제 봇 생성(`factory telegram setup`) → `telegram test` →
      영상 1건 `telegram preview` → 폰에서 버튼 3개 각각 눌러보기 → `telegram install`.
      코드·모킹 테스트만 완료(10/15), 실발송·실등록 0회

### 발행 대기 큐 (2026-10-14, 스케줄러 미설치라 자동 발행 안 됨)

| item | platform | profile | slot(UTC) | 상태 |
|---|---|---|---|---|
| `q_20261014093232_7z7q` | instagram | unni | 2026-10-14T11:00Z | approved+armed, 카드 Supabase 호스팅 완료 |
| `q_20261014092453_iwhh` | threads | unni | 2026-10-14T12:00Z | approved+armed |
| `q_20261014094110_f6kn` | tiktok | unni | 2026-10-14T12:02Z | approved+armed, `privacy_level=SELF_ONLY` |
| `q_20261014092821_h1k8` | threads | sujin | 2026-10-15T00:15Z | approved+armed |
| `q_20261014094006_9o6o` | youtube | unni | 2026-10-15T01:00Z | approved+armed, `privacy=private`·`publish_at=skip` |

⚠️ `factory schedule install` 하는 순간 지난 슬롯부터 발행된다. 손으로 쏠 거면 설치하지 말 것.

## P0 (완료)
- [x] ✅ 레포 뼈대·CLI (`factory/cli.py`, `pyproject.toml`)
- [x] ✅ spec 모델 4종 (`factory/spec/`)
- [x] ✅ i18n ko/en (`factory/i18n/`)
- [x] ✅ 큐·스케줄러·tick·launchd·cron·토큰갱신 (`factory/queue/`, `factory/scheduler/`)
- [x] ✅ phase 머신 중복발행 방어 (`factory/spec/queue.py`, `factory/scheduler/tick.py`)
- [x] ✅ publisher 5종 + media_host, 전부 --dry-run (`factory/publishers/`)
- [x] ✅ 네이버 cors_server 벤더링, 487KB 드라이런 통과 (`factory/publishers/naver/`)
- [x] ✅ 텍스트 생성기 + 캐싱 프리픽스 (`factory/generators/text.py`, `factory/rules.py`)
- [x] ✅ 게이트: repeat_guard·limits·forbidden_words·caption_safe_zone·cta_single·duplicate_copy (`factory/gates/`)
- [x] ✅ 포맷 yaml 15종 (`factory/formats/`)
- [x] ✅ 규칙 문서 4종 (`docs/rules/`)
- [x] ✅ 스킬 6종 (`.claude/skills/`)
- [x] ✅ _example 프로필, README BYO Supabase
- [x] ✅ gen image 실호출 + 견적 대조·BUDGET_EXCEEDED (`factory/generators/image.py`)
- [x] ✅ pytest 348 passed (`uv run pytest`, numpy·pillow 포함)
- [x] ✅ `factory auth threads` OAuth (`factory/auth/`) + Instagram 스텁, setup SKILL 반영
- [x] ✅ `factory.toml [extra_corpus]` — repeat_guard 에 외부 발행 이력 합류
- [x] ✅ `factory queue add --extra key=value`
- [x] ✅ `factory media host --item q_...` (`media_host` 를 부르는 쪽이 아예 없었다)
- [x] ✅ 운영 프로필 `unni`·`sujin` 구성 (`~/.contents-factory/profiles/`)
- [x] ✅ 영상 파이프라인 P2 (`factory/generators/video/`) + 게이트 stt_wpm·limb_motion·
      bg_motion·identity. feat/gen-image-video-p2 머지, 워크트리·브랜치 정리 완료
- [x] ✅ 엔드카드를 assemble 오버레이로 (모델이 "SAJU UNUI" 로 틀리던 것)
- [x] ✅ brief id 충돌 수정 — `b_<UTC초>_<4자>`, 생성 시 덮어쓰기 가드
- [x] ✅ 원장 멱등키 `ledger.charge(op=...)` — 작업 1건 = 과금 1건

## P1
- [x] ✅ TikTok MCP 토큰 만료 — 정적 `HIGGSFIELD_MCP_TOKEN` 폐기. 인증은 CLI
      (`higgsfield auth login`)이고 bearer 는 매 요청 직전 `higgsfield auth token` 이다.
      키가 없으니 갱신 명령도 없다 (`factory/higgsfield.py`)
- [x] ✅ Windows 지원 — `factory init --os`, `scheduler/windows.py`(schtasks),
      `osinfo.py`(which/.exe·icacls·홈 경로), `factory doctor`, 네이버 playwright 백엔드.
      실제 Windows 호스트 검증은 **미실시** (macOS에서 모킹 테스트만)
- [ ] ❌ Windows 실기 검증 1회 — init·doctor·schedule install(schtasks)·naver login·
      publish --dry-run 을 실제 Windows 호스트에서 돌려볼 것
- [ ] ❌ 네이버 playwright 백엔드 실발행 1건 (테스트 블로그, 사람 입회).
      현재 dry-run·로그인 창까지만 실측
- [ ] ❌ `factory auth instagram` 교환 구현 (현재 동의 URL + 수동 절차만)
- [ ] ❌ `factory auth threads` 실사용 검증 (지인 계정 1건)
- [ ] ❌ 엔드카드 수정 후 신규 테이크 1건 — 재조립본은 옛 테이크라 모델이 구운
      "SAJU UNUI" 가 새 카드 뒤에 유령으로 남아 있다
- [ ] ❌ 프로필 옵션 `auto_approve` (armed 게이트 유지)
- [ ] ❌ 수진 프로필용 비케이스 포맷(2인칭 관찰글) — 케이스썰은 실사연만 규칙
- [ ] ❌ 네이버 이미지 업로드 (`IMAGE_UPLOAD_UNSUPPORTED` 해제)
- [x] ✅ 텔레그램 미리보기·컨펌 채널 (`factory/telegram/`, `factory/review/telegram.py`)
      — setup(BotFather 3단계·토큰 숨김입력·/start chat_id 자동저장) / preview(글 `<pre>`·
      sendPhoto·sendVideo 1080×1920 supports_streaming, 50MB 초과는 ffmpeg 프리뷰본) /
      serve(getUpdates 오프셋·백로그 스킵·버튼 3개·멱등) / 알림 정책(🚨 실패·unknown·
      토큰만료, 📊 일일요약, 그 외 무음) / install launchd·cron·schtasks.
      **이 맥에서는 `--dry-run` 만 실행 — 실제 등록·실발송 0회**
- [ ] ❌ CTA 로테이션 적용 로직 (`platforms.json cta_rotation`)
- [ ] ❌ 미구현 게이트 SKIP 해소: text_legibility·logo_geometry·product_identity

## 컴맹 플로우 (PLAN §9) — 두뇌 완료 2026-10-15

- [x] ✅ `factory wizard` 상태머신 — setup 7단계 / make 4단계, next·answer·skip·back·
      status·reset, 상태는 `state/wizard.json` 하나 (`factory/wizard/{questions,engine,
      apply,state,cli}.py`). 답 저장은 전부 기존 CLI 경로(init·profile·brief·gen·gate·
      queue)를 in-process 로 호출하고 `ran` 에 실행한 명령을 그대로 돌려준다
- [x] ✅ 말투 3지선다 → persona·writer_rules·brand 생성 (`factory profile template`,
      `wizard/templates.py`)
- [x] ✅ 슬롯 질문 — 아침/점심/저녁/직접을 **로컬 시각으로 받아 UTC 변환** 후
      `platforms.json` 기록 (`factory profile platform` 이 그 파일의 소유 명령)
- [x] ✅ 포맷·플랫폼 자동 선택 (종류 × 프로필 enabled, 사용자에게 안 물음)
- [x] ✅ 자유문 분류 haiku-4-5 (`wizard/classify.py`), 키 없으면 재질문.
      부정 표현 우선 판정 — "아직 안했어요" 를 yes 로 읽던 버그 실측 후 수정
- [x] ✅ `simple = true` (기본) — 큐 적재 때 승인+무장 동시, 게이트 FAIL 자동 재생성 2회,
      실패 시 사람 말 한 줄. `simple = false` 는 기존 분리 동작
- [x] ✅ 에러 사전 286개 (`factory/i18n/errors.{ko,en}.json`) + `factory explain <CODE>`.
      `FactoryError` 코드 203개 전수 + tick 판정·게이트 위반·HTTP. 위저드와 publish 실패
      경로가 같은 사전을 읽고, 실패 출력은 코드 + 사람 말 한 줄
- [x] ✅ `factory start` — doctor → 에이전트 감지(claude/codex, 둘 다면 1회 질문 후
      `factory.toml agent` 기록) → 레포에서 `claude "/wizard"` / `codex "factory wizard를
      시작해"` 실행, `--no-launch` 는 명령만 (`factory/start.py`)
- [x] ✅ `factory key set|list` — 값은 getpass 숨김 입력, 출력·argv 에 절대 안 들어감
- [x] ✅ `CLAUDE.md` = `AGENTS.md` (동일 59줄, 한영 병기) + `.claude/skills/wizard/`,
      기존 스킬 6종 머리에 "컴맹이면 /wizard" 1줄
- [x] ✅ pytest 599 passed (이전 399 → +200)
- [x] ✅ `install.sh` / `install.ps1` + 바탕화면 런처 (`factory/launcher.py`,
      `factory launcher create|remove|status`). 런처가 PATH 앞에 표준 bin 디렉토리를
      붙인다 — 더블클릭 실행은 프로필을 안 거쳐서 uv·claude·node 가 안 보였다.
      `.bat` 는 ASCII 유지(`%USERPROFILE%`), 한글 계정명도 안 깨진다
- [x] ✅ `factory remote install|remove|status` (`factory/remote.py`) — 폰 원격제어를
      launchd/schtasks/systemd/cron 에 상시 등록. 워크스페이스 신뢰 없으면 거절
- [x] ✅ 텔레그램 미리보기·컨펌 (`factory/telegram/`, `factory/review/telegram.py`).
      입구 아님 — 미리보기 + 버튼 3개 + 장애·일일요약만
- [x] ✅ 위저드 ↔ 텔레그램 배선 (`factory/wizard/{engine,apply}.py`):
      `review = "telegram"` + 토큰·chat 있을 때만 초안 단계에서 `telegram preview
      --brief`(버튼 없음), 큐 적재 직후 `--item`(버튼 3개). 미설정이면 조용히 터미널만.
      터미널·폰 중 **먼저 온 답 채택**, `extra.edit_request` 는 `brief revise` 로 소비 후
      즉시 비움. 고쳐줘는 serve 가 그 자리에서 승인·무장을 해제한다(안 하면 슬롯이
      고치라던 원문을 발행)
- [x] ✅ `factory doctor` 에 telegram·phone remote 행 (둘 다 blocking 아님)
- [ ] ❌ 지인 1명 실측: `factory start` → setup 7문항 → 첫 글 발행까지 혼자 가는지
- [ ] ❌ **실기 미검증 (전부 모킹·드라이런만)**:
      ① `install.sh`/`install.ps1` 실행 ② 바탕화면 런처 더블클릭 ③ `factory remote
      install` 후 폰 앱 접속 ④ 텔레그램 실전송(봇 생성·버튼 누름·고쳐줘 왕복)
      ⑤ 윈도우 호스트 전반

## P2
- [x] ✅ 영상 리뷰 sendVideo 인라인 (`factory/review/telegram.py`, 문서첨부 금지 규약 포함)
- [ ] ❌ 테이크 접합부 FRAME_JUMP (멀티테이크 실측에 남은 유일한 bg_motion 위반)

## P3
- [ ] ❌ Meta 앱 `threads_content_publish` 등 검수 제출 (공개 전환 시)
- [ ] ❌ SEO/GEO 감사 모듈
- [ ] ❌ 공개 정리 (라이선스·예시 프로필 검수)
