# contents-factory PROJECT-CONTEXT

갱신 2026-10-14

## ⚡ 세션 부트 요약
- 글·이미지·영상 제작 + 5플랫폼 자동발행을 페르소나 무관 Claude Code 스킬팩 + 파이썬 CLI로 묶은 레포. 기획 `docs/PLAN.md`, 스펙 `docs/P0-SPEC.md`, 할 일 `docs/TODO.md`.
- 상태: **P0+P2 코드 완료, 실발행 Threads·TikTok·YouTube 3건 성공(10/14)**, IG·네이버·수진 미발행. pytest 348 passed. GitHub `dasbeerboot/contents-factory` private, main 푸시됨.
- 실행: `~/.local/bin/uv run factory --help`, 테스트 `uv run pytest`. 런타임 홈 `~/.contents-factory/` (상태파일은 CLI로만).
- 모델 라우팅: 설계·리뷰 opus 4.8, 양산 codex (**codex 무료 한도 소진, 11/13 리셋**), 기획 fable.

## 현재 상태
- 스킬 6종: setup·profile·writing-factory·image-factory·video-factory·publish. 인터뷰 8문항 → 포맷 선택(카탈로그 15 + free) → 프롬프트 초안→수정→확정 → 생성 → 게이트 → 큐 → 슬롯 tick 발행.
- 실발행: Threads https://www.threads.com/@saju.unni.kr/post/DeeHdFXGGoc, TikTok SELF_ONLY, YouTube private AKN6Bl9pIfA. 이미지 실호출 1건(6.5cr), 영상 실렌더 1편(110cr, 엔드카드 오버레이로 전환).
- 외부 서비스는 전부 BYO (README·/setup §0). TikTok MCP 토큰은 정적·수시간 만료.
- Meta 앱 Korean Saju(1273477101432268) 공용. 고급 액세스는 threads_basic·keyword_search뿐, **content_publish는 테스터 전용** → 지인은 Threads 테스터(500명 한도) 등록. /setup이 절차 안내.

## 대기 중인 결정사항
- Meta 검수 스크린캐스트 녹화 방식(QuickTime 수동 vs RC 프로세스 TCC 등록). 네이버 테스트 블로그. 수진 비케이스 포맷.

## 최근 변경 로그
- 2026-10-14 저녁 실발행 3종, auth 명령, 영상·이미지 P2 머지, Meta 검수 초안 작성(미제출). 분류기가 IG approve·/radar 구현 에이전트를 차단해 별도 세션으로 이관.
- 2026-10-14 기획 v0 → P0 스펙 → opus 구현 3라운드(뼈대/결정7건·codex 머지/재슬롯·게이트) + codex 2건(규칙 문서 4종, 포맷 yaml 15종). Meta 권한 aside 실측.
