# contents-factory PROJECT-CONTEXT

갱신 2026-10-15

## ⚡ 세션 부트 요약
- 글·이미지·영상 제작 + 5플랫폼 자동발행을 페르소나 무관 Claude Code 스킬팩 + 파이썬 CLI로 묶은 레포. 기획 `docs/PLAN.md`, 스펙 `docs/P0-SPEC.md`, 할 일 `docs/TODO.md`.
- 상태: **P0+P2+PLAN §9(컴맹 플로우) 코드 완료, 실발행 Threads·TikTok·YouTube 3건 성공(10/14)**, IG·네이버·수진 미발행. pytest 798 passed, 1 skipped. GitHub `dasbeerboot/contents-factory` private — §9 작업분(위저드·텔레그램·설치) 3커밋은 **로컬 main 에만 있고 push 안 됨**.
- OS: macOS·Windows·Linux 지원. `factory init --os`가 `factory.toml`의 `os`에 기록하고 스케줄러(launchd/schtasks/cron)·네이버 백엔드·크레덴셜 권한이 그걸 읽는다. 호스트 점검은 `factory doctor`. **Windows 실기 검증은 미실시(macOS 모킹 테스트만)**.
- 실행: `~/.local/bin/uv run factory --help`, 테스트 `uv run pytest`. 런타임 홈 `~/.contents-factory/` (상태파일은 CLI로만).
- 모델 라우팅: 설계·리뷰 opus 4.8, 양산 codex (**codex 무료 한도 소진, 11/13 리셋**), 기획 fable.

## 현재 상태
- **입구는 두 겹이다.** 컴맹용 = `factory start` → 에이전트(`claude "/wizard"` / `codex`) → `factory wizard`(setup 7문항 / make 4문항). 전문가용 = 기존 스킬 6종. 스킬 머리마다 "컴맹이면 /wizard" 1줄.
- 위저드는 CLI가 두뇌다. 질문·검증·포맷 선택·게이트 재생성·승인 규칙이 전부 `factory/wizard/` 에 있고, 에이전트는 `next` → 사용자에게 묻기 → `answer` 루프만 돈다. 계약은 루트 `CLAUDE.md` = `AGENTS.md`(동일 59줄).
- 오류는 코드 하나당 할 일 하나: `factory explain <CODE>`, 사전 `factory/i18n/errors.{ko,en}.json` 314개(FactoryError 전수 + tick 판정·게이트 위반·HTTP·런처·원격·텔레그램). 위저드와 publish 실패가 같은 사전을 읽는다.
- 폰은 입구가 아니다. `review = "telegram"` + 토큰·chat 있을 때만 위저드가 초안을 폰에 미러링하고(`telegram preview --brief`, 버튼 없음), 큐 적재 직후 버튼 3개를 보낸다(`--item`). **터미널·폰 중 먼저 온 답 채택**, 고쳐줘 한 줄은 `brief revise` 로 소비하고 그 자리에서 승인·무장 해제.
- 설치는 한 줄(`install.sh`/`install.ps1`) + 바탕화면 런처(`factory launcher create`). 런처가 PATH 앞에 표준 bin 디렉토리를 붙이고, `factory start` 는 에이전트를 절대경로로 실행한다 — 더블클릭 실행은 프로필을 안 거친다.
- 스킬 6종: setup·profile·writing-factory·image-factory·video-factory·publish. 인터뷰 8문항 → 포맷 선택(카탈로그 15 + free) → 프롬프트 초안→수정→확정 → 생성 → 게이트 → 큐 → 슬롯 tick 발행.
- 실발행: Threads https://www.threads.com/@saju.unni.kr/post/DeeHdFXGGoc, TikTok SELF_ONLY, YouTube private AKN6Bl9pIfA. 이미지 실호출 1건(6.5cr), 영상 실렌더 1편(110cr, 엔드카드 오버레이로 전환).
- 외부 서비스는 전부 BYO (README·/setup §0). **Higgsfield는 붙여넣을 키가 없다** — `higgsfield auth login`(CLI OAuth)이고 TikTok MCP bearer는 매 요청 직전 `higgsfield auth token`이다. 정적 `HIGGSFIELD_MCP_TOKEN` 폐기(10/15).
- 네이버 백엔드 2종: `aside`(macOS 15+ 전용) / `playwright`(영속 프로필 `state/naver-profile`, `factory naver login`). `platforms.json`의 `naver_blog.backend`, 비우면 os가 결정. **playwright 실발행 경로는 미실측(dry-run·로그인까지만)**.
- Meta 앱 Korean Saju(1273477101432268) 공용. 고급 액세스는 threads_basic·keyword_search뿐, **content_publish는 테스터 전용** → 지인은 Threads 테스터(500명 한도) 등록. /setup이 절차 안내.

## 대기 중인 결정사항
- Meta 검수 스크린캐스트 녹화 방식(QuickTime 수동 vs RC 프로세스 TCC 등록). 네이버 테스트 블로그. 수진 비케이스 포맷.

## 최근 변경 로그
- 2026-10-15 PLAN §9 컴맹 플로우 완성 3커밋(로컬 main): ①위저드 두뇌+에러 사전+`factory start` ②텔레그램 미리보기·컨펌 머지 ③한 줄 설치·런처·폰 원격제어 머지 + 배선(위저드↔텔레그램 먼저 온 답 채택, 런처 PATH, doctor telegram·phone remote 행). 워크트리 2개·브랜치 2개 정리. pytest 399→798. **실기 검증 0회** — 설치 스크립트 실행·런처 더블클릭·텔레 실전송·원격 폰 접속은 전부 모킹·드라이런만.
- 2026-10-15 Higgsfield 인증을 CLI(`auth token`) 호출로 전환 + Windows 지원(osinfo·schtasks·doctor·네이버 playwright 백엔드). 실기 Windows·네이버 실발행 검증은 남아 있음.
- 2026-10-14 저녁 실발행 3종, auth 명령, 영상·이미지 P2 머지, Meta 검수 초안 작성(미제출). 분류기가 IG approve·/radar 구현 에이전트를 차단해 별도 세션으로 이관.
- 2026-10-14 기획 v0 → P0 스펙 → opus 구현 3라운드(뼈대/결정7건·codex 머지/재슬롯·게이트) + codex 2건(규칙 문서 4종, 포맷 yaml 15종). Meta 권한 aside 실측.
