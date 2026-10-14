# 발행 규칙

## 목적

이 문서는 콘텐츠 큐, 승인, 슬롯, 자동발행, 플랫폼별 발행기의 공통 운영 규칙이다.

목표는 승인과 발행을 분리하고, 슬롯 시각과 일일 상한을 지키며, 실패나 지각분이 조용히 중복 발행되지 않게 하는 것이다.

## 절차

1. 모든 발행 후보는 큐 아이템으로 만든다. 큐에는 id, profile, platform, kind, text, media, scheduled_at, approved, armed, published 상태가 있어야 한다.
2. scheduled_at은 UTC ISO 형식으로 기록한다. 로컬 문자열, 오프셋 문자열, 존재하지 않는 시각은 거부한다.
3. 큐 작성과 수정은 소유 CLI나 API만 사용한다. 상태 파일을 손으로 편집하지 않는다.
4. 새 아이템을 넣을 때는 런타임 필드를 미리 채우지 않는다. published, published_id, retry_count, auto_approved, hold, delivery 계열 필드는 발행기가 쓴다.
5. 승인과 발행은 다르다. approved=true여도 슬롯 전에는 발행하지 않는다.
6. armed=false면 approved=true여도 발행하지 않는다. arm 명령은 근거 문자열이 있어야 한다.
7. tick은 정해진 주기로 큐를 훑고, 슬롯 시각이 도래한 아이템만 처리한다.
8. 슬롯에 늦은 항목은 late_policy를 적용한다. 기본은 carry_once로 다음 슬롯 1회 이월하고, drop이면 폐기한다.
9. 자동승인 보조창이 있는 구현은 max_late를 둔다. max_late를 넘긴 미승인 잔재는 자동 승인하지 않고 hold 또는 stale로 남긴다.
10. 발행 직전 repeat_guard를 다시 한 번 실행한다. 큐 적재 시 통과했어도 발행 직전 실패하면 발행하지 않는다.
11. 플랫폼별 profile+platform 일일 상한을 확인한다. daily_cap을 넘으면 다음 슬롯으로 넘기거나 보류한다.
12. 플랫폼 토큰은 만료 7일 전부터 terminal 또는 telegram으로 경고한다.
13. 브라우저 세션이 필요한 발행기는 로그인 세션을 먼저 확인한다. 세션이 없으면 전용 에러 코드로 큐를 유지한다.
14. 발행 결과는 원장에 append-only로 남긴다. 성공 id, 실패 코드, retry_count, hold 사유를 덮어쓰기 없이 추적한다.
15. 실패는 코드형으로 기록한다. secret, presigned URL, 토큰, 원문 예외 메시지를 로그나 알림에 그대로 남기지 않는다.

P0 필수 규칙:

- 승인 ≠ 발행. 슬롯 시각(UTC)에만 tick이 발행한다.
- 승인 후 지각분은 late_policy를 적용한다. carry_once가 기본이고 drop을 선택할 수 있다.
- armed=false면 approved여도 발행하지 않는다. arm 명령은 근거 문자열 필수다.
- 같은 프로필과 플랫폼의 일일 상한은 platforms 설정의 daily_cap을 따른다.
- repeat_guard는 발행 직전에 다시 1회 실행한다.
- 토큰 만료 7일 전 terminal 또는 telegram 경고가 있어야 한다.
- 네이버 발행은 로그인된 aside 세션이 필요하다. 미로그인은 NAVER_SESSION_MISSING으로 큐를 유지한다.

## 게이트(PASS·FAIL 기준)

큐 게이트 PASS:

- scheduled_at이 UTC ISO 형식이다.
- id가 큐 전체에서 중복되지 않는다.
- text가 비어 있지 않고 플랫폼 길이 제한 안에 있다.
- media가 필요한 포맷은 로컬 파일 경로 또는 공개 URL이 있다.
- approved, armed, published 상태가 명확하다.
- 런타임 전용 필드를 편성 단계에서 미리 쓰지 않았다.
- cancelled, rejected, hold 상태가 아니거나 해제 근거가 남아 있다.

큐 게이트 FAIL:

- 날짜 파일 이름과 scheduled_at이 다르다는 이유로 손으로 파일을 옮긴다.
- 발행된 항목을 재슬롯한다.
- 본문이나 답글이 빈 문자열이거나 제한을 넘는다.
- 큐 전체에 같은 id가 이미 있다.
- 런타임 필드가 편성 입력에 들어 있다.

자동발행 게이트 PASS:

- 자동발행 스위치가 ON이다.
- approved가 아직 true가 아니고, published가 false다.
- rejected, cancelled, hold가 아니다.
- now >= scheduled_at이고 지각 시간이 허용 범위 안이다.
- 린트와 repeat_guard가 통과했다.
- media가 있으면 파일 또는 URL이 확인된다.
- 통과 로그에 id, scheduled_at, lint 결과가 남았다.

자동발행 게이트 FAIL:

- 스위치가 OFF다.
- 지각 상한을 넘었다.
- 린트 실패, 반복 가드 실패, 파일 누락, 토큰 문제, 로그인 세션 누락이 있다.
- 사람이 취소하거나 반려한 항목이다.

발행 게이트 PASS:

- 플랫폼별 daily_cap 안에 있다.
- armed=true이고 근거가 있다.
- 토큰이 유효하거나 갱신 가능하다.
- 발행 전 repeat_guard가 PASS다.
- 중복 방지 원장에 이미 published인 같은 키가 없다.
- 컨테이너형 플랫폼은 생성, 처리 완료, publish 단계를 각각 확인했다.

발행 게이트 FAIL:

- approved=true라도 armed=false다.
- 슬롯 전 또는 허용되지 않은 즉시발행이다.
- 토큰 만료, 세션 없음, 공개 URL 없음, 컨테이너 오류가 있다.
- 플랫폼 API가 성공 id를 주지 않았는데 published로 표시한다.
- 실패가 났는데 다음 런에서 무한 재시도한다.

관측 게이트 PASS:

- 스킵도 reason을 남긴다. 예: cancelled, rejected, hold, no_scheduled_at, bad_scheduled_at, not_due.
- 아무 발행도 없을 때 하트비트와 대상 없음이 구분된다.
- dry-run은 큐를 쓰지 않고 어떤 동작을 했을지 출력한다.
- watchdog은 오래 걸린 단일 인스턴스를 종료하고 다음 tick이 이어받게 한다.

## 금지

- 승인과 발행을 같은 의미로 취급하기.
- 등록 기본값을 자동발사로 두기.
- 근거 없이 armed=true로 바꾸기.
- 지각 상한 없이 오래된 미승인 잔재를 한꺼번에 발행하기.
- 큐 JSON, 상태 JSON, 토글 파일을 손으로 편집하기.
- 발행 성공을 확인하기 전에 published=true로 쓰기.
- 플랫폼 실패, 세션 누락, 토큰 실패를 조용히 삼키기.
- cancelled 또는 rejected 항목을 approved=true만 보고 발행하기.
- 같은 날짜와 슬롯에 플랫폼이 허용하는 수보다 많은 항목을 걸기.
- secret, 토큰, presigned URL, 원문 예외 메시지를 알림이나 로그에 노출하기.
- 네이버 로그인 세션이 없는데 실패 처리로 큐를 버리기.

## 예시

좋은 큐 처리:

- 새 글은 approved=false, armed=false로 적재한다.
- 슬롯이 도래하면 repeat_guard와 린트를 다시 돌린다.
- approved=true지만 armed=false인 항목은 QUEUE_NOT_ARMED로 스킵한다.
- 토큰 만료가 7일 이내면 발행 전 별도 경고를 띄운다.

나쁜 큐 처리:

- "승인됐으니 지금 바로 내보내자"라고 판단한다.
- 오래된 미승인 항목을 자동발행 ON과 동시에 모두 발사한다.
- 실패했는데 published=true로 바꾼다.
- 브라우저 로그인이 없어서 실패한 항목을 삭제한다.

슬롯 예시:

- PASS: `2026-10-15T12:00:00Z` 슬롯에 맞춰 큐에 넣고 tick이 해당 시각 이후 처리한다.
- FAIL: `내일 저녁` 같은 로컬 문자열을 큐에 넣는다.

플랫폼 예시:

- PASS: 공개 URL이 필요한 플랫폼은 먼저 media_host에 올리고 처리 완료 상태를 확인한 뒤 publish한다.
- FAIL: 로컬 파일 경로를 API에 직접 넘기거나, 컨테이너가 처리 중인데 성공으로 기록한다.
