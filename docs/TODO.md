# contents-factory TODO

> 갱신 2026-10-14. 근거 표기: ✅(파일) / 🟡(부분) / ❌(미구현)

## 액션플랜 (주원 손)
- [ ] 🟡 §8-3 실발행 5종 — **큐 4종이 approved+armed 로 대기 중**(2026-10-14, 아래 표).
      실발행 클릭은 주원 몫. 네이버만 미준비(aside 세션 없음)
- [ ] ❌ saju-website `src/app/api/threads/exchange/route.ts` 배포 + main 머지
      (tsc 통과, 미커밋·미배포. `factory auth threads` 의 위임 교환 경로가 이걸 쓴다)
- [ ] ❌ §8-4 Threads 테스터 경로 검증 — 새 계정을 앱 역할 테스터로 추가 → 초대 수락 →
      `factory auth threads --profile <name> --app-id <ID> --redirect <URI>` → 1건 발행
- [ ] ❌ 네이버 aside 로그인 세션 준비
- [ ] ❌ 지인 1명 온보딩 실측 (클론 → /setup → /writing-factory → 큐 → 드라이런)

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
- [x] ✅ gen image 드라이런 (`factory/generators/image.py`) — 실호출 미검증
- [x] ✅ pytest 252 passed
- [x] ✅ `factory auth threads` OAuth (`factory/auth/`) + Instagram 스텁, setup SKILL 반영
- [x] ✅ `factory.toml [extra_corpus]` — repeat_guard 에 외부 발행 이력 합류
- [x] ✅ `factory queue add --extra key=value`
- [x] ✅ `factory media host --item q_...` (`media_host` 를 부르는 쪽이 아예 없었다)
- [x] ✅ 운영 프로필 `unni`·`sujin` 구성 (`~/.contents-factory/profiles/`)

## P1
- [ ] ❌ TikTok MCP 토큰이 24h 만료인데 팩토리는 정적 `HIGGSFIELD_MCP_TOKEN` 만 읽는다.
      원본 파이프라인은 refresh_token 으로 조용히 갱신한다 — 갱신 경로 이식 필요
- [ ] ❌ `factory auth instagram` 교환 구현 (현재 동의 URL + 수동 절차만)
- [ ] ❌ 프로필 옵션 `auto_approve` (armed 게이트 유지)
- [ ] ❌ 네이버 이미지 업로드 (`IMAGE_UPLOAD_UNSUPPORTED` 해제)
- [ ] ❌ gen image 실호출 1건 + 크레딧 원장 기록 검증
- [ ] ❌ 텔레그램 리뷰 채널 (`factory/review/telegram.py`)
- [ ] ❌ CTA 로테이션 적용 로직 (`platforms.json cta_rotation`)
- [ ] ❌ 미구현 게이트 SKIP 해소: text_legibility·logo_geometry·product_identity

## P2
- [ ] ❌ gen video (스틸→Seedance→TTS→자막→조립) + 게이트 stt_wpm·limb_motion·bg_motion·identity
- [ ] ❌ 영상 리뷰 sendVideo 인라인

## P3
- [ ] ❌ Meta 앱 `threads_content_publish` 등 검수 제출 (공개 전환 시)
- [ ] ❌ SEO/GEO 감사 모듈
- [ ] ❌ 공개 정리 (라이선스·예시 프로필 검수)
