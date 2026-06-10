# 토요일 정책 플래그 승인 흐름 — 설계 (2026-06-05)

## 배경 / 문제
운용지원실장(ops_support)은 전술 파라미터는 적극 튜닝하되, 정책/구조 플래그
(`config.OPS_PROTECTED_KEYS = {ALLOW_US_STOCKS, ALLOW_DERIVATIVES, ENABLE_CHEAP_FALLBACK,
DETERMINISTIC_SCORING}`)는 자율로 바꿀 수 없다(2026-06-05 거버넌스). 사장 지시:
**평일엔 계속 차단하되, 토요일(주간 점검)엔 사장 승인을 받아 정책 플래그를 바꿀 수 있게** 한다.

## 트리거별 정책 키 처리
| 트리거 | 전술 파라미터 | 정책 플래그 |
|---|---|---|
| cycle (평일·시간당) | 적극 적용 | 차단(드롭, 현행 유지) |
| weekly (토 06시 KST) | 적극 적용 | **승인 대기함 회부 → 사장 승인 시 적용** |
| manual (사장 직접지시) | 적용 | 즉시 적용(현행 유지) |

## 컴포넌트

### 1) `infra/ops_param_clamp.partition_protected(overrides, trigger) -> (kept, to_review, notes)`
기존 `strip_protected`를 대체한다.
- `trigger == "manual"` → `kept = 전체`, `to_review = {}`, `notes = []` (사장 권위).
- `trigger == "weekly"` → 정책 키는 `to_review`로, 나머지는 `kept`로. 각 정책 키에 회부 note.
- 그 외(cycle) → 정책 키는 드롭(note), 나머지 `kept`. (현행 strip_protected 동작과 동일.)

### 2) `infra/policy_approval_inbox.py` (신규)
Coresight 인박스를 모델로 한 **per-uid** 승인 큐. **단, 승인 시 지시문이 아니라
오버라이드를 적용**한다(`profile_overrides.set_overrides`).
- 저장: `data/profiles/<uid>/policy_pending.json`.
- `enqueue(uid, key, proposed_value, current_value, rationale) -> dict`
  - `id = key` (플래그당 1건). 동일 키 재제안 시 **최신값으로 갱신**(중복 누적 없음), status 를 pending 으로 되돌림.
- `list_pending(uid) -> list[dict]` — status=="pending" 만, 최신순.
- `approve(uid, key) -> bool` — `profile_overrides.set_overrides(uid, {key: proposed_value})` 적용, status="approved", approved_at 기록.
- `reject(uid, key) -> bool` — status="rejected" (목록에서 사라짐) 또는 제거.
- admin 전용 아님 — 계정 소유자가 자기 계정 정책을 승인.

항목 스키마: `{id, key, proposed_value, current_value, rationale, proposed_at, status, label}`.

### 3) ops 워커 연결 (`infra/ops_support_worker.py:_handle_param_tuning`)
gate 통과 후:
```
kept, to_review, prot_notes = partition_protected(raw_ov, trigger)
raw_ov = kept                      # 전술/허용 키만 clamp→set_overrides
for key, val in to_review.items(): # weekly 정책 키만
    cur = runtime.get(key, uid=actor_uid)
    policy_approval_inbox.enqueue(actor_uid, key, val, cur, rationale)
    # 알림: log_response_event(uid) 로 "정책 변경 승인 대기" agent_msg 발신
```
`prot_notes` 는 rationale 에 합류. clamp 는 `kept` 에만 적용.

### 4) 대시보드 / 알림
- `server/app.py` 엔드포인트(로그인 uid 기준):
  - `GET  /api/policy_changes/pending` → `{pending: [...]}`
  - `POST /api/policy_changes/approve` `{key}` → 적용
  - `POST /api/policy_changes/reject`  `{key}`
- `server/static/index.html`: "🔐 정책 변경 승인 대기" 섹션 — 항목별 `현재값 → 제안값` + 근거 + **[승인]/[거부]** 버튼. pending 0건이면 섹션 숨김.
- 모바일: 기존 피드로 알림 텍스트만 수신(승인 버튼은 대시보드). **APK 재빌드 불필요**.

## 데이터 흐름
weekly ops → param_overrides 제안 → gate → partition_protected →
  (전술) clamp → set_overrides(즉시) ·
  (정책) enqueue(policy_pending.json) + 알림 → [사장이 대시보드에서 승인] →
  approve → set_overrides(정책 적용, 다음 로그인/런타임 반영).

## 에러 처리
- enqueue/approve I/O 실패는 fail-soft(로그 경고), ops 사이클을 죽이지 않음.
- approve 시 키가 STRATEGY_TUNABLE_KEYS 밖이면 set_overrides 가 조용히 탈락(기존 화이트리스트).
- 동일 키 중복 제안은 갱신으로 흡수(누적 방지).

## 테스트 (TDD)
- `partition_protected`: manual(전부 kept)/weekly(정책→to_review)/cycle(정책 드롭) × 전술키 통과.
- `policy_approval_inbox`: enqueue 신규·중복갱신, approve→set_overrides 호출+status 전이, reject 제거, list_pending 필터.
- 워커 통합: weekly 정책키 enqueue·전술키 set_overrides / cycle 정책키 미적용(기존 보장 유지) / manual 정책키 즉시 적용.

## 범위 제외 (YAGNI)
- 모바일 네이티브 승인 UI(알림만).
- pending 자동 만료.
- 정책 변경 이력 별도 대시보드(ops_history 로 충분).
