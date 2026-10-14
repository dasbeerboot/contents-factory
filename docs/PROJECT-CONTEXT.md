# contents-factory PROJECT-CONTEXT

갱신 2026-10-14

## ⚡ 세션 부트 요약
- 글·이미지·영상 제작 + 5플랫폼 자동발행을 페르소나 무관 Claude Code 스킬팩 + 파이썬 CLI로 묶은 레포. 기획 `docs/PLAN.md`, 스펙 `docs/P0-SPEC.md`, 할 일 `docs/TODO.md`.
- 상태: **P0 코드 완료, 실발행 0건**. pytest 252 passed. GitHub `dasbeerboot/contents-factory` private, main 푸시됨.
- 실행: `~/.local/bin/uv run factory --help`, 테스트 `uv run pytest`. 런타임 홈 `~/.contents-factory/` (상태파일은 CLI로만).
- 모델 라우팅: 설계·리뷰 opus 4.8, 양산 codex (**codex 무료 한도 소진, 11/13 리셋**), 기획 fable.

## 현재 상태
- 스킬 6종: setup·profile·writing-factory·image-factory·video-factory·publish. 인터뷰 8문항 → 포맷 선택(카탈로그 15 + free) → 프롬프트 초안→수정→확정 → 생성 → 게이트 → 큐 → 슬롯 tick 발행.
- publisher 5종 전부 `--dry-run`만 검증. gen image는 argv 조립까지, gen video 없음.
- Meta 앱 Korean Saju(1273477101432268) 공용. 고급 액세스는 threads_basic·keyword_search뿐, **content_publish는 테스터 전용** → 지인은 Threads 테스터(500명 한도) 등록. /setup이 절차 안내.

## 대기 중인 결정사항
- 없음. 주원 손 작업은 TODO 액션플랜 4건.

## 최근 변경 로그
- 2026-10-14 기획 v0 → P0 스펙 → opus 구현 3라운드(뼈대/결정7건·codex 머지/재슬롯·게이트) + codex 2건(규칙 문서 4종, 포맷 yaml 15종). Meta 권한 aside 실측.
