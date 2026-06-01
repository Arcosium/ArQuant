# 에이전트 조직개편 + thesis 버그 수정 설계 (2026-05-29)

## 배경
5/28 밤 US장 ~ 5/29 아침 KR장 1사이클 로그(uid=1) 검토 결과:
- 매수 → 1~2h 만에 소폭 차익 청산하는 **단타 churn** 반복(JBHT +1.5%/70분 등).
- 펀드기획팀장이 사이클 내내 **무발언** → 사장 지적.
- 운용전략실장 final_report가 리스크 반려된 매수를 "최종 매수 선정"으로 **허위 보고**(#62).

### 근본 원인 (펀드기획팀장 무발언)
`main_swarm.py`:
- `3708 ok = filled` / `3724 if ok: _record_buy_thesis(...)` / `3863 if not rec.get("ok"): return`
- US 시장가 매수는 주문시점 `filled=False`(체결은 `_poll_fills_until_confirmed` 폴링으로 확정) → thesis 기록 분기를 **영원히 못 탐**.
- 폴링 확정 경로(`2197~`)엔 `_record_buy_thesis` 호출 **누락**.
- 결과: US 종목 thesis 0건 → `format_thesis_reminder` 빈 문자열 → 사후관리실장 프롬프트에 아무것도 주입 안 됨.
- KR 매수만 ~2초 내 `filled=True` → KR thesis만 존재(uid=2의 402340). **CLAUDE.md가 경고한 KR/US 비대칭 버그 재현.**

## 컴포넌트

### ① thesis 기록 비대칭 수정 (버그)
- thesis 기록의 단일 진실 소스를 "체결 확정 시점"으로 통일.
- `_poll_fills_until_confirmed`에서 매수 체결 확인 시 `_record_buy_thesis` 호출 추가.
- KR 즉시확정 경로 유지. 양쪽 모두 "이미 thesis 있으면 skip" 중복가드.
- 테스트: US 비동기 체결 → thesis 기록되는지 (TDD).

### ② 펀드기획팀장 → 펀드기획실장 (결정론적 거부권)
`_parse_sell_decisions` 직후 게이트:
- 손절가 터치(`cur ≤ stop`) → 매도 허용/우선 (손절 절대 비차단)
- 목표가 도달(`cur ≥ target`) → 매도 허용
- 그 외 **계획 보유기간 미경과 + 소폭 이익(현재가 > 진입가) + 손절·목표 미해당**인데 매도결정 → `보유` 강제 오버라이드 + "📛 펀드기획실장 거부권 발동" _emit
- **손실 종목 손절 매도는 비차단**(손실 가두기 방지)
- `ALLOW_DAY_TRADING=ON` 또는 `THESIS_VETO_ENABLED=False`면 비활성
- "주문 절대 스킵 금지"와 무충돌: 의사결정 단계 오버라이드(리스크 매수반려와 동급), 투명 로깅.

### ③ 운용전략실장 리포트 가드
- `main_swarm.py:3935` 리포트 프롬프트에 리스크 반려 주문 목록+사유 명시 주입.
- 지침 추가: "후보 선정 ≠ 체결. 반려·미체결을 '매수 완료'로 쓰지 말 것."

### ④ 운용지원실장 신규 튜닝 파라미터 2종
기존 포지션사이징·예산 파라미터는 이미 충분(MAX_TRADES_PER_CYCLE, PER_ORDER_BUDGET_RATIO, MAX_CYCLE_BUDGET_RATIO, MIN_HOLDING_DAYS_FOR_SELL, ALLOW_DAY_TRADING, STOP/TAKE_PROFIT_PCT …). 신규 에이전트 불필요.
신규 추가(STRATEGY_TUNABLE_KEYS + META + 프리셋 등록):
- `THESIS_VETO_ENABLED` (bool, 기본 True) — 펀드기획실장 거부권 토글. ②가 의존.

검토 결과 `MIN_ORDER_NOTIONAL_RATIO`는 **추가 안 함**: 관찰된 churn(빠른 왕복 + 1주 단위)은
thesis 거부권 + 기존 PER_ORDER_BUDGET_RATIO·MAX_TRADES_PER_CYCLE·ALLOW_DAY_TRADING로 커버.
'현금 대비 작은 매수 스킵' 노브는 비싼 종목 1주 문제를 못 풀어 죽은 노브가 됨(YAGNI).

거부권은 ALLOW_DAY_TRADING과 결합(사장 확정 2026-05-29): 데이트레이딩 ON이면 거부권 OFF.
→ 기본 balanced 프리셋(데이트레이딩 ON)에선 휴면. churn 차단하려면 ALLOW_DAY_TRADING=False
  (defensive/conservative 프리셋) 또는 해당 프로필 오버라이드 필요.

## 배포
일괄 구현 → `python3.11 -m pytest` 전체 통과 → 사장 확인 후 `arquant.service` 재시작 1회.
